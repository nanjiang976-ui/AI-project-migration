"""Behavior shared by the native Windows and macOS fixture suites."""
import json


class RecordChecks:
    def mark_fixture(self, step, status="passed"):
        return json.loads(self.helper("run_record", "mark", "--run", self.audit, "--step", step,
                                      "--status", status, "--evidence", "synthetic fixture",
                                      "--next-action", "continue fixture verification"))

    def assert_cleanup(self, report, eligible):
        self.assertEqual(next(iter(report)), "cleanup_notice")
        notice = report["cleanup_notice"]
        self.assertIn("请勿删除", notice["warning"])
        self.assertIn(str(self.old), notice["warning"])
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
