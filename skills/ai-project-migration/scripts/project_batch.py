"""Coordinate approved local Codex project roots using the single-directory migration tools."""
import argparse
import json
import os
from pathlib import Path
import shlex
import stat
import subprocess
import sys
from types import SimpleNamespace
from uuid import uuid4

import cutover
import git_check
from run_record import timestamp, volumes
from tree_copy import exclusive_lock, recheck_verified_copy

HERE = Path(__file__).resolve().parent
PLAN = "batch.json"
OWNER = "batch-owner.json"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read(path):
    return cutover.ordinary_record(Path(path))[0]


def pretty(value):
    print(json.dumps(value, ensure_ascii=True))


def usable_path(value):
    """Allow aliases only as configuration references, not as cutover endpoints."""
    require(isinstance(value, (str, Path)), "Use a complete local path.")
    return cutover.local_path(value)


def physical(value):
    path = usable_path(value)
    if os.name == "nt":
        from tree_copy import checked_root
    else:
        from mac_fs import checked_root
    return checked_root(path)


def real_ref(value):
    path = usable_path(value).resolve(strict=True)
    return physical(path)


def disjoint(paths):
    if os.name == "nt":
        from tree_copy import check_disjoint
    else:
        from mac_tree import disjoint as check_disjoint
    check_disjoint(paths)


def create_private(path, parent):
    if os.name == "nt":
        from migration_ntfs import create_secured, restore_security
        create_secured(path, parent, directory=True, protect=True)
        restore_security(parent, path, True)
    else:
        from mac_fs import create_private as private_mac
        private_mac(path)


def key(path):
    """Normalize local paths while preserving the visible spelling in the UI mapping."""
    text = str(usable_path(path))
    return text.casefold() if os.name == "nt" else text


def snapshot_projects(file):
    data = read(file)
    require(data.get("version") == 1 and isinstance(data.get("host_id"), str)
            and data["host_id"].strip() and data.get("complete") is True,
            "需要同一宿主完整的项目来源清单。仅有主要路径时，请先从编辑项目补齐。")
    projects = {}
    for row in data["projects"]:
        pid = row["id"]
        sources = row["sources"]
        require(isinstance(pid, str) and pid and pid not in projects and isinstance(sources, list) and sources,
                "项目 ID 重复或来源不完整")
        roles = [s["role"] for s in sources]
        require(roles.count("primary") == 1 and all(r in ("primary", "secondary") for r in roles),
                f"确认项目的主要目录及各条角色：{pid}")
        normalized = [{"path": str(usable_path(s["path"])), "role": s["role"]} for s in sources]
        require(len({key(s["path"]) for s in normalized}) == len(normalized), f"重复的来源条目：{pid}")
        projects[pid] = {"id": pid, "name": row.get("name") or pid, "sources": normalized}
    require(projects, "未发现项目来源")
    return data["host_id"], projects


def reference_inventory(projects):
    refs = []
    for pid, project in projects.items():
        for item in project["sources"]:
            path = real_ref(item["path"])
            refs.append((pid, item, path, cutover.real_dir(path)))
    return refs


def overlaps(a, b):
    try:
        disjoint((a, b))
        return False
    except ValueError:
        return True


def legacy_hint(choice, source, target):
    legacy = choice.get("legacy_run")
    if legacy:
        config = read(physical(legacy) / "run.json")
        require((config["source"], config["target"]) == (str(source), str(target)),
                "已有单目录记录的原路径或目标与本次选择不一致")
        return f"先在原会话续做单目录检查点：{legacy}；该目标不会重新复制。"
    return "若是未完成的单目录迁移，请先按原检查点接续；已有副本不应当作新目标覆盖。"


