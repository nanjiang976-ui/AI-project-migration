"""Inspect a Codex migration in place without creating or changing migration records."""
import argparse
import json
import os
from pathlib import Path
import stat
import sys

import cutover
import git_check
import project_batch
import run_record


CHECK_ERRORS = (OSError, RuntimeError, ValueError, KeyError, TypeError, cutover.MigrationError)


def read(path):
    path = cutover.local_path(path)
    cutover.plain_chain(path.parent)
    value, _ = cutover.ordinary_record(path)
    cutover.require(isinstance(value, dict), f"需要 JSON 对象：{path}")
    return value


def identifier(value):
    cutover.require(isinstance(value, str) and 0 < len(value) <= 256 and value.isprintable()
                    and value == value.strip(), "缺少可核对的宿主、项目或会话 ID")
    return value


def path_str(value):
    cutover.require(isinstance(value, str) and value, "请提供本机完整路径")
    return str(cutover.local_path(value))


def load_snapshot(path):
    snapshot = read(path)
    cutover.require(snapshot["version"] == 1 and type(snapshot["complete"]) is bool,
                    "请提供版本 1 的 Codex 来源清单，并注明清单是否完整")
    host = identifier(snapshot["host_id"])
    projects = {}
    for row in snapshot["projects"]:
        project_id = identifier(row["id"])
        sources = [dict(path=path_str(s["path"]), role=s["role"]) for s in row["sources"]]
        roles = [s["role"] for s in sources]
        cutover.require(project_id not in projects, "项目 ID 重复")
        cutover.require(all(x in ("primary", "secondary") for x in roles) and
                        (roles.count("primary") == 1 if snapshot["complete"] else roles.count("primary") <= 1),
                        f"请核对项目的主要与次要来源：{project_id}")
        cutover.require(len({Path(s["path"]) for s in sources}) == len(sources), "来源条目重复")
        projects[project_id] = dict(id=project_id, name=row.get("name") or project_id, sources=sources)
    return host, snapshot["complete"], projects


def inspect_path(value):
    if value is None:
        return {"kind": "unknown", "path": None}
    path = Path(value)
    try:
        st = cutover.stat_if_present(path)
        if st is None:
            return dict(path=value, kind="missing")
        if cutover.reparse(st):
            if not (path.is_junction() if os.name == "nt" else path.is_symlink()):
                return dict(path=value, kind="unsupported")
            named = os.readlink(path)
            if os.name == "nt" and named.startswith(("\\\\?\\", "\\??\\")):
                named = named[4:]
            link = Path(named)
            if not link.is_absolute():
                link = path.parent / link
            result = dict(path=value, kind="link", named_target=str(link))
            try:
                resolved = path.resolve(strict=True)
                result.update(resolved=str(resolved), identity=cutover.real_dir(resolved))
            except CHECK_ERRORS as error:
                result.update(kind="broken_link", issue=str(error))
            return result
        if not stat.S_ISDIR(st.st_mode):
            return dict(path=value, kind="file")
        return dict(path=value, kind="directory", resolved=str(path.resolve(strict=True)),
                    identity=cutover.real_dir(path))
    except CHECK_ERRORS as error:
        return dict(path=value, kind="inaccessible", issue=str(error))


def checkpoint_items(folder, host, project):
    """Return candidate mappings, without adopting or changing a checkpoint."""
    directory = cutover.local_path(folder)
    cutover.plain_chain(directory)
    if os.path.lexists(directory / "batch.json"):
        plan = project_batch.open_plan(directory)
        cutover.require(plan["host_id"] == host, "批次属于另一个宿主")
        entry = next((p for p in plan["projects"] if p["id"] == project), None)
        cutover.require(entry is not None, "批次未登记本项目")
        items = []
        for root in plan["roots"]:
            if project in root["project_ids"]:
                child = cutover.local_path(root["run"])
                cutover.require(child == directory / root["id"], "批次子记录路径不符")
                item = {k: path_str(root[k]) for k in ("source", "target", "backup")}
                item["record"] = dict(kind="batch", path=str(directory), run=str(child),
                                      expected_source_id=root["source_id"], root_id=root["id"],
                                      planned_sources=entry["after"])
                items.append(item)
        return items
    config = read(directory / "run.json")
    cutover.require(config.get("version") in (1, 2) and config.get("host") == "codex"
                    and config.get("host_id", host) == host and config.get("project_id") == project,
                    "单目录检查点的宿主、版本或项目 ID 不符")
    cutover.require(not os.path.lexists(directory / "batch-owner.json"),
                    "这是批次子记录。请读取批次主目录。")
    item = {k: path_str(config[k]) for k in ("source", "target", "backup")}
    item["record"] = dict(kind="single", path=str(directory), run=str(directory),
                          host_binding="explicit" if "host_id" in config else "local_checkpoint")
    return [item]


