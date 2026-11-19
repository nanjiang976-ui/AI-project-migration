"""Native local APFS copy/verify backend, using the shared migration receipt format."""
import argparse
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import unicodedata
from uuid import uuid4

import mac_fs as fs
from tree_copy import (changed_since, dump_json, exclusive_lock, link_groups,
                       save_manifest, summarize)

FIELDS = ("type", "mode", "uid", "gid", "acl", "flags", "size", "sha256", "streams", "mtime_ns")


def signature(s):
    return (s.st_ino, s.st_dev, s.st_mode, s.st_flags, s.st_uid, s.st_gid, s.st_nlink,
            s.st_size, s.st_mtime_ns, s.st_ctime_ns)


def entry(path, s, vol_uuid, with_hash):
    folder = stat.S_ISDIR(s.st_mode)
    row = dict(type="dir" if folder else "file", mode=stat.S_IMODE(s.st_mode),
               uid=s.st_uid, gid=s.st_gid, flags=s.st_flags, acl=fs.acl(path),
               size=0 if folder else s.st_size, identity=[vol_uuid, s.st_ino],
               link_count=s.st_nlink, mtime_ns=s.st_mtime_ns, ctime_ns=s.st_ctime_ns,
               streams=fs.xattrs(path, with_hash))
    if with_hash and not folder:
        flags = os.O_RDONLY | os.O_NOFOLLOW
        with os.fdopen(os.open(path, flags), "rb") as handle:
            if signature(os.fstat(handle.fileno())) != signature(s):
                raise ValueError(f"Source changed before reading: {path}")
            row["sha256"] = hashlib.file_digest(handle, "sha256").hexdigest()
    if signature(path.lstat()) != signature(s):
        raise ValueError(f"Entry changed during inventory: {path}")
    return row


def snapshot(root, with_hash):
    uuid, dev = fs.volume_info(root)
    fs.physical_chain(root)
    rows = {}
    stack = [(".", root)]
    while stack:
        key, path = stack.pop()
        s = path.lstat()
        if (s.st_dev != dev or not s.st_ino or
                not (stat.S_ISDIR(s.st_mode) or stat.S_ISREG(s.st_mode))):
            raise ValueError(f"Unexpected symlink, nested mount, or special file: {path}")
        rows[key] = entry(path, s, uuid, with_hash)
        if stat.S_ISDIR(s.st_mode):
            with os.scandir(path) as items:
                names = sorted((i.name for i in items), reverse=True)
            for name in names:
                stack.append((name if key == "." else key + "/" + name, path / name))
    if fs.volume(root) != uuid:
        raise ValueError(f"Volume changed during inventory: {root}")
    return rows


def disjoint(paths):
    # Comparing existing ancestor identities detects firmlink aliases (e.g. /Users
    # and /System/Volumes/Data/Users) as well as projected non-existent children.
    def projected(path):
        p = path
        while not p.exists():
            p = p.parent
        s = p.stat()
        return (s.st_dev, s.st_ino), path.relative_to(p).parts

    for i, a in enumerate(paths):
        for j, b in enumerate(paths):
            if i == j:
                continue
            key = projected(a)
            if any(projected(p) == key for p in (b, *b.parents)):
                raise ValueError(f"Source, destination and audit must be disjoint: {a}, {b}")


def check_run_volumes(src, dst, audit):
    """An unmounted custom mount point may still exist on the system volume."""
    run = audit.parent / "run.json"
    if not os.path.lexists(run):  # Standalone file verification has no host checkpoint.
        return
    if run.is_symlink() or not run.is_file():
        raise ValueError("Expected an ordinary migration checkpoint")
    config = json.loads(run.read_text(encoding="utf-8"))
    if (str(src) not in (config.get("source"), config.get("backup"))
            or str(dst) != config.get("target")):
        raise ValueError("Input paths disagree with the host migration checkpoint")
    pinned = config.get("volumes", {})
    if (fs.volume(src) != pinned.get("source_volume")
            or fs.volume(dst) != pinned.get("target_volume")):
        raise ValueError("A volume differs from the preflight checkpoint; check mounts")