def draft(snapshot, selection):
    host, projects = snapshot_projects(snapshot)
    requests = read(selection)
    require(isinstance(requests.get("allow_same_volume", False), bool), "同盘选择须显式确认")
    inventory = reference_inventory(projects)
    roots = []
    touched = {}
    for index, choice in enumerate(requests["sources"], 1):
        chosen = usable_path(choice["source"])
        require(any(key(item["path"]) == key(chosen) for _, item, _, _ in inventory),
                f"所选来源不在已核实的 Codex 项目清单中：{chosen}")
        source = real_ref(chosen)
        ident = cutover.real_dir(source)
        require(not any(row["source_id"] == ident for row in roots), f"同一个实体只能选择一次：{source}")
        target = physical(choice["target"])
        require(target.parent.is_dir(), f"目标的父目录不存在：{target.parent}")
        backup = source.with_name(source.name + cutover.BACKUP_SUFFIX)
        if choice.get("legacy_run"):
            raise ValueError(legacy_hint(choice, source, target))
        require(not os.path.lexists(target) and not os.path.lexists(backup),
                f"目标或备份名称已经存在：{target} / {backup}。{legacy_hint(choice, source, target)}")
        refs = [(pid, item) for pid, item, _, identity in inventory if identity == ident]
        projects_using_root = {pid for pid, _ in refs}
        require(len(refs) == len(projects_using_root), f"同一项目通过多个条目引用该实体，请先确认角色：{source}")
        confirmed = choice.get("project_ids")
        require(isinstance(confirmed, list) and len(confirmed) == len(projects_using_root)
                and set(confirmed) == projects_using_root,
                f"请确认 {source} 的所有引用项目一起换源：{', '.join(sorted(projects_using_root))}")
        for pid, cfg, referenced, identity in inventory:
            protected = (target, backup) if identity == ident else (source, target, backup)
            require(not any(overlaps(p, referenced) for p in protected),
                    f"选定目录与项目来源重叠：{pid} / {cfg['path']}")
        vol = volumes(source, target)
        require(not vol["same_volume"] or requests.get("allow_same_volume"),
                f"{source} → {target}：{vol['message']}")
        root = dict(id=f"source-{index:02}", source=str(source), target=str(target), backup=str(backup),
                    source_id=ident, volumes=vol, project_ids=[pid for pid, _ in refs],
                    references=[{"project_id": pid, **item} for pid, item in refs])
        roots.append(root)
        for pid, item in refs:
            touched.setdefault(pid, {})[key(item["path"])] = str(target)
    require(roots, "请选择本次迁移的来源")
    disjoint([Path(row[k]) for row in roots for k in ("source", "target", "backup")])
    changes = []
    for i, (pid, replacements) in enumerate(touched.items(), 1):
        project = projects[pid]
        before = project["sources"]
        after = [dict(item, path=replacements.get(key(item["path"]), item["path"])) for item in before]
        changes.append(dict(id=pid, name=project["name"], before=before, after=after,
                            receipt=f"project-{i:02}.json"))
    return dict(version=1, host_id=host, known_project_ids=list(projects), roots=roots, projects=changes)


def invoke(script, *args):
    result = subprocess.run([sys.executable, "-B", "-X", "utf8", str(HERE / f"{script}.py"),
                             *map(str, args)], capture_output=True, encoding="utf-8")
    if result.returncode:
        detail = (result.stderr or result.stdout).strip()
        raise ValueError(f"{script}: {detail or result.returncode}")
    return result.stdout.strip()


def create(args):
    batch = physical(args.batch)
    plan = draft(args.snapshot, args.selection)
    require(not os.path.lexists(batch) and batch.parent.is_dir(),
            "批次记录应使用已存在私有父目录下的新目录")
    disjoint([batch, *[Path(row[k]) for row in plan["roots"] for k in ("source", "target", "backup")]])
    cutover.plain_chain(batch.parent)
    # Verify that audit output stays outside every configured project, including untouched ones.
    _, all_projects = snapshot_projects(args.snapshot)
    for project in all_projects.values():
        for source in project["sources"]:
            require(not overlaps(batch, real_ref(source["path"])), "批次记录不能放进项目来源中")
    create_private(batch, batch.parent)
    plan["batch_id"] = str(uuid4())
    plan["batch_dir_id"] = cutover.real_dir(batch)
    plan["created"] = timestamp()
    for row in plan["roots"]:
        row["run"] = str(batch / row["id"])
    cutover.save_exclusive(batch / PLAN, plan)
    pretty(result_status(batch, plan))


def open_plan(batch):
    cutover.plain_chain(batch)
    plan = read(batch / PLAN)
    require(plan["version"] == 1 and plan["batch_dir_id"] == cutover.real_dir(batch),
            "批次记录目录被替换")
    return plan


def expected_owner(batch, plan, root):
    return {"batch": str(batch), "id": root["id"], "batch_id": plan["batch_id"]}


def child_directory(batch, plan, root):
    run = Path(root["run"])
    require(run == batch / root["id"], "目录子记录不属于此批次")
    cutover.plain_chain(run)
    require(read(run / OWNER) == expected_owner(batch, plan, root), "目录子记录归属不符")
    cfg = read(run / "run.json")
    require(all(cfg[k] == root[k] for k in ("source", "target", "backup", "volumes")),
            "目录子记录的路径或磁盘不符")
    return run