def collect_records(paths, host, project):
    groups, issues = {}, []
    for path in paths:
        try:
            for item in checkpoint_items(path, host, project):
                groups.setdefault(Path(item["source"]), []).append(item)
        except CHECK_ERRORS as error:
            issues.append(dict(record=path, reason=str(error)))
    return groups, issues


def select_rows(request, project, records):
    """Keep unknown project entries visible and only group corroborated pairs."""
    selected = {}
    choices = request.get("sources", [])
    for entry in choices:
        old = path_str(entry["source"])
        cutover.require(Path(old) not in selected, f"同一旧路径只能选择一次：{old}")
        selected[Path(old)] = dict(source=old, target=path_str(entry["target"]) if entry.get("target") else None,
                                  backup=path_str(entry["backup"]) if entry.get("backup") else None)
    remaining = {Path(s["path"]): s["path"] for s in project["sources"]}
    rows = []
    for old in dict.fromkeys((*selected, *records)):
        choice = selected.get(old)
        candidates = records.get(old, [])
        issues = []
        if candidates and choice:
            matches = [c for c in candidates if (not choice["target"] or
                                                 Path(c["target"]) == Path(choice["target"]))
                       and (not choice["backup"] or Path(c["backup"]) == Path(choice["backup"]))]
            if not matches:
                issues.append("所填目标或备份与已有检查点不符，先核对使用哪份记录")
            candidates = matches
        if len(candidates) > 1:
            issues.append("同一来源匹配多份检查点，请先选定一份")
        record = candidates[0] if len(candidates) == 1 else None
        row = dict(choice or record or {"source": str(old), "target": None, "backup": None})
        row.update(selected=old in selected, record=record["record"] if record else None, issues=issues)
        if record is not None:
            row["target"] = row["target"] or record["target"]
            row["backup"] = row["backup"] or record["backup"]
        # An explicit pairing or a matching checkpoint explains the two entries.
        # Merely sharing the basename never hides a possibly independent source.
        remaining.pop(old, None)
        if row.get("target"):
            remaining.pop(Path(row["target"]), None)
        rows.append(row)
    for old in remaining.values():
        rows.append(dict(source=old, target=None, backup=None, selected=False, record=None, issues=[]))
    return rows


