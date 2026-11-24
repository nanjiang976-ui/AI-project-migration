"""Copy or independently verify two physical NTFS project trees; never delete files."""
import argparse
from collections import defaultdict
from contextlib import contextmanager
from datetime import datetime, timezone
from functools import partial
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import sys
from uuid import uuid4

if os.name == "nt":
    import msvcrt
    import migration_ntfs as nt

SETTABLE = 0x01 | 0x02 | 0x04 | 0x10 | 0x20 | 0x80 | 0x100 | 0x2000
COMPARE_FIELDS = ("type", "size", "sha256", "streams", "attrs", "mtime_ns", "security",
                  "junction_tag", "junction_target")
CHUNK = 2**20
POLICY_KEY = "materialize_dependency_hardlinks"


def read_hardlink_policy(record):
    value = record.get(POLICY_KEY, False)
    if type(value) is not bool:
        raise ValueError(f"{POLICY_KEY} must be an explicit boolean")
    return value


def hardlink_policy_fields(enabled):
    read_hardlink_policy({POLICY_KEY: enabled})
    return {POLICY_KEY: True} if enabled else {}


def checked_root(arg):
    # Path() collapses explicit "." components. Validate the literal spelling first.
    raw = str(arg).replace("/", "\\").rstrip("\\")
    components = raw.split("\\")[1:]
    if any(not x or x in (".", "..") or x.endswith((".", " ")) or Path(x).is_reserved()
           or any(ch in ':*?"<>|' or ord(ch) < 32 for ch in x) for x in components):
        raise ValueError(f"Ambiguous or reserved Windows path: {arg}")
    path = Path(arg)
    if (not path.is_absolute() or len(path.drive) != 2 or path.drive[1] != ":"
            or path == Path(path.anchor) or any(x == ".." or ":" in x for x in path.parts[1:])):
        raise ValueError(f"Use an absolute local directory below a drive root: {path}")
    nt.volume(path)
    for p in (*reversed(path.parents), path):
        if os.path.lexists(p):
            if p.lstat().st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                raise ValueError(f"Reparse point on requested path; select the real directory: {p}")
            if not p.is_dir():
                raise NotADirectoryError(p)
    return path


def check_disjoint(paths):
    physical = [nt.physical(p) for p in paths]
    if any(p == p.parent for p in physical):
        raise ValueError("A volume root cannot be migrated or used for audit")
    for i, a in enumerate(physical):
        for b in physical[i+1:]:
            if a.is_relative_to(b) or b.is_relative_to(a):
                raise ValueError("Source, destination, and audit must be disjoint physical directories")


def object_id(path, *, allow_junction=False):
    s = os.lstat(nt.wide(path))
    if s.st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT and not (
            allow_junction and s.st_reparse_tag == nt.JUNCTION_TAG):
        raise ValueError(f"Reparse point: {path}")
    if not s.st_ino:
        raise ValueError(f"Missing NTFS file identity: {path}")
    return [s.st_dev, s.st_ino]


def signature(s):
    kind = stat.S_IFMT(s.st_mode)
    reparse_flag = s.st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT
    ordinary_directory = kind == stat.S_IFDIR and not reparse_flag
    # NTFS may return different sizes for a directory index. The manifest
    # checks the directory entries and their metadata independently.
    size = 0 if ordinary_directory else s.st_size
    return [s.st_dev, s.st_ino, s.st_nlink, size, s.st_mtime_ns, kind, s.st_reparse_tag, reparse_flag]


