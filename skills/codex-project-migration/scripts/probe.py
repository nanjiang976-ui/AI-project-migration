"""Prove one existing chat actually reads and writes the migrated main directory."""
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


def probe(plan, request):
    data, source, target, backup = read_plan(plan)
    require(observe(data, source, target, backup) == "linked", "请先完成并验证目录联接")
    cwd = Path.cwd()
    require(cwd == source and cwd.samefile(target),
            f"当前默认工作目录为 {cwd}，本次需要旧主目录 {source}。请在合适的旧聊天中运行，不要切换目录")
    report = Path(data["audit"]) / ("probe-" + str(request) + ".json")
    require(not os.path.lexists(report), f"该次请求已有报告，请核对原结果：{report}")
    name = ".codex-migration-probe-" + uuid4().hex + ".tmp"
    relative = Path(name)
    peer = target / name
    saved = backup / name
    token = secrets.token_bytes(32)
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
        with peer.open("r+b") as f:
            check_file(f, identity)
            require(f.read() == token, "D 盘读取内容不符")
            f.write(reply)
            f.flush()
            os.fsync(f.fileno())
        with relative.open("rb") as f:
            check_file(f, identity)
            require(f.read() == token + reply, "旧路径未能读取 D 盘追加内容")
        require(not os.path.lexists(saved), "备份内意外出现测试文件")
    finally:
        if identity is not None:
            # Remove only the exact object this invocation created. A stale alias
            # on cleanup must not make us unlink an unrelated replacement.
            st = relative.lstat()
            require(file_id(st) == identity and stat.S_ISREG(st.st_mode) and not reparse(st),
                    f"测试文件身份已变化，保留现场：{source / name}")
            relative.unlink()
    require(not any(os.path.lexists(p) for p in (relative, peer, saved)), "测试文件清理未通过")
    require(state(plan)[0] == "linked", "验收期间联接发生变化")
    result = dict(utc=datetime.now(timezone.utc).isoformat(), request_id=str(request),
                  cwd=str(cwd), target=str(target), backup=str(backup), file_id=list(identity),
                  default_cwd_confirmed=True, old_write_d_read=True, d_write_old_read=True,
                  same_file=True, backup_isolated=True, probe_removed=True)
    save_exclusive(report, result)
    print(json.dumps({**result, "report": str(report)}, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True)
    parser.add_argument("--request-id", type=UUID,
                        help="匹配本次手动或代发的测试请求；同一编号的报告不会覆盖")
    args = parser.parse_args()
    request = args.request_id or uuid4()
    probe(args.plan, request)


if __name__ == "__main__":
    try:
        main()
    except (MigrationError, OSError, KeyError, ValueError) as e:
        print(f"已停止：{e}", file=sys.stderr)
        sys.exit(1)
