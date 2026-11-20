"""Read-only recovery checks shared by the native Windows and APFS suites."""
import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4

if os.name == "nt":
    from migration_ntfs import attributes


class ResumeInspectionChecks:
    @staticmethod
    def source_entry(path, role):
        return {"path": str(path), "role": role}

    def desktop_snapshot(self, sources):
        return {"version": 1, "host_id": "fixture-local", "complete": True,
                "projects": [{"id": "demo", "name": "Demo", "sources": sources}]}

    def namespace_fingerprint(self):
        """Read fixture attributes and bytes without following links or junctions."""
        items = {}
        stack = [self.old.parent, self.new.parent]
        while stack:
            path = stack.pop()
            s = path.lstat()
            if os.name == "nt":
                # Use persisted Win32 attributes, excluding CPython's transient
                # NT kernel bits on newly created fixture entries.
                attrs = attributes(path)
            else:
                attrs = s.st_flags
            marker = (s.st_mode, s.st_ino, s.st_mtime_ns, s.st_size, attrs)
            if path.is_symlink() or path.is_junction():
                value = os.readlink(path)
            elif path.is_dir():
                stack.extend(path.iterdir())
                value = "directory"
            else:
                value = hashlib.sha256(path.read_bytes()).hexdigest()
            items[str(path)] = (marker, value)
        return items

    def diagnostic(self, snapshot, *, mappings=(), records=(), sessions=(), history_complete=False,
                   repeat=False, project_id="demo"):
        value = dict(project_id=project_id, sources=list(mappings), records=[str(p) for p in records],
                     sessions=list(sessions), history_complete=history_complete)
        desktop = self.write_batch_evidence(f"resume-projects-{uuid4().hex}.json", snapshot)
        request = self.write_batch_evidence(f"resume-input-{uuid4().hex}.json", value)
        before = self.namespace_fingerprint()
        results = [json.loads(self.helper("resume_check", "--snapshot", desktop, "--input", request))
                   for _ in range(2 if repeat else 1)]
        self.assertEqual(before, self.namespace_fingerprint(), "Inspection changed the fixture namespace.")
        for result in results:
            self.assertIs(result["read_only"], True)
            self.assertIs(result["cleanup_authorized"], False)
            self.assertIsInstance(result["questions"], list)
            for row in result["rows"]:
                self.assertTrue(row["next_action"])
        if repeat:
            self.assertEqual(results[0], results[1], "Repeated inspection changed its diagnosis.")
        self.inspection_questions = results[0]["questions"]
        return {Path(row["source"]): row for row in results[0]["rows"]}

    def inspected_pair(self, snapshot, **options):
        return self.diagnostic(snapshot, mappings=[{"source": str(self.old), "target": str(self.new)}],
                               **options)[self.old]

    def write_manual_target(self):
        self.new.mkdir()
        sample = next(p for p in self.old.iterdir() if p.is_file())
        (self.new / sample.name).write_bytes(sample.read_bytes())

    def junction_fixture(self, entry, dest):
        if os.name == "nt":
            from _winapi import CreateJunction
            CreateJunction(str(dest), str(entry))
        else:
            entry.symlink_to(dest, target_is_directory=True)

    def verify_legacy_copy(self):
        self.helper("run_record", "start", self.old, self.new, "--run", self.audit, "--host", "codex",
                    "--project-id", "demo")
        for action in ("copy", "verify"):
            self.helper("tree_copy", action, self.old, self.new, "--audit", self.copy_audit)
        for step in (1, 2, 3):
            self.helper("run_record", "mark", "--run", self.audit, "--step", step, "--status", "passed",
                        "--evidence", "fixture", "--next-action", "next")

    def test_resume_manual_copy_not_certified_by_matching_names(self):
        self.write_manual_target()
        snapshot = self.desktop_snapshot([self.source_entry(self.old, "primary")])
        row = self.inspected_pair(snapshot, repeat=True)
        self.assertEqual(row["state"], "verify_existing_copy")

    def test_resume_requests_incomplete_source_inventory_and_missing_targets(self):
        secondary = self.old.parent / "Assets"
        secondary.mkdir()
        snapshot = self.desktop_snapshot([self.source_entry(self.old, "primary"),
                                         self.source_entry(secondary, "secondary")])
        snapshot["complete"] = False
        rows = self.diagnostic(snapshot, repeat=True)
        self.assertEqual(set(rows), {self.old, secondary})
        self.assertTrue(all(row["target"] is None for row in rows.values()))
        self.assertTrue(self.inspection_questions, "The incomplete inventory and targets need confirmation.")

    def test_resume_manual_move_requires_new_evidence(self):
        self.write_manual_target()
        self.old.rename(self.old.with_name("previous-physical-source"))
        snapshot = self.desktop_snapshot([self.source_entry(self.new, "primary")])
        row = self.inspected_pair(snapshot)
        self.assertEqual(row["state"], "manual_move_needs_confirmation")

    def test_resume_correct_link_is_not_old_chat_acceptance(self):
        self.write_manual_target()
        self.old.rename(self.backup)
        self.junction_fixture(self.old, self.new)
        snapshot = self.desktop_snapshot([self.source_entry(self.new, "primary")])
        session = dict(id="old-demo-session", project_id="demo", cwd=str(self.old),
                       history_readable=True, archived=False)
        row = self.inspected_pair(snapshot, sessions=[session], history_complete=True, repeat=True)
        self.assertIn(row["state"], ("check_chats", "blocked"))
        self.assertIs(row["chat"]["observed"][0]["cwd_reaches_target"], True)
        self.assertIs(row["chat"]["observed"][0]["prior_probe"], False)
        self.assertIs(row["chat"]["new_probes_run"], False)

    def test_resume_wrong_and_dangling_links_stop_inspection_progress(self):
        self.write_manual_target()
        self.old.rename(self.backup)
        other = self.new.parent / "unrelated-folder"
        other.mkdir()
        self.junction_fixture(self.old, other)
        snapshot = self.desktop_snapshot([self.source_entry(self.new, "primary")])
        self.assertEqual(self.inspected_pair(snapshot)["state"], "blocked")
        other.rename(other.with_name("unrelated-folder-retained"))
        self.assertEqual(self.inspected_pair(snapshot)["state"], "blocked")

    def test_resume_existing_single_checkpoint_with_shared_secondary(self):
        secondary = self.old.parent / "Shared"
        secondary.mkdir()
        self.verify_legacy_copy()
        snapshot = self.desktop_snapshot([self.source_entry(self.new, "primary"),
                                         self.source_entry(self.old, "secondary"),
                                         self.source_entry(secondary, "secondary")])
        snapshot["projects"].append({"id": "shared-owner", "name": "Shared owner", "sources": [
            self.source_entry(secondary, "primary")]})
        session = dict(id="old-demo-session", project_id="demo", cwd=str(self.old),
                       history_readable=True, archived=False)
        mappings = [{"source": str(self.old), "target": str(self.new)}, {"source": str(secondary)}]
        rows = self.diagnostic(snapshot, mappings=mappings, records=[self.audit],
                               sessions=[session], history_complete=True, repeat=True)
        self.assertEqual(set(rows), {self.old, secondary})
        self.assertEqual(rows[self.old]["state"], "review_source_entries")
        self.assertEqual(rows[secondary]["state"], "choose_target")
        # A new directory at the same target path cannot inherit old copy evidence.
        self.new.rename(self.new.with_name("prior-verified-target"))
        self.write_manual_target()
        stale = self.diagnostic(snapshot, mappings=mappings, records=[self.audit])[self.old]
        self.assertIn(stale["state"], ("blocked", "verify_existing_copy"))

    def test_resume_working_link_with_historical_receipt_and_no_visible_backup(self):
        self.verify_legacy_copy()
        self.helper("cutover", "prepare", "--source", self.old, "--target", self.new,
                    "--audit", self.audit, "--copy-audit", self.copy_audit, "--mode", "auto")
        self.helper("cutover", "apply", "--plan", self.plan, "--mode", "auto")
        self.backup.rename(self.old.parent / "backup-retained-elsewhere")
        snapshot = self.desktop_snapshot([self.source_entry(self.new, "primary")])
        row = self.inspected_pair(snapshot, records=[self.audit], repeat=True)
        self.assertEqual(row["filesystem"]["source"]["kind"], "link")
        self.assertEqual(Path(row["filesystem"]["source"]["resolved"]), self.new)
        self.assertEqual(row["filesystem"]["backup"]["kind"], "missing")
        self.assertEqual(row["record"]["verified_link_phase"], "linked_no_backup")
        self.assertEqual(row["state"], "check_chats")

    def test_resume_partially_completed_batch_keeps_remaining_source_separate(self):
        self.setup_batch()
        self.batch_command("stage")
        self.batch_command("prepare", "--mode", "auto")
        first = self.entity_in(self.old)
        self.helper("cutover", "apply", "--plan", Path(first["run"]) / "cutover-plan.json",
                    "--mode", "auto")
        snapshot = self.load_evidence(self.source_snapshot)
        mappings = self.load_evidence(self.selection)["sources"]
        rows = self.diagnostic(snapshot, mappings=mappings, records=[self.batch], repeat=True)
        self.assertEqual(rows[self.old]["state"], "switch_source")
        self.assertEqual(rows[self.shared]["state"], "build_link")
        self.assertIn(self.untouched, rows)
