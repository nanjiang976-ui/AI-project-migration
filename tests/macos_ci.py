"""Native macOS regression on two dedicated APFS disk images, never on user projects."""
import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import plistlib
import shutil
import stat
import subprocess
import sys
import unittest
from uuid import uuid4

from record_checks import RecordChecks

SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "codex-project-migration" / "scripts"


class DiskImage:
    def __init__(self, scratch, label, filesystem):
        self.label = label
        self.image = scratch / (label + ".sparseimage")
        self.mount = Path("/Volumes") / label
        self.attached = False
        if os.path.lexists(self.image) or os.path.lexists(self.mount):
            raise FileExistsError(label)
        subprocess.run(["hdiutil", "create", "-size", "384m", "-type", "SPARSE",
                        "-fs", filesystem, "-volname", label, str(self.image)], check=True)

    def attach(self):
        if os.path.lexists(self.mount):
            raise RuntimeError(f"Unexpected occupied mount point: {self.mount}")
        subprocess.run(["hdiutil", "attach", "-nobrowse", "-owners", "on", str(self.image)], check=True)
        self.attached = True
        info = plistlib.loads(subprocess.check_output(["diskutil", "info", "-plist", str(self.mount)]))
        if info.get("MountPoint") != str(self.mount) or info.get("FilesystemType") != "apfs":
            raise RuntimeError(f"Expected a dedicated APFS mount: {self.mount}")
        return info["VolumeUUID"]

    def detach(self):
        if self.attached:
            subprocess.run(["hdiutil", "detach", str(self.mount)], check=True)
            self.attached = False


@contextmanager
def mounted(scratch, suffix, filesystem):
    disk = DiskImage(scratch, "Mig" + suffix + uuid4().hex[:10], filesystem)
    try:
        uuid = disk.attach()
        print(f"Dedicated test image: {disk.image}; mount: {disk.mount}; volume UUID: {uuid}", flush=True)
        yield disk, uuid
    finally:
        disk.detach()


