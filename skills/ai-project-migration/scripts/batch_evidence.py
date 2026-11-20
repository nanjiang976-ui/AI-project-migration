"""Bind a Codex multi-source batch to observations from its real project chats."""
import argparse
import json
import os
from pathlib import Path
import sys
from uuid import UUID, uuid4

from cutover import (MigrationError, local_path, ordinary_record,
                     real_dir, require, save_exclusive, state)
from project_batch import (assert_no_new_references, child_directory, configuration_phase,
                           open_plan, snapshot_projects)
from run_record import notice, timestamp, verify_backup_files


def load(path):
    return ordinary_record(local_path(path))[0]


def binding_id(value):
    require(isinstance(value, str) and 0 < len(value) <= 256
            and value == value.strip() and value.isprintable(), "需要有效的宿主标识")
    return value


def by_id(items, name):
    result = {}
    for item in items:
        identifier = binding_id(item["id"])
        require(identifier not in result, f"{name} 的标识重复")
        result[identifier] = item
    return result


def read_batch(path):
    path = local_path(path)
    if path.name != "batch.json":
        path /= "batch.json"
    data = open_plan(path.parent)
    binding_id(data["batch_id"])
    binding_id(data["host_id"])
    return path.parent, data


def batch_binding(data):
    return {key: data[key] for key in ("batch_id", "host_id")}


def check_bound(record, data):
    require(all(record.get(key) == value for key, value in batch_binding(data).items()),
            "证据属于另一个迁移批次或宿主")


def receipt_path(directory, project):
    path = directory / project["receipt"]
    require(path.parent == directory and path.suffix == ".json", "项目回执路径异常")
    return path


def confirm_project(directory, data, project):
    receipt = load(receipt_path(directory, project))
    check_bound(receipt, data)
    require(receipt["id"] == project["id"] and configuration_phase(project, receipt["sources"]) == "confirmed",
            "该项目的源路径或主次尚未按计划回读")
    for entry in project["after"]:
        require(local_path(entry["path"]).is_dir(), "项目配置指向不可读目录")


def root_run(directory, data, root):
    run = child_directory(directory, data, root)
    config = load(run / "run.json")
    return run, config


def linked_root(directory, data, root):
    run, _ = root_run(directory, data, root)
    phase, source, target, backup = state(run / "cutover-plan.json")
    require(phase == "linked" and (source, target, backup) ==
            tuple(local_path(root[key]) for key in ("source", "target", "backup")),
            "请先核对本次旧入口和目标实体")
    return source, target


def canonical(path):
    path = local_path(path)
    require(path.is_dir(), f"工作目录不存在：{path}")
    return path.resolve(strict=True)


def map_old_cwd(data, project, cwd):
    """Capture the pre-move path identity before the original name is relinked."""
    actual = canonical(cwd)
    matches = []
    for entry in project["before"]:
        root_path = canonical(entry["path"])
        if actual.is_relative_to(root_path):
            matches.append((entry, actual.relative_to(root_path)))
    require(len(matches) <= 1, "旧工作目录落在重叠的来源中，须先核对它的归属")
    if not matches:
        # Codex can place a worktree outside the project roots. Preserve its
        # cwd identity here and check Git registrations separately at cleanup.
        return dict(origin=None, source_id=None, peer=str(actual), identity=real_dir(actual))
    entry, suffix = matches[0]
    root_id = None
    peer = actual
    for root in data["roots"]:
        if any(r["project_id"] == project["id"] and local_path(r["path"]) == local_path(entry["path"])
               for r in root["references"]):
            root_id = root["id"]
            peer = local_path(root["target"]) / suffix
            break
    return dict(origin=entry["path"], source_id=root_id, peer=str(peer), identity=real_dir(actual))


def extract_history(payload, data):
    require(payload["host_id"] == data["host_id"], "历史来自不同的 Codex 宿主")
    projects = by_id(payload["projects"], "项目")
    require(set(projects) == set(by_id(data["projects"], "项目")), "历史清单须覆盖批次内每个项目")
    sessions_seen = set()
    result = {}
    for project_id, row in projects.items():
        require(type(row["complete"]) is bool, "请明确说明是否已读取完整历史")
        sessions = by_id(row["sessions"], "会话")
        require(not (sessions_seen & sessions.keys()), "同一个会话不能计入多个项目")
        sessions_seen.update(sessions)
        filtered = {}
        for session, value in sessions.items():
            require(type(value["history_readable"]) is bool and type(value["archived"]) is bool,
                    "历史可读性和归档状态必须来自宿主核对")
            cwd = value.get("cwd")
            filtered[session] = dict(cwd=str(local_path(cwd)) if cwd else None,
                                     history_readable=value["history_readable"], archived=value["archived"])
        result[project_id] = dict(complete=row["complete"], sessions=filtered)
    return result