def read_hash(path):
    with open(path, "rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def visit(root):
    stack = [(".", root)]
    while stack:
        rel, path = stack.pop()
        info = os.lstat(nt.wide(path))
        attrs = nt.attributes(path)
        junction = bool(attrs & stat.FILE_ATTRIBUTE_REPARSE_POINT)
        if junction and (rel == "." or info.st_reparse_tag != nt.JUNCTION_TAG
                         or not stat.S_ISDIR(info.st_mode)):
            raise ValueError(f"Unsupported reparse point at {path}")
        if attrs & ~(SETTABLE | (0x400 if junction else 0)):
            raise ValueError(f"Unsupported NTFS attributes 0x{attrs:x} at {path}; inspect before copying")
        if not info.st_ino or not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
            raise ValueError(f"Unrecognized object at {path}")
        yield rel, path, info
        if stat.S_ISDIR(info.st_mode) and not junction:
            with os.scandir(nt.wide(path)) as entries:
                children = sorted((e.name for e in entries), reverse=True)
            for name in children:
                stack.append((name if rel == "." else rel + "/" + name, path / name))


def stream_record(path, with_hash):
    streams = {}
    for name, size in nt.streams(path).items():
        data = {"size": size}
        if with_hash:
            data["sha256"] = read_hash(nt.wide(path) + name)
        streams[name] = data
    return streams


def junction_target(root, path, *, junction_root=None):
    """Interpret a junction relative to its pinned tree without following it."""
    raw = os.readlink(nt.wide(path))
    target = raw.removeprefix("\\\\?\\").removeprefix("\\??\\")
    target_path = Path(target)
    if (not target_path.is_absolute() or len(target_path.drive) != 2
            or any(part in (".", "..") for part in target.split("\\"))):
        raise ValueError(f"Unsupported junction target: {path} -> {raw}")
    namespace = junction_root or root
    try:
        relative = target_path.relative_to(namespace)
    except ValueError as exc:
        raise ValueError(f"Junction target is outside the selected tree: {path} -> {raw}") from exc
    # WindowsPath comparisons fold case even in case-sensitive NTFS directories.
    # Check the actual root for a differently spelled prefix before remapping it.
    declared_root = Path(*target_path.parts[:len(namespace.parts)])
    if declared_root.parts[1:] != namespace.parts[1:]:
        identities = []
        for spelling in (declared_root, namespace):
            try:
                identities.append(object_id(spelling, allow_junction=True))
            except FileNotFoundError:
                identities.append(None)
        # Between rename and relinking, both old spellings can be absent. The
        # backup is checked against the pinned manifest, including the raw target.
        if identities[0] != identities[1] or (identities[0] is None and junction_root is None):
            raise ValueError(f"Junction target is outside the selected tree: {path} -> {raw}")
    # In a renamed backup, use its own physical directory members. The old raw
    # target may already resolve to NEW through the compatibility root junction.
    current = root
    for name in relative.parts:
        current = current / name
        info = os.lstat(nt.wide(current))
        if (info.st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT
                or not stat.S_ISDIR(info.st_mode)):
            raise ValueError(f"Junction target must be a physical directory in this tree: {path} -> {raw}")
    # Canonical spelling also accepts the ordinary NTFS case-insensitive aliases
    # that package managers may store in a junction's absolute target.
    relative = nt.physical(current).relative_to(nt.physical(root))
    return relative.as_posix(), raw


def record(path, info, with_hash, *, root=None, junction_root=None):
    junction = bool(info.st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)
    directory = stat.S_ISDIR(info.st_mode)
    data = dict(type="junction" if junction else "dir" if directory else "file",
                size=0 if directory else info.st_size,
                attrs=nt.attributes(path), mtime_ns=info.st_mtime_ns,
                identity=[info.st_dev, info.st_ino], link_count=info.st_nlink)
    data["security"], data["protected"] = nt.security(path, nofollow=junction)
    if junction:
        if root is None:
            raise ValueError("A pinned tree root is required to inspect a junction")
        data["junction_target"], data["junction_raw_target"] = junction_target(
            root, path, junction_root=junction_root)
        data["junction_tag"] = info.st_reparse_tag
        data["streams"] = {}
        if nt.junction_streams(path):
            raise ValueError(f"Named streams on junction objects require separate handling: {path}")
    else:
        data["streams"] = stream_record(path, with_hash)
    if with_hash and not directory:
        data["sha256"] = read_hash(nt.wide(path))
    if signature(os.lstat(nt.wide(path))) != signature(info) or nt.attributes(path) != data["attrs"]:
        raise RuntimeError(f"Changed during read: {path}")
    return data


def snapshot(root, with_hash, *, junction_root=None, on_entry=None):
    rows = {}
    for rel, path, st in visit(root):
        rows[rel] = record(path, st, with_hash, root=root, junction_root=junction_root)
        if on_entry is not None:
            on_entry()
    return rows


def metadata(row):
    result = {k: v for k, v in row.items() if k != "sha256"}
    result["streams"] = {name: {"size": v["size"]} for name, v in row["streams"].items()}
    return result


def changed_since(rows, now):
    return sorted(k for k in rows.keys() | now.keys()
                  if k not in rows or k not in now or metadata(rows[k]) != now[k])


def guard_parent(root, path, ids):
    """Detect renamed/replaced parent directories before writes or opening source data."""
    for parent in (path.parent, *path.parent.parents):
        if not parent.is_relative_to(root):
            break
        rel = parent.relative_to(root).as_posix()
        if object_id(parent) != ids[rel]:
            raise ValueError(f"Directory identity changed: {parent}")


def link_groups(rows):
    by_id = defaultdict(list)
    for k, row in rows.items():
        if row["type"] == "file" and row["link_count"] > 1:
            by_id[tuple(row["identity"])].append(k)
    groups = set()
    external = []
    for members in by_id.values():
        key = tuple(sorted(members))
        if rows[members[0]]["link_count"] > len(members):
            external.append(list(key))
        if len(key) > 1:
            groups.add(key)
    return groups, sorted(external)


def inspect_hardlinks(root, *, require_stable=False):
    """Inspect native identities before spending time hashing file contents."""
    rows = {}
    observed = {}
    for rel, _, info in visit(root):
        if require_stable:
            observed[rel] = signature(info)
        if stat.S_ISREG(info.st_mode):
            rows[rel] = {"type": "file", "identity": [info.st_dev, info.st_ino],
                         "link_count": info.st_nlink}
    if require_stable and observed != {rel: signature(info) for rel, _, info in visit(root)}:
        raise RuntimeError(f"Source changed during the hardlink scan: {root}")
    return link_groups(rows)


def has_dependency_parent(relative):
    return "node_modules" in relative.casefold().split("/")[:-1]


def check_source_hardlinks(external, materialize, report):
    if external and (not materialize or any(
            not has_dependency_parent(name) for members in external for name in members)):
        report["external_hardlink_references"] = external
        raise ValueError("Source files have hardlinks outside the selected project; "
                         "only explicitly approved node_modules groups can be materialized")


def materialization_evidence(source_external, destination_external=()):
    return dict(scope="node_modules", source_external_groups=len(source_external),
                source_external_entries=sum(len(members) for members in source_external),
                destination_external_groups=len(destination_external))


def dump_json(path, data):
    with path.open("x", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=True, indent=2)


def save_manifest(path, rows):
    with gzip.open(path, "xt", encoding="utf-8") as out:
        for k in sorted(rows):
            out.write(json.dumps({"path": k, **rows[k]}, ensure_ascii=True, separators=(",", ":")) + "\n")


def verify_manifest(root, manifest, snapshot_tree, exceptions, *, progress=None, side=None):
    info = manifest.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or (
            os.name == "nt" and info.st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT):
        raise ValueError(f"需要本次核验的独立普通清单文件：{manifest}")
    if progress is not None:
        label = "原件" if side == "source" else "目标"
        progress.step(f"scan-{side}", f"重新读取{label}并计算哈希", root)
        live = snapshot_tree(root, True, on_entry=progress.advance)
        progress.step(f"compare-{side}", f"比对{label}的核验清单", root)
    else:
        live = snapshot_tree(root, True)
    remaining = set(live)
    with gzip.open(manifest, "rt", encoding="utf-8") as file:
        for line in file:
            expected = json.loads(line)
            name = expected.pop("path")
            ignored = exceptions.get(name, ())
            if name not in remaining or (
                    {k: v for k, v in expected.items() if k not in ignored}
                    != {k: v for k, v in live[name].items() if k not in ignored}):
                raise ValueError(f"文件在副本验收后变化：{root} / {name}")
            remaining.remove(name)
            if progress is not None:
                progress.advance()
    if remaining:
        raise ValueError(f"副本验收后出现额外项：{root} / {min(remaining)}")
    return live


def recheck_verified_copy(original, target, evidence, *, renamed, pointer_exceptions,
                          source_junction_root=None, progress=None):
    snapshot_tree = snapshot
    if sys.platform == "darwin":
        from mac_tree import snapshot as snapshot_tree
    directory = Path(evidence["directory"])
    stem = evidence["report"].removesuffix("-verify.json")
    # Renaming the original can change its APFS root ctime, even without any writes.
    original_exceptions = {".": ("ctime_ns",)} if renamed else {}
    original_snapshot = snapshot_tree
    if os.name == "nt":
        original_snapshot = partial(snapshot_tree, junction_root=source_junction_root)
    original_live = verify_manifest(original, directory / (stem + "-source.jsonl.gz"),
                                    original_snapshot, original_exceptions, progress=progress, side="source")
    target_live = verify_manifest(target, directory / (stem + "-destination.jsonl.gz"),
                                  snapshot_tree, pointer_exceptions, progress=progress, side="target")
    # Revisit metadata after hashing both trees so writes during the other scan
    # can also stop the cutover.
    for side, label, root, live, scan in (
            ("source", "原件", original, original_live, original_snapshot),
            ("target", "目标", target, target_live, snapshot_tree)):
        if progress is not None:
            progress.step(f"check-{side}-stability", f"复查{label}是否变化", root)
            current = scan(root, False, on_entry=progress.advance)
        else:
            current = scan(root, False)
        changes = changed_since(live, current)
        if changes:
            raise ValueError(f"切换复核期间文件发生变化：{root} / {changes[0]}")


def summarize(rows):
    summary = {"files": sum(r["type"] == "file" for r in rows.values()),
               "directories_including_root": sum(r["type"] == "dir" for r in rows.values()),
               "default_stream_bytes": sum(r["size"] for r in rows.values()),
               "named_streams": sum(len(r["streams"]) for r in rows.values())}
    junctions = sum(r["type"] == "junction" for r in rows.values())
    if junctions:
        summary["junctions"] = junctions
    return summary


def differing(a, b, rel):
    fields = [k for k in COMPARE_FIELDS if a.get(k) != b.get(k)]
    if (True if rel == "." else a["protected"]) != b["protected"]:
        fields.append("dacl_protection")
    return fields


def verify(src, dst, audit, report, *, source_junction_root=None,
           materialize_dependency_hardlinks=False):
    report.update(hardlink_policy_fields(materialize_dependency_hardlinks))
    _, external = inspect_hardlinks(src)
    check_source_hardlinks(external, materialize_dependency_hardlinks, report)
    _, destination_external = inspect_hardlinks(dst)
    if destination_external:
        report["external_hardlink_references"] = {"destination": destination_external}
        raise ValueError("Destination files have hardlinks outside the candidate")
    source_snapshot = partial(snapshot, junction_root=source_junction_root)
    a = source_snapshot(src, True)
    b = snapshot(dst, True)
    for label, rows in (("source", a), ("destination", b)):
        save_manifest(audit / (report["run"] + "-" + label + ".jsonl.gz"), rows)
    changes = {}
    for label, root, rows, scan in (("source", src, a, source_snapshot),
                                    ("destination", dst, b, snapshot)):
        paths = changed_since(rows, scan(root, False))
        if paths:
            changes[label + "_changed_during_verification"] = paths
    for field, paths in (("source_only", a.keys() - b.keys()), ("destination_only", b.keys() - a.keys())):
        if paths:
            changes[field] = sorted(paths)
    fields = defaultdict(list)
    for p in sorted(a.keys() & b.keys()):
        for field in differing(a[p], b[p], p):
            fields[field].append(p)
    changes.update(fields)
    ga, xa = link_groups(a)
    gb, xb = link_groups(b)
    if ga != gb:
        changes["missing_internal_link_groups"] = sorted(ga - gb)
        changes["extra_internal_link_groups"] = sorted(gb - ga)
    check_source_hardlinks(xa, materialize_dependency_hardlinks, report)
    if xb:
        changes["external_hardlink_references"] = {"source": xa, "destination": xb}
    if materialize_dependency_hardlinks:
        report["dependency_hardlink_materialization"] = materialization_evidence(xa, xb)
    report.update(source_summary=summarize(a), destination_summary=summarize(b),
                  internal_hardlink_groups={"source": len(ga), "destination": len(gb)},
                  differences=changes)
    return not changes


@contextmanager
def exclusive_lock(audit):
    # The kernel releases the lock if the process exits or crashes.
    with (audit / "copy.lock").open("a+b") as f:
        if sys.platform == "darwin":
            import fcntl
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                yield
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)
            return
        if f.seek(0, os.SEEK_END) == 0:
            f.write(b".")
            f.flush()
        f.seek(0)
        msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        try:
            yield
        finally:
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)


