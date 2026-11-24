"""Append-only checkpoints and final safety notices for a local project migration."""
import argparse
from datetime import datetime, timezone
from functools import partial
import json
import os
from pathlib import Path
import sys
from uuid import UUID, uuid4

from cutover import MigrationError, local_path, real_dir, save_exclusive, verify_link_target
from tree_copy import POLICY_KEY, changed_since, read_hardlink_policy, verify_manifest


FINISHED = {"passed", "not_applicable", "unverified"}


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def absolute(value):
    path = Path(value)
    if not path.is_absolute() or path == Path(path.anchor) or ".." in path.parts:
        raise ValueError(f"Choose an absolute path below the volume root: {value}")
    if os.name == "nt":
        for part in path.parts[1:]:
            if (len(path.drive) != 2 or part.endswith((" ", ".")) or Path(part).is_reserved()
                    or any(c in '<>:"|?*' or ord(c) < 32 for c in part)):
                raise ValueError(f"Invalid local path: {value}")
    return path


def read(path):
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"Expected an ordinary receipt: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def volume_id(path):
    if sys.platform == "darwin":
        import mac_fs
        return mac_fs.volume(path)
    import migration_ntfs as nt
    nt.volume(path)
    return nt.physical(path).drive.casefold()


def volumes(old, new):
    source, target = (volume_id(path) for path in (old, new))
    same = source == target
    message = "这次移动不会释放这个盘的空间。确认仍要整理目录后再继续。" if same else "源和目标位于不同的本地盘。"
    return dict(source_volume=source, target_volume=target, same_volume=same, message=message)


def create_run(args, run):
    materialize = read_hardlink_policy(vars(args))
    if materialize and os.name != "nt":
        raise ValueError("Dependency hardlink materialization requires Windows NTFS.")
    old, new = absolute(args.source), absolute(args.target)
    backup = old.with_name(old.name + "_迁移备份")
    if not old.is_dir() or old.resolve(strict=True) != old or new.resolve() != new:
        raise ValueError("Select physical source and destination directories.")
    if not new.parent.is_dir() or not run.parent.is_dir() or run.parent.resolve() != run.parent:
        raise ValueError("Choose existing physical destination and private audit parent directories.")
    for path in (backup, new):
        if os.path.lexists(path):
            raise FileExistsError(f"Already occupied: {path}. Check for a previous run.")
    paths = (old, new, backup, run)
    if os.name == "nt":
        import migration_ntfs as nt
        from tree_copy import check_disjoint
        check_disjoint(paths)
    else:
        import mac_fs
        from mac_tree import disjoint
        disjoint([mac_fs.checked_root(p) for p in paths])

    vol = volumes(old, new)
    if vol["same_volume"] and not args.allow_same_volume:
        raise ValueError(vol["message"] + " (--allow-same-volume)")
    if args.host == "codex" and not (args.project_id or "").strip():
        raise ValueError("Codex migrations need the existing Codex project ID.")
    if os.name == "nt":
        nt.create_secured(run, old, directory=True, protect=True)
        nt.restore_security(old, run, True)
    else:
        mac_fs.create_private(run)
    config = dict(version=2, migration_id=str(uuid4()), host=args.host,
                   project_id=args.project_id or None, source=str(old), target=str(new),
                   backup=str(backup), started=timestamp(), volumes=vol)
    if materialize:
        config[POLICY_KEY] = True
    save_exclusive(run / "run.json", config)


def read_probe(config, run, binding):
    """Only the registered request can establish filesystem evidence for this old session."""
    if not binding.get("request_id") or not binding.get("session_id"):
        raise ValueError("旧记录缺少已绑定的请求编号或原会话标识。请重新打开步骤 6，登记并复核。")
    request = str(UUID(binding["request_id"]))
    session = binding["session_id"]
    receipt = read(run / f"probe-{request}.json")
    if receipt.get("request_id") != request or receipt.get("target") != config["target"] or (
            receipt.get("backup") != config["backup"]):
        raise ValueError("指定读写回执的请求、目标或备份不匹配。")
    flags = ("default_cwd_confirmed", "same_file", "backup_isolated", "probe_removed",
             "old_write_d_read", "d_write_old_read")
    if not all(receipt.get(k) is True for k in flags) or receipt.get("filesystem_only") is not False:
        raise ValueError("指定请求必须对应完整的会话双向读写回执；纯文件系统测试不能作为旧会话证据。")
    if receipt.get("old_path_link_verified") is not True or receipt.get("source") != config["source"]:
        raise ValueError("指定请求未核实本次原名入口。")
    form = receipt.get("cwd_form")
    claimed = receipt.get("claimed_resumed_session_id")
    if claimed is not None and claimed != session:
        raise ValueError("回执声明的原会话 ID 与已登记会话不同。")
    if form == "resolved_target":
        if claimed != session or receipt.get("cwd") != config["target"]:
            raise ValueError("解析至目标的默认目录缺少匹配的恢复会话标识或路径。")
    elif form != "old_path" or receipt.get("cwd") != config["source"]:
        raise ValueError("旧会话默认工作目录未通过核对。")