def init_child(batch, plan, root):
    run = Path(root["run"])
    if not os.path.lexists(run):
        create_private(run, batch)
    cutover.plain_chain(run)
    owner = expected_owner(batch, plan, root)
    if not os.path.lexists(run / OWNER):
        require(not list(run.iterdir()), f"已有未验证的目录记录：{run}")
        cutover.save_exclusive(run / OWNER, owner)
    require(read(run / OWNER) == owner, "目录记录归属不符")
    if not os.path.lexists(run / "run.json"):
        cutover.save_exclusive(run / "run.json", dict(
            version=2, migration_id=str(uuid4()), host="codex", project_id=root["project_ids"][0],
            source=root["source"], target=root["target"], backup=root["backup"], volumes=root["volumes"],
            started=timestamp()))
    return child_directory(batch, plan, root)


def old_identity(root):
    old = Path(root["source"])
    require(cutover.real_dir(old) == root["source_id"]
            and volumes(old, Path(root["target"])) == root["volumes"],
            f"源目录或目标盘的身份已变化：{old}")


def copy_verified(root, run):
    proof, info = cutover.verified_copy(Path(root["source"]), Path(root["target"]), run, run/"tree-copy")
    require(proof["source_id"] == root["source_id"], f"核验记录的源实体改变：{root['source']}")
    return info


def stage(batch, plan):
    # Preflight all roots before creating the first copy.
    roots = plan["roots"]
    for root in roots:
        old_identity(root)
        run = Path(root["run"])
        require(not os.path.lexists(Path(root["backup"])) and not os.path.lexists(run / cutover.PLAN_NAME)
                and not any(run.glob("git-repair-*.json")),
                "已进入离线切换，请接续切换步骤，不要重新复制")
        if os.path.lexists(run):
            cutover.plain_chain(run)
            if (run / "run.json").is_file():
                child_directory(batch, plan, root)
            else:
                require({p.name for p in run.iterdir()} <= {OWNER}, f"未验证的目录记录：{run}")
                if (run / OWNER).is_file():
                    require(read(run / OWNER) == expected_owner(batch,plan,root), "目录记录归属不符")
    runs = {}
    for root in roots:
        run = init_child(batch, plan, root)
        runs[root["id"]] = run
        baseline = run / "git-before.json"
        if not os.path.lexists(baseline):
            invoke("git_check", "baseline", root["source"], "--out", baseline)
        verify_baseline(root, run)
    for root in roots:
        run = runs[root["id"]]
        collect_copy(root, run)
    pretty(result_status(batch, plan))


def collect_copy(root, run):
    audit = run/"tree-copy"
    try:
        copy_verified(root,run)
    except (OSError, ValueError, KeyError, cutover.MigrationError):
        invoke("tree_copy", "copy", root["source"], root["target"], "--audit", audit)
    invoke("tree_copy", "verify", root["source"], root["target"], "--audit", audit)
    copy_verified(root,run)


def verify_baseline(root, run):
    report = read(run/"git-before.json")
    require(report.get("schema") == 1 and report.get("ok") is True and report.get("mode") == "baseline"
            and report["root"] == root["source"] and report["snapshot"]["root"] == root["source"],
            f"缺少可核实的 Git 基线：{root['source']}")
    return report["snapshot"]


def phase(root, run):
    plan = run / cutover.PLAN_NAME
    if not plan.is_file():
        return "verified"
    seen, source, target, backup = cutover.state(plan)
    require((str(source),str(target),str(backup)) == tuple(root[k] for k in ("source","target","backup")),
            "离线切换记录的路径与此批次不符")
    return seen


def preflight_cutover(batch, plan, needs_plans=False):
    # Run the full read-only gate over *all* roots before the first rename.
    items = []
    for root in plan["roots"]:
        run = child_directory(batch, plan, root)
        evidence = copy_verified(root, run)
        snap = verify_baseline(root, run)
        cutover.checked_paths(root["source"], root["target"], run)
        status = phase(root, run)
        require(not needs_plans or status != "verified", f"该目录还没有离线切换计划：{root['source']}")
        if status == "verified":
            require(not os.path.lexists(root["backup"]), f"备份名称已经占用：{root['backup']}")
        if status != "linked":
            original = Path(root["backup"] if status == "renamed" else root["source"])
            require(cutover.real_dir(original) == root["source_id"], f"原件身份变化：{original}")
            pending = git_check.verify(Path(root["target"]), snap, allow_source=not needs_plans)
            recheck_verified_copy(original, Path(root["target"]), evidence, renamed=status == "renamed",
                                  pointer_exceptions=git_check.repair_metadata_exceptions(snap, pending))
        items.append((root, run, status))
    return items


def require_history(batch, plan):
    history = read(batch/"history-before.json")
    require(history.get("batch_id") == plan["batch_id"] and history.get("host_id") == plan["host_id"],
            "请先保存属于本批次的旧聊天基线，即使只能记录未能核实的范围")


