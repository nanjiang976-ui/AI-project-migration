"""Materialize approved dependency hardlinks while keeping source and target independent."""
from copy import deepcopy
import json
import os
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import tree_copy


class DependencyHardlinkChecks:
    def dependency_hardlink_fixture(self, root=None):
        root = root or self.old
        store = self.left / ("dependency-store-" + uuid4().hex[:8])
        store.mkdir()
        external = store / "package.js"
        external.write_bytes(b"package bytes\n")
        first = root / "node_modules" / ".pnpm" / "package@1" / "node_modules" / "package" / "index.js"
        second = root / "nested" / "node_modules" / "same-package.js"
        first.parent.mkdir(parents=True)
        second.parent.mkdir(parents=True)
        os.link(external, first)
        os.link(external, second)
        return external, first.relative_to(root), second.relative_to(root)

    def start_materialized_dependency_run(self):
        self.helper("run_record", "start", self.old, self.new, "--host", "generic",
                    "--run", self.audit, "--allow-same-volume", "--materialize-dependency-hardlinks")
        self.helper("git_check", "baseline", self.old, "--out", self.audit / "git-before.json")

    def test_dependency_external_hardlinks_default_copy_rejects_before_hashing(self):
        external, first, _ = self.dependency_hardlink_fixture()
        self.audit.mkdir()
        tree_copy.prepare_audit(self.copy_audit, self.old)
        with patch.object(tree_copy, "read_hash", side_effect=AssertionError("payload hashing started")) as hashed:
            with self.assertRaises(ValueError):
                tree_copy.copy(self.old, self.new, self.copy_audit, {"run": "default-hardlink-preflight"})
        hashed.assert_not_called()
        self.assertFalse(os.path.lexists(self.new))
        self.assertTrue(external.samefile(self.old / first))

    def test_dependency_external_hardlinks_default_verify_rejects_before_hashing(self):
        self.dependency_hardlink_fixture()
        self.audit.mkdir()
        self.new.mkdir()
        tree_copy.prepare_audit(self.copy_audit, self.old)
        with patch.object(tree_copy, "read_hash", side_effect=AssertionError("payload hashing started")) as hashed:
            with self.assertRaises(ValueError):
                tree_copy.verify(self.old, self.new, self.copy_audit, {"run": "default-verify-preflight"})
        hashed.assert_not_called()

    def test_dependency_materialization_preserves_internal_groups_and_source_independence(self):
        external, first, second = self.dependency_hardlink_fixture()
        os.link(self.old / "sample.txt", self.old / "ordinary-internal-alias.txt")
        self.start_materialized_dependency_run()
        self.copy_cli("copy")
        report = self.copy_cli("verify")
        self.assertEqual(report["differences"], {})
        evidence = report["dependency_hardlink_materialization"]
        self.assertEqual(evidence["scope"], "node_modules")
        self.assertTrue(evidence["source_external_groups"])
        self.assertTrue(evidence["source_external_entries"])
        self.assertEqual(evidence["destination_external_groups"], 0)
        copied = self.new / first
        self.assertTrue(copied.samefile(self.new / second))
        self.assertEqual(copied.stat().st_nlink, 2)
        self.assertFalse(copied.samefile(external))
        self.assertTrue((self.new / "sample.txt").samefile(self.new / "ordinary-internal-alias.txt"))
        self.assertEqual((self.new / "sample.txt").stat().st_nlink, 2)
        external.write_bytes(b"new package store bytes")
        self.assertEqual((self.old / first).read_bytes(), b"new package store bytes")
        self.assertEqual(copied.read_bytes(), b"package bytes\n")
        copied.write_bytes(b"new target package bytes")
        self.assertEqual((self.new / second).read_bytes(), b"new target package bytes")
        self.assertEqual(external.read_bytes(), b"new package store bytes")

    def test_dependency_materialization_rejects_source_group_crossing_allowed_scope(self):
        external, _, _ = self.dependency_hardlink_fixture()
        outside_scope = self.old / "application.js"
        os.link(external, outside_scope)
        self.audit.mkdir()
        tree_copy.prepare_audit(self.copy_audit, self.old)
        with patch.object(tree_copy, "read_hash", side_effect=AssertionError("payload hashing started")) as hashed:
            with self.assertRaises(ValueError):
                tree_copy.copy(self.old, self.new, self.copy_audit, {"run": "scope-boundary"},
                               materialize_dependency_hardlinks=True)
        hashed.assert_not_called()
        self.assertFalse(os.path.lexists(self.new))
        self.assertTrue(outside_scope.samefile(external))

    def test_dependency_materialization_and_pnpm_junctions_share_the_full_copy_chain(self):
        self.pnpm_fixture()
        external, first, second = self.dependency_hardlink_fixture()
        self.start_materialized_dependency_run()
        self.copy_cli("copy")
        self.copy_cli("verify")
        self.assert_pnpm_links(self.new)
        self.assertTrue((self.new / first).samefile(self.new / second))
        self.assertFalse((self.new / first).samefile(external))
        self.prepare("auto")
        self.apply("auto")
        self.assertEqual(self.phase(), "linked")
        self.assert_pnpm_links(self.new)
        self.assertEqual(self.command_for_final_comparison(), {})

    def test_dependency_materialization_rejects_similar_directory_name_outside_scope(self):
        external = self.left / "external-source-file.js"
        external.write_bytes(b"outside allowed scope")
        misleading = self.old / "not_node_modules" / "index.js"
        misleading.parent.mkdir()
        os.link(external, misleading)
        self.audit.mkdir()
        tree_copy.prepare_audit(self.copy_audit, self.old)
        with patch.object(tree_copy, "read_hash", side_effect=AssertionError("payload hashing started")) as hashed:
            with self.assertRaises(ValueError):
                tree_copy.copy(self.old, self.new, self.copy_audit, {"run": "similar-scope-name"},
                               materialize_dependency_hardlinks=True)
        hashed.assert_not_called()
        self.assertFalse(os.path.lexists(self.new))

    def test_dependency_materialization_rejects_target_links_outside_candidate(self):
        _, first, _ = self.dependency_hardlink_fixture()
        self.start_materialized_dependency_run()
        self.copy_cli("copy")
        self.copy_cli("verify")
        target = self.new / first
        external = self.right / "outside-target-alias.txt"
        os.link(target, external)
        with patch.object(tree_copy, "read_hash", side_effect=AssertionError("payload hashing started")) as hashed:
            with self.assertRaises(ValueError):
                tree_copy.verify(self.old, self.new, self.copy_audit, {"run": "target-outside-link"},
                                 materialize_dependency_hardlinks=True)
        hashed.assert_not_called()
        self.helper("tree_copy", "copy", self.old, self.new, "--audit", self.copy_audit, success=False)
        self.assertTrue(external.samefile(target))
        self.assertEqual(external.read_bytes(), b"package bytes\n")

    def test_dependency_materialization_cli_requires_bound_run_and_matching_opt_in(self):
        self.dependency_hardlink_fixture()
        self.helper("tree_copy", "copy", self.old, self.new, "--audit", self.right / "unbound-copy-audit",
                    "--materialize-dependency-hardlinks", success=False)
        self.assertFalse(os.path.lexists(self.new))
        self.helper("run_record", "start", self.old, self.new, "--host", "generic",
                    "--run", self.audit, "--allow-same-volume")
        self.helper("tree_copy", "copy", self.old, self.new, "--audit", self.copy_audit,
                    "--materialize-dependency-hardlinks", success=False)
        self.assertFalse(os.path.lexists(self.new))

    def test_dependency_materialization_rejects_non_boolean_run_policy(self):
        self.dependency_hardlink_fixture()
        self.start_materialized_dependency_run()
        config_path = self.audit / "run.json"
        baseline = json.loads(config_path.read_text(encoding="utf-8"))
        for value in ("true", 1, None, []):
            with self.subTest(value=value):
                altered = {**baseline, "materialize_dependency_hardlinks": value}
                config_path.write_text(json.dumps(altered), encoding="utf-8")
                self.helper("tree_copy", "copy", self.old, self.new, "--audit", self.copy_audit, success=False)
                self.assertFalse(os.path.lexists(self.new))

    def test_dependency_materialization_owner_flag_tampering_blocks_resume_and_cutover(self):
        self.dependency_hardlink_fixture()
        self.start_materialized_dependency_run()
        self.copy_cli("copy")
        self.copy_cli("verify")
        owner_path = self.copy_audit / "owner.json"
        owner = json.loads(owner_path.read_text(encoding="utf-8"))
        self.assertIs(owner["materialize_dependency_hardlinks"], True)
        owner["materialize_dependency_hardlinks"] = False
        owner_path.write_text(json.dumps(owner), encoding="utf-8")
        self.helper("tree_copy", "copy", self.old, self.new, "--audit", self.copy_audit, success=False)
        self.prepare("auto", success=False)
        self.assertFalse(os.path.lexists(self.backup))

    def test_dependency_materialization_verify_receipt_flag_tampering_blocks_cutover(self):
        self.dependency_hardlink_fixture()
        self.start_materialized_dependency_run()
        self.copy_cli("copy")
        report = self.copy_cli("verify")
        receipt = self.copy_audit / (report["run"] + "-verify.json")
        self.assertIs(report["materialize_dependency_hardlinks"], True)
        report["materialize_dependency_hardlinks"] = False
        receipt.write_text(json.dumps(report), encoding="utf-8")
        self.prepare("auto", success=False)
        self.assertFalse(os.path.lexists(self.backup))

    def test_dependency_materialization_single_run_cutover_and_backup_eligibility(self):
        external, first, _ = self.dependency_hardlink_fixture()
        self.start_materialized_dependency_run()
        self.mark_fixture(1)
        self.copy_cli("copy")
        self.mark_fixture(2)
        self.copy_cli("verify")
        self.mark_fixture(3)
        self.mark_fixture(4)
        self.prepare("auto")
        self.apply("auto")
        self.mark_fixture(5)
        request = str(uuid4())
        session = "fixture-original-dependency-chat"
        self.helper("run_record", "mark", "--run", self.audit, "--step", 6, "--status", "waiting",
                    "--request-id", request, "--session-id", session, "--evidence", "synthetic host identity",
                    "--next-action", "run the fixture probe")
        self.helper("probe", "--plan", self.plan, "--request-id", request,
                    "--resumed-session-id", session, cwd=self.old)
        self.mark_fixture(6)
        differences = self.command_for_final_comparison()
        self.assertLessEqual(set(differences), {"mtime_ns"})
        self.assertLessEqual(set(differences.get("mtime_ns", [])), {"."})
        self.assert_cleanup(self.mark_fixture(7), True, linked=True)
        external.write_bytes(b"new bytes reachable only from preserved source aliases")
        self.assertEqual((self.new / first).read_bytes(), b"package bytes\n")
        self.assert_cleanup(json.loads(self.helper("run_record", "show", "--run", self.audit)),
                            False, linked=True)

    def test_dependency_materialization_batch_rejects_non_boolean_policy(self):
        self.batch_fixture()
        baseline = self.load_evidence(self.selection)
        for index, value in enumerate(("true", 1, None, [])):
            with self.subTest(value=value):
                altered = deepcopy(baseline)
                altered["sources"][0]["materialize_dependency_hardlinks"] = value
                choices = self.write_batch_evidence(f"invalid-dependency-policy-{index}.json", altered)
                self.batch_command("create", "--snapshot", self.source_snapshot,
                                   "--selection", choices, success=False)
                self.assertFalse(os.path.lexists(self.batch))

    def test_dependency_materialization_shared_batch_accepts_independent_target_and_rechecks_backup(self):
        self.batch_fixture()
        external, first, _ = self.dependency_hardlink_fixture(self.shared)
        choice = self.choice(self.shared, self.shared_new, ["demo", "library"])
        choice["materialize_dependency_hardlinks"] = True
        self.selection = self.write_batch_evidence("shared-dependencies-only.json", {
            "sources": [choice], "allow_same_volume": False,
        })
        self.batch_command("create", "--snapshot", self.source_snapshot, "--selection", self.selection)
        self.synthetic_history("before")
        self.batch_command("stage")
        root = self.entity_in(self.shared)
        self.assertIs(root["materialize_dependency_hardlinks"], True)
        child = json.loads((Path(root["run"]) / "run.json").read_text(encoding="utf-8"))
        self.assertIs(child["materialize_dependency_hardlinks"], True)
        self.batch_command("prepare", "--mode", "auto")
        self.batch_command("link", "--mode", "auto")
        readback = self.verify_after_for("demo", "library")
        self.accept_project("demo", readback)
        self.accept_project("library", readback)
        self.synthetic_history("after")
        self.record_new_chat("demo", self.primary, self.primary, self.shared_new, self.untouched)
        self.record_new_chat("library", self.shared_new, self.shared_new)
        for project, cwd in (("demo", self.primary), ("library", self.shared)):
            request = str(uuid4())
            self.register_probe(project, self.shared, project + "-old", request)
            self.attached_probe(request, cwd)
            self.attest_probe(project, project + "-old", request)
        self.verify_batch_backup(self.shared)
        self.assertTrue(self.batch_cleanup_status(readback)[root["id"]]["eligible"])
        self.assertFalse((self.shared_new / first).samefile(external))
        external.write_bytes(b"unique preserved dependency-store change")
        self.assertEqual((self.shared_new / first).read_bytes(), b"package bytes\n")
        self.assertFalse(self.batch_cleanup_status(readback)[root["id"]]["eligible"])