def bind_probe(args, config, run, pending, events):
    """Return the durable binding for a stage-six waiting or passed event."""
    req = str(args.request_id) if args.request_id is not None else None
    session = args.session_id
    if session is not None and (not session or session != session.strip() or len(session) > 256
                                or not session.isprintable()):
        raise ValueError("Supply a non-empty host-confirmed original session ID.")
    if args.status == "waiting":
        if req is None and session is None:
            return {}
        if req is None or session is None:
            raise ValueError("Register --request-id and --session-id together before the test.")
        binding = dict(request_id=req, session_id=session)
        if binding != pending:
            previously_registered = any(e.get("request_id") == req for e in events)
            if previously_registered or os.path.lexists(run / f"probe-{req}.json"):
                raise ValueError("Choose a fresh request UUID and register it before running the probe.")
        return binding
    if pending is None:
        raise ValueError("Register the old session and request as waiting before marking the test passed.")
    if (req is not None and req != pending["request_id"]) or (
            session is not None and session != pending["session_id"]):
        raise ValueError("Step 6 must use the registered request UUID and the same session.")
    read_probe(config, run, pending)
    return pending


def current_progress(events):
    step = 1
    completed_chat = None
    pending = None
    for event in events:
        if event["status"] == "reopened":
            step = event["step"]
            if step <= 6:
                completed_chat = None
                pending = None
        elif event["step"] == 6 and event["status"] == "waiting" and event.get("request_id"):
            pending = dict(request_id=event["request_id"], session_id=event["session_id"])
        elif event["status"] in FINISHED:
            if event["step"] == 6:
                completed_chat = event
            step += 1
    return step, completed_chat, pending


def git_time_exceptions(run, old, target, *, batch=False):
    before = read(run / "git-before.json")
    if not before.get("ok") or before.get("root") != str(old):
        raise ValueError("缺少已确认的 Git 基准")
    if before["kind"] == "non_git":
        return set()
    from git_check import verify
    if batch:
        verify(target, before["snapshot"])
        return {".git/worktrees/" + tree["admin"] + "/gitdir" for tree in before["snapshot"]["linked"]}
    checks = []
    for path in run.glob("*.json"):
        if path.name in {"cutover-plan.json", "git-before.json", "run.json"}:
            continue
        if path.name.startswith(("event-", "probe-")):
            continue
        doc = read(path)
        if isinstance(doc, dict) and doc.get("mode") == "verify" and doc.get("root") == str(target):
            checks.append(doc)
    latest = max(checks, key=lambda item: item["utc"], default={})
    if latest.get("ok") is not True or latest.get("kind") != before["kind"]:
        raise ValueError("缺少成功的最新 Git 验收，或最新核验失败")
    verify(target, before["snapshot"])
    return {".git/worktrees/" + tree["admin"] + "/gitdir" for tree in before["snapshot"]["linked"]}


def verified_link(config, run):
    from cutover import state
    phase, old, target, backup = state(run / "cutover-plan.json")
    paths = (str(old), str(target), str(backup))
    if phase != "linked" or paths != (config["source"], config["target"], config["backup"]):
        raise ValueError("旧路径、目标或备份身份未经确认")
    return old, target, backup


