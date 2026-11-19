"""Run offline Windows/NTFS integration tests inside two explicitly chosen scratch roots."""
import argparse
import json
import os
from pathlib import Path
import shutil
import sys
import unittest
from uuid import uuid4

sys.dont_write_bytecode = True
REPOSITORY = Path(__file__).resolve().parents[1]
SCRIPTS = REPOSITORY / "skills" / "ai-project-migration" / "scripts"


def validate_roots(source_name, target_name):
    from tree_copy import checked_root, check_disjoint

    source, target = [checked_root(name) for name in (source_name, target_name)]
    check_disjoint([source, target, REPOSITORY])
    for root in (source, target):
        if not root.is_dir() or any(root.iterdir()):
            raise ValueError(f"Choose an existing, empty, dedicated scratch directory: {root}")
        for parent in (root, *root.parents):
            if os.path.lexists(parent / ".git") or (parent / "SKILL.md").is_file():
                raise ValueError(f"Scratch directory belongs to an existing project: {root}")
            if (parent / "HEAD").is_file() and (parent / "objects").is_dir():
                raise ValueError(f"Scratch directory is inside Git metadata: {root}")
    return source, target


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-scratch", required=True, help="Existing empty local NTFS directory")
    parser.add_argument("--target-scratch", required=True, help="Another existing empty local NTFS directory")
    args = parser.parse_args()
    if os.name != "nt" or sys.version_info < (3, 12):
        parser.error("Windows and CPython 3.12+ are required.")
    if not shutil.which("git") or not shutil.which("powershell.exe"):
        parser.error("This developer suite requires Git and Windows PowerShell.")
    sys.path.insert(0, str(SCRIPTS))
    try:
        source, target = validate_roots(args.source_scratch, args.target_scratch)
    except (ValueError, OSError) as error:
        parser.exit(2, f"Scratch validation: {error}\n")
    name = "migration-tests-" + uuid4().hex
    source_run, target_run = source / name, target / name
    cross_volume = source.stat().st_dev != target.stat().st_dev
    print(f"Source fixtures: {source_run}\nTarget fixtures and report: {target_run}\n"
          f"Source and target are on different drives: {cross_volume}\nBoth fixture directories will be retained.", flush=True)
    source_run.mkdir()
    target_run.mkdir()
    from scenarios import MigrationTests

    MigrationTests.configure(SCRIPTS, source_run, target_run)
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(MigrationTests)
    names = [test.id().rsplit(".", 1)[-1] for test in suite]
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    report = {
        "cross_volume": cross_volume,
        "desktop_and_chats": "synthetic; no desktop project or user conversations used",
        "scenarios": names,
        "executed": result.testsRun,
        "skipped": [test.id().rsplit(".", 1)[-1] for test, _ in result.skipped],
        "failed": [test.id().rsplit(".", 1)[-1] for test, _ in result.failures + result.errors],
        "success": result.wasSuccessful(),
    }
    path = target_run / "result.json"
    with path.open("x", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2)
    print(f"Local report: {path}")
    return 0 if report["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