def history(args):
    directory, data = read_batch(args.batch)
    projects = by_id(data["projects"], "项目")
    reported = extract_history(load(args.input), data)
    if args.phase == "before":
        for root in data["roots"]:
            require(real_dir(local_path(root["source"])) == root["source_id"],
                    "迁移前历史须在原目录仍是实体时登记")
        for pid, project in projects.items():
            for session in reported[pid]["sessions"].values():
                if session["cwd"]:
                    try:
                        session["mapping"] = map_old_cwd(data, project, session["cwd"])
                    except (MigrationError, OSError, ValueError):
                        session["mapping"] = None
        target = directory / "history-before.json"
    else:
        # Post-move observations can be refreshed without replacing prior audits.
        check_bound(load(directory / "history-before.json"), data)
        target = directory / f"history-after-{stamp()}.json"
    write(target, {**batch_binding(data), "projects": reported})


def request_path(directory, request):
    return directory / f"request-{UUID(str(request))}.json"


def check_cwd(observed, session):
    mapping = session.get("mapping")
    require(mapping is not None and session.get("cwd"), "原会话缺少可验证的默认工作目录")
    peer = local_path(mapping["peer"])
    old_cwd = local_path(session["cwd"])
    actual = local_path(observed)
    require(actual in (old_cwd, peer, peer.resolve(strict=True)), f"默认工作目录偏离原会话：{actual}")
    require(peer.is_dir() and actual.samefile(peer) and old_cwd.samefile(peer),
            "原会话工作目录和预期目录未指向同一实体")
    if mapping["source_id"] is None:
        require(real_dir(peer) == mapping["identity"], "未迁目录或独立工作树的身份已改变")


def register(args):
    directory, data = read_batch(args.batch)
    project = by_id(data["projects"], "项目")[args.project_id]
    root = by_id(data["roots"], "目录")[args.source_id]
    confirm_project(directory, data, project)
    source, target = linked_root(directory, data, root)
    refs = [r for r in root["references"] if r["project_id"] == args.project_id]
    require(len(refs) == 1, "此目录不是该项目独立登记的迁移来源")
    baseline = load(directory / "history-before.json")
    check_bound(baseline, data)
    old_chat = baseline["projects"][args.project_id]["sessions"][args.session_id]
    mapping = old_chat.get("mapping")
    require(mapping and old_chat["history_readable"] and not old_chat["archived"],
            "请先在宿主中核实可继续使用的原会话")
    if mapping["source_id"] == args.source_id:
        mode = "default_cwd"
    else:
        require(refs[0]["role"] == "secondary" and mapping["origin"] is not None,
                "主来源请使用该来源默认目录中的原会话")
        mode = "attached_source"
    require(local_path(refs[0]["path"]).samefile(source), "此项目原来源地址未解析到同一实体")
    write(request_path(directory, args.request_id), {**batch_binding(data),
          "request_id": str(args.request_id), "project_id": args.project_id, "source_id": args.source_id,
          "session_id": args.session_id, "mode": mode, "old_entry": refs[0]["path"]})


def probe_inputs(path, request_id):
    directory, data = read_batch(path)
    request = load(request_path(directory, request_id))
    check_bound(request, data)
    require(request["request_id"] == str(request_id), "请求编号与文件不符")
    project = by_id(data["projects"], "项目")[request["project_id"]]
    root = by_id(data["roots"], "目录")[request["source_id"]]
    confirm_project(directory, data, project)
    source, target = linked_root(directory, data, root)
    baseline = load(directory / "history-before.json")
    check_bound(baseline, data)
    chat = baseline["projects"][project["id"]]["sessions"][request["session_id"]]
    cwd = Path.cwd()
    check_cwd(cwd, chat)
    refs = [r for r in root["references"] if r["project_id"] == project["id"]]
    require(len(refs) == 1 and refs[0]["path"] == request["old_entry"],
            "请求中的目录须是这个项目原有的来源")
    old_entry = local_path(request["old_entry"])
    require(old_entry.samefile(source), "原入口不再指向已验证的实体")
    if request["mode"] == "default_cwd":
        require(chat["mapping"]["source_id"] == root["id"], "原会话的默认目录与该来源不符")
        suffix = local_path(chat["mapping"]["peer"]).relative_to(target)
        seed_dir = Path(".")
    else:
        require(request["mode"] == "attached_source" and refs[0]["role"] == "secondary"
                and chat["mapping"]["source_id"] != root["id"] and chat["mapping"]["origin"] is not None,
                "附加来源验证须来自同项目中另一个来源的默认工作目录")
        suffix = Path(".")
        seed_dir = old_entry
    return directory, data, request, root, seed_dir, old_entry, suffix