def cleanup_check(config, run, chat):
    """A standalone run can only approve cleanup of its own backup."""
    if os.path.lexists(run / "batch-owner.json"):
        raise ValueError("此目录属于多源批次，请使用批次验收核对所有引用项目后再判断备份")
    if not chat or chat["status"] not in ("passed", "not_applicable"):
        raise ValueError("旧会话核验尚有待处理项")
    if chat["status"] == "passed":
        read_probe(config, run, chat)
    return verify_backup_files(config, run, chat["status"] == "passed")


def verify_backup_files(config, run, probe_wrote, *, batch=False):
    """Verify current contents and count backup junctions through the old entry."""
    old, target, backup = verified_link(config, run)
    folder = run / "backup-check"
    marker = max(folder.glob("*-started.json"), default=None)
    if marker is None:
        raise ValueError("缺少清理前的备份复核记录")
    tag = marker.name.removesuffix("-started.json")
    begun = read(marker)
    comparison = read(folder / (tag + "-verify.json"))
    expected = dict(action="verify", run=tag, source=str(backup), destination=str(target))
    if any(record.get(key) != value for record in (begun, comparison) for key, value in expected.items()):
        raise ValueError("最新复核的现场与本次备份不匹配")
    if any(read_hardlink_policy(record) != read_hardlink_policy(config)
           for record in (begun, comparison)):
        raise ValueError("备份复核的依赖复制选项与迁移授权不匹配")
    diffs = comparison.get("differences")
    if comparison.get("status") not in ("passed", "differences") or not isinstance(diffs, dict):
        raise ValueError("最近一次备份核验未完成")
    allowed_times = git_time_exceptions(run, old, target, batch=batch)
    if probe_wrote is True:
        allowed_times.add(".")
    elif probe_wrote:
        allowed_times.update(probe_wrote)
    if (set(diffs) - {"mtime_ns"} or set(diffs.get("mtime_ns", [])) - allowed_times
            or comparison.get("source_summary") != comparison.get("destination_summary")):
        raise ValueError("备份有独有内容、两边存在差异或发生了未解释的变更")
    if os.name == "nt":
        from tree_copy import snapshot
        backup_snapshot = partial(snapshot, junction_root=old)
    else:
        from mac_tree import snapshot
        backup_snapshot = snapshot
    trees = (("source", backup, backup_snapshot), ("destination", target, snapshot))
    current = [
        verify_manifest(root, folder / (tag + "-" + side + ".jsonl.gz"), scan, {})
        for side, root, scan in trees
    ]
    # Recheck both trees after hashing either side so a change during the other scan is caught.
    backup_junctions = 0
    for (side, root, scan), rows in zip(trees, current):
        if changed_since(rows, scan(root, False)):
            raise ValueError(f"复核期间文件清单或文件内容发生变化：{root}")
        junctions = sum(row["type"] == "junction" for row in rows.values())
        summary = comparison[side + "_summary"]
        if (junctions != summary.get("junctions", 0)
                or len(rows) != summary["files"] + summary["directories_including_root"] + junctions):
            raise ValueError("文件清单条目数量不符")
        if side == "source":
            # The snapshot accepts a backup junction only when its raw target is under old.
            backup_junctions = junctions
    verified_link(config, run)
    return backup_junctions


def notice(config, eligible, reason, *, backup_junctions=0):
    old, new, backup = (config[k] for k in ("source", "target", "backup"))
    warning = f"⚠️ 请勿删除原位置下的原名文件夹：{old}"
    try:
        target_path = local_path(new)
        verify_link_target(local_path(old), target_path)
        real_dir(target_path)
    except (MigrationError, OSError, ValueError) as exc:
        if eligible:
            eligible, reason = False, str(exc)
    else:
        kind = "Windows 目录联接" if os.name == "nt" else "macOS 目录符号链接"
        warning += (f"\n这里是通往项目新位置的{kind}，旧聊天仍可通过它访问项目。"
                    "请放心，保留这个入口几乎不占用硬盘空间。"
                    "删除入口会使旧路径失效。进入其中删除文件会直接影响目标盘上的项目文件。")
    backup_message = (f"验收通过后可手动清理的实体备份：{backup}" if eligible
                      else f"⛔ 暂勿清理备份：{backup}")
    backup_message += "。" + reason
    if eligible and backup_junctions:
        backup_message += (f"\n⚠️ 备份内有 {backup_junctions:,} 个目录联接可经旧入口访问新项目。"
                           "进入这些联接删除文件，会影响正在使用的项目。")
    return dict(warning=warning,
                target=new, target_message=f"正在使用的项目实体，必须保留：{new}",
                backup=backup, eligible=eligible, backup_message=backup_message), reason


