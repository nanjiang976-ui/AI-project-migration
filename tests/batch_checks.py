"""Batch migration regressions. Projects and chats are synthetic host readbacks."""
import base64
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

from cutover_checks import PAUSE_FIXTURE, SCRIPTS, capture_stalled_progress


class ProjectBatchChecks:
    def batch_fixture(self):
        # The host snapshots and all file mutations belong to this test's
        # existing dedicated scratch directories.
        self.primary = self.old
        self.shared = self.old.parent / "SharedLibrary"
        self.untouched = self.old.parent / "UnselectedNotes"
        self.shared.mkdir()
        self.untouched.mkdir()
        (self.shared / "shared.txt").write_bytes(b"shared bytes")
        (self.untouched / "notes.txt").write_bytes(b"notes bytes")
        self.shared_new = self.new.parent / "SharedTarget"
        self.batch = self.audit.with_name("batch [2]'资料 $(3)")
        self.batch_plan = self.batch / "batch.json"
        self.source_snapshot = self.write_batch_evidence("before.json", {
            "version": 1, "host_id": "fixture-local", "complete": True,
            "projects": [
                {"id": "demo", "name": "Demo", "sources": [
                    self.folders(self.primary, "primary"),
                    self.folders(self.shared, "secondary"),
                    self.folders(self.untouched, "secondary"),
                ]},
                {"id": "library", "name": "Library", "sources": [
                    self.folders(self.shared, "primary"),
                ]},
            ],
        })
        self.selection = self.write_batch_evidence("selection.json", {
            "sources": [
                self.choice(self.primary, self.new, ["demo"]),
                self.choice(self.shared, self.shared_new, ["demo", "library"]),
            ],
            "allow_same_volume": False,
        })

    @staticmethod
    def folders(path, role):
        return {"path": str(path), "role": role}

    @staticmethod
    def choice(source, target, projects):
        return {"source": str(source), "target": str(target), "project_ids": projects}

    def write_batch_evidence(self, name, data):
        path = self.new.parent / name
        with path.open("x", encoding="utf-8") as out:
            json.dump(data, out, ensure_ascii=False)
        return path

    @staticmethod
    def load_evidence(path):
        return json.loads(path.read_text(encoding="utf-8"))

    def batch_command(self, action, *args, **options):
        return self.helper("project_batch", action, "--batch", self.batch, *args, **options)

    def setup_batch(self, *, git_fixture=False):
        self.batch_fixture()
        if git_fixture:
            tracked = next(path for path in self.primary.iterdir() if path.is_file())
            self.git(self.primary, "init", "-q")
            self.git(self.primary, "add", tracked.name)
            self.git(self.primary, "commit", "-qm", "fixture")
            self.fixture_worktree = self.primary.parent / "IndependentWorktree"
            self.git(self.primary, "worktree", "add", "--detach", self.fixture_worktree, "HEAD")
            (self.fixture_worktree / tracked.name).write_bytes(b"uncommitted worktree change")
            (self.fixture_worktree / "untracked.txt").write_bytes(b"untracked worktree data")
        self.batch_command("create", "--snapshot", self.source_snapshot,
                           "--selection", self.selection)
        self.synthetic_history("before")

    def entity_in(self, source):
        roots = self.load_evidence(self.batch_plan)["roots"]
        return next(r for r in roots if Path(r["source"]) == source)

    def linked_physical(self, old, target):
        return (old.is_junction() if os.name == "nt" else old.is_symlink()) and old.samefile(target)

    def verify_after_for(self, *project_ids, extra_source=None):
        expected = self.load_evidence(self.batch_plan)["projects"]
        snapshot = self.load_evidence(self.source_snapshot)
        for project in snapshot["projects"]:
            if project["id"] in project_ids:
                project["sources"] = deepcopy(next(row["after"] for row in expected
                                                   if row["id"] == project["id"]))
        if extra_source is not None:
            row = next(x for x in snapshot["projects"] if x["id"] == project_ids[0])
            row["sources"].append(self.folders(extra_source, "secondary"))
        return self.write_batch_evidence(f"readback-{uuid4().hex}.json", snapshot)

    def accept_project(self, project, readback, *, success=True):
        return self.batch_command("confirm", "--snapshot", readback, "--project-id", project,
                                  success=success)

    def current_batch_status(self):
        return json.loads(self.batch_command("status"))

    def assert_primary_and_shared_selections(self, plan):
        self.assertEqual(len(plan["roots"]), 2)
        self.assertEqual(set(self.entity_in(self.shared)["project_ids"]), {"demo", "library"})
        by_project = {row["id"]: row for row in plan["projects"]}
        self.assertEqual(by_project["demo"]["after"], [
            self.folders(self.new, "primary"),
            self.folders(self.shared_new, "secondary"),
            self.folders(self.untouched, "secondary"),
        ])
        self.assertEqual(by_project["library"]["after"], [self.folders(self.shared_new, "primary")])

    def test_batch_shared_scope_and_configuration_retries(self):
        self.setup_batch(git_fixture=True)
        plan = self.load_evidence(self.batch_plan)
        self.assert_primary_and_shared_selections(plan)
        self.batch_command("stage")
        self.assertEqual((self.shared_new / "shared.txt").read_bytes(), b"shared bytes")
        self.assertFalse(self.linked_physical(self.primary, self.new))
        self.assertFalse(self.linked_physical(self.shared, self.shared_new))
        self.batch_command("prepare", "--mode", "auto")
        self.batch_command("link", "--mode", "auto")
        self.assertTrue(self.linked_physical(self.primary, self.new))
        self.assertTrue(self.linked_physical(self.shared, self.shared_new))
        common_dir = self.git(self.fixture_worktree, "rev-parse", "--path-format=absolute",
                              "--git-common-dir")
        self.assertTrue(Path(common_dir).samefile(self.new / ".git"))
        self.assertEqual((self.fixture_worktree / "untracked.txt").read_bytes(),
                         b"untracked worktree data")
        self.assertTrue(self.untouched.is_dir())
        self.assertEqual((self.untouched / "notes.txt").read_bytes(), b"notes bytes")
        before_completion = self.verify_after_for("demo")
        self.accept_project("demo", before_completion)
        pending = json.loads(self.accept_project("library", before_completion))
        self.assertEqual(pending["phase"], "needs_edit")
        self.assertIsNone(pending["receipt"])
        status = {row["id"]: row for row in self.current_batch_status()["projects"]}
        self.assertTrue(status["demo"]["confirmed"])
        self.assertFalse(status["library"]["confirmed"])
        changed = self.verify_after_for("demo", extra_source=self.new.parent)
        self.accept_project("demo", changed, success=False)
        fresh = self.verify_after_for("demo", "library")
        demoted = self.load_evidence(fresh)
        wrong_roles = demoted["projects"][0]["sources"]
        wrong_roles[0]["role"], wrong_roles[1]["role"] = wrong_roles[1]["role"], wrong_roles[0]["role"]
        self.accept_project("demo", self.write_batch_evidence("wrong-roles.json", demoted), success=False)
        self.accept_project("demo", fresh)
        self.accept_project("library", fresh)
        verified = self.current_batch_status()
        self.assertTrue(all(row["confirmed"] for row in verified["projects"]))
        # Each physical source gets one child plan and one managed copy owner,
        # even when both project lists reference it in different roles.
        for source in (self.primary, self.shared):
            run = Path(self.entity_in(source)["run"])
            self.assertTrue((run / "batch-owner.json").is_file())
            self.assertEqual(len(list(run.rglob("owner.json"))), 1)
        self.assertEqual(len(list(self.batch.rglob("cutover-plan.json"))), 2)

    def test_batch_rejects_incomplete_inventory_and_conflicting_paths(self):
        self.batch_fixture()
        base = self.load_evidence(self.source_snapshot)
        choices = self.load_evidence(self.selection)
        incomplete = deepcopy(base)
        incomplete["complete"] = False
        overlap = deepcopy(choices)
        overlap["sources"][1]["target"] = str(self.new / "nested")
        collide = deepcopy(choices)
        collide["sources"][1]["target"] = str(self.new)
        refused = deepcopy(choices)
        refused["sources"][1]["project_ids"] = ["demo"]
        for label, snapshot, selection in (
            ("incomplete", incomplete, choices),
            ("overlap", base, overlap),
            ("collision", base, collide),
            ("refused", base, refused),
        ):
            with self.subTest(label=label):
                self.batch_command("create", "--snapshot",
                                   self.write_batch_evidence(label + "-inventory.json", snapshot),
                                   "--selection", self.write_batch_evidence(label + "-choices.json", selection),
                                   success=False)
                self.assertFalse(self.batch.exists())
                self.assertFalse(self.new.exists())
                self.assertFalse(self.shared_new.exists())
                self.assertTrue(self.primary.is_dir() and self.shared.is_dir())

    def test_batch_bad_following_copy_keeps_source_namespace(self):
        self.setup_batch()
        link = self.shared / "outside-directory-link"
        if os.name == "nt":
            from _winapi import CreateJunction
            CreateJunction(str(self.untouched), str(link))
        else:
            link.symlink_to(self.untouched, target_is_directory=True)
        self.batch_command("stage", success=False)
        self.batch_command("prepare", "--mode", "auto", success=False)
        self.assertEqual((link / "notes.txt").read_bytes(), b"notes bytes")
        self.assertFalse(self.primary.with_name(self.primary.name + "_迁移备份").exists())
        self.assertFalse(self.shared.with_name(self.shared.name + "_迁移备份").exists())
        self.assertFalse(self.linked_physical(self.primary, self.new))
        self.assertFalse(self.linked_physical(self.shared, self.shared_new))
        status = self.current_batch_status()
        self.assertFalse(any(row["confirmed"] for row in status["projects"]))

    def test_batch_reuses_first_link_after_interruption(self):
        self.setup_batch()
        self.batch_command("stage")
        self.batch_command("prepare", "--mode", "auto")
        primary = self.entity_in(self.primary)
        shared = self.entity_in(self.shared)
        self.helper("cutover", "apply", "--plan", Path(primary["run"]) / "cutover-plan.json",
                    "--mode", "auto")
        first_link_id = self.primary.lstat().st_ino
        self.assertTrue(self.linked_physical(self.primary, self.new))
        # Simulate a conflicting backup directory on the second operation.
        conflict = Path(shared["backup"])
        conflict.mkdir()
        (conflict / "sentinel").write_bytes(b"unrelated")
        self.batch_command("link", "--mode", "auto", "--progress", success=False)
        self.assertEqual((conflict / "sentinel").read_bytes(), b"unrelated")
        self.assertEqual(self.primary.lstat().st_ino, first_link_id)
        self.assertTrue(self.shared.is_dir())
        self.assertFalse(self.linked_physical(self.shared, self.shared_new))
        diagnostic, = self.batch.glob("cutover-diagnostic-*.json")
        failure = self.load_evidence(diagnostic)
        before_retry = {row["source"]: row for row in failure["status"]["roots"]}
        self.assertEqual(failure["scope"], "batch")
        self.assertEqual(failure["source"], str(self.shared))
        self.assertEqual(before_retry[str(self.primary)]["phase"], "linked")
        self.assertIn("error", before_retry[str(self.shared)])
        conflict.rename(self.shared.parent / "preserved-conflict")
        self.batch_command("link", "--mode", "auto", "--progress")
        self.assertEqual(self.primary.lstat().st_ino, first_link_id)
        self.assertTrue(self.linked_physical(self.shared, self.shared_new))
        self.batch_command("link", "--mode", "auto")
        self.assertEqual(self.primary.lstat().st_ino, first_link_id)
        self.assertTrue(all(row["phase"] == "linked" for row in self.current_batch_status()["roots"]))

    def test_batch_progress_streams_the_child_before_the_next_source_runs(self):
        self.setup_batch()
        self.batch_command("stage")
        command = json.loads(self.batch_command("prepare", "--mode", "auto"))["command"]
        self.assertIn("--progress", command)
        waiting, resume = self.batch / "waiting", self.batch / "resume"
        code = PAUSE_FIXTURE + """
import runpy
import subprocess
import sys
from unittest.mock import patch

script, waiting, resume, *args = sys.argv[1:]
sys.path.insert(0, str(Path(script).parent))
run = subprocess.run
applies = 0

def pause_between_children(argv, *args, **options):
    global applies
    if any(Path(value).name == 'cutover.py' for value in argv) and 'apply' in argv:
        applies += 1
        if applies == 2:
            pause_until_released(waiting, resume)
    return run(argv, *args, **options)

sys.argv = [script, *args]
with patch.object(subprocess, 'run', pause_between_children):
    runpy.run_path(script, run_name='__main__')
"""
        output, progress = capture_stalled_progress(
            self, [sys.executable, "-B", "-X", "utf8", "-c", code, SCRIPTS / "project_batch.py",
                   waiting, resume, "link", "--batch", self.batch, "--mode", "auto", "--progress"],
            waiting, resume, "进度：旧入口已通过核验",
        )
        self.assertIn("进度：批次旧入口已通过核验", progress)
        status = json.loads(output)
        self.assertTrue(all(row["phase"] == "linked" for row in status["roots"]))
        self.assertEqual(status, json.loads(self.batch_command("link", "--mode", "auto")))

    def test_batch_progress_diagnoses_preflight_failure_before_any_rename(self):
        self.setup_batch()
        self.batch_command("stage")
        self.batch_command("prepare", "--mode", "auto")
        (self.shared_new / "shared.txt").write_bytes(b"late target edit")
        self.batch_command("link", "--mode", "auto", "--progress", success=False)
        for original, target in ((self.primary, self.new), (self.shared, self.shared_new)):
            self.assertFalse(self.linked_physical(original, target))
            self.assertTrue(original.is_dir())
            self.assertFalse(Path(self.entity_in(original)["backup"]).exists())
        report, = self.batch.glob("cutover-diagnostic-*.json")
        diagnostic = self.load_evidence(report)
        self.assertEqual((diagnostic["scope"], diagnostic["stage"], diagnostic["source"]),
                         ("batch", "compare-target", str(self.shared)))
        self.assertIn("shared.txt", diagnostic["error"])
        self.assertEqual({row["phase"] for row in diagnostic["status"]["roots"]}, {"ready"})
    def test_batch_preserves_existing_primary_when_moving_shared_secondary(self):
        self.batch_fixture()
        self.new.mkdir()
        (self.new / "already-moved.txt").write_bytes(b"already moved")
        config = self.load_evidence(self.source_snapshot)
        config["projects"][0]["sources"][0] = self.folders(self.new, "primary")
        self.source_snapshot = self.write_batch_evidence("primary-already-selected.json", config)
        choice = self.choice(self.shared, self.shared_new, ["demo", "library"])
        self.selection = self.write_batch_evidence("shared-only.json", {
            "sources": [choice], "allow_same_volume": False,
        })
        self.batch_command("create", "--snapshot", self.source_snapshot,
                           "--selection", self.selection)
        self.synthetic_history("before")
        after = self.load_evidence(self.batch_plan)
        self.assertEqual(len(after["roots"]), 1)
        by_project = {row["id"]: row for row in after["projects"]}
        self.assertEqual(by_project["demo"]["after"], [
            self.folders(self.new, "primary"),
            self.folders(self.shared_new, "secondary"),
            self.folders(self.untouched, "secondary"),
        ])
        self.batch_command("stage")
        offline = json.loads(self.batch_command("prepare", "--mode", "manual"))["command"]
        self.batch_command("link", "--mode", "manual", success=False)
        self.assertFalse(self.linked_physical(self.shared, self.shared_new))
        entry = self.entity_in(self.shared)
        self.shared.rename(Path(entry["backup"]))
        if os.name == "nt":
            encoded = base64.b64encode(offline.encode("utf-16le")).decode("ascii")
            self.command(["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded])
        else:
            self.command(["/bin/sh", "-c", offline])
        self.assertTrue(self.linked_physical(self.shared, self.shared_new))
        readback = self.verify_after_for("demo", "library")
        from_append_then_remove = self.load_evidence(readback)
        appended_sources = from_append_then_remove["projects"][0]["sources"]
        appended_sources.append(appended_sources.pop(1))
        reordered = self.write_batch_evidence("appended-secondary.json", from_append_then_remove)
        self.accept_project("demo", reordered)
        self.accept_project("library", reordered)
        self.assertEqual((self.new / "already-moved.txt").read_bytes(), b"already moved")
        self.assertTrue(self.primary.is_dir())

    def evidence(self, action, *args, **options):
        return self.helper("batch_evidence", action, "--batch", self.batch_plan, *args, **options)

    def synthetic_history(self, phase):
        # Retain original logical working-directory values through cutover. The
        # same test process then uses those paths to run real filesystem probes.
        baseline = self.load_evidence(self.source_snapshot)
        projects = []
        for project in baseline["projects"]:
            project_id = project["id"]
            primary = next(source["path"] for source in project["sources"] if source["role"] == "primary")
            session = {"id": project_id + "-old", "cwd": primary, "history_readable": True, "archived": False}
            projects.append({"id": project_id, "complete": True, "sessions": [session]})
        value = {"host_id": baseline["host_id"], "projects": projects}
        return self.evidence("history", "--phase", phase, "--input",
                             self.write_batch_evidence(phase + "-chats.json", value))

    def register_probe(self, project, source, session, request, *, success=True):
        return self.evidence("register", "--project-id", project, "--source-id",
                             self.entity_in(source)["id"], "--session-id", session,
                             "--request-id", request, success=success)

    def attest_probe(self, project, session, request, *, success=True):
        result = self.write_batch_evidence(f"chat-attestation-{uuid4().hex}.json", {
            "host_id": "fixture-local", "project_id": project, "session_id": session,
            "request_id": request,
        })
        return self.evidence("attest", "--request-id", request, "--input", result, success=success)

    def attached_probe(self, request, cwd, *, success=True):
        return self.helper("probe", "--batch", self.batch_plan, "--request-id", request,
                           cwd=cwd, success=success)

    def record_new_chat(self, project, main, *readable_sources):
        return self.evidence("new-chat", "--input",
                             self.write_batch_evidence(project + "-new-chat.json", {
                                 "host_id": "fixture-local", "project_id": project,
                                 "session_id": project + "-new", "cwd": str(main),
                                 "readable_sources": [str(x) for x in readable_sources],
                             }))

    def verify_batch_backup(self, source):
        root = self.entity_in(source)
        scripts = Path(__file__).resolve().parents[1] / "skills" / "ai-project-migration" / "scripts"
        cmd = [sys.executable, "-B", str(scripts / "tree_copy.py"), "verify", root["backup"],
               root["target"], "--audit", str(Path(root["run"]) / "backup-check")]
        process = subprocess.run(cmd, cwd=self.new.parent, env=self.env, capture_output=True,
                                 stdin=subprocess.DEVNULL, timeout=120, text=True, encoding="utf-8")
        self.assertIn(process.returncode, (0, 2), process.stderr)
        result = self.load_evidence(Path(json.loads(process.stdout)["report"]))
        self.assertIn(result["status"], ("passed", "differences"))
        self.assertLessEqual(set(result["differences"]), {"mtime_ns"}, result)
        self.assertLessEqual(set(result["differences"].get("mtime_ns", [])), {"."}, result)

    def batch_cleanup_status(self, readback):
        report = json.loads(self.evidence("status", "--snapshot", readback))
        return {row["source_id"]: row for row in report["roots"]}

    def test_batch_moves_only_one_unshared_secondary_from_old_primary_chat(self):
        self.batch_fixture()
        config = self.load_evidence(self.source_snapshot)
        config["projects"] = [project for project in config["projects"] if project["id"] == "demo"]
        self.source_snapshot = self.write_batch_evidence("unshared-before.json", config)
        self.selection = self.write_batch_evidence("unshared-secondary-only.json", {
            "sources": [self.choice(self.shared, self.shared_new, ["demo"])],
            "allow_same_volume": False,
        })
        primary_files = {path.name: path.read_bytes() for path in self.primary.iterdir()}
        primary_id, secondary_id, untouched_id = (path.stat().st_ino for path in
                                                  (self.primary, self.shared, self.untouched))

        self.batch_command("create", "--snapshot", self.source_snapshot, "--selection", self.selection)
        self.synthetic_history("before")
        plan = self.load_evidence(self.batch_plan)
        self.assertEqual([row["source"] for row in plan["roots"]], [str(self.shared)])
        self.assertEqual([row["id"] for row in plan["projects"]], ["demo"])
        expected_sources = [self.folders(self.primary, "primary"), self.folders(self.shared_new, "secondary"),
                            self.folders(self.untouched, "secondary")]
        self.assertEqual(plan["projects"][0]["after"], expected_sources)

        self.batch_command("stage")
        self.batch_command("prepare", "--mode", "auto")
        self.batch_command("link", "--mode", "auto")
        self.assertTrue(self.linked_physical(self.shared, self.shared_new))
        readback = self.verify_after_for("demo")
        confirmation = json.loads(self.accept_project("demo", readback))
        self.assertEqual(confirmation["phase"], "confirmed")
        self.assertEqual(confirmation["actual"], expected_sources)
        self.synthetic_history("after")
        self.record_new_chat("demo", self.primary, self.primary, self.shared_new, self.untouched)

        request = str(uuid4())
        self.register_probe("demo", self.shared, "demo-old", request)
        receipt = json.loads(self.attached_probe(request, self.primary))
        self.assertEqual((receipt["mode"], receipt["cwd"], receipt["old_entry"], receipt["target"]),
                         ("attached_source", str(self.primary), str(self.shared), str(self.shared_new)))
        for flag in ("old_write_target_read", "target_write_old_read", "same_file", "backup_isolated", "probe_removed"):
            self.assertIs(receipt[flag], True)
        root = self.entity_in(self.shared)
        backup = Path(root["backup"])
        self.verify_batch_backup(self.shared)
        self.assertFalse(self.batch_cleanup_status(readback)[root["id"]]["eligible"])
        self.attest_probe("demo", "unknown-session", request, success=False)
        self.attest_probe("demo", "demo-old", request)
        final = self.batch_cleanup_status(readback)[root["id"]]
        self.assertTrue(final["eligible"], final)
        self.assertEqual(final["projects"], [{"project_id": "demo", "checked_old_chats": 1,
                                             "tested": ["demo-old"], "request_id": request}])

        self.assertEqual(self.primary.lstat().st_ino, primary_id)
        self.assertEqual({path.name: path.read_bytes() for path in self.primary.iterdir()}, primary_files)
        self.assertFalse(os.path.lexists(self.new) or os.path.lexists(self.backup))
        self.assertEqual(self.untouched.lstat().st_ino, untouched_id)
        self.assertEqual((self.untouched / "notes.txt").read_bytes(), b"notes bytes")
        self.assertEqual(backup.lstat().st_ino, secondary_id)
        self.assertFalse(backup.samefile(self.shared_new))
        for path in (self.shared, self.shared_new, backup):
            self.assertEqual({item.name: item.read_bytes() for item in path.iterdir()}, {"shared.txt": b"shared bytes"})

    def test_batch_shared_probe_requires_each_project_and_old_session(self):
        self.setup_batch()
        self.batch_command("stage")
        self.batch_command("prepare", "--mode", "auto")
        self.batch_command("link", "--mode", "auto")
        readback = self.verify_after_for("demo", "library")
        self.accept_project("demo", readback)
        self.accept_project("library", readback)
        self.synthetic_history("after")
        self.record_new_chat("demo", self.new, self.new, self.shared_new, self.untouched)
        self.record_new_chat("library", self.shared_new, self.shared_new)

        self.register_probe("demo", self.shared, "library-old", str(uuid4()), success=False)
        request = str(uuid4())
        self.register_probe("demo", self.shared, "demo-old", request)
        self.attached_probe(request, self.new.parent, success=False)
        self.helper("probe", "--batch", self.batch_plan, "--request-id", request,
                    "--filesystem-only", cwd=self.primary, success=False)
        self.attached_probe(request, self.primary)
        self.attest_probe("library", "library-old", request, success=False)
        self.attest_probe("demo", "demo-old", request)
        self.attached_probe(request, self.primary, success=False)  # Request is not reusable.
        self.assertEqual([p.name for p in self.shared_new.iterdir()], ["shared.txt"])

        primary_request = str(uuid4())
        self.register_probe("demo", self.primary, "demo-old", primary_request)
        self.attached_probe(primary_request, self.primary)
        self.attest_probe("demo", "demo-old", primary_request)
        self.verify_batch_backup(self.primary)
        self.verify_batch_backup(self.shared)
        # The shared-source probe from Demo cannot attest an older Library chat.
        primary_id = self.entity_in(self.primary)["id"]
        shared_id = self.entity_in(self.shared)["id"]
        initial = self.batch_cleanup_status(readback)
        self.assertTrue(initial[primary_id]["eligible"], initial[primary_id])
        self.assertFalse(initial[shared_id]["eligible"], initial[shared_id])
        reassurance = "请放心，保留这个入口几乎不占用硬盘空间。"
        self.assertIn(reassurance, initial[shared_id]["warning"])

        library_request = str(uuid4())
        self.register_probe("library", self.shared, "library-old", library_request)
        self.attached_probe(library_request, self.shared)
        self.attest_probe("library", "library-old", library_request)
        self.verify_batch_backup(self.shared)
        complete = self.batch_cleanup_status(readback)
        self.assertTrue(complete[shared_id]["eligible"], complete[shared_id])
        self.assertIn(reassurance, complete[shared_id]["warning"])
        self.assertEqual([p.name for p in self.shared_new.iterdir()], ["shared.txt"])
        changed_scope = self.load_evidence(readback)
        changed_scope["projects"].append({"id": "new-reference", "name": "NewReference", "sources": [
            self.folders(self.shared_new, "primary"),
        ]})
        new_scope = self.write_batch_evidence("unconfirmed-new-reference.json", changed_scope)
        self.assertFalse(self.batch_cleanup_status(new_scope)[shared_id]["eligible"])
        (self.shared_new / "shared.txt").write_bytes(b"edited since backup comparison")
        after_change = self.batch_cleanup_status(readback)
        self.assertTrue(after_change[primary_id]["eligible"], after_change[primary_id])
        self.assertFalse(after_change[shared_id]["eligible"], after_change[shared_id])