def prepare_copy(src, dst, audit, *, materialize_dependency_hardlinks=False):
    state = audit / "owner.json"
    source_key = {"source": str(src), "destination": str(dst), "source_id": object_id(src)}
    source_key.update(hardlink_policy_fields(materialize_dependency_hardlinks))
    if os.path.lexists(dst):
        if not state.is_file():
            raise FileExistsError("Destination already exists without this run's ownership record: " + str(dst))
        saved = json.loads(state.read_text(encoding="utf-8"))
        if not read_hardlink_policy(saved):
            saved.pop(POLICY_KEY, None)
        if {**source_key, "destination_id": object_id(dst)} != saved:
            raise ValueError("Destination or source identity changed; use a new candidate")
    else:
        if state.exists():
            raise ValueError("Recorded candidate is missing; choose a new audit directory")
        nt.create_secured(dst, src, directory=True, protect=True)
        nt.restore_security(src, dst, True)
        dump_json(state, {**source_key, "destination_id": object_id(dst)})


def copy_streams(src, dst, row, allow_existing):
    current = nt.streams(dst)
    if current.keys() - row["streams"].keys():
        raise ValueError(f"Unexpected destination named stream: {dst}")
    for name, expected in row["streams"].items():
        path = nt.wide(dst) + name
        if name in current:
            if not allow_existing or read_hash(path) != expected["sha256"]:
                raise ValueError(f"Conflicting destination named stream: {dst}")
            continue
        with open(nt.wide(src) + name, "rb") as a, open(path, "xb") as b:
            shutil.copyfileobj(a, b, CHUNK)