def proof(directory, data, request):
    request_id = request["request_id"]
    receipt = load(directory / f"probe-{UUID(request_id)}.json")
    check_bound(receipt, data)
    keys = ("request_id", "project_id", "source_id", "session_id", "mode", "old_entry")
    require(all(receipt[k] == request[k] for k in keys), "探针与登记的项目、来源或会话不符")
    flags = ("same_file", "backup_isolated", "probe_removed", "old_write_target_read", "target_write_old_read")
    require(all(receipt.get(key) is True for key in flags), "缺少完整的双向读写和清理回执")
    root = by_id(data["roots"], "目录")[request["source_id"]]
    require(all(receipt.get(key) == root[key] for key in ("source", "target", "backup")),
            "探针中的路径不是该迁移来源")
    chat = load(directory / "history-before.json")["projects"][request["project_id"]]["sessions"][request["session_id"]]
    check_cwd(receipt["cwd"], chat)
    source, target = linked_root(directory, data, root)
    require(local_path(receipt["old_entry"]).samefile(source), "原入口已变化")
    relative = (local_path(chat["mapping"]["peer"]).relative_to(target)
                if request["mode"] == "default_cwd" else Path("."))
    require(receipt["probe_relative_directory"] == relative.as_posix(), "探针实际写入目录与请求不符")
    return receipt


def attest(args):
    directory, data = read_batch(args.batch)
    request = load(request_path(directory, args.request_id))
    check_bound(request, data)
    claim = load(args.input)
    expected = {k: request[k] for k in ("project_id", "session_id", "request_id")}
    require(claim["host_id"] == data["host_id"] and all(claim[k] == v for k, v in expected.items()),
            "宿主实际返回的项目、会话或测试请求不匹配")
    proof(directory, data, request)
    write(directory / f"attest-{UUID(request['request_id'])}.json",
          {**batch_binding(data), **expected, "source_id": request["source_id"]})


def new_chat(args):
    directory, data = read_batch(args.batch)
    claim = load(args.input)
    require(claim["host_id"] == data["host_id"], "新聊天来自不同宿主")
    project = by_id(data["projects"], "项目")[claim["project_id"]]
    confirm_project(directory, data, project)
    old = load(directory / "history-before.json")
    check_bound(old, data)
    sid = binding_id(claim["session_id"])
    require(all(sid not in p["sessions"] for p in old["projects"].values()), "请验证新建的本地聊天")
    result = {**batch_binding(data), "project_id": project["id"], "session_id": sid,
              "cwd": claim["cwd"], "readable_sources": claim["readable_sources"]}
    verify_new_chat(project, result)
    write(directory / f"new-{project['receipt'][:-5]}-{stamp()}.json", result)


def verify_new_chat(project, evidence):
    main = next(e["path"] for e in project["after"] if e["role"] == "primary")
    require(canonical(evidence["cwd"]) == canonical(main), "新聊天默认目录不是原主要来源对应位置")
    reported = [local_path(p) for p in evidence["readable_sources"]]
    require(len(reported) == len(set(reported)) and set(reported) ==
            {local_path(e["path"]) for e in project["after"]}, "新聊天尚未验证所有配置来源的读取")
    require(all(p.is_dir() for p in reported), "新聊天读取后的来源状态变化")


def validate_history(directory, data, before, after, project_id, checked):
    start = before["projects"][project_id]
    result = after["projects"][project_id]
    require(start["complete"] and result["complete"], "迁移前或迁移后的聊天历史覆盖不完整")
    for sid, old in start["sessions"].items():
        seen = result["sessions"].get(sid, {})
        require(old["history_readable"] and seen.get("history_readable") and seen.get("cwd"),
                f"旧会话未核对原 ID、历史或工作目录：{sid}")
        check_cwd(seen["cwd"], old)
        origin = old["mapping"]["source_id"]
        if origin is not None and origin not in checked:
            linked_root(directory, data, by_id(data["roots"], "目录")[origin])
            checked.add(origin)
    return len(start["sessions"])