class MacMigrationTests(RecordChecks, unittest.TestCase):
    source_image = None
    target_image = None

    def setUp(self):
        run_id = uuid4().hex[:10]
        left = self.source_image.mount / run_id
        right = self.target_image.mount / run_id
        left.mkdir()
        right.mkdir()
        self.old = left / "Demo [sample]' 例"
        self.new = right / "Demo"
        self.backup = self.old.with_name(self.old.name + "_迁移备份")
        self.audit = left / "audit"
        self.copy_audit = self.audit / "tree-copy"
        self.plan = self.audit / "cutover-plan.json"
        self.old.mkdir()
        self.data = self.old / "source.txt"
        self.data.write_bytes(b"fixture payload")
        # A dedicated source-side audit remains available while the target is detached.
        self.env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        self.env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1", GIT_TEMPLATE_DIR="",
                        GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0", PYTHONDONTWRITEBYTECODE="1",
                        GIT_AUTHOR_NAME="Fixture", GIT_COMMITTER_NAME="Fixture",
                        GIT_AUTHOR_EMAIL="fixture@example.invalid", GIT_COMMITTER_EMAIL="fixture@example.invalid")

    def command(self, argv, *, cwd=None, codes=(0,)):
        cp = subprocess.run([str(v) for v in argv], cwd=cwd or self.old.parent,
                            env=self.env, stdin=subprocess.DEVNULL, capture_output=True, timeout=180)
        output = cp.stdout.decode(errors="replace")
        self.assertIn(cp.returncode, codes, f"{argv[0]} rc={cp.returncode}\n{output}\n{cp.stderr.decode(errors='replace')}")
        return output.strip()

    def helper(self, name, *args, fail=False, success=True, **opts):
        if fail or not success:
            opts["codes"] = (1, 2)
        return self.command([sys.executable, "-B", SCRIPTS / (name + ".py"), *args], **opts)

    def copy(self, action, *, fail=False):
        return self.helper("tree_copy", action, self.old, self.new, "--audit", self.copy_audit, fail=fail)

    def verified(self):
        self.audit.mkdir(exist_ok=True)
        self.copy("copy")
        result = json.loads(self.copy("verify"))
        self.assertEqual(json.loads(Path(result["report"]).read_text())["differences"], {})

    def prepare(self, mode):
        return self.helper("cutover", "prepare", "--source", self.old, "--target", self.new,
                           "--audit", self.audit, "--copy-audit", self.copy_audit,
                           "--mode", mode).splitlines()[-1]

    def git(self, root, *args):
        return self.command(["git", "-C", root, *args])

    def test_metadata_and_internal_links(self):
        (self.old / "空目录").mkdir()
        alias = self.old / "same inode"
        os.link(self.data, alias)
        def put_xattr(path, key, value):
            self.command(["/usr/bin/xattr", "-wx", key, value.hex(), path])
        def get_xattr(path, key):
            return bytes.fromhex(self.command(["/usr/bin/xattr", "-px", key, path]))
        put_xattr(self.data, "com.example.migration", b"attribute\0value")
        put_xattr(self.old / "空目录", "com.example.directory", b"folder attribute")
        put_xattr(self.data, "com.apple.ResourceFork", b"synthetic resource fork")
        self.data.chmod(0o640)
        self.command(["chmod", "+a", "group:everyone allow read,readattr,readextattr,readsecurity", self.data])
        os.chflags(self.data, stat.UF_NODUMP)
        os.utime(self.data, ns=(1700000000123456789, 1700000000123456789))
        self.verified()
        dest = self.new / self.data.name
        self.assertTrue(dest.samefile(self.new / alias.name))
        self.assertTrue((self.new / "空目录").is_dir())
        a, b = self.data.stat(), dest.stat()
        for field in ("st_uid", "st_gid", "st_flags", "st_mode", "st_mtime_ns"):
            self.assertEqual(getattr(a, field), getattr(b, field), field)
        self.assertEqual(get_xattr(dest, "com.apple.ResourceFork"), b"synthetic resource fork")
        self.assertEqual(get_xattr(dest, "com.example.migration"), b"attribute\0value")
        self.assertEqual(get_xattr(self.new / "空目录", "com.example.directory"), b"folder attribute")
        acl = lambda path: self.command(["ls", "-lde", path]).splitlines()[1:]
        self.assertTrue(acl(self.data))
        self.assertEqual(acl(self.data), acl(dest))
        put_xattr(dest, "com.example.migration", b"changed")
        self.copy("verify", fail=True)

    def test_name_collision_or_unsupported_structure_stops(self):
        self.audit.mkdir()
        (self.old / "ReadMe").write_bytes(b"upper")
        (self.old / "README").write_bytes(b"lower")
        self.assertEqual((self.old / "ReadMe").read_bytes(), b"upper", "source image must be case sensitive")
        self.copy("copy", fail=True)
        self.assertEqual((self.old / "README").read_bytes(), b"lower")

    def test_external_link_refused(self):
        self.audit.mkdir()
        (self.old / "outside").symlink_to(self.old.parent, target_is_directory=True)
        self.copy("copy", fail=True)
        self.assertTrue((self.old / "outside").is_symlink())

    def test_manual_cutover_and_probe(self):
        self.verified()
        paste = self.prepare("manual")
        self.command(["/bin/sh", "-c", paste], codes=(1, 2))
        self.assertFalse(self.backup.exists())
        self.old.rename(self.backup)
        self.assertEqual(json.loads(self.helper("cutover", "status", "--plan", self.plan))["phase"], "renamed")
        self.command(["/bin/sh", "-c", paste])
        self.command(["/bin/sh", "-c", paste])
        self.assertTrue(self.old.is_symlink() and self.old.samefile(self.new))
        req = str(uuid4())
        report = json.loads(self.helper("probe", "--plan", self.plan, "--request-id", req,
                                        "--filesystem-only", cwd=self.old))
        for field in ("old_write_d_read", "d_write_old_read", "same_file", "probe_removed",
                      "backup_isolated", "old_path_link_verified"):
            self.assertTrue(report[field], field)
        self.assertTrue(Path(report["cwd"]).samefile(self.new))
        self.helper("probe", "--plan", self.plan, "--request-id", req, "--filesystem-only", cwd=self.old, fail=True)
        self.helper("probe", "--plan", self.plan, "--filesystem-only", cwd=self.backup, fail=True)
        self.assertEqual([p.name for p in self.new.iterdir()], [self.data.name])

    def test_wrong_symlink_left_untouched(self):
        self.verified()
        self.prepare("auto")
        self.old.rename(self.backup)
        self.old.symlink_to(self.audit, target_is_directory=True)
        self.helper("cutover", "apply", "--plan", self.plan, "--mode", "auto", fail=True)
        self.assertTrue(self.old.samefile(self.audit) and self.backup.exists())

    def test_detached_target_and_remount(self):
        self.verified()
        paste = self.prepare("auto")
        self.target_image.detach()
        try:
            self.helper("cutover", "status", "--plan", self.plan, fail=True)
            self.helper("cutover", "apply", "--plan", self.plan, "--mode", "auto", fail=True)
            self.assertFalse(self.backup.exists())
            self.assertFalse(self.new.exists())
        finally:
            self.target_image.attach()
        self.command(["/bin/sh", "-c", paste])
        self.assertTrue(self.old.samefile(self.new))

    def test_git_and_backup(self):
        self.helper("run_record", "start", self.old, self.new, "--run", self.audit, "--host", "generic")
        noop = self.audit / "non-git.json"
        self.helper("git_check", "baseline", self.old, "--out", noop)
        self.assertEqual(json.loads(noop.read_text())["kind"], "non_git")
        self.git(self.old, "init", "-q")
        self.git(self.old, "add", self.data.name)
        self.git(self.old, "commit", "-qm", "fixture")
        linked = self.old.parent / "independent"
        self.git(self.old, "worktree", "add", "--detach", linked, "HEAD")
        (linked / self.data.name).write_bytes(b"staged")
        self.git(linked, "add", self.data.name)
        (linked / self.data.name).write_bytes(b"dirty")
        (linked / "untracked").write_bytes(b"keep")
        baseline = self.audit / "git-before.json"
        self.helper("git_check", "baseline", self.old, "--out", baseline)
        self.mark_fixture(1)
        self.verified()
        self.mark_fixture(2)
        self.mark_fixture(3)
        self.helper("git_check", "repair", self.new, "--baseline", baseline, "--out", self.audit/"repair.json")
        self.mark_fixture(4)
        self.command(["/bin/sh", "-c", self.prepare("auto")])
        self.mark_fixture(5)
        self.mark_fixture(6, "not_applicable")
        self.helper("git_check", "verify", self.new, "--baseline", baseline, "--out", self.audit/"final.json")
        self.assertEqual((linked/"untracked").read_bytes(), b"keep")
        self.assertEqual((linked/self.data.name).read_bytes(), b"dirty")
        self.assertEqual(self.git(linked, "show", ":" + self.data.name), "staged")
        self.assertTrue(Path(self.git(linked, "rev-parse", "--path-format=absolute", "--git-common-dir")).samefile(self.new / ".git"))
        out = self.helper("tree_copy", "verify", self.backup, self.new, "--audit", self.audit/"backup-check", codes=(0, 2))
        info = json.loads(Path(json.loads(out)["report"]).read_text())
        self.assertIn(info["status"], ("passed", "differences"))
        admin = json.loads(baseline.read_text())["snapshot"]["linked"][0]["admin"]
        diffs = info["differences"]
        self.assertLessEqual(set(diffs), {"mtime_ns"})
        self.assertLessEqual(set(diffs.get("mtime_ns", [])), {".", f".git/worktrees/{admin}/gitdir"})
        self.assert_cleanup(self.mark_fixture(7), True)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--scratch", type=Path, required=True)
    args = p.parse_args()
    if sys.platform != "darwin" or sys.version_info < (3, 12):
        p.error("Use macOS with Python 3.12+.")
    scratch = args.scratch.resolve(strict=True)
    if (scratch == Path("/") or not scratch.is_dir() or any(scratch.iterdir())
            or not shutil.which("hdiutil")
            or any((up / ".git").exists() or (up / "SKILL.md").is_file()
                   for up in (scratch, *scratch.parents))):
        p.error("Choose an existing empty scratch directory for the two exclusive disk image files.")
    # hdiutil uses the full filesystem personality name; APFSX is a diskutil alias.
    with mounted(scratch, "Src", "Case-sensitive APFS") as (a, auuid), mounted(scratch, "Dst", "APFS") as (b, buuid):
        if auuid == buuid or a.mount.stat().st_dev == b.mount.stat().st_dev:
            raise RuntimeError("Expected distinct APFS volumes")
        MacMigrationTests.source_image, MacMigrationTests.target_image = a, b
        tests = unittest.defaultTestLoader.loadTestsFromTestCase(MacMigrationTests)
        result = unittest.TextTestRunner(verbosity=2).run(tests)
        if os.environ.get("GITHUB_ACTIONS") == "true":
            for case, trace in result.failures + result.errors:
                # Expose fixture diagnostics in the check itself as well as the runner log.
                detail = trace[-2800:].replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
                print(f"::error title={case.id()}::{detail}", flush=True)
            summary = os.environ.get("GITHUB_STEP_SUMMARY")
            if summary:
                with open(summary, "a", encoding="utf-8") as handle:
                    handle.write(f"**Native APFS test volumes:** two independently mounted sparse images. "
                                 f"Executed {result.testsRun}; failures {len(result.failures)}, "
                                 f"errors {len(result.errors)}. No live agent sessions were used.\n")
    print("Images detached. Retained both test images in", scratch, flush=True)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