def shell_for(batch, mode):
    cmd = (sys.executable,"-B",str(Path(__file__).resolve()),"link","--batch",str(batch),"--mode",mode)
    if sys.platform == "darwin":
        return f"(cd {shlex.quote(str(batch))} && {shlex.join(cmd)})"
    args = " ".join(cutover.ps_literal(x) for x in cmd)
    return "& { Set-Location -LiteralPath " + cutover.ps_literal(batch) + " -ErrorAction Stop; & " + args + " }"


def prepare(batch, plan, mode, current_snapshot=None):
    require_history(batch, plan)
    items = preflight_cutover(batch, plan)
    if current_snapshot:
        host, current = snapshot_projects(current_snapshot)
        require(host == plan["host_id"], "项目配置来自不同宿主")
        assert_no_new_references(plan, current)
        for entry in plan["projects"]:
            now = current[entry["id"]]["sources"]
            expected = configuration_phase(entry, now)
            if expected != "needs_edit":
                require(all(status == "linked" for _, _, status in items),
                        f"建立全部旧入口前，项目来源出现了变化：{entry['name']}")
    for root, run, status in items:
        if status == "linked":
            continue
        out = run / ("git-repair-" + uuid4().hex + ".json")
        invoke("git_check", "repair", root["target"], "--baseline", run/"git-before.json", "--out", out)
        invoke("cutover", "prepare", "--source", root["source"], "--target", root["target"],
               "--audit", run, "--copy-audit", run/"tree-copy", "--mode", mode)
    pretty(dict(command=shell_for(batch,mode), mode=mode,
                roots=[dict(id=r["id"],source=r["source"],target=r["target"],backup=r["backup"]) for r, _, _ in items]))


def link(batch, plan, mode):
    require_history(batch, plan)
    items = preflight_cutover(batch, plan, needs_plans=True)
    if mode == "manual":
        require(all(status != "ready" for _, _, status in items), "请先完成所有原件的手动改名，再运行同一条命令")
    for root, run, status in items:
        if status != "linked":
            # The existing cutover validates fresh identities again under its own lock.
            invoke("cutover", "apply", "--plan", run/"cutover-plan.json", "--mode", mode)
    pretty(result_status(batch, plan))


def source_identity_if_reachable(path):
    try:
        return cutover.real_dir(real_ref(path))
    except FileNotFoundError:
        return None


def assert_no_new_references(plan, current):
    affected = {p["id"] for p in plan["projects"]}
    require(set(current).issuperset(plan.get("known_project_ids", affected)),
            "有已核实的项目缺失，请补齐当前来源列表")
    for root in plan["roots"]:
        ids = {tuple(root["source_id"]), tuple(cutover.real_dir(Path(root["target"])))}
        expected = set(root["project_ids"])
        for pid, now in current.items():
            for entry in now["sources"]:
                identity = source_identity_if_reachable(entry["path"])
                if identity is not None and tuple(identity) in ids:
                    require(pid in expected, f"出现计划之外的引用项目：{pid} / {entry['path']}")


def configuration_phase(entry, actual):
    if actual == entry["before"]:
        return "needs_edit"

    # Intermediate steps may attach the destination before removing the old entry.
    # Only these approved replacements and the old/new primary may be present.
    keys = lambda rows: {key(s["path"]): s["role"] for s in rows}
    first, final, current = map(keys, (entry["before"], entry["after"], actual))
    unchanged = {k: v for k, v in first.items() if final.get(k) == v}
    allowed_primaries = {k for config in (first,final) for k,v in config.items() if v == "primary"}
    require(set(current) <= set(first) | set(final) and all(current.get(k) == v for k,v in unchanged.items())
            and any(current.get(k) == "primary" for k in allowed_primaries),
            f"当前项目来源超出已确认计划：{entry['name']}")
    kept_before = [key(s["path"]) for s in entry["before"] if key(s["path"]) in unchanged]
    require([key(s["path"]) for s in actual if key(s["path"]) in unchanged] == kept_before,
            f"未迁来源的相对顺序改变：{entry['name']}")
    if current == final:
        return "confirmed"
    for before, after in zip(entry["before"],entry["after"]):
        require(key(before["path"]) in current or key(after["path"]) in current,
                f"来源及其目标均未挂入项目：{before['path']}")
    return "partial"


