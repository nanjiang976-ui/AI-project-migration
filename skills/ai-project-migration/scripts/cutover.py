"""Prepare an offline directory-link cutover and verify its state on Windows or macOS."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import stat
import sys
from uuid import uuid4

PLAN_NAME = "cutover-plan.json"
BACKUP_SUFFIX = "_迁移备份"


def link_type():
    return "SymbolicLink" if sys.platform == "darwin" else "Junction"


class MigrationError(Exception):
    """An unexpected path or state needs attention before proceeding."""


def require(condition, message):
    if not condition:
        raise MigrationError(message)


def local_path(value):
    p = Path(value)
    if sys.platform == "darwin":
        require(p.is_absolute() and p != Path("/") and ".." not in p.parts
                and not str(value).startswith("//") and not any(ord(c) < 32 for c in str(value)),
                f"需要本地 APFS 项目绝对路径：{p}")
        return p
    require(p.is_absolute() and len(p.drive) == 2 and p.drive[1] == ":" and p != Path(p.anchor),
            f"需要本地磁盘中的绝对路径，不能使用盘的根目录：{p}")
    for part in p.parts[1:]:
        require(part not in (".", "..") and not part.endswith((" ", "."))
                and not any(c in '<>:"|?*' or ord(c) < 32 for c in part)
                and not Path(part).is_reserved(), f"不安全的路径：{p}")
    return p


def stat_if_present(p):
    try:
        return p.lstat()
    except FileNotFoundError:
        return None


def reparse(st):
    if os.name == "nt":
        return bool(st.st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)
    return stat.S_ISLNK(st.st_mode)


def real_dir(p):
    st = p.lstat()
    require(stat.S_ISDIR(st.st_mode) and not reparse(st), f"需要实体目录：{p}")
    require(st.st_ino and st.st_dev, f"无法确认所在盘和目录的系统标识：{p}")
    if sys.platform == "darwin":
        from mac_fs import object_id
        return object_id(p)
    return [st.st_dev, st.st_ino]


def plain_chain(p):
    for ancestor in [*reversed(p.parents), p]:
        st = ancestor.lstat()
        require(stat.S_ISDIR(st.st_mode) and not reparse(st), f"路径中的目录并非实体：{ancestor}")


def no_overlap(a, b):
    require(not (a.is_relative_to(b) or b.is_relative_to(a)), f"路径不得重叠：{a} / {b}")


def check_darwin_ancestry(paths, dependencies):
    # Path comparisons alone do not detect aliases on case-insensitive APFS.
    # lstat the old entry itself: it may already be the intended link to target.
    for root in paths:
        root_st = stat_if_present(root)
        if root_st is None or reparse(root_st):
            continue
        for other in (*paths, *dependencies):
            if other == root:
                continue
            for ancestor in (other, *other.parents):
                info = stat_if_present(ancestor)
                if info is not None and file_id(info) == file_id(root_st):
                    raise MigrationError(f"路径实际嵌套或同名别称：{root} / {other}")


def checked_paths(source, target, audit):
    source, target, audit = map(local_path, (source, target, audit))
    backup = source.with_name(source.name + BACKUP_SUFFIX)
    for p in (source.parent, target.parent, audit):
        plain_chain(p)
    paths = (source, target, backup, audit)
    if sys.platform == "darwin":
        require(not any(os.path.ismount(p) for p in paths), "请在盘内选择项目或审计目录，不能直接使用挂载点")
    for i, p in enumerate(paths):
        for q in paths[i + 1:]:
            no_overlap(p, q)
            # Canonicalize existing ancestors as well (e.g. DOS 8.3 aliases).
            # The old entry itself can legitimately be a junction on resume.
            no_overlap(p.parent.resolve(strict=True) / p.name,
                       q.parent.resolve(strict=True) / q.name)
    target_real = target.resolve(strict=True)
    audit_real = audit.resolve(strict=True)
    no_overlap(target_real, audit_real)
    for p in (source, backup):
        st = stat_if_present(p)
        physical = (p.resolve(strict=True) if st and not reparse(st)
                    else p.parent.resolve(strict=True) / p.name)
        no_overlap(physical, target_real)
        no_overlap(physical, audit_real)
    dependencies = (Path(__file__).resolve(), Path(sys.executable).resolve())
    for dep in dependencies:
        require(not any(dep.is_relative_to(root) for root in (source, target, backup)),
                f"离线命令依赖的程序位于迁移范围内：{dep}")
    if sys.platform == "darwin":
        check_darwin_ancestry(paths, dependencies)
    return paths


def file_id(st):
    return (st.st_dev, st.st_ino)


def rename_exclusive(source, target):
    if sys.platform == "darwin":
        # Darwin renamex_np(..., RENAME_EXCL) refuses a colliding entry atomically.
        # A pre-check followed by plain POSIX rename could replace an empty directory.
        import ctypes
        rename = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True).renamex_np
        rename.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        rename.restype = ctypes.c_int
        if rename(os.fsencode(source), os.fsencode(target), 0x00000004) != 0:
            code = ctypes.get_errno()
            raise OSError(code, os.strerror(code), str(target))
        return
    require(os.name == "nt", "本机不支持已验证的独占改名操作")
    os.rename(source, target)


def save_exclusive(path, data):
    """Publish a complete receipt without overwriting a concurrent writer."""
    stage = path.with_name("." + uuid4().hex + ".tmp")
    with stage.open("x", encoding="utf-8") as handle:
        staged_id = file_id(os.fstat(handle.fileno()))
        try:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        except BaseException:
            handle.close()
            if file_id(stage.lstat()) == staged_id:
                stage.unlink()
            raise
    try:
        rename_exclusive(stage, path)
    except OSError:
        if stage.exists() and file_id(stage.lstat()) == staged_id:
            stage.unlink()
        raise


def ordinary_record(path):
    st = path.lstat()
    require(stat.S_ISREG(st.st_mode) and not reparse(st) and st.st_nlink == 1,
            f"需要本次审计的独立普通文件：{path}")
    raw = path.read_bytes()
    return json.loads(raw), hashlib.sha256(raw).hexdigest()


def verified_copy(source, target, audit, directory):
    from tree_copy import read_hardlink_policy
    directory = local_path(directory)
    require(directory.parent == audit, "复制审计目录必须直接位于本次迁移审计目录下")
    plain_chain(directory)
    owner, owner_hash = ordinary_record(directory / "owner.json")
    materialize = read_hardlink_policy(owner)
    config_file = audit / "run.json"
    if stat_if_present(config_file) is not None:
        config, _ = ordinary_record(config_file)
        require((Path(config.get("source", "")), Path(config.get("target", ""))) == (source, target)
                and read_hardlink_policy(config) == materialize,
                "复制模式与已批准的迁移记录不一致")
    else:
        require(not materialize, "依赖硬链接物化需要已批准的迁移记录")
    require(Path(owner["source"]) == source and Path(owner["destination"]) == target,
            "复制记录与计划中的原路径或目标路径不符")
    require(owner["destination_id"] == real_dir(target) and owner["source_id"] != owner["destination_id"],
            "目标目录已被替换，或与原目录是同一实体")
    markers = sorted(directory.glob("*-started.json"))
    require(markers, "缺少受管复制/核验的启动记录")
    marker = markers[-1]
    begun, _ = ordinary_record(marker)
    tag = marker.name.removesuffix("-started.json")
    require(re.fullmatch(r"[0-9]{8}T[0-9]{12}Z-[a-f0-9]{10}", tag) and begun.get("run") == tag,
            "最新操作的审计编号异常")
    require(begun.get("action") == "verify" and Path(begun["source"]) == source
            and Path(begun["destination"]) == target, "最后一步必须是同一原件和目标的独立核验")
    receipt = directory / (tag + "-verify.json")
    require(stat_if_present(receipt) is not None, "最近的校验尚未结束，请等待或重做独立核验")
    report, report_hash = ordinary_record(receipt)
    require(read_hardlink_policy(begun) == materialize and read_hardlink_policy(report) == materialize,
            "复制归属、核验启动记录及回执中的依赖硬链接模式不一致")
    if materialize:
        details = report.get("dependency_hardlink_materialization", {})
        require(isinstance(details, dict), "依赖硬链接物化证据格式不完整")
        counts = (details.get("source_external_groups"), details.get("source_external_entries"),
                  details.get("destination_external_groups"))
        require(details.get("scope") == "node_modules"
                and all(type(count) is int and count >= 0 for count in counts)
                and counts[2] == 0 and counts[1] >= counts[0]
                and (counts[0] == 0) == (counts[1] == 0),
                "核验回执缺少完整的依赖硬链接物化证据")
    require(all(report.get(k) == begun[k] for k in ("run", "action", "source", "destination"))
            and report.get("status") == "passed" and report.get("differences") == {}
            and report.get("source_summary") == report.get("destination_summary")
            and isinstance(report.get("source_summary"), dict)
            and bool(report["source_summary"].get("directories_including_root")),
            f"最近的校验未通过或报告不完整：{receipt}")
    evidence = dict(directory=str(directory), directory_id=real_dir(directory),
                    owner_sha256=owner_hash, report=receipt.name, report_sha256=report_hash)
    return owner, evidence


def read_plan(filename):
    from tree_copy import read_hardlink_policy
    filename = local_path(filename)
    require(filename.name == PLAN_NAME, f"需要本次迁移的 {PLAN_NAME}")
    plain_chain(filename.parent)
    st = filename.lstat()
    require(stat.S_ISREG(st.st_mode) and not reparse(st), "接续记录必须是普通文件")
    with filename.open(encoding="utf-8") as handle:
        data = json.load(handle)
    require(data["version"] == 2, "不支持该接续记录版本，需人工核对旧记录")
    # Older v2 plans came exclusively from the Windows-only release.
    require(data.get("platform", "win32") == sys.platform, "该计划来自不同的操作系统")
    source, target, backup, audit = checked_paths(data["source"], data["target"], filename.parent)
    require(audit == Path(data["audit"]) and backup == Path(data["backup"]), "接续记录与目录不匹配")
    require(real_dir(source.parent) == data["parent_id"] and real_dir(audit) == data["audit_id"],
            "原目录的父目录或审计目录已被替换")
    require(real_dir(target) == data["target_id"], f"目标盘或目标目录的系统标识已改变：{target}")
    pinned = data["copy_evidence"]
    owner, current = verified_copy(source, target, audit, pinned["directory"])
    require(read_hardlink_policy(data) == read_hardlink_policy(owner),
            "离线计划的依赖硬链接模式与复制记录不一致")
    require(owner["source_id"] == data["source_id"] and owner["destination_id"] == data["target_id"]
            and current["directory_id"] == pinned["directory_id"]
            and current["owner_sha256"] == pinned["owner_sha256"],
            "本计划依据的原件、目标或复制审计已被替换")
    require(re.fullmatch(r"[0-9]{8}T[0-9]{12}Z-[a-f0-9]{10}-verify\.json", pinned["report"]),
            "计划中的核验报告名不合法")
    _, initial_digest = ordinary_record(Path(pinned["directory"]) / pinned["report"])
    require(initial_digest == pinned["report_sha256"], "建立计划时的核验报告已变化")
    return data, source, target, backup


def verify_link_target(source, target):
    expected_type = source.is_symlink() if sys.platform == "darwin" else source.is_junction()
    require(expected_type and source.samefile(target), f"原路径的链接类型或目标不符：{source}")
    # samefile can pass via a second link. Require the declared target itself.
    named_target = os.readlink(source)
    if os.name == "nt" and (named_target.startswith("\\\\?\\") or named_target.startswith("\\??\\")):
        named_target = named_target[4:]
    require(Path(named_target) == target, f"联接目标不是计划中的准确路径：{named_target}")


def observe(data, source, target, backup):
    old = stat_if_present(source)
    saved = stat_if_present(backup)
    if saved is not None:
        require(real_dir(backup) == data["source_id"], f"备份名已占用或备份身份不符：{backup}")
    if old is None:
        require(saved is not None, f"找不到经记录的原件或备份：{backup}")
        return "renamed"
    if reparse(old):
        verify_link_target(source, target)
        require(saved is not None, f"联接存在，但找不到备份以完成本次清理前验收：{backup}")
        return "linked"
    require(real_dir(source) == data["source_id"], f"源盘或原项目的系统标识与准备时不符：{source}")
    require(saved is None, f"原件和备份名同时存在：{backup}")
    return "ready"


def state(filename):
    data, source, target, backup = read_plan(filename)
    return observe(data, source, target, backup), source, target, backup


def ps_literal(value):
    # PowerShell can interpret curved apostrophes as closing quotes.
    parts = re.split("([\u2018-\u201b])", str(value))
    expressions = ["[char]0x%04x" % ord(part) if i % 2
                   else "'" + part.replace("'", "''") + "'"
                   for i, part in enumerate(parts)]
    return expressions[0] if len(parts) == 1 else "(" + " + ".join(expressions) + ")"


def prepare(args):
    from tree_copy import hardlink_policy_fields, read_hardlink_policy
    source, target, backup, audit = checked_paths(args.source, args.target, args.audit)
    plan = audit / PLAN_NAME
    owner, evidence = verified_copy(source, target, audit, args.copy_audit)
    if stat_if_present(plan) is None:
        present = stat_if_present(source)
        require(not present or not reparse(present),
                "本次缺少切换计划，但原名已为联接。请先只读核实复制记录、目标、备份身份和现有联接，不要改名或覆盖")
        data = dict(version=2, platform=sys.platform, source=str(source), target=str(target), backup=str(backup),
                    audit=str(audit), source_id=owner["source_id"], target_id=owner["destination_id"],
                    copy_evidence=evidence,
                    audit_id=real_dir(audit), parent_id=real_dir(source.parent))
        data.update(hardlink_policy_fields(read_hardlink_policy(owner)))
        observe(data, source, target, backup)
        save_exclusive(plan, data)
    existing, _, _, _ = read_plan(plan)
    require(evidence["directory"] == existing["copy_evidence"]["directory"],
            "该计划已绑定另一个复制审计目录")
    phase, old, dest, _ = state(plan)
    require((source, target) == (old, dest), "该审计目录属于另一项迁移，请使用其对应的接续记录")
    cmd = (sys.executable, "-B", str(Path(__file__).resolve()),
           "apply", "--plan", str(plan), "--mode", args.mode)
    print(f"phase={phase}\nplan={plan}\nbackup={backup}")
    if sys.platform == "darwin":
        print(f"(cd {shlex.quote(str(audit))} && {shlex.join(cmd)})")
    else:
        command = "& " + " ".join(ps_literal(arg) for arg in cmd)
        print("& { Set-Location -LiteralPath " + ps_literal(audit)
              + " -ErrorAction Stop; " + command + " }")


def status(args):
    phase, source, target, backup = state(args.plan)
    print(json.dumps(dict(phase=phase, link_type=link_type(), source=str(source), target=str(target),
                          backup=str(backup)), ensure_ascii=False))


def verified_git_exceptions(data, source, target):
    import git_check

    baseline_path = Path(data["audit"]) / "git-before.json"
    if stat_if_present(baseline_path) is None:
        return {}
    baseline, _ = ordinary_record(baseline_path)
    snap = baseline["snapshot"]
    require(baseline.get("schema") == 1 and baseline.get("ok") is True
            and baseline.get("mode") == "baseline" and baseline["root"] == snap["root"] == str(source),
            f"Git 基准与本次原项目不一致：{baseline_path}")
    if snap["linked"]:
        git_check.verify(target, snap)
    return git_check.repair_metadata_exceptions(snap)


def apply_locked(args):
    from tree_copy import recheck_verified_copy

    data, source, target, backup = read_plan(args.plan)
    phase = observe(data, source, target, backup)
    if phase == "ready":
        require(args.mode == "auto", f"你选择了手动改名。请将 {source} 改名为 {backup.name} 后重试")
    if phase != "linked":
        # A successful new verify can supersede the receipt that created the plan.
        _, evidence = verified_copy(source, target, Path(data["audit"]), data["copy_evidence"]["directory"])
        original = backup if phase == "renamed" else source
        exceptions = verified_git_exceptions(data, source, target)
        recheck_verified_copy(original, target, evidence, renamed=phase == "renamed",
                              pointer_exceptions=exceptions, source_junction_root=source)
        require(observe(data, source, target, backup) == phase, "切换复核期间目录状态发生变化")
    if phase == "ready":
        rename_exclusive(source, backup)
        phase, source, target, backup = state(args.plan)
    if phase == "renamed":
        try:
            if sys.platform == "darwin":
                os.symlink(target, source, target_is_directory=True)
            else:
                from _winapi import CreateJunction
                CreateJunction(str(target), str(source))
        except (OSError, ImportError) as exc:
            raise MigrationError(f"联接未创建，原件备份位于 {backup}。"
                                 f"排除问题后用同一命令接续。原因：{exc}") from exc
    phase, source, target, backup = state(args.plan)
    require(phase == "linked", "联接尚未通过检查")
    print(f"FullName : {source}\nLinkType : {link_type()}\nTarget   : {target}\nBackup   : {backup}")


def apply(args):
    from tree_copy import exclusive_lock
    data, _, _, _ = read_plan(args.plan)
    copy_dir = Path(data["copy_evidence"]["directory"])
    lock_info = (copy_dir / "copy.lock").lstat()
    require(stat.S_ISREG(lock_info.st_mode) and not reparse(lock_info) and lock_info.st_nlink == 1,
            "受管复制锁文件异常")
    with exclusive_lock(copy_dir):
        apply_locked(args)


def main():
    require(sys.platform in ("win32", "darwin") and sys.version_info >= (3, 12),
            "需要 Windows 或 macOS 及 CPython 3.12 或更新版本")
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(required=True)
    p = commands.add_parser("prepare")
    for flag in ("source", "target", "audit", "copy-audit"):
        p.add_argument("--" + flag, required=True)
    p.add_argument("--mode", choices=("manual", "auto"), required=True)
    p.set_defaults(handler=prepare)
    for name, handler in (("status", status), ("apply", apply)):
        p = commands.add_parser(name)
        p.add_argument("--plan", required=True)
        if name == "apply":
            p.add_argument("--mode", choices=("manual", "auto"), required=True)
        p.set_defaults(handler=handler)
    args = parser.parse_args()
    args.handler(args)


if __name__ == "__main__":
    try:
        main()
    except (MigrationError, OSError, EOFError, KeyError, RuntimeError, ValueError) as e:
        print(f"已停止：{e}", file=sys.stderr)
        sys.exit(1)