def capacity(rows, dst, report, present_ids=(), existing_dirs=()):
    counted = set(present_ids)
    remaining = 0
    for name, row in rows.items():
        if row["type"] == "junction":
            continue
        iid = tuple(row["identity"])
        if row["type"] == "file" and iid in counted:
            continue
        counted.add(iid)
        remaining += row["size"]
        done = nt.streams(dst / name) if name in existing_dirs else {}
        remaining += sum(v["size"] for k, v in row["streams"].items() if k not in done)
    free = shutil.disk_usage(dst.parent).free
    # Physical allocation units, filesystem metadata and space used by other apps
    # can differ from the logical payload. A successful preflight is an estimate.
    report["capacity"] = {"remaining_unique_payload_bytes": remaining, "free_bytes": free}
    if remaining > free:
        raise OSError(f"Target space is insufficient: estimated {remaining} bytes, available {free}")


def copy(src, dst, audit, report, *, materialize_dependency_hardlinks=False):
    report.update(hardlink_policy_fields(materialize_dependency_hardlinks))
    _, external = inspect_hardlinks(src)
    check_source_hardlinks(external, materialize_dependency_hardlinks, report)
    rows = snapshot(src, True)
    save_manifest(audit / (report["run"] + "-source.jsonl.gz"), rows)
    groups, external = link_groups(rows)
    report.update(source_summary=summarize(rows), internal_hardlink_groups=len(groups))
    check_source_hardlinks(external, materialize_dependency_hardlinks, report)
    if materialize_dependency_hardlinks:
        report["dependency_hardlink_materialization"] = materialization_evidence(external)
    fresh = not os.path.lexists(dst)
    if fresh:
        capacity(rows, dst, report)
    if materialize_dependency_hardlinks:
        prepare_copy(src, dst, audit, materialize_dependency_hardlinks=True)
    else:
        prepare_copy(src, dst, audit)
    src_dirs = {k: r["identity"] for k, r in rows.items() if r["type"] == "dir"}
    dest_items = {k: s for k, _, s in visit(dst)}
    extras = sorted(dest_items.keys() - rows.keys())
    if extras:
        report["destination_only"] = extras
        raise ValueError("Unexpected destination entries; no overwrite or purge performed")

    journal = audit / "directories.jsonl"
    owned = {}
    if journal.exists():
        with journal.open(encoding="utf-8") as f:
            for line in f:
                name, identity = json.loads(line)
                owned[name] = identity
    owned["."] = object_id(dst)
    planned_files = defaultdict(list)
    for k, info in dest_items.items():
        srcrow = rows[k]
        if info.st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            actual = record(dst / k, info, True, root=dst)
            if (srcrow["type"] != "junction" or owned.get(k) != actual["identity"]
                    or differing(srcrow, actual, k)):
                raise ValueError(f"Unowned/changed candidate junction: {k}")
        elif stat.S_ISDIR(info.st_mode):
            if srcrow["type"] != "dir" or owned.get(k) != [info.st_dev, info.st_ino]:
                raise ValueError(f"Unowned/changed candidate directory: {k}")
        else:
            actual = record(dst / k, info, True)
            if differing(srcrow, actual, k):
                raise ValueError(f"Candidate file differs: {k}; retain this candidate and use a new one")
            planned_files[tuple(actual["identity"])].append(k)
    # Never modify a file also reachable outside the candidate or across source groups.
    for members in planned_files.values():
        if (dest_items[members[0]].st_nlink != len(members)
                or len({tuple(rows[p]["identity"]) for p in members}) != 1):
            raise ValueError("Unexpected candidate hardlink membership: " + members[0])
    anchors, anchor_ids = {}, {}
    # On resume anchor each existing complete group before adding missing paths.
    for members in planned_files.values():
        k = members[0]
        iid = tuple(rows[k]["identity"])
        if iid in anchors:
            raise ValueError("Candidate split a source hardlink group; use a new candidate: " + k)
        anchors[iid] = dst / k
        anchor_ids[iid] = object_id(dst / k)

    if not fresh:
        capacity(rows, dst, report, anchors, {k for k in dest_items if k in owned})

    directories = []
    junctions = []
    with journal.open("a", encoding="utf-8") as entries:
        for rel, row in rows.items():
            s, d = src / rel, dst / rel
            guard_parent(src, s, src_dirs)
            guard_parent(dst, d, owned)
            if object_id(s, allow_junction=row["type"] == "junction") != row["identity"]:
                raise ValueError(f"Source entry changed: {s}")
            if row["type"] == "junction":
                junctions.append(rel)
                continue
            directory = row["type"] == "dir"
            if directory:
                directories.append(rel)
                if rel not in dest_items:
                    nt.create_secured(d, s, directory=True)
                    owned[rel] = object_id(d)
                    entries.write(json.dumps([rel, owned[rel]], ensure_ascii=True) + "\n")
                    entries.flush()
                if object_id(d) != owned[rel]:
                    raise ValueError(f"Candidate directory changed: {d}")
                nt.restore_security(s, d, rel == ".")
                copy_streams(s, d, row, True)
                continue
            if rel in dest_items:
                continue
            iid = tuple(row["identity"])
            if iid in anchors:
                guard_parent(dst, anchors[iid], owned)
                # An existing anchor may have been swapped since it was verified.
                if object_id(anchors[iid]) != anchor_ids[iid]:
                    raise ValueError(f"Candidate hardlink anchor changed: {anchors[iid]}")
                os.link(nt.wide(anchors[iid]), nt.wide(d))
                continue
            with nt.create_secured(d, s, directory=False) as outf, open(nt.wide(s), "rb") as inf:
                shutil.copyfileobj(inf, outf, CHUNK)
            copy_streams(s, d, row, False)
            nt.restore_security(s, d, False)
            os.utime(nt.wide(d), ns=(d.stat().st_atime_ns, row["mtime_ns"]))
            nt.write_attrs(d, row["attrs"])
            anchors[iid] = d
            anchor_ids[iid] = object_id(d)
        # All physical targets now exist. A pnpm package can refer back to an
        # ancestor package without making the copy traversal recursive.
        for rel in junctions:
            s, d, row = src / rel, dst / rel, rows[rel]
            guard_parent(src, s, src_dirs)
            guard_parent(dst, d, owned)
            if object_id(s, allow_junction=True) != row["identity"]:
                raise ValueError(f"Source junction changed: {s}")
            target = dst / row["junction_target"]
            guard_parent(dst, target, owned)
            if object_id(target) != owned.get(row["junction_target"]):
                raise ValueError(f"Candidate junction target changed: {target}")
            if rel in dest_items:
                continue
            nt.create_junction(d, target, s)
            owned[rel] = object_id(d, allow_junction=True)
            entries.write(json.dumps([rel, owned[rel]], ensure_ascii=True) + "\n")
            entries.flush()
            nt.junction_metadata(s, d, row["mtime_ns"], row["attrs"])
    # Adding a hardlink can set ARCHIVE on its shared file object. Restore the
    # expected attributes once all names exist, using each hardlink group once.
    for members in groups:
        iid = tuple(rows[members[0]]["identity"])
        d = anchors[iid]
        rel = d.relative_to(dst).as_posix()
        s, row = src / rel, rows[rel]
        guard_parent(src, s, src_dirs)
        guard_parent(dst, d, owned)
        if (object_id(s) != row["identity"] or object_id(d) != anchor_ids[iid]
                or anchor_ids[iid] == row["identity"]):
            raise ValueError(f"File identity changed before restoring attributes: {d}")
        nt.write_attrs(d, row["attrs"])
    for rel in reversed(directories):
        d, row = dst / rel, rows[rel]
        guard_parent(dst, d, owned)
        if object_id(d) != owned[rel]:
            raise ValueError(f"Candidate directory changed: {d}")
        os.utime(nt.wide(d), ns=(d.stat().st_atime_ns, row["mtime_ns"]))
        nt.write_attrs(d, row["attrs"])
    drift = changed_since(rows, snapshot(src, False))
    if drift:
        report["source_changed_during_copy"] = drift
        raise RuntimeError("Source changed during copy; re-inventory after saving/pausing all writes")
    return True


