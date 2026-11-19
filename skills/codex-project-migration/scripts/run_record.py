"""Append-only checkpoints and final safety notices for a local project migration."""
import argparse
from datetime import datetime, timezone
import gzip
import json
import os
from pathlib import Path
import sys
from uuid import uuid4


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def absolute(value):
    path = Path(value)
    if not path.is_absolute() or path == Path(path.anchor) or ".." in path.parts:
        raise ValueError(f"Choose an absolute path below the volume root: {value}")
    if os.name == "nt" and (len(path.drive) != 2 or any(x.endswith((" ", ".")) or Path(x).is_reserved()
           or any(c in '<>:"|?*' or ord(c) < 32 for c in x) for x in path.parts[1:])):
        raise ValueError(f"Invalid local path: {value}")
    return path


def store_new(path, data):
    temp = path.with_name(f".{uuid4().hex}.partial")
    with temp.open("x", encoding="utf-8") as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    if os.name == "nt":
        os.rename(temp, path)
    else:
        os.link(temp, path)  # Atomic publication without replacing a concurrent event.
        temp.unlink()


def read(path):
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"Expected an ordinary receipt: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def volume_ids(old, new):
    if sys.platform == "darwin":
        import mac_fs
        return [mac_fs.volume(p) for p in (old, new)]
    import migration_ntfs as nt
    for p in (old,new):
        nt.volume(p)
    return [nt.physical(p).drive.casefold() for p in (old,new)]


def volumes(old, new):
    source, target = volume_ids(old, new)
    same = source == target
    return dict(source_volume=source, target_volume=target, same_volume=same,
                message="这次移动不会释放该卷空间。确认仍要整理目录后再继续。" if same else "源和目标是不同的本地卷。")


def create_run(args, run):
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
        roots = [nt.physical(p) for p in paths]
    else:
        import mac_fs
        roots = [mac_fs.checked_root(p) for p in paths]
    for i, p in enumerate(roots):
        for q in roots[i+1:]:
            if p.is_relative_to(q) or q.is_relative_to(p):
                raise ValueError("Keep the source, target, backup and audit paths separate.")
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
    store_new(run/"run.json", dict(version=2, migration_id=str(uuid4()), host=args.host,
              project_id=args.project_id or None, source=str(old), target=str(new), backup=str(backup),
              started=timestamp(), volumes=vol))


def cleanup_check(config, run, chat_status):
    """Check the latest completed backup verification, never a stale success ahead of a failed attempt."""
    from cutover import state
    phase, old, target, backup = state(run/"cutover-plan.json")
    if (phase != "linked" or (str(old),str(target),str(backup)) !=
            (config["source"], config["target"], config["backup"])):
        return False, "旧路径、目标或备份身份未经确认"
    if chat_status not in ("passed","not_applicable"):
        return False, "旧会话核验尚有待处理项"
    if chat_status == "passed":
        receipts = (read(p) for p in run.glob("probe-*.json"))
        if not any(p.get("same_file") and p.get("probe_removed") and p.get("old_write_d_read")
                   and p.get("d_write_old_read") and not p.get("filesystem_only")
                   and p.get("target") == str(target) and p.get("backup") == str(backup) for p in receipts):
            return False, "缺少本次双向读写回执"
    folder=run/"backup-check"
    marker=max(folder.glob("*-started.json"),default=None)
    if marker is None:
        return False, "缺少清理前的备份复核记录"
    tag=marker.name.removesuffix("-started.json")
    begun=read(marker)
    cmp=read(folder/(tag+"-verify.json"))
    if any(cmp.get(k)!=v or begun.get(k)!=v for k,v in {
            "action":"verify","run":tag,"source":str(backup),"destination":str(target)}.items()):
        return False, "最新复核的现场与本次备份不匹配"
    if cmp.get("status") not in ("passed","differences") or not isinstance(cmp.get("differences"),dict):
        return False, "最近一次备份核验未完成"
    diffs=cmp["differences"]
    allowed_times={"."} if chat_status=="passed" else set()
    before=read(run/"git-before.json")
    if not before.get("ok") or before.get("root")!=str(old):
        return False, "缺少已确认的 Git 基准"
    if before["kind"]!="non_git":
        checks=[]
        for path in run.glob("*.json"):
            if path.name in ("cutover-plan.json","git-before.json","run.json"): continue
            doc=read(path)
            if isinstance(doc,dict) and doc.get("mode")=="verify" and doc.get("root")==str(target):
                checks.append(doc)
        latest=max(checks,key=lambda item:item["utc"],default={})
        if latest.get("ok") is not True or latest.get("kind")!=before["kind"]:
            return False,"缺少成功的最新 Git 验收，或最新核验失败"
        from git_check import verify as verify_git
        verify_git(target,before["snapshot"])
        for tree in before["snapshot"]["linked"]:
            allowed_times.add(".git/worktrees/"+tree["admin"]+"/gitdir")
    if (set(diffs)-{"mtime_ns"} or set(diffs.get("mtime_ns",[]))-allowed_times
            or cmp.get("source_summary")!=cmp.get("destination_summary")):
        return False,"备份有独有内容、两边存在差异或发生了未解释的变更"
    # A previous success cannot authorize deleting a backup modified since that check.
    if sys.platform=="darwin":
        from mac_tree import snapshot
    else:
        from tree_copy import snapshot
    from tree_copy import changed_since
    for side,root in (("source",backup),("destination",target)):
        current=snapshot(root,True)
        with gzip.open(folder/(tag+"-"+side+".jsonl.gz"),"rt",encoding="utf-8") as saved:
            for line in saved:
                row=json.loads(line)
                key=row.pop("path")
                if current.get(key)!=row:
                    return False,"文件已变化；请重新比较备份和目标"
        if len(current)!=sum(cmp[side+"_summary"][k] for k in ("files","directories_including_root")):
            return False,"文件清单已变化；请重新比较备份和目标"
        if changed_since(current,snapshot(root,False)):
            return False,"复核期间文件仍在变化"
    return True,"本次已复验备份、目标和白名单内的时间差异；如继续编辑，请重新核验"