def inspect_record(row, source, target, backup):
    record = row["record"]
    if record is None:
        return
    run = Path(record["run"])
    try:
        cutover.plain_chain(run)
        if record["kind"] == "batch":
            plan = project_batch.open_plan(Path(record["path"]))
            root = next(x for x in plan["roots"] if x["id"] == record["root_id"])
            project_batch.child_directory(Path(record["path"]), plan, root)
        config = read(run / "run.json")
        cutover.require(all(Path(config[k]) == Path(row[k]) for k in ("source", "target", "backup")),
                        "检查点路径与本次观察的对应关系不符")
        events = [read(p) for p in sorted(run.glob("event-*.json"))]
        step, chat, pending = run_record.current_progress(events)
        if record["kind"] == "single":
            record.update(recorded_next_step=step if step <= 7 else None,
                          last_event_status=events[-1]["status"] if events else None)
        record["inspected"] = True
    except CHECK_ERRORS as error:
        row["issues"].append(f"记录尚不可用于接续：{error}")
        return
    if target["kind"] != "directory":
        if step >= 3 or (run / cutover.PLAN_NAME).exists():
            row["issues"].append("已有迁移记录，但目标实体当前不存在或无法核对")
        return
    if (run / "tree-copy").exists():
        try:
            owner, evidence = cutover.verified_copy(Path(row["source"]), Path(row["target"]),
                                                   run, run / "tree-copy")
            original = source if source["kind"] == "directory" else backup
            cutover.require("expected_source_id" not in record
                            or owner["source_id"] == record["expected_source_id"],
                            "复制记录中的原件与批次身份不符")
            if original["kind"] == "missing" and source["kind"] == "link":
                record.update(historical_target_receipt=evidence["report"], current_contents_rehashed=False)
            else:
                cutover.require(original.get("identity") is not None and owner["source_id"] == original["identity"],
                                "原件或备份实体与复制记录不符")
                record.update(copy_receipt=evidence["report"], current_contents_rehashed=False)
        except CHECK_ERRORS as error:
            record["copy_issue"] = str(error)
            row["issues"].append(f"已有副本待重新核验：{error}")
    if (run / cutover.PLAN_NAME).exists():
        try:
            plan, old, new, saved = cutover.read_plan(run / cutover.PLAN_NAME)
            cutover.require((old, new, saved) == tuple(Path(row[k]) for k in ("source", "target", "backup")),
                            "离线切换回执路径不符")
            if (backup["kind"] == "missing" and source["kind"] == "link"
                    and source.get("identity") == target.get("identity")
                    and Path(source["named_target"]) == new):
                phase = "linked_no_backup"
            else:
                phase = cutover.observe(plan, old, new, saved)
            record["verified_link_phase"] = phase
        except CHECK_ERRORS as error:
            row["issues"].append(f"离线切换记录与现场待核对：{error}")
    if (run / "git-before.json").exists():
        try:
            baseline = read(run / "git-before.json")
            cutover.require(baseline.get("ok") is True and baseline.get("mode") == "baseline"
                            and Path(baseline["root"]) == Path(row["source"]), "Git 基线不对应此来源")
            pending_worktrees = git_check.verify(Path(row["target"]), baseline["snapshot"], allow_source=True)
            record["git"] = dict(worktrees=len(baseline["snapshot"]["linked"]), pending=pending_worktrees)
        except CHECK_ERRORS as error:
            record["git"] = {"issue": str(error)}
    if record["kind"] == "single" and record.get("verified_link_phase") in ("linked", "linked_no_backup"):
        binding = chat if chat and chat["status"] == "passed" else pending
        if binding:
            try:
                run_record.read_probe(config, run, binding)
                record["probe"] = dict(session_id=binding["session_id"], request_id=binding["request_id"],
                                       completed=bool(chat and chat["status"] == "passed"))
            except CHECK_ERRORS as error:
                record["probe_issue"] = str(error)


def references(projects, observations, row, source, target):
    identities = [p["identity"] for p in (source, target) if p.get("identity") is not None]
    refs = []
    for project_id, project in projects.items():
        for entry in project["sources"]:
            current = observations[entry["path"]]
            name = Path(entry["path"])
            side = None
            if name == Path(row["source"]):
                side = "old"
            elif row["target"] and name == Path(row["target"]):
                side = "target"
            elif current.get("identity") in identities:
                side = "alias"
            if side:
                refs.append(dict(project_id=project_id, project=project["name"], side=side, **entry))
    return refs


def chats(request, row, references, target):
    related = {ref["project_id"] for ref in references} | {request["project_id"]}
    results = []
    historical_probe = (row["record"] or {}).get("probe")
    for chat in request.get("sessions", []):
        if chat.get("project_id") not in related:
            continue
        identifier(chat["id"])
        cwd = path_str(chat["cwd"]) if chat.get("cwd") else None
        resolved = None
        if cwd:
            try:
                candidate = Path(cwd).resolve(strict=True)
                if candidate.is_dir():
                    resolved = str(candidate)
            except CHECK_ERRORS:
                pass
        reached = bool(resolved and target.get("resolved") and
                       Path(resolved).is_relative_to(Path(target["resolved"])))
        results.append(dict(id=chat["id"], project_id=chat["project_id"], cwd=cwd, resolved_cwd=resolved,
                            history_readable=chat.get("history_readable"), archived=chat.get("archived"),
                            cwd_reaches_target=reached,
                            prior_probe=bool(historical_probe and historical_probe["session_id"] == chat["id"])))
    return dict(history_complete=request.get("history_complete") is True,
                observed=results, new_probes_run=False)


