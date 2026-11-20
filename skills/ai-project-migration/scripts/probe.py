"""Verify the real cwd and old path against the target; session provenance belongs to the host."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import secrets
import stat
import sys
from uuid import UUID, uuid4

from cutover import MigrationError, file_id, observe, read_plan, reparse, require, save_exclusive, state


def check_file(handle, identity):
    require(file_id(os.fstat(handle.fileno())) == identity, "两条路径指向不同文件")


def probe(plan, request, resumed_session_id=None, filesystem_only=False):
    data, source, target, backup = read_plan(plan)
    require(observe(data, source, target, backup) == "linked", "请先完成并验证目录联接")
    cwd = Path.cwd()
    # POSIX getcwd() resolves directory symlinks. A host can also legitimately
    # reopen an old session already bound to the physical target on Windows.
    # The host must corroborate the old session ID before claiming chat coverage.
    if cwd == source:
        cwd_form = "old_path"
    elif cwd == target and (resumed_session_id or filesystem_only):
        cwd_form = "resolved_target"
    else:
        raise MigrationError(f"默认工作目录为 {cwd}。请从原会话默认目录运行；如果应用已恢复到目标目录，"
                             "先核实迁移前会话标识，或明确只验证文件系统。")
    require(cwd.samefile(target) and source.samefile(target), "原路径、默认目录与目标实体不一致")
    report = Path(data["audit"]) / ("probe-" + str(request) + ".json")
    require(not os.path.lexists(report), f"该次请求已有报告，请核对原结果：{report}")
    identity = exercise(source, target, backup, Path("."))
    require(state(plan)[0] == "linked", "验收期间联接发生变化")
    result = dict(utc=datetime.now(timezone.utc).isoformat(), request_id=str(request),
                  cwd=str(cwd), cwd_form=cwd_form, source=str(source), target=str(target),
                  backup=str(backup), file_id=list(identity), old_path_link_verified=True,
                  claimed_resumed_session_id=resumed_session_id, filesystem_only=filesystem_only,
                  session_identity_requires_host_match=True, old_write_target_read=True,
                  target_write_old_read=True,
                  default_cwd_confirmed=True, old_write_d_read=True, d_write_old_read=True,
                  same_file=True, backup_isolated=True, probe_removed=True)
    emit(report, result)


def exercise(source, target, backup, seed_dir):
    """Write and remove one temporary file through two verified directory paths."""
    require(source.samefile(target) and seed_dir.samefile(target) and not backup.samefile(target),
            "实际写入与原名入口、目标、备份不匹配")
    name = ".codex-migration-probe-" + uuid4().hex + ".tmp"
    relative = seed_dir / name
    via_old_name = source / name
    peer = target / name
    saved = backup / name
    token = secrets.token_bytes(32)
    alias_token = secrets.token_bytes(32)
    reply = secrets.token_bytes(32)
    require(not os.path.lexists(saved), "备份中存在同名测试文件")
    identity = None
    try:
        with relative.open("xb") as f:
            identity = file_id(os.fstat(f.fileno()))
            require(all(identity), "无法读取临时文件标识")
            f.write(token)
            f.flush()
            os.fsync(f.fileno())
        with via_old_name.open("r+b") as f:
            check_file(f, identity)
            require(f.read() == token, "通过原名入口未能读取测试文件")
            f.write(alias_token)
            f.flush()
            os.fsync(f.fileno())
        with peer.open("r+b") as f:
            check_file(f, identity)
            require(f.read() == token + alias_token, "目标路径读取内容不符")
            f.write(reply)
            f.flush()
            os.fsync(f.fileno())
        for reader in (relative, via_old_name):
            with reader.open("rb") as f:
                check_file(f, identity)
                require(f.read() == token + alias_token + reply, f"未能读回目标路径追加的内容：{reader}")
        require(not os.path.lexists(saved), "备份内意外出现测试文件")
    finally:
        if identity is not None:
            # Remove only the exact object this invocation created. A stale alias
            # on cleanup must not make us unlink an unrelated replacement.
            st = relative.lstat()
            require(file_id(st) == identity and stat.S_ISREG(st.st_mode) and not reparse(st),
                    f"测试文件身份已变化，保留现场：{source / name}")
            relative.unlink()
    require(not any(os.path.lexists(p) for p in (relative, via_old_name, peer, saved)), "测试文件清理未通过")
    return identity


def emit(report, result):
    save_exclusive(report, result)
    print(json.dumps({**result, "report": str(report)}, ensure_ascii=False))


def batch_probe(plan, request):
    from batch_evidence import batch_binding, probe_inputs
    directory, data, registered, root, seed, old_entry, suffix = probe_inputs(plan, request)
    report = directory / ("probe-" + str(request) + ".json")
    require(not os.path.lexists(report), f"该次请求已有报告，请核对原结果：{report}")
    before = Path.cwd()
    identity = exercise(old_entry / suffix, Path(root["target"]) / suffix,
                        Path(root["backup"]) / suffix, seed)
    probe_inputs(plan, request)
    require(Path.cwd() == before, "验收期间会话工作目录发生变化")
    emit(report, {**batch_binding(data), **registered,
                  **{k: root[k] for k in ("source", "target", "backup")},
                  "utc": datetime.now(timezone.utc).isoformat(), "cwd": str(before),
                  "probe_relative_directory": suffix.as_posix(), "file_id": list(identity),
                  "same_file": True, "backup_isolated": True, "probe_removed": True,
                  "old_write_target_read": True, "target_write_old_read": True,
                  "session_identity_requires_host_match": True})


def main():
    require(sys.platform in ("win32", "darwin") and sys.version_info >= (3, 12),
            "需要 Windows 或 macOS 及 CPython 3.12 或更新版本")
    parser = argparse.ArgumentParser(description=__doc__)
    plan = parser.add_mutually_exclusive_group(required=True)
    plan.add_argument("--plan")
    plan.add_argument("--batch", help="只使用该批次预先登记的旧会话及来源")
    parser.add_argument("--request-id", type=UUID,
                        help="匹配本次手动或代发的测试请求；同一编号的报告不会覆盖")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--resumed-session-id",
                      help="由宿主核对的迁移前既有会话 ID；可接受已解析到目标实体的默认目录")
    mode.add_argument("--filesystem-only", action="store_true",
                      help="仅核对文件系统读写；不能作为既有会话恢复证明")
    args = parser.parse_args()
    if args.batch:
        require(args.request_id and not args.resumed_session_id and not args.filesystem_only,
                "批次探针必须使用已登记的请求，默认工作目录由原会话核实")
        batch_probe(args.batch, args.request_id)
        return
    if args.resumed_session_id is not None:
        require(args.request_id and args.resumed_session_id.strip() == args.resumed_session_id
                and 0 < len(args.resumed_session_id) <= 256 and args.resumed_session_id.isprintable(),
                "恢复会话测试需要有效的原会话标识和预先登记的 --request-id")
    request = args.request_id or uuid4()
    probe(args.plan, request, args.resumed_session_id, args.filesystem_only)


if __name__ == "__main__":
    try:
        main()
    except (MigrationError, OSError, KeyError, TypeError, ValueError) as e:
        print(f"已停止：{e}", file=sys.stderr)
        sys.exit(1)
