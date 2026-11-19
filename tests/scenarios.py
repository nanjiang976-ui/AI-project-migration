"""Exercise the packaged command-line entry points with isolated, synthetic projects."""
import base64
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
from unittest.mock import patch
from uuid import uuid4

from _winapi import CreateJunction
import migration_ntfs as nt
import tree_copy
from record_checks import RecordChecks


class MigrationTests(RecordChecks, unittest.TestCase):
    @classmethod
    def configure(cls, scripts, source_run, target_run):
        cls.scripts = scripts
        cls.source_run = source_run
        cls.target_run = target_run
        temp = source_run / "process-temp"
        home = source_run / "process-home"
        for path in (temp, home, home / "empty-template"):
            path.mkdir()
        # Keep Git configs, identities, hooks and external worktree overrides
        # from the developer's environment out of every fixture operation.
        cls.env = {k: v for k, v in os.environ.items() if not k.upper().startswith("GIT_")}
        cls.env.update(
            TEMP=str(temp), TMP=str(temp), HOME=str(home), XDG_CONFIG_HOME=str(home),
            GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
            GIT_TEMPLATE_DIR=str(home / "empty-template"), GIT_TERMINAL_PROMPT="0",
            GIT_OPTIONAL_LOCKS="0", GIT_AUTHOR_NAME="Fixture", GIT_COMMITTER_NAME="Fixture",
            GIT_AUTHOR_EMAIL="fixture@example.invalid", GIT_COMMITTER_EMAIL="fixture@example.invalid",
            GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0="core.longpaths", GIT_CONFIG_VALUE_0="true",
            PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1",
        )

    def setUp(self):
        name = uuid4().hex[:12]
        self.left = self.source_run / name
        self.right = self.target_run / name
        self.left.mkdir()
        self.right.mkdir()
        self.old = self.left / "Demo [a]'’ $(2+3)"
        self.new = self.right / "Demo"
        self.audit = self.right / "audit"
        self.copy_audit = self.audit / "tree-copy"
        self.plan = self.audit / "cutover-plan.json"
        self.backup = self.old.with_name(self.old.name + "_迁移备份")
        self.old.mkdir()
        (self.old / "sample.txt").write_bytes(b"fixture\n")

    def command(self, argv, *, cwd=None, success=True):
        cp = subprocess.run([str(v) for v in argv], cwd=cwd or self.right, env=self.env,
                            capture_output=True, stdin=subprocess.DEVNULL, timeout=120)
        out = cp.stdout.decode("utf-8", errors="replace").strip()
        err = cp.stderr.decode("utf-8", errors="replace").strip()
        self.assertEqual(cp.returncode == 0, success, f"{argv[0]} returned {cp.returncode}\n{out}\n{err}")
        return out

    def helper(self, name, *args, **options):
        return self.command([sys.executable, "-B", self.scripts / (name + ".py"), *args], **options)

    def copy_cli(self, action, *, success=True, destination=None, audit=None):
        output = self.helper("tree_copy", action, self.old, destination or self.new,
                             "--audit", audit or self.copy_audit, success=success)
        return json.loads(Path(json.loads(output)["report"]).read_text(encoding="utf-8"))

    def copy_verified(self):
        self.audit.mkdir(exist_ok=True)
        self.copy_cli("copy")
        return self.copy_cli("verify")

    def prepare(self, mode, *, success=True):
        out = self.helper("cutover", "prepare", "--source", self.old, "--target", self.new,
                          "--audit", self.audit, "--copy-audit", self.copy_audit,
                          "--mode", mode, success=success)
        return out.splitlines()[-1] if success else None

    def apply(self, mode, *, success=True):
        return self.helper("cutover", "apply", "--plan", self.plan, "--mode", mode, success=success)

    def phase(self):
        return json.loads(self.helper("cutover", "status", "--plan", self.plan))["phase"]

    def run_paste(self, line, shell="powershell.exe"):
        # Transport the generated interactive line unchanged, including Unicode.
        encoded = base64.b64encode(line.encode("utf-16le")).decode("ascii")
        out = self.command([shell, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded])
        self.assertIn("Junction", out)
        self.assertEqual(self.phase(), "linked")
        self.assertTrue(self.old.is_junction() and self.old.samefile(self.new))

    def git(self, root, *args):
        return self.command(["git", "-C", root, *args])

    def test_copy_ntfs_objects(self):
        data = self.old / "sample.txt"
        (self.old / "empty").mkdir()
        (self.old / "directory").mkdir()
        link = self.old / "directory" / "第二个文件名.txt"
        os.link(data, link)
        for path in (data, self.old / "directory"):
            with open(str(path) + ":test_metadata", "xb") as stream:
                stream.write(b"example stream")
        nt.write_attrs(data, 0x01 | 0x02 | 0x20)
        report = self.copy_verified()
        self.assertEqual(report["differences"], {})
        self.assertEqual(report["source_summary"]["named_streams"], 3)
        self.assertTrue((self.new / "sample.txt").samefile(self.new / link.relative_to(self.old)))
        self.assertTrue((self.new / "empty").is_dir())
        self.assertEqual(nt.security(data), nt.security(self.new / "sample.txt"))

    def test_copy_preserves_unowned_or_conflicting_destination(self):
        self.audit.mkdir()
        self.new.mkdir()
        (self.new / "keep.txt").write_bytes(b"untouched")
        self.copy_cli("copy", success=False)
        self.assertEqual((self.new / "keep.txt").read_bytes(), b"untouched")
        other = self.right / "owned"
        self.copy_cli("copy", destination=other)
        self.copy_cli("copy", destination=other)  # Resume against owned objects only.
        (self.old / "sample.txt").write_bytes(b"changed")
        (other / "extra.txt").write_bytes(b"extra")
        report = self.copy_cli("verify", destination=other, success=False)
        self.assertIn("extra.txt", report["differences"]["destination_only"])
        self.assertIn("sample.txt", report["differences"]["sha256"])

    def test_source_change_and_nested_junction_stop_copy(self):
        self.audit.mkdir()
        tree_copy.prepare_audit(self.copy_audit, self.old)
        original = tree_copy.prepare_copy

        def change_after_snapshot(source, dest, audit):
            original(source, dest, audit)
            (source / "sample.txt").write_bytes(b"changed during the copy")

        with patch.object(tree_copy, "prepare_copy", side_effect=change_after_snapshot):
            report = {"run": "source-drift"}
            with self.assertRaises(RuntimeError):
                tree_copy.copy(self.old, self.new, self.copy_audit, report)
        self.assertIn("sample.txt", report["source_changed_during_copy"])
        link = self.old / "unexpected-junction"
        CreateJunction(str(self.new), str(link))
        other = self.right / "candidate-two"
        self.copy_cli("copy", destination=other, audit=self.audit / "different-audit", success=False)
        self.assertFalse(other.exists())

    def test_manual_rename_and_bidirectional_probe(self):
        self.copy_verified()
        line = self.prepare("manual")
        self.apply("manual", success=False)
        self.assertFalse(self.backup.exists())
        self.old.rename(self.backup)
        self.assertEqual(self.phase(), "renamed")
        self.run_paste(line)
        self.run_paste(line)  # Already correct.
        token = str(uuid4())
        self.helper("probe", "--plan", self.plan, "--request-id", token, cwd=self.new, success=False)
        response = json.loads(self.helper("probe", "--plan", self.plan, "--request-id", token, cwd=self.old))
        for field in ("old_write_d_read", "d_write_old_read", "same_file", "probe_removed", "backup_isolated"):
            self.assertTrue(response[field])
        self.assertEqual(response["request_id"], token)
        self.assertEqual([p.name for p in self.new.iterdir()], ["sample.txt"])
        self.helper("probe", "--plan", self.plan, "--request-id", token, cwd=self.old, success=False)
        resolved = json.loads(self.helper("probe", "--plan", self.plan, "--request-id", str(uuid4()),
                                          "--resumed-session-id", "fixture-original-session", cwd=self.new))
        self.assertEqual(resolved["cwd_form"], "resolved_target")
        self.assertTrue(resolved["session_identity_requires_host_match"])

    def test_auto_rename_powershell(self):
        self.copy_verified()
        self.run_paste(self.prepare("auto"))
        self.assertEqual((self.backup / "sample.txt").read_bytes(), b"fixture\n")

    @unittest.skipUnless(shutil.which("pwsh.exe"), "PowerShell 7 is optional")
    def test_auto_rename_powershell7(self):
        self.copy_verified()
        self.run_paste(self.prepare("auto"), "pwsh.exe")

    def test_backup_collision_and_wrong_junction(self):
        self.copy_verified()
        self.prepare("auto")
        self.backup.mkdir()
        self.apply("auto", success=False)
        self.assertFalse(self.old.is_junction())
        self.backup.rename(self.left / "preserved-existing-backup")
        self.old.rename(self.backup)
        wrong = self.right / "unrelated"
        wrong.mkdir()
        CreateJunction(str(wrong), str(self.old))
        self.apply("auto", success=False)
        self.assertTrue(self.old.samefile(wrong))
        self.assertTrue((self.backup / "sample.txt").is_file())

    def test_directory_lock_then_retry(self):
        self.copy_verified()
        self.prepare("auto")
        # This real directory handle allows reads/writes but denies delete-sharing.
        handle = nt.create(nt.wide(self.old), 0x80000000, 3, None, 3, 0x02000000, None)
        self.assertNotEqual(handle, nt.INVALID)
        try:
            self.apply("auto", success=False)
            self.assertFalse(self.backup.exists())
        finally:
            nt.close(handle)
        self.apply("auto")
        self.assertEqual(self.phase(), "linked")

    def test_resume_after_rename_before_link(self):
        import cutover
        from types import SimpleNamespace

        self.copy_verified()
        self.prepare("auto")
        with patch("_winapi.CreateJunction", side_effect=PermissionError("injected test interruption")):
            with self.assertRaises(cutover.MigrationError):
                cutover.apply(SimpleNamespace(plan=self.plan, mode="auto"))
        self.assertEqual(self.phase(), "renamed")
        self.assertFalse(os.path.lexists(self.old))
        self.apply("auto")
        self.assertEqual(self.phase(), "linked")

    def test_failed_verification_or_target_replacement_blocks_cutover(self):
        self.copy_verified()
        self.prepare("auto")
        (self.new / "sample.txt").write_bytes(b"edited in destination")
        self.copy_cli("verify", success=False)
        self.apply("auto", success=False)
        self.assertFalse(self.backup.exists())
        self.new.rename(self.right / "retained-candidate")
        self.new.mkdir()
        self.prepare("auto", success=False)
        self.assertTrue(self.old.is_dir())

    def test_non_git_and_no_external_worktree(self):
        self.audit.mkdir()
        plain = self.audit / "ordinary.json"
        self.helper("git_check", "baseline", self.old, "--out", plain)
        self.assertEqual(json.loads(plain.read_text(encoding="utf-8"))["kind"], "non_git")
        self.git(self.old, "init", "-q")
        main = self.audit / "git-main.json"
        self.helper("git_check", "baseline", self.old, "--out", main)
        self.assertEqual(json.loads(main.read_text(encoding="utf-8"))["kind"], "main_only")

    def test_full_migration_of_dirty_git_worktree(self):
        self.git(self.old, "init", "-q")
        self.git(self.old, "add", "sample.txt")
        self.git(self.old, "commit", "-qm", "Fixture")
        linked = self.left / "independent-worktree"
        self.git(self.old, "worktree", "add", "--detach", linked, "HEAD")
        (linked / "sample.txt").write_bytes(b"staged")
        self.git(linked, "add", "sample.txt")
        (linked / "sample.txt").write_bytes(b"work in progress")
        (linked / "untracked.txt").write_bytes(b"keep")
        self.helper("run_record", "start", self.old, self.new, "--project-id", "demo-project",
                    "--run", self.audit, "--allow-same-volume")
        before = self.audit / "git-before.json"
        self.helper("git_check", "baseline", self.old, "--out", before)
        self.assertEqual(json.loads(before.read_text(encoding="utf-8"))["worktree_count"], 1)

        def mark(step, status="passed", *identity):
            return json.loads(self.helper("run_record", "mark", "--run", self.audit, "--step", step,
                                          "--status", status, "--evidence", "synthetic fixture",
                                          "--next-action", "continue fixture verification", *identity))

        mark(1)
        self.copy_cli("copy")
        mark(2)
        self.copy_cli("verify")
        mark(3)
        self.helper("git_check", "repair", self.new, "--baseline", before, "--out", self.audit / "repair.json")
        mark(4)
        line = self.prepare("auto")
        mark(5, "waiting")
        progress = json.loads(self.helper("run_record", "show", "--run", self.audit))
        self.assertEqual(progress["next_step"], 5)
        self.run_paste(line)
        mark(5)
        token = str(uuid4())
        mark(6, "waiting", "--request-id", token, "--session-id", "fixture-confirmed-old-chat")
        self.helper("probe", "--plan", self.plan, "--request-id", token, cwd=self.old)
        mark(6)
        self.helper("git_check", "verify", self.new, "--baseline", before, "--out", self.audit / "final.json")
        self.assertTrue(Path(self.git(linked, "rev-parse", "--path-format=absolute", "--git-common-dir"))
                        .samefile(self.new / ".git"))
        self.assertEqual(self.git(linked, "show", ":sample.txt"), "staged")
        self.assertEqual((linked / "sample.txt").read_bytes(), b"work in progress")
        self.assertEqual((linked / "untracked.txt").read_bytes(), b"keep")
        # The probe and Git repair can legitimately touch these timestamps. Hashes,
        # streams, ACLs and every other manifest entry must still agree.
        verify_out = self.command_for_final_comparison()
        admin = json.loads(before.read_text(encoding="utf-8"))["snapshot"]["linked"][0]["admin"]
        self.assertLessEqual(set(verify_out), {"mtime_ns"})
        self.assertLessEqual(set(verify_out.get("mtime_ns", [])), {".", f".git/worktrees/{admin}/gitdir"})
        end = mark(7)
        self.assertIsNone(end["next_step"])
        self.assert_cleanup(end, True)
        self.assert_cleanup(json.loads(self.helper("run_record", "show", "--run", self.audit)), True)

        # A failed *later* check supersedes an earlier green Git receipt.
        extra = linked / "after-completion.txt"
        extra.write_bytes(b"new external work")
        self.helper("git_check", "verify", self.new, "--baseline", before,
                    "--out", self.audit / "final-later-failure.json", success=False)
        self.assert_cleanup(json.loads(self.helper("run_record", "show", "--run", self.audit)), False)
        extra.rename(self.left / "preserved-extra.txt")
        self.helper("git_check", "verify", self.new, "--baseline", before,
                    "--out", self.audit / "final-later-success.json")

        # A historical clean backup report cannot authorize deletion of new data.
        (self.backup / "saved-after-completion.txt").write_bytes(b"unique backup data")
        self.assert_cleanup(json.loads(self.helper("run_record", "show", "--run", self.audit)), False)

    def command_for_final_comparison(self):
        cmd = [sys.executable, "-B", self.scripts / "tree_copy.py", "verify", self.backup, self.new,
               "--audit", self.audit / "backup-check"]
        cp = subprocess.run(cmd, cwd=self.right, env=self.env, capture_output=True, timeout=120)
        self.assertIn(cp.returncode, (0, 2), cp.stderr.decode(errors="replace"))
        meta = json.loads(cp.stdout)
        self.assertIn(meta["status"], ("passed", "differences"))
        return json.loads(Path(meta["report"]).read_text(encoding="utf-8"))["differences"]