def confirm(batch, plan, args):
    statuses = result_status(batch, plan)
    require(all(r["phase"] == "linked" for r in statuses["roots"]),
            "先核对所有旧入口，再更新项目来源配置")
    host, current = snapshot_projects(args.snapshot)
    require(host == plan["host_id"], "项目配置来自不同宿主")
    assert_no_new_references(plan,current)
    matches = [p for p in plan["projects"] if p["id"] == args.project_id]
    require(len(matches)==1, "只能核对已确认的项目")
    entry = matches[0]
    actual = current[entry["id"]]["sources"]
    phase_name = configuration_phase(entry, actual)
    receipt = batch/entry["receipt"]
    record = dict(id=entry["id"],sources=actual,host_id=host,batch_id=plan["batch_id"])
    if os.path.lexists(receipt):
        require(phase_name == "confirmed" and read(receipt) == record, "已核验的项目设置又发生变化；请先复核")
    elif phase_name == "confirmed":
        cutover.save_exclusive(receipt, record)
    pretty(dict(project_id=entry["id"],phase=phase_name,actual=actual,proposed=entry["after"],
                receipt=str(receipt) if phase_name=="confirmed" else None))


def result_status(batch, plan):
    statuses = []
    for root in plan["roots"]:
        run = Path(root["run"])
        status = dict(id=root["id"],source=root["source"],target=root["target"],backup=root["backup"],phase="pending")
        if os.path.lexists(run):
            try:
                run = child_directory(batch,plan,root)
                if (run/"tree-copy"/"owner.json").is_file():
                    copy_verified(root,run)
                    status["phase"] = phase(root,run)
            except (OSError, ValueError, KeyError, cutover.MigrationError) as exc:
                status["error"] = str(exc)
        statuses.append(status)
    configs = []
    for p in plan["projects"]:
        receipt = batch/p["receipt"]
        expected = dict(id=p["id"],host_id=plan["host_id"],batch_id=plan["batch_id"])
        confirmed = False
        if os.path.lexists(receipt):
            observed = read(receipt)
            confirmed = all(observed.get(k) == v for k, v in expected.items()) and (
                configuration_phase(p, observed["sources"]) == "confirmed")
        configs.append(dict(id=p["id"],name=p["name"],confirmed=confirmed,proposed=p["after"]))
    phases = {x["phase"] for x in statuses}
    if any("error" in row for row in statuses):
        next_step = "inspect"
    elif phases == {"linked"}:
        next_step = "evidence" if all(p["confirmed"] for p in configs) else "confirm"
    elif "pending" in phases:
        next_step = "stage"
    elif "verified" in phases:
        next_step = "prepare"
    else:
        next_step = "link"
    return dict(batch=str(batch),batch_id=plan["batch_id"],roots=statuses,projects=configs,next=next_step)


def main():
    require(sys.platform in ("win32","darwin") and sys.version_info >= (3,12),
            "需要 Windows 或 macOS 及 CPython 3.12 或更新版本")
    parser = argparse.ArgumentParser(description=__doc__)
    cmds = parser.add_subparsers(dest="command", required=True)
    for name in ("preview","create"):
        sub = cmds.add_parser(name)
        sub.add_argument("--snapshot",required=True)
        sub.add_argument("--selection",required=True)
        if name=="create":
            sub.add_argument("--batch",required=True)
    for name in ("stage","prepare","link","confirm","status"):
        sub = cmds.add_parser(name)
        sub.add_argument("--batch",required=True)
        if name in ("prepare","link"):
            sub.add_argument("--mode",choices=("manual","auto"),default="auto")
        if name == "prepare":
            sub.add_argument("--snapshot", help="在发出离线命令前核对宿主的当前完整来源清单")
        if name=="confirm":
            sub.add_argument("--snapshot",required=True)
            sub.add_argument("--project-id",required=True)
    args=parser.parse_args()
    if args.command=="preview":
        pretty(draft(args.snapshot,args.selection))
        return
    if args.command=="create":
        create(args)
        return
    batch=physical(args.batch)
    plan=open_plan(batch)
    if args.command=="status":
        pretty(result_status(batch,plan))
        return
    lock = cutover.stat_if_present(batch/"copy.lock")
    require(lock is None or (stat.S_ISREG(lock.st_mode) and not cutover.reparse(lock) and lock.st_nlink==1),
            "批次锁文件异常")
    with exclusive_lock(batch):
        if args.command=="stage":
            stage(batch,plan)
        elif args.command=="prepare":
            prepare(batch, plan, args.mode, args.snapshot)
        elif args.command=="link":
            link(batch,plan,args.mode)
        else:
            confirm(batch,plan,args)


if __name__=="__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, TypeError, cutover.MigrationError) as exc:
        print(f"批次停止：{exc}", file=sys.stderr)
        sys.exit(1)