def record(args):
    if args.command == "volumes":
        print(json.dumps(volumes(absolute(args.source), absolute(args.target)), ensure_ascii=False))
        return
    run = absolute(args.run)
    if args.command == "start":
        create_run(args, run)
    if not run.is_dir() or run.resolve(strict=True) != run:
        raise ValueError("Run directory must be physical.")
    config = read(run / "run.json")
    events = [read(p) for p in sorted(run.glob("event-*.json"))]
    step, chat, pending = current_progress(events)
    eligible = False
    backup_junctions = 0
    reason = "核验未完成"
    marking_final = step == 7 and args.command == "mark" and args.status == "passed"
    if marking_final or (step > 7 and args.command == "show"):
        try:
            backup_junctions = cleanup_check(config, run, chat)
            eligible = True
            reason = "本次已复验备份、目标和许可的时间差异；如继续编辑，请重新核验"
        except (OSError, ValueError, KeyError, RuntimeError, TypeError, MigrationError) as exc:
            reason = str(exc)

    cleanup, reason = notice(config, eligible, reason, backup_junctions=backup_junctions)
    rejected = False
    if args.command in ("mark", "reopen"):
        if args.command == "reopen":
            if not 1 <= args.step < step:
                raise ValueError("Only a completed stage can be reopened.")
            step = args.step
        elif step != args.step or not 1 <= step <= 7:
            raise ValueError(f"Expected step {step}.")
        if args.status in ("not_applicable", "unverified") and step != 6:
            raise ValueError("A chat coverage decision is only applicable at step 6.")
        if not args.evidence.strip() or not args.next_action.strip():
            raise ValueError("Provide an evidence reference and the next action.")
        event = dict(step=step, status=args.status, evidence=args.evidence,
                     next_action=args.next_action, recorded=timestamp())
        if step == 6 and args.status in ("waiting", "passed"):
            event.update(bind_probe(args, config, run, pending, events))
        elif getattr(args, "request_id", None) is not None or getattr(args, "session_id", None) is not None:
            raise ValueError("A session binding applies only to step 6 waiting or passed.")
        if marking_final and not cleanup["eligible"]:
            event.update(status="blocked", verification_error=reason)
            rejected = True
        save_exclusive(run / f"event-{len(events) + 1:06}.json", event)
        events.append(event)
        if event["status"] in FINISHED:
            step += 1

    result = dict(cleanup_notice=cleanup, run=str(run), config=config,
                  next_step=step if step <= 7 else None, last_event=events[-1] if events else None)
    print(json.dumps(result, ensure_ascii=False))
    if rejected:
        raise SystemExit(2)


def main():
    if sys.platform not in ("win32", "darwin"):
        raise SystemExit("Requires native Windows or macOS.")
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    start = commands.add_parser("start")
    preview = commands.add_parser("volumes")
    for command in (start, preview):
        command.add_argument("source")
        command.add_argument("target")
    start.add_argument("--project-id")
    start.add_argument("--host", choices=("codex", "claude-code", "generic"), default="codex")
    start.add_argument("--allow-same-volume", action="store_true")
    start.add_argument("--materialize-dependency-hardlinks", action="store_true",
                       help="Copy approved node_modules external hardlinks as independent target files")
    show = commands.add_parser("show")
    mark = commands.add_parser("mark")
    reopen = commands.add_parser("reopen")
    reopen.set_defaults(status="reopened")
    for command in (start, show, mark, reopen):
        command.add_argument("--run", required=True)
    mark.add_argument("--status", choices=("waiting", "blocked", "passed", "not_applicable", "unverified"),
                      required=True)
    mark.add_argument("--request-id", type=UUID)
    mark.add_argument("--session-id")
    for command in (mark, reopen):
        command.add_argument("--step", type=int, required=True)
        command.add_argument("--evidence", required=True)
        command.add_argument("--next-action", required=True)
    try:
        record(parser.parse_args())
    except (OSError, ValueError, KeyError, TypeError, MigrationError) as exc:
        parser.exit(1, f"Migration checkpoint: {exc}\n")


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="backslashreplace")
    main()
