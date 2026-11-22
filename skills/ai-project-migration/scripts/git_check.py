"""Capture, reconnect and check Git worktrees during a local project move."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
from datetime import datetime, timezone


def require(condition, message):
    if not condition:
        raise ValueError(message)


class GitCommandError(ValueError):
    """Git was found, but refused a command."""


def physical(path, directory=True):
    path = Path(os.path.abspath(path))
    for item in [*reversed(path.parents), path]:
        info = item.lstat()
        require(not stat.S_ISLNK(info.st_mode) and
                not (os.name == "nt" and info.st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT),
                f"Unexpected reparse point: {item}")
    require(path.is_dir() if directory else path.is_file(), f"Unexpected type: {path}")
    return path


def identity(path):
    if sys.platform == "darwin":
        from mac_fs import object_id
        return object_id(path)
    info = path.stat()
    return [info.st_dev, info.st_ino]


def git_program():
    name = "git.exe" if os.name == "nt" else "git"
    program = shutil.which(name)
    if not program:
        raise FileNotFoundError("Git is not available on the current PATH")
    path = Path(program).absolute()
    if sys.platform == "darwin" and path.resolve() == Path("/usr/bin/git"):
        # Invoking the system dispatcher without developer tools can open an
        # installer. Use the selected toolchain's installed Git directly.
        directory = os.environ.get("DEVELOPER_DIR")
        if not directory:
            selected = subprocess.run(["/usr/bin/xcode-select", "--print-path"],
                                      stdin=subprocess.DEVNULL, capture_output=True, timeout=10)
            directory = os.fsdecode(selected.stdout).rstrip("\r\n") if selected.returncode == 0 else ""
        developer = Path(directory)
        if developer.suffix.lower() == ".app":
            developer = developer / "Contents" / "Developer"
        candidate = developer / "usr" / "bin" / "git"
        if not developer.is_absolute() or not candidate.is_file() or not os.access(candidate, os.X_OK):
            raise FileNotFoundError("Apple Git requires installed and selected developer tools")
        path = candidate.resolve()
        if path == Path("/usr/bin/git"):
            raise FileNotFoundError("Selected Apple toolchain resolves to the Git installer dispatcher")
    return path


def git(root, *args, overrides=(), soft=False, program=None, timeout=None):
    env = os.environ.copy()
    for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE",
                "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_NAMESPACE"):
        env.pop(key, None)
    env.update(GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0", GIT_NO_LAZY_FETCH="1")
    cmd = [str(program if program is not None else git_program()),
           "--no-optional-locks", "-c", "core.fsmonitor=false",
           "-c", "core.quotepath=false", "-C", str(root), *overrides, *args]
    result = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, env=env, timeout=timeout)
    if soft and result.returncode == 1:
        return b""
    if result.returncode:
        reason = result.stderr.decode(errors="replace").strip()
        raise GitCommandError(f"Git failed in {root}: {reason}")
    return result.stdout


def as_path(data):
    return Path(os.fsdecode(data.rstrip(b"\r\n")))


def pointer(file, prefix=b""):
    physical(file, directory=False)
    raw = file.read_bytes().rstrip(b"\r\n")
    require(raw.startswith(prefix) and raw[len(prefix):] and b"\n" not in raw,
            f"Invalid Git pointer: {file}")
    return Path(os.path.abspath(file.parent / os.fsdecode(raw[len(prefix):])))


def has_main_marker(root):
    dot = root / ".git"
    if not os.path.lexists(dot):
        for parent in [root, *root.parents]:
            require(not os.path.lexists(parent / ".git"), f"Project is inside a Git checkout: {parent}")
            require(not ((parent / "HEAD").is_file() and (parent / "objects").is_dir()),
                    f"Bare repository or Git metadata directory: {parent}")
        return False
    require(dot.is_dir(), f"Choose the main checkout, or inspect a separate Git directory: {dot}")
    physical(dot)
    return True


def is_main(root):
    if not has_main_marker(root):
        return False
    dot = root / ".git"
    top = as_path(git(root, "rev-parse", "--show-toplevel"))
    common = as_path(git(root, "rev-parse", "--path-format=absolute", "--git-common-dir"))
    require(top == root and common == dot, f"Project is not an independent main checkout: {root}")
    return True


def registered(root):
    blocks = git(root, "worktree", "list", "--porcelain", "-z").rstrip(b"\0").split(b"\0\0")
    rows = {}
    for block in blocks:
        fields = dict(f.partition(b" ")[::2] for f in block.split(b"\0"))
        require(b"worktree" in fields and b"HEAD" in fields and b"prunable" not in fields
                and b"bare" not in fields, "Missing, prunable or bare worktree; inspect Git first")
        tree = physical(as_path(fields[b"worktree"]))
        require(tree not in rows, f"Duplicate worktree registration: {tree}")
        rows[tree] = fields
    require(root in rows, "Main checkout is absent from its own registry")
    return rows


def state(tree, fields, overrides=()):
    def read(*args, soft=False):
        return git(tree, *args, overrides=overrides, soft=soft).rstrip(b"\r\n")
    head = read("rev-parse", "--verify", "-q", "HEAD", soft=True)
    branch = read("symbolic-ref", "--quiet", "HEAD", soft=True)
    if head:
        read("cat-file", "-e", "HEAD^{tree}")
    else:
        require(branch and not fields[b"HEAD"].strip(b"0"), f"Invalid or missing HEAD: {tree}")
    require((not head or head == fields[b"HEAD"]) and branch == fields.get(b"branch", b""),
            f"HEAD or branch disagrees with registered checkout: {tree}")
    raw = git(tree, "status", "--porcelain=v1", "-z", "--untracked-files=all", overrides=overrides)
    items = iter(raw.split(b"\0")[:-1])
    entries = untracked = 0
    for record in items:
        require(len(record) >= 4 and record[2:3] == b" ", f"Cannot parse Git status: {tree}")
        entries += 1
        untracked += record[:2] == b"??"
        if b"R" in record[:2] or b"C" in record[:2]:
            require(next(items, None) is not None, "Incomplete rename entry")
    staged = git(tree, "ls-files", "--stage", "-z", overrides=overrides)
    return {"head": head.decode("ascii") or None, "branch": os.fsdecode(branch) or None,
            "locked": os.fsdecode(fields[b"locked"]) if b"locked" in fields else None,
            "status_count": entries, "untracked_count": untracked,
            "status_sha256": hashlib.sha256(raw).hexdigest(),
            "index_entries_sha256": hashlib.sha256(staged).hexdigest()}


def admin_names(root):
    storage = root / ".git" / "worktrees"
    if not os.path.lexists(storage):
        return set()
    return {physical(sub).name for sub in physical(storage).iterdir()}


def validate_registration(common, name, tree):
    top = physical(common / "worktrees")
    admin = physical(top / name)
    require(admin.parent == top and pointer(admin / "commondir") == common
            and pointer(admin / "gitdir") == tree / ".git", f"Invalid reverse Git registration: {tree}")
    return admin


def baseline(root):
    snap = {"root": str(root), "root_id": identity(root), "kind": "non_git", "linked": []}
    if not is_main(root):
        return snap
    common = root / ".git"
    records = registered(root)
    snap.update(kind="main_only", git_id=identity(common), main=state(root, records.pop(root)))
    seen = set()
    for tree, fields in records.items():
        require(not (tree.is_relative_to(root) or root.is_relative_to(tree)),
                f"Nested linked worktree needs a separate relocation plan: {tree}")
        f = tree / ".git"
        admin = pointer(f, b"gitdir: ")
        require(admin.parent == common / "worktrees" and admin.name not in seen,
                f"Worktree belongs to a different repository: {tree}")
        validate_registration(common, admin.name, tree)
        require(as_path(git(tree, "rev-parse", "--path-format=absolute", "--git-common-dir")) == common,
                f"Unexpected shared Git directory: {tree}")
        snap["linked"].append({"path": str(tree), "dir_id": identity(tree), "admin": admin.name,
                               "original_gitfile": os.fsdecode(f.read_bytes()),
                               "state": state(tree, fields)})
        seen.add(admin.name)
    require(seen == admin_names(root), "Unlisted private Git worktree metadata; inspect before moving")
    if seen:
        snap["kind"] = "linked_worktrees"
    return snap


def check_state(actual, expected, path):
    changed = [k for k in expected if expected[k] != actual.get(k)]
    require(not changed, f"Migration baseline changed ({', '.join(changed)}): {path}")


def verify(destination, snap, allow_source=False):
    old = Path(snap["root"])
    require(old != destination and not destination.is_relative_to(old) and not old.is_relative_to(destination),
            "Specify a separate physical destination")
    if not is_main(destination):
        require(snap["kind"] == "non_git", "Destination is missing the Git repository")
        return []
    require(snap["kind"] in {"main_only", "linked_worktrees"}, "Unexpected Git repository at destination")
    common = destination / ".git"
    require(identity(destination) != snap["root_id"] and identity(common) != snap["git_id"],
            "Destination is the original checkout")
    records = registered(destination)
    check_state(state(destination, records.pop(destination)), snap["main"], destination)
    expected = {Path(t["path"]): t for t in snap["linked"]}
    require(set(records) == set(expected), "The set of registered worktrees has changed")
    require(admin_names(destination) == {item["admin"] for item in expected.values()},
            "Private Git worktree directories disagree with baseline")
    pending = []
    for tree, fields in records.items():
        item = expected[tree]
        name = item["admin"]
        require(identity(tree) == item["dir_id"], f"Original worktree directory was replaced: {tree}")
        admin = validate_registration(common, name, tree)
        target = pointer(tree / ".git", b"gitdir: ")
        if target != admin:
            require(allow_source and target == old / ".git" / "worktrees" / name,
                    f"Worktree points outside the expected destination: {tree}")
            require(os.fsdecode((tree / ".git").read_bytes()) == item["original_gitfile"],
                    f"Original worktree pointer was changed: {tree}")
            require(identity(physical(old)) == snap["root_id"] and
                    identity(physical(old / ".git")) == snap["git_id"],
                    "Original checkout identity changed; refusing to reassign a worktree")
            validate_registration(old / ".git", name, tree)
            pending.append(str(tree))
        actual_common = as_path(git(tree, "rev-parse", "--path-format=absolute", "--git-common-dir"))
        require(actual_common == (common if target == admin else old / ".git"), f"Unexpected common dir: {tree}")
        check_state(state(tree, fields), item["state"], tree)
        if target != admin:
            # Read the proposed private index/HEAD before changing the actual worktree pointer.
            override = ["--git-dir", str(admin), "--work-tree", str(tree)]
            check_state(state(tree, fields, override), item["state"], tree)
    if pending:
        require(is_main(old), "Original main checkout is unavailable for a preflight check")
        old_records = registered(old)
        require(set(old_records) == {old, *expected}, "Source registrations changed since baseline")
        check_state(state(old, old_records[old]), snap["main"], old)
    return pending


def repair_metadata_exceptions(snap, pending=()):
    # The tracked bytes and permissions of these verified reverse pointers must still match.
    return {f".git/worktrees/{row['admin']}/gitdir": ("mtime_ns", "ctime_ns", "identity")
            for row in snap["linked"] if row["path"] not in pending}


def main():
    require(sys.platform in ("win32", "darwin"), "This helper targets Windows and macOS.")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["baseline", "repair", "verify"])
    parser.add_argument("root", type=Path)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    root = physical(args.root)
    before = None
    if args.mode == "baseline":
        require(args.baseline is None, "Capture mode does not take --baseline")
        snap = baseline(root)
    else:
        require(args.baseline is not None, "Supply a verified --baseline receipt")
        before = json.loads(args.baseline.read_text(encoding="utf-8-sig"))
        require(before.get("schema") == 1 and before.get("mode") == "baseline" and before.get("ok") is True,
                "Unsupported or incomplete baseline")
        snap = before["snapshot"]
    report_dir = physical(args.out.parent)
    for guard in [root, Path(snap["root"]), *(Path(item["path"]) for item in snap["linked"])]:
        require(not report_dir.is_relative_to(guard), "Choose an audit directory outside all checkouts")
    report = {"schema": 1, "mode": args.mode, "root": str(root), "ok": False, "kind": snap["kind"],
              "worktree_count": len(snap["linked"]), "utc": datetime.now(timezone.utc).isoformat()}
    # Reserve an independent receipt before any Git mutation. Retain failed attempts for diagnosis.
    with (report_dir / args.out.name).open("x", encoding="utf-8") as handle:
        try:
            if args.mode == "baseline":
                report["snapshot"] = snap
            else:
                pending = verify(root, snap, allow_source=args.mode == "repair")
                if pending:
                    report["repair_candidates"] = pending
                    git(root, "worktree", "repair")
                    verify(root, snap)
                report["shared_git_dir"] = str(root / ".git") if snap["kind"] != "non_git" else None
                report["verified_paths"] = [str(root)] + [t["path"] for t in snap["linked"]]
            report["ok"] = True
        except (OSError, ValueError, KeyError, TypeError) as exc:
            report["error"] = str(exc)
            raise
        finally:
            json.dump(report, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
    print(f"{args.mode}: {snap['kind']}; {len(snap['linked'])} linked worktrees; receipt: {args.out}")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, TypeError) as error:
        sys.exit(str(error))