def notice(config, valid, reason):
    old, new, backup = (config[k] for k in ("source","target","backup"))
    kind="目录联接" if os.name=="nt" else "目录符号链接"
    return dict(warning=f"⚠️ 请勿删除原位置下的原名文件夹：{old}（{kind}）。旧聊天通过它访问新地址；进入后删除内容会直接影响目标实体。",
                target=new, target_message=f"正在使用的项目实体，必须保留：{new}", backup=backup, eligible=valid,
                backup_message=(f"验收通过后可手动清理的实体备份：{backup}" if valid else
                                f"⛔ 暂勿清理备份：{backup}")+"。"+reason)


def record(args):
    if args.command=="volumes":
        print(json.dumps(volumes(absolute(args.source),absolute(args.target)),ensure_ascii=False))
        return
    run=absolute(args.run)
    if args.command=="start": create_run(args,run)
    if not run.is_dir() or run.resolve(strict=True)!=run:
        raise ValueError("Run directory must be physical.")
    config=read(run/"run.json")
    events=[read(p) for p in sorted(run.glob("event-*.json"))]
    step=1
    chat_status=None
    finished=("passed","not_applicable","unverified")
    for e in events:
        if e["status"]=="reopened":
            step=e["step"]
            if step<=6: chat_status=None
        elif e["status"] in finished:
            if e["step"]==6: chat_status=e["status"]
            step+=1
    eligible,reason=False,"核验未完成"
    if (step>7 and args.command=="show") or (step==7 and args.command=="mark" and args.status=="passed"):
        from cutover import MigrationError
        try: eligible,reason=cleanup_check(config,run,chat_status)
        except (ValueError,KeyError,OSError,RuntimeError,TypeError,MigrationError) as exc: reason=str(exc)
    rejected=False
    if args.command in ("mark","reopen"):
        if args.command=="reopen":
            if not 1<=args.step<step: raise ValueError("Only a completed stage can be reopened.")
            step=args.step
        elif step!=args.step or not 1<=step<=7: raise ValueError(f"Expected step {step}.")
        if args.status in ("not_applicable","unverified") and step!=6:
            raise ValueError("A chat coverage decision is only applicable at step 6.")
        if not args.evidence.strip() or not args.next_action.strip():
            raise ValueError("Provide an evidence reference and the next action.")
        if step==7 and args.status=="passed" and not eligible:
            args.status="blocked"
            rejected=True
        ev=dict(step=step,status=args.status,evidence=args.evidence,next_action=args.next_action,recorded=timestamp())
        if rejected: ev["verification_error"]=reason
        store_new(run/f"event-{len(events)+1:06}.json",ev)
        events.append(ev)
        if args.status in finished: step+=1
    print(json.dumps(dict(cleanup_notice=notice(config,eligible,reason),run=str(run),config=config,
                          next_step=step if step<=7 else None,last_event=events[-1] if events else None),ensure_ascii=False))
    if rejected: raise SystemExit(2)


def main():
    if os.name!="nt" and sys.platform!="darwin": raise SystemExit("Requires native Windows or macOS.")
    parser=argparse.ArgumentParser(description=__doc__)
    subs=parser.add_subparsers(dest="command",required=True)
    start=subs.add_parser("start")
    preview=subs.add_parser("volumes")
    for cmd in (start,preview):
        cmd.add_argument("source"); cmd.add_argument("target")
    start.add_argument("--project-id")
    start.add_argument("--host",choices=("codex","claude-code","generic"),default="codex")
    start.add_argument("--allow-same-volume",action="store_true")
    show=subs.add_parser("show")
    mark=subs.add_parser("mark")
    reopen=subs.add_parser("reopen");reopen.set_defaults(status="reopened")
    for cmd in (start,show,mark,reopen): cmd.add_argument("--run",required=True)
    mark.add_argument("--status",choices=("waiting","blocked","passed","not_applicable","unverified"),required=True)
    for cmd in (mark,reopen):
        cmd.add_argument("--step",type=int,required=True)
        cmd.add_argument("--evidence",required=True)
        cmd.add_argument("--next-action",required=True)
    try: record(parser.parse_args())
    except (OSError,ValueError,KeyError,TypeError) as exc: parser.exit(1,f"Migration checkpoint: {exc}\n")


if __name__=="__main__":
    if hasattr(sys.stdout,"reconfigure"):
        sys.stdout.reconfigure(errors="backslashreplace")
    main()