def transition(row, source, target, backup, refs, project_id, complete):
    """Report the next check, without granting any mutation or cleanup authority."""
    record = row["record"] or {}
    old_roles = [r["role"] for r in refs if r["project_id"] == project_id and r["side"] == "old"]
    new_roles = [r["role"] for r in refs if r["project_id"] == project_id and r["side"] == "target"]
    git = record.get("git", {})
    role_change = old_roles == ["primary"] and new_roles == ["secondary"]
    if not complete:
        return "blocked", "先从「编辑项目」补齐来源和共享项目清单，再选择本次迁移范围。"
    if (source["kind"] not in ("directory", "missing", "link") or
            target["kind"] not in ("directory", "missing", "unknown") or
            backup["kind"] not in ("directory", "missing")):
        return "blocked", "先核对异常路径或联接，保留现场。"
    if source["kind"] == "directory" and backup["kind"] == "directory":
        return "blocked", "原路径与备份名同时存在，先确认两个实体的归属。"
    if not row["target"]:
        return "choose_target", "请确认这项是否要迁移，并提供已经搬到或计划使用的目标完整路径。"
    if (source["kind"] == "directory" and source.get("identity") == target.get("identity")) or (
            backup["kind"] == "directory" and backup.get("identity") == target.get("identity")):
        return "blocked", "原件或备份和目标实际指向同一目录，请核对真实路径。"
    if source["kind"] == "link":
        if target["kind"] != "directory" or source.get("identity") != target.get("identity"):
            return "blocked", "原路径的联接并未指向所选目标。请先核对原地址及目标。"
        if source.get("named_target") and Path(source["named_target"]) != Path(row["target"]):
            return "blocked", "原路径经由其他地址解析到目标，请先核对联接链和预期入口。"
        if row["issues"]:
            return "blocked", "迁移记录与现有联接仍有差异，请核对后接续。"
        if git.get("issue") or git.get("pending"):
            return "review_git", "当前原地址可到达目标。先核对 Git 工作树差异再接续验收。"
        if role_change:
            return "switch_source", "先核对当前主要项，在「编辑项目」将已确认目标设为主要，再移除旧配置条目。"
        if old_roles and new_roles:
            return "review_source_entries", "核对并移除同一项目中重复的旧配置条目，保留联接及其他来源。"
        if not new_roles:
            return "switch_source", "核对项目源列表与原角色，再在「编辑项目」换成已核实的目标。"
        if backup["kind"] == "missing":
            return "check_chats", "原地址可到达目标，当前没有实体备份。核对已有记录和原聊天读写；不要据此认定迁移完整。"
        if record.get("verified_link_phase") == "linked" and record.get("probe", {}).get("completed"):
            return "review_cleanup", "用原记录复核全部项目和备份。此前探针仅表示历史证据。"
        return "check_chats", "进入原聊天验证真实读写，同时核对所有引用项目。"
    if target["kind"] == "missing":
        if source["kind"] == "directory" and not row["issues"]:
            return "not_started", "确认选择、路径和共享范围后，按正常流程复制。"
        return "blocked", "目标未找到。先核对原实体、备份和对应的迁移记录。"
    if source["kind"] == "missing" and backup["kind"] == "missing":
        return "manual_move_needs_confirmation", "核对手动搬迁的目标、原入口和历史。未找到原件或备份，无法证明文件完整性。"
    if not record.get("copy_receipt") or row["issues"]:
        return "verify_existing_copy", "保留已有目录，先比较原件或备份与目标，再选择经验证的接续路径。"
    if git.get("issue") or git.get("pending"):
        return "review_git", "保留原件和已有目标，先核对 Git 工作树及仍待处理的关联。"
    if record.get("kind") == "batch":
        return "build_link", "回到原批次复核全部来源、Git 和现场身份，再按原批次接续原名入口。"
    if role_change:
        return "switch_source", "复核最新文件差异，再把已确认的目标设为主要、保留其他来源。"
    if old_roles and new_roles:
        return "review_source_entries", "复核两边最新差异，核对既有目标和角色，移除已确认重复的旧配置条目。"
    if not new_roles:
        return "switch_source", "先复核两边最新差异，再按原记录换源并保留原主次角色。"
    return "build_link", "先按原记录确认最新文件差异和项目设置，再完成旧路径的离线切换。"