def prepare_audit(path, src):
    if not path.exists():
        nt.create_secured(path, src, directory=True, protect=True)
        nt.restore_security(src, path, True)
    # A provided directory must not expose filenames, hashes or ownership records
    # to a broader or different ACL than the source. Use a new child if necessary.
    if nt.security(path)[0] != nt.security(src)[0]:
        raise PermissionError(f"Use a fresh audit directory inheriting the source access rules: {path}")
    for p in path.iterdir():
        info = p.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or info.st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT):
            raise ValueError(f"Unexpected object in this audit directory: {p}")


def main():
    if sys.platform == "darwin":
        from mac_tree import main as mac_main
        return mac_main()
    if os.name != "nt":
        raise ValueError("Supported platforms: Windows NTFS and macOS APFS")
    from cutover import MigrationError, ordinary_record
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["copy", "verify"])
    parser.add_argument("source")
    parser.add_argument("destination")
    parser.add_argument("--audit", required=True)
    parser.add_argument("--materialize-dependency-hardlinks", action="store_true")
    args = parser.parse_args()
    src, dst, audit = map(checked_root, (args.source, args.destination, args.audit))
    check_disjoint((src, dst, audit))
    if not src.is_dir() or not dst.parent.is_dir() or not audit.parent.is_dir():
        raise ValueError("Source and the destination/audit parent directories must already exist")
    checkpoint = audit.parent / "run.json"
    source_junction_root = None
    materialize_dependency_hardlinks = False
    if checkpoint.is_file():
        try:
            config, _ = ordinary_record(checkpoint)
        except MigrationError as exc:
            raise ValueError(str(exc)) from exc
        materialize_dependency_hardlinks = read_hardlink_policy(config)
        if str(src) not in (config.get("source"), config.get("backup")) or str(dst) != config.get("target"):
            raise ValueError("Paths differ from this migration's checkpoint.")
        if str(src) == config.get("backup"):
            source_junction_root = Path(config["source"])
        pinned = config.get("volumes")  # v1 checkpoints predate volume pinning
        if pinned is not None and (nt.physical(src).drive.casefold() != pinned["source_volume"]
                                  or nt.physical(dst).drive.casefold() != pinned["target_volume"]):
            raise ValueError("Volume identity changed after preflight. Recheck the source and target.")
    if args.materialize_dependency_hardlinks and not materialize_dependency_hardlinks:
        raise ValueError("Dependency hardlink materialization needs the matching approved run.json")
    if args.action == "verify":
        owner_directory = audit
        if materialize_dependency_hardlinks and str(src) == config.get("backup"):
            from cutover import read_plan
            try:
                plan, _, _, _ = read_plan(audit.parent / "cutover-plan.json")
            except MigrationError as exc:
                raise ValueError(str(exc)) from exc
            owner_directory = Path(plan["copy_evidence"]["directory"])
        owner_path = owner_directory / "owner.json"
        if os.path.lexists(owner_path):
            try:
                owner, _ = ordinary_record(owner_path)
            except MigrationError as exc:
                raise ValueError(str(exc)) from exc
            if read_hardlink_policy(owner) != materialize_dependency_hardlinks:
                raise ValueError("Verification policy differs from the managed copy owner")
        elif materialize_dependency_hardlinks:
            raise ValueError("Dependency hardlink verification needs the managed copy owner")
    prepare_audit(audit, src)
    with exclusive_lock(audit):
        tag = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid4().hex[:10]
        report = {"run": tag, "action": args.action, "source": str(src), "destination": str(dst)}
        report.update(hardlink_policy_fields(materialize_dependency_hardlinks))
        dump_json(audit / (tag + "-started.json"), report)
        try:
            if args.action == "copy":
                success = copy(src, dst, audit, report,
                               materialize_dependency_hardlinks=materialize_dependency_hardlinks)
            else:
                success = verify(src, dst, audit, report, source_junction_root=source_junction_root,
                                 materialize_dependency_hardlinks=materialize_dependency_hardlinks)
            report["status"] = "passed" if success else "differences"
        except (OSError, ValueError, RuntimeError) as exc:
            report.update(status="stopped", error=str(exc))
        path = audit / (tag + "-" + args.action + ".json")
        dump_json(path, report)
    print(json.dumps({"status": report["status"], "report": str(path)}, ensure_ascii=True))
    return 0 if report["status"] == "passed" else 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(2)