def source_proof(directory, data, project, root):
    candidates = []
    for path in sorted(directory.glob("attest-*.json"), key=lambda p: p.stat().st_mtime_ns, reverse=True):
        record = load(path)
        if record["project_id"] == project["id"] and record["source_id"] == root["id"]:
            candidates.append(record)
    require(candidates, "需要在该项目的一条可续聊旧会话里实测这个来源")
    latest = candidates[0]
    check_bound(latest, data)
    request = load(request_path(directory, latest["request_id"]))
    require(all(latest[k] == request[k] for k in ("project_id", "source_id", "session_id", "request_id")),
            "宿主回读与请求绑定不符")
    return proof(directory, data, request)


def validate_current(projects, data, root):
    planned = by_id(data["projects"], "项目")
    for pid in root["project_ids"]:
        require(pid in projects and configuration_phase(planned[pid], projects[pid]["sources"]) == "confirmed",
                "项目的已验收来源或主次发生变化")
    assert_no_new_references({**data, "roots": [root]}, projects)


def latest(directory, pattern):
    path = max(directory.glob(pattern), default=None)
    require(path is not None, f"缺少验收记录：{pattern}")
    return load(path)


def root_evidence(directory, data, current, root):
    validate_current(current, data, root)
    run, config = root_run(directory, data, root)
    linked_root(directory, data, root)
    before = load(directory / "history-before.json")
    after = latest(directory, "history-after-*.json")
    check_bound(before, data)
    check_bound(after, data)
    projects = by_id(data["projects"], "项目")
    outcomes = []
    probe_dirs = set()
    checked = {root["id"]}
    for pid in root["project_ids"]:
        project = projects[pid]
        confirm_project(directory, data, project)
        n = validate_history(directory, data, before, after, pid, checked)
        new = latest(directory, f"new-{project['receipt'][:-5]}-*.json")
        check_bound(new, data)
        require(new["project_id"] == pid, "新聊天回执属于另一项目")
        verify_new_chat(project, new)
        actual = source_proof(directory, data, project, root) if n else None
        if actual:
            probe_dirs.add(actual["probe_relative_directory"])
        outcomes.append(dict(project_id=pid, checked_old_chats=n,
                             tested=[actual["session_id"]] if actual else [],
                             request_id=actual["request_id"] if actual else None))
    verify_backup_files(config, run, probe_dirs, batch=True)
    return outcomes


def status(args):
    directory, data = read_batch(args.batch)
    host, current = snapshot_projects(args.snapshot)
    require(host == data["host_id"], "须在清理前回读本机完整项目来源")
    result = []
    for root in data["roots"]:
        try:
            outcomes = root_evidence(directory, data, current, root)
            eligible, reason = True, "本次已复验相关项目及当前备份；如有新写入，请重新核验"
        except (MigrationError, ValueError, KeyError, OSError, TypeError, RuntimeError) as exc:
            eligible, reason, outcomes = False, str(exc), []
        result.append(dict(source_id=root["id"], **notice(root, eligible, reason), projects=outcomes))
    print(json.dumps(dict(batch_id=data["batch_id"], roots=result), ensure_ascii=False))


def stamp():
    return timestamp().translate(str.maketrans("", "", "-:.+")) + "-" + uuid4().hex[:8]


def write(path, data):
    save_exclusive(path, data)
    print(json.dumps(dict(report=str(path)), ensure_ascii=False))


def main():
    require(sys.platform in ("win32", "darwin") and sys.version_info >= (3, 12),
            "需要 Windows 或 macOS 及 CPython 3.12 或更新版本")
    parser = argparse.ArgumentParser(description=__doc__)
    children = parser.add_subparsers(required=True)
    for handler in (history, register, attest, new_chat, status):
        child = children.add_parser(handler.__name__.replace("_", "-"))
        child.add_argument("--batch", required=True)
        child.set_defaults(handler=handler)
        if handler in (history, attest, new_chat):
            child.add_argument("--input", required=True)
        if handler is history:
            child.add_argument("--phase", choices=("before", "after"), required=True)
        if handler in (register, attest):
            child.add_argument("--request-id", type=UUID, required=True)
        if handler is register:
            for arg in ("project-id", "source-id", "session-id"):
                child.add_argument("--" + arg, required=True)
        if handler is status:
            child.add_argument("--snapshot", required=True)
    args = parser.parse_args()
    args.handler(args)


if __name__ == "__main__":
    try:
        main()
    except (MigrationError, OSError, KeyError, ValueError, TypeError) as e:
        print(f"已停止：{e}", file=sys.stderr)
        sys.exit(1)