def inspect(snapshot_path, request_path):
    host, complete, projects = load_snapshot(snapshot_path)
    request = read(request_path)
    if "host_id" in request:
        cutover.require(request["host_id"] == host, "聊天观察来自另一个宿主")
    project = projects[identifier(request["project_id"])]
    groups, record_issues = collect_records(request.get("records", []), host, project["id"])
    observed = {item["path"]: inspect_path(item["path"]) for p in projects.values() for item in p["sources"]}
    rows = select_rows(request, project, groups)
    questions = []
    if not complete:
        questions.append("请提供本机 Codex 的完整来源列表、主次角色以及已知共享项目。")
    if not request.get("sources"):
        questions.append("请确认本次只接续已有记录，还是也迁移其他来源。")
    if record_issues:
        questions.append("请核对报告中不匹配的检查点，或提供对应的原记录路径。")
    for row in rows:
        old = Path(row["source"])
        row["backup"] = row["backup"] or str(old.with_name(old.name + cutover.BACKUP_SUFFIX))
        paths = [Path(row[k]) for k in ("source", "target", "backup") if row[k]]
        for i, first in enumerate(paths):
            for second in paths[i+1:]:
                cutover.require(not first.is_relative_to(second) and not second.is_relative_to(first),
                                "原目录、目标或备份路径重叠")
        source, target, backup = [inspect_path(row[k]) for k in ("source", "target", "backup")]
        inspect_record(row, source, target, backup)
        if record_issues and row["selected"] and row["record"] is None:
            row["issues"].append("本次提供了无法核对的检查点，先确认此来源是否已有迁移记录")
        refs = references(projects, observed, row, source, target)
        if not refs and row["record"] is None:
            row["issues"].append("该路径尚未与项目源列表或可信迁移记录关联")
        state, action = transition(row, source, target, backup, refs, project["id"], complete)
        if not refs and row["record"] is None:
            state, action = "blocked", "先向用户核对原路径与项目的归属，再判断后续操作。"
        row.update(state=state, next_action=action,
                   filesystem=dict(source=source, target=target, backup=backup),
                   configuration=dict(complete=complete, references=refs,
                                      shared=len({r["project_id"] for r in refs}) > 1),
                   chat=chats(request, row, refs, target))
        if not row["target"]:
            questions.append(f"若要迁移 {row['source']}，请给出目标的完整路径。")
        if row["configuration"]["shared"] and row["selected"]:
            questions.append(f"请确认 {row['source']} 的所有引用项目是否一起换源。")
    return dict(read_only=True, host_id=host, project_id=project["id"], project_name=project["name"],
                complete_source_inventory=complete, rows=rows, record_issues=record_issues, questions=questions,
                cleanup_authorized=False)


def main():
    if sys.platform not in ("win32", "darwin"):
        raise SystemExit("Requires native Windows or macOS.")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", required=True, help="Verified Codex source list and roles")
    parser.add_argument("--input", required=True, help="Selected project, known paths, checkpoints and chat metadata")
    args = parser.parse_args()
    try:
        print(json.dumps(inspect(args.snapshot, args.input), ensure_ascii=True))
    except CHECK_ERRORS as error:
        parser.exit(1, f"接续检查停止：{error}\n")


if __name__ == "__main__":
    main()
