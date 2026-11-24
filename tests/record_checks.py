"""Behavior shared by the native Windows and macOS fixture suites."""
import json
import os
from pathlib import Path
import sys
from unittest.mock import patch
from uuid import uuid4


class RecordChecks:
    def mark_fixture(self, step, status="passed"):
        return json.loads(self.helper("run_record", "mark", "--run", self.audit, "--step", step,
                                      "--status", status, "--evidence", "synthetic fixture",
                                      "--next-action", "continue fixture verification"))

    def assert_cleanup(self, report, eligible, *, linked=False):
        self.assertEqual(next(iter(report)), "cleanup_notice")
        notice = report["cleanup_notice"]
        self.assertIn("请勿删除", notice["warning"])
        self.assertIn(str(self.old), notice["warning"])
        reassurance = "请放心，保留这个入口几乎不占用硬盘空间。"
        if linked:
            self.assertIn(reassurance, notice["warning"])
            kind = "Windows 目录联接" if os.name == "nt" else "macOS 目录符号链接"
            self.assertIn(kind, notice["warning"])
        else:
            self.assertNotIn(reassurance, notice["warning"])
        self.assertIn(str(self.new), notice["target"])
        self.assertEqual(notice["backup"], str(self.backup))
        self.assertIs(notice["eligible"], eligible)
        if not eligible:
            self.assertIn("暂勿清理备份", notice["backup_message"])

    def test_volume_direction_and_same_volume_gate(self):
        # Exercise the actual volume identities in both directions, independent of drive letters.
        forward = json.loads(self.helper("run_record", "volumes", self.old, self.new))
        reverse = json.loads(self.helper("run_record", "volumes", self.new.parent, self.old.parent/"reverse"))
        self.assertEqual(forward["source_volume"], reverse["target_volume"])
        self.assertEqual(forward["target_volume"], reverse["source_volume"])
        self.assertEqual(forward["same_volume"], self.old.stat().st_dev == self.new.parent.stat().st_dev)
        near = self.old.parent / "same-volume-target"
        same = json.loads(self.helper("run_record", "volumes", self.old, near))
        self.assertTrue(same["same_volume"])
        self.assertIn("不会释放", same["message"])
        args = ["run_record", "start", self.old, near, "--run", self.audit, "--host", "generic"]
        self.helper(*args, success=False)
        self.assertFalse(self.audit.exists())
        report = json.loads(self.helper(*args, "--allow-same-volume"))
        self.assertFalse(report["config"].get("project_id"), "generic mode must not invent a platform project ID")
        self.assertEqual(report["next_step"], 1)

    def test_read_legacy_checkpoint_without_rewriting_it(self):
        self.audit.mkdir()
        old_run = self.audit / "run.json"
        legacy = {"version": 1, "project_id": "mock-old-id", "source": str(self.old),
                  "target": str(self.new), "backup": str(self.backup), "started": "2026-01-01T00:00:00+00:00"}
        content = json.dumps(legacy).encode()
        old_run.write_bytes(content)
        self.mark_fixture(1)
        output = json.loads(self.helper("run_record", "show", "--run", self.audit))
        self.assertEqual(output["next_step"], 2)
        self.assertEqual(old_run.read_bytes(), content)
        self.assert_cleanup(output, False)

    def test_unverified_old_chat_cannot_approve_cleanup(self):
        self.helper("run_record", "start", self.old, self.new, "--run", self.audit,
                    "--host", "generic", "--allow-same-volume")
        for step in range(1, 6):
            self.mark_fixture(step)
        current = self.mark_fixture(6, "unverified")
        self.assertEqual(current["next_step"], 7)
        self.assert_cleanup(current, False)
        self.helper("run_record", "mark", "--run", self.audit, "--step", 7, "--status", "passed",
                    "--evidence", "synthetic; host history unavailable", "--next-action", "end", success=False)
        self.assert_cleanup(json.loads(self.helper("run_record", "show", "--run", self.audit)), False)

    def test_backup_change_during_destination_scan_invalidates_cleanup(self):
        self.helper("run_record", "start", self.old, self.new, "--run", self.audit,
                    "--host", "generic", "--allow-same-volume")
        self.helper("git_check", "baseline", self.old, "--out", self.audit / "git-before.json")
        for action in ("copy", "verify"):
            self.helper("tree_copy", action, self.old, self.new, "--audit", self.copy_audit)
        self.prepare("auto")
        self.helper("cutover", "apply", "--plan", self.plan, "--mode", "auto")
        self.helper("tree_copy", "verify", self.backup, self.new,
                    "--audit", self.audit / "backup-check")
        config = json.loads((self.audit / "run.json").read_text(encoding="utf-8"))

        scripts = Path(__file__).resolve().parents[1] / "skills" / "ai-project-migration" / "scripts"
        with patch.object(sys, "path", [str(scripts), *sys.path]):
            from run_record import verify_backup_files
            if os.name == "nt":
                import tree_copy as tree
            else:
                import mac_tree as tree

        original_snapshot = tree.snapshot
        added = self.backup / "added-during-destination-scan.txt"

        def change_backup_after_destination_snapshot(root, with_hash, **options):
            rows = original_snapshot(root, with_hash, **options)
            if root == self.new and with_hash:
                added.write_bytes(b"late backup file")
            return rows

        with patch.object(tree, "snapshot", side_effect=change_backup_after_destination_snapshot):
            with self.assertRaisesRegex(ValueError, "复核期间"):
                verify_backup_files(config, self.audit, False)
        self.assertEqual(added.read_bytes(), b"late backup file")
        self.assertFalse((self.new / added.name).exists())

    def test_generic_manual_session_receipt_binding(self):
        # The executor and owning agent may differ. Simulate a generic host with
        # manual session selection and no history-list / message-send capability.
        self.helper("run_record", "start", self.old, self.new, "--host", "generic",
                    "--run", self.audit, "--allow-same-volume")
        self.mark_fixture(1)
        self.helper("tree_copy", "copy", self.old, self.new, "--audit", self.copy_audit)
        self.mark_fixture(2)
        self.helper("tree_copy", "verify", self.old, self.new, "--audit", self.copy_audit)
        self.mark_fixture(3)
        self.mark_fixture(4)
        command = self.prepare("auto")
        if os.name == "nt":
            self.run_paste(command)
        else:
            self.command(["/bin/sh", "-c", command])
        self.mark_fixture(5)
        session = "fixture-manually-confirmed-old-session"

        def register():
            token = str(uuid4())
            self.helper("run_record", "mark", "--run", self.audit, "--step", 6, "--status", "waiting",
                        "--request-id", token, "--session-id", session, "--evidence", "synthetic host baseline",
                        "--next-action", "user manually tests the selected session")
            return token

        def report(success):
            return self.helper("run_record", "mark", "--run", self.audit, "--step", 6, "--status", "passed",
                               "--evidence", "fixture host outcome", "--next-action", "final review",
                               success=success)

        first = register()
        self.helper("probe", "--plan", self.plan, "--request-id", str(uuid4()),
                    "--resumed-session-id", session, cwd=self.new)
        report(False)  # Unrelated request, even if generated by the right host.
        self.helper("probe", "--plan", self.plan, "--request-id", first, "--filesystem-only", cwd=self.new)
        report(False)  # Matching request only demonstrates filesystem identity.
        second = register()
        self.helper("probe", "--plan", self.plan, "--request-id", second,
                    "--resumed-session-id", "different-fixture-session", cwd=self.new)
        report(False)
        third = register()
        self.helper("probe", "--plan", self.plan, "--request-id", third,
                    "--resumed-session-id", session, cwd=self.new)
        self.assertEqual(json.loads(report(True))["next_step"], 7)
