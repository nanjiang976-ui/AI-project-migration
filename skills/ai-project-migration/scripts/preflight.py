"""Check the selected local project paths and required Git without writing a migration record."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

sys.dont_write_bytecode = True

import cutover
import git_check
from run_record import volume_id


def check(name, status, detail, **evidence):
    return dict(name=name, status=status, detail=detail, **evidence)


def checked_root(path):
    if sys.platform == "darwin":
        from mac_fs import checked_root as validate
    else:
        from tree_copy import checked_root as validate
    return validate(cutover.local_path(path) if sys.platform == "darwin" else path)


def check_target_parent(parent):
    if sys.platform == "darwin":
        if not os.access(parent, os.W_OK | os.X_OK):
            raise PermissionError(f"Cannot create a directory in {parent}")
    else:
        import migration_ntfs as nt
        # Open the existing directory requesting FILE_ADD_SUBDIRECTORY. This
        # checks the current process's access without creating a test folder.
        handle = nt.create(nt.wide(parent), 0x0004, 7, None, 3, 0x02000000, None)
        if handle == nt.INVALID:
            nt.fail(parent)
        nt.close(handle)


def inspect_path(name, value, *, resume=False):
    if value is None:
        return None, check(name, "not_applicable", "This step does not use this path.")
    try:
        path = checked_root(value)
        existing = os.path.lexists(path)
        if name == "source":
            if not existing:
                raise FileNotFoundError(f"Source directory is unavailable: {path}")
            with os.scandir(path) as entries:
                next(entries, None)
        elif existing and not resume:
            raise FileExistsError(f"Target already exists; inspect the existing migration first: {path}")
        elif not existing:
            check_target_parent(path.parent)
        evidence = {"path": str(path), "volume": volume_id(path)}
        if name == "target":
            evidence["free_bytes"] = shutil.disk_usage(path if existing else path.parent).free
    except (OSError, ValueError, cutover.MigrationError, subprocess.SubprocessError) as error:
        return None, check(name, "blocked", str(error), path=value)
    return path, check(name, "passed", "Local migration volume and path verified.", **evidence)


def check_paths(source, target, resume, results):
    paths = tuple(path for path in (source, target) if path is not None)
    if any(row["status"] == "blocked" for row in results):
        return check("volumes", "blocked", "One or more selected paths are blocked.", paths=results)
    if not paths:
        return check("volumes", "not_applicable", "This step does not need a volume check.")

    try:
        if not resume:
            backup = source.with_name(source.name + cutover.BACKUP_SUFFIX)
            if os.path.lexists(backup):
                raise FileExistsError(f"Backup already exists; inspect the existing migration first: {backup}")
            paths += (backup,)
        dependencies = (Path(__file__).resolve(), Path(sys.executable).resolve())
        if sys.platform == "darwin":
            from mac_tree import disjoint
            cutover.check_darwin_ancestry(paths, dependencies)
        else:
            from tree_copy import check_disjoint as disjoint
        disjoint(paths)
        for dependency in dependencies:
            if any(dependency.is_relative_to(path.resolve()) for path in paths):
                raise ValueError(f"Python or the Skill is inside the migration scope: {dependency}")
    except (OSError, ValueError, cutover.MigrationError, subprocess.SubprocessError) as error:
        return check("volumes", "blocked", str(error), paths=results)

    evidence = {"paths": results}
    if source is not None and target is not None:
        evidence["same_volume"] = results[0]["volume"] == results[1]["volume"]
    return check("volumes", "passed", "The required paths and volumes are available.", **evidence)


def check_git(root):
    if root is None:
        return check("git", "not_applicable", "This step does not use Git.")
    try:
        if not git_check.has_main_marker(root):
            return check("git", "not_applicable", "The selected directory is not a Git main checkout.")
    except (OSError, ValueError) as error:
        return check("git", "blocked", str(error))

    try:
        program = git_check.git_program()
    except FileNotFoundError as error:
        return check("git", "missing", str(error))
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        return check("git", "blocked", str(error))

    evidence = {"path": str(program)}
    try:
        def git(*args):
            return git_check.git(root, *args, program=program, timeout=10)

        evidence["version"] = os.fsdecode(git("--version")).strip()
        top = git_check.as_path(git("rev-parse", "--show-toplevel"))
        output = git("rev-parse", "--path-format=absolute", "--git-common-dir")
        common = git_check.as_path(output)
        if not common.is_absolute() or len(output.splitlines()) != 1:
            return check("git", "incompatible", "Git did not support rev-parse --path-format=absolute.", **evidence)
        if top != root or common != root / ".git":
            return check("git", "blocked", f"Not an independent Git main checkout: {root}", **evidence)
        worktrees = git("worktree", "list", "--porcelain", "-z")
        if not worktrees.endswith(b"\0\0"):
            return check("git", "incompatible", "Git did not produce NUL-delimited worktree records.", **evidence)
    except git_check.GitCommandError as error:
        message = str(error)
        unsupported = ("unknown option", "unrecognized option", "unknown switch")
        status = "incompatible" if any(reason in message.lower() for reason in unsupported) else "blocked"
        return check("git", status, message, **evidence)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        return check("git", "blocked", str(error), **evidence)
    return check("git", "passed", "Git and the required read-only worktree commands are available.", **evidence)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source")
    parser.add_argument("--target")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--git-root", help="Verified physical Git root required by the next resume step")
    args = parser.parse_args()
    if not args.resume and (not args.source or not args.target or args.git_root):
        parser.error("a new migration requires --source and --target; --git-root is for --resume")

    if sys.platform not in ("win32", "darwin"):
        checks = [check("system", "incompatible", "Native Windows or macOS is required.")]
    elif sys.implementation.name != "cpython" or sys.version_info < (3, 12):
        checks = [check("python", "incompatible", "CPython 3.12 or newer is required.")]
    else:
        source, old = inspect_path("source", args.source, resume=args.resume)
        target, new = inspect_path("target", args.target, resume=args.resume)
        checks = [check_paths(source, target, args.resume, [old, new])]
        if args.git_root:
            root, result = inspect_path("source", args.git_root, resume=True)
            checks.append(check_git(root) if root else check("git", "blocked", result["detail"]))
        elif not args.resume and source is None:
            checks.append(check("git", "pending", "The source must be available to determine whether Git applies."))
        else:
            checks.append(check_git(None if args.resume else source))

    print(json.dumps({"checks": checks}, ensure_ascii=True))
    return 0 if all(row["status"] in ("passed", "not_applicable") for row in checks) else 1


if __name__ == "__main__":
    sys.exit(main())