def collisions(rows, dst):
    # _PC_CASE_SENSITIVE from Darwin's sys/unistd.h. APFS normalizes lookups even
    # on case-sensitive volumes. Exclusive creation is the final kernel check.
    sensitive = os.pathconf(dst.parent, 11)
    if sensitive not in (0, 1):
        raise ValueError("Unable to determine target volume case sensitivity")
    seen = {}
    for p in rows:
        key = unicodedata.normalize("NFD", p)
        if not sensitive:
            key = key.casefold()
        if key in seen:
            raise ValueError(f"Names may collide on the target: {seen[key]}, {p}")
        seen[key] = p


def differ(a, b):
    return [k for k in FIELDS if a.get(k) != b.get(k)]


def verify(src, dst, audit, report):
    a, b = snapshot(src, True), snapshot(dst, True)
    diffs = defaultdict(list)
    for name, root, rows in (("source", src, a), ("destination", dst, b)):
        save_manifest(audit / (report["run"] + "-" + name + ".jsonl.gz"), rows)
        drift = changed_since(rows, snapshot(root, False))
        if drift:
            diffs[name + "_changed_during_verification"] = drift
    for name, paths in (("source_only", a.keys() - b.keys()), ("destination_only", b.keys() - a.keys())):
        if paths:
            diffs[name] = sorted(paths)
    for k in sorted(a.keys() & b.keys()):
        for field in differ(a[k], b[k]):
            diffs[field].append(k)
    ga, xa = link_groups(a)
    gb, xb = link_groups(b)
    if ga != gb:
        diffs["missing_internal_link_groups"] = sorted(ga - gb)
        diffs["extra_internal_link_groups"] = sorted(gb - ga)
    if xa or xb:
        diffs["external_hardlink_references"] = {"source": xa, "destination": xb}
    report.update(source_summary=summarize(a), destination_summary=summarize(b),
                  internal_hardlink_groups={"source": len(ga), "destination": len(gb)}, differences=diffs)
    return not diffs


def ids_contained(path, root, dev, entries):
    for parent in (path.parent, *path.parent.parents):
        if not parent.is_relative_to(root):
            break
        k = parent.relative_to(root).as_posix()
        s = parent.lstat()
        if not stat.S_ISDIR(s.st_mode) or s.st_dev != dev or s.st_ino != entries[k]:
            raise ValueError(f"Directory was replaced: {parent}")


def copy(src, dst, audit, report):
    srcdev = src.stat().st_dev
    dest_uuid, dstdev = fs.volume_info(dst.parent)
    rows = snapshot(src, True)
    save_manifest(audit / (report["run"] + "-source.jsonl.gz"), rows)
    groups, external = link_groups(rows)
    report.update(source_summary=summarize(rows), internal_hardlink_groups=len(groups))
    if external:
        report["external_hardlink_references"] = external
        raise ValueError("Source has hardlinks outside the project")
    collisions(rows, dst)
    ownerfile = audit / "owner.json"
    owner = dict(source=str(src), destination=str(dst), source_id=rows["."]["identity"])
    if not os.path.lexists(dst):
        if ownerfile.exists():
            raise ValueError("Verified candidate disappeared; choose a new run")
        fs.create_private(dst)
        if dst.stat().st_dev != dstdev:
            raise ValueError("Destination parent volume changed")
        dump_json(ownerfile, dict(**owner, destination_id=fs.object_id(dst)))
    elif (not ownerfile.is_file() or json.loads(ownerfile.read_text(encoding="utf-8")) !=
            dict(**owner, destination_id=fs.object_id(dst))):
        raise ValueError(f"Destination is an unowned or replaced candidate: {dst}")
    existing = snapshot(dst, True)
    extra = existing.keys() - rows.keys()
    if extra:
        report["destination_only"] = sorted(extra)
        raise ValueError("Destination has extra data")
    ledger = audit / "directories.jsonl"
    owned = {".": existing["."]["identity"][1]}
    if ledger.exists():
        with ledger.open(encoding="utf-8") as f:
            owned.update((k, inode) for k, inode in map(json.loads, f))
    anchors, anchor_ids, used = {}, {}, {}
    for key, dstrow in existing.items():
        row = rows[key]
        if dstrow["type"] == "dir":
            if row["type"] != "dir" or owned.get(key) != dstrow["identity"][1]:
                raise ValueError(f"Unowned or replaced candidate directory: {key}")
        else:
            if differ(row, dstrow):
                raise ValueError(f"Conflicting or incomplete target file; preserve and inspect: {key}")
            srcid, dstid = row["identity"][1], dstrow["identity"][1]
            if anchor_ids.setdefault(srcid, dstid) != dstid or used.setdefault(dstid, srcid) != srcid:
                raise ValueError(f"Candidate hardlink membership differs: {key}")
            anchors[srcid] = dst / key
    if link_groups(existing)[1]:
        raise ValueError("Existing candidate has external hardlinks")
    payload, counted = 0, set(anchors)
    for key, row in rows.items():
        if row["type"] == "file":
            if row["identity"][1] in counted:
                continue
            counted.add(row["identity"][1])
        payload += row["size"] + sum(a["size"] for a in row["streams"].values())
    free = shutil.disk_usage(dst.parent).free
    report["capacity"] = {"remaining_unique_payload_bytes": payload, "free_bytes": free}
    if payload > free:
        raise OSError(f"Not enough target space: {free} free, {payload} required")
    srcdirs = {k: r["identity"][1] for k, r in rows.items() if r["type"] == "dir"}
    dirs = []
    with ledger.open("a", encoding="utf-8") as log:
        for k, row in rows.items():
            s, d = src / k, dst / k
            ids_contained(s, src, srcdev, srcdirs)
            ids_contained(d, dst, dstdev, owned)
            srcinfo = s.lstat()
            if srcinfo.st_dev != srcdev or srcinfo.st_ino != row["identity"][1]:
                raise ValueError(f"Source replaced during copy: {s}")
            if row["type"] == "dir":
                dirs.append(k)
                if k not in existing:
                    fs.create_private(d)
                    owned[k] = d.stat().st_ino
                    log.write(json.dumps([k, owned[k]]) + "\n")
                    log.flush()
                continue
            if k in existing:
                continue
            iid = row["identity"][1]
            if iid in anchors:
                anchor = anchors[iid]
                ids_contained(anchor, dst, dstdev, owned)
                astat = anchor.lstat()
                if astat.st_dev != dstdev or astat.st_ino != anchor_ids[iid]:
                    raise ValueError(f"Hardlink anchor changed: {anchor}")
                os.link(anchor, d, follow_symlinks=False)
            else:
                fs.transfer(s, d, row)
                anchors[iid], anchor_ids[iid] = d, d.lstat().st_ino
    for k in reversed(dirs):
        s, d = src / k, dst / k
        ids_contained(s, src, srcdev, srcdirs)
        ids_contained(d, dst, dstdev, owned)
        old = d.lstat()
        if old.st_ino != owned[k] or old.st_dev != dstdev:
            raise ValueError(f"Target directory replaced: {d}")
        # Never remove or overwrite independent directory-stream edits on retry.
        if k in existing and any(rows[k]["streams"].get(n) != x for n, x in existing[k]["streams"].items()):
            raise ValueError(f"Conflicting candidate directory attributes: {d}")
        fs.transfer(s, d, rows[k], directory=True)
    drift = changed_since(rows, snapshot(src, False))
    if drift:
        report["source_changed_during_copy"] = drift
        raise RuntimeError("Source changed during copy")
    if fs.volume(dst) != dest_uuid:
        raise RuntimeError("Destination volume changed during copy")
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["copy", "verify"])
    parser.add_argument("source")
    parser.add_argument("destination")
    parser.add_argument("--audit", required=True)
    args = parser.parse_args()
    src, dst, audit = map(fs.checked_root, (args.source, args.destination, args.audit))
    disjoint((src, dst, audit))
    if not src.is_dir() or not dst.parent.is_dir() or not audit.parent.is_dir():
        raise ValueError("Source and destination/audit parent directories must already exist")
    check_run_volumes(src, dst, audit)
    if not audit.exists():
        fs.create_private(audit)
    fs.require_private(audit)
    for item in audit.iterdir():
        info = item.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError(f"Unexpected object in audit directory: {item}")
    with exclusive_lock(audit):
        tag = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid4().hex[:10]
        report = dict(run=tag, action=args.action, source=str(src), destination=str(dst), platform="darwin")
        dump_json(audit / (tag + "-started.json"), report)
        try:
            action = copy if args.action == "copy" else verify
            success = action(src, dst, audit, report)
            report["status"] = "passed" if success else "differences"
        except (OSError, ValueError, RuntimeError) as e:
            report.update(status="stopped", error=str(e))
        path = audit / (tag + "-" + args.action + ".json")
        dump_json(path, report)
    print(json.dumps({"status": report["status"], "report": str(path)}, ensure_ascii=True))
    return 0 if report["status"] == "passed" else 2
