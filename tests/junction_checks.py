"""Keep pnpm's internal directory junctions intact across native NTFS migration."""
import json
import os
from unittest.mock import patch
from uuid import uuid4

from _winapi import CreateJunction
import migration_ntfs as nt
import tree_copy


class JunctionChecks:
    def pnpm_fixture(self):
        modules = self.old / "node_modules"
        package_a = modules / ".pnpm" / "package-a@1" / "node_modules" / "package-a"
        package_b = modules / ".pnpm" / "package-b@1" / "node_modules" / "package-b"
        for package, data in ((package_a, b"package a\n"), (package_b, b"package b\n")):
            package.mkdir(parents=True)
            (package / "index.js").write_bytes(data)
        CreateJunction(str(package_a), str(modules / "package-a"))
        # These ordinary package directories refer back to one another through
        # dependency entries, as pnpm does. Traversing links would recurse.
        CreateJunction(str(package_b), str(package_a.parent / "package-b"))
        CreateJunction(str(package_a), str(package_b.parent / "package-a"))
        self.package_a_relative = package_a.relative_to(self.old)
        self.package_b_relative = package_b.relative_to(self.old)
        return modules / "package-a"

    def assert_pnpm_links(self, root):
        package_a = root / self.package_a_relative
        package_b = root / self.package_b_relative
        alias = root / "node_modules" / "package-a"
        for link, target in ((alias, package_a), (package_a.parent / "package-b", package_b),
                             (package_b.parent / "package-a", package_a)):
            self.assertTrue(link.is_junction())
            self.assertTrue(link.samefile(target))
        self.assertEqual((alias / "index.js").read_bytes(), b"package a\n")
        self.assertEqual((package_a.parent / "package-b" / "index.js").read_bytes(), b"package b\n")

    def test_pnpm_junction_copy_maps_targets_without_traversing_package_cycles(self):
        alias = self.pnpm_fixture()
        before = tree_copy.snapshot(self.old, True)
        self.assertNotIn("node_modules/package-a/index.js", before)
        report = self.copy_verified()
        self.assertEqual(report["differences"], {})
        self.assertEqual(len(tree_copy.snapshot(self.new, True)), len(before))
        self.assert_pnpm_links(self.old)
        self.assert_pnpm_links(self.new)
        copied_alias = self.new / "node_modules" / "package-a"
        self.assertFalse(copied_alias.samefile(alias))
        self.assertEqual(copied_alias.lstat().st_mtime_ns, alias.lstat().st_mtime_ns)
        self.assertEqual(copied_alias.lstat().st_file_attributes, alias.lstat().st_file_attributes)

    def test_pnpm_junction_copy_resumes_and_rejects_replaced_owned_link(self):
        self.pnpm_fixture()
        self.audit.mkdir()
        self.copy_cli("copy")
        alias = self.new / "node_modules" / "package-a"
        identity = alias.lstat().st_ino
        self.copy_cli("copy")
        self.assertEqual(alias.lstat().st_ino, identity)
        self.copy_cli("verify")
        alias.rename(self.right / "retained-original-junction")
        CreateJunction(str(self.new / self.package_a_relative), str(alias))
        self.copy_cli("copy", success=False)
        self.assertTrue(alias.is_junction())
        self.assertEqual((alias / "index.js").read_bytes(), b"package a\n")

    def test_pnpm_junction_acl_copy_uses_link_object_metadata(self):
        alias = self.pnpm_fixture()
        package = self.old / self.package_a_relative
        with open(str(package) + ":package_metadata", "xb") as stream:
            stream.write(b"package directory stream")
        package_attributes = nt.attributes(package)
        nt.write_attrs(alias, alias.lstat().st_file_attributes | 0x02)
        self.command(["icacls", package, "/inheritance:d"])
        link_security = nt.security(alias, nofollow=True)
        package_security = nt.security(package)
        self.assertNotEqual(link_security[1], package_security[1])
        self.copy_verified()
        self.assertEqual(nt.security(self.new / "node_modules" / "package-a", nofollow=True),
                         link_security)
        self.assertEqual(nt.security(self.new / self.package_a_relative), package_security)
        self.assertEqual(nt.security(package), package_security)
        self.assertTrue((self.new / "node_modules" / "package-a").lstat().st_file_attributes & 0x02)
        self.assertEqual(nt.attributes(self.new / self.package_a_relative), package_attributes)
        with open(str(self.new / self.package_a_relative) + ":package_metadata", "rb") as stream:
            self.assertEqual(stream.read(), b"package directory stream")

    def test_pnpm_junction_candidate_directory_collision_is_preserved(self):
        self.pnpm_fixture()
        self.audit.mkdir()
        self.copy_cli("copy")
        alias = self.new / "node_modules" / "package-a"
        alias.rename(self.right / "retained-junction-before-collision")
        alias.mkdir()
        (alias / "keep.txt").write_bytes(b"independent candidate data")
        self.copy_cli("copy", success=False)
        self.assertEqual((alias / "keep.txt").read_bytes(), b"independent candidate data")

    def test_pnpm_junction_verify_detects_changed_internal_target(self):
        self.pnpm_fixture()
        self.copy_verified()
        alias = self.new / "node_modules" / "package-a"
        alias.rename(self.right / "retained-junction-before-retarget")
        CreateJunction(str(self.new / self.package_b_relative), str(alias))
        report = self.copy_cli("verify", success=False)
        self.assertIn("node_modules/package-a", report["differences"]["junction_target"])
        self.assertEqual((alias / "index.js").read_bytes(), b"package b\n")

    def test_external_directory_junction_stops_before_creating_candidate(self):
        outside = self.left / "unselected-directory"
        outside.mkdir()
        (outside / "keep.txt").write_bytes(b"outside project")
        CreateJunction(str(outside), str(self.old / "external-dependency"))
        self.audit.mkdir()
        self.copy_cli("copy", success=False)
        self.assertFalse(os.path.lexists(self.new))
        self.assertEqual((outside / "keep.txt").read_bytes(), b"outside project")

    def test_broken_directory_junction_stops_before_creating_candidate(self):
        target = self.old / "original-package"
        target.mkdir()
        CreateJunction(str(target), str(self.old / "broken-dependency"))
        target.rename(self.old / "retained-package")
        self.audit.mkdir()
        self.copy_cli("copy", success=False)
        self.assertFalse(os.path.lexists(self.new))
        self.assertTrue((self.old / "broken-dependency").is_junction())
        self.assertTrue((self.old / "retained-package").is_dir())

    def test_junction_target_through_another_junction_is_rejected(self):
        target = self.old / "physical-package"
        target.mkdir()
        first = self.old / "package-alias"
        CreateJunction(str(target), str(first))
        CreateJunction(str(first), str(self.old / "indirect-package"))
        self.audit.mkdir()
        self.copy_cli("copy", success=False)
        self.assertFalse(os.path.lexists(self.new))
        self.assertTrue(first.samefile(target))

    def test_pnpm_junction_source_retarget_during_copy_is_rejected(self):
        alias = self.pnpm_fixture()
        self.audit.mkdir()
        tree_copy.prepare_audit(self.copy_audit, self.old)
        original = tree_copy.prepare_copy

        def retarget_after_snapshot(source, destination, audit):
            original(source, destination, audit)
            alias.rename(self.left / "retained-source-junction")
            CreateJunction(str(self.old / self.package_b_relative), str(alias))

        with patch.object(tree_copy, "prepare_copy", side_effect=retarget_after_snapshot):
            with self.assertRaises((ValueError, RuntimeError)):
                tree_copy.copy(self.old, self.new, self.copy_audit, {"run": "junction-source-drift"})
        self.assertEqual((alias / "index.js").read_bytes(), b"package b\n")
        self.assertTrue((self.left / "retained-source-junction").is_junction())

    def test_pnpm_junction_checkpoint_accepts_backup_after_cutover(self):
        self.pnpm_fixture()
        self.helper("run_record", "start", self.old, self.new, "--host", "generic",
                    "--run", self.audit, "--allow-same-volume")
        self.helper("git_check", "baseline", self.old, "--out", self.audit / "git-before.json")
        self.mark_fixture(1)
        self.copy_cli("copy")
        self.mark_fixture(2)
        self.copy_cli("verify")
        self.mark_fixture(3)
        self.mark_fixture(4)
        self.prepare("auto")
        self.apply("auto")
        self.assertEqual(self.phase(), "linked")
        self.mark_fixture(5)
        self.assert_pnpm_links(self.new)
        # The old absolute targets remain in the preserved backup, while the
        # copied links refer directly to the new physical package locations.
        backup_alias = self.backup / "node_modules" / "package-a"
        self.assertIn(str(self.old), os.readlink(backup_alias))
        self.assertIn(str(self.new), os.readlink(self.new / "node_modules" / "package-a"))
        backup_alias_file = backup_alias / "index.js"
        self.assertTrue(backup_alias_file.samefile(self.new / self.package_a_relative / "index.js"))
        self.assertFalse(backup_alias_file.samefile(self.backup / self.package_a_relative / "index.js"))
        request = str(uuid4())
        session = "fixture-original-pnpm-chat"
        self.helper("run_record", "mark", "--run", self.audit, "--step", 6, "--status", "waiting",
                    "--request-id", request, "--session-id", session, "--evidence", "synthetic host identity",
                    "--next-action", "run the fixture probe")
        self.helper("probe", "--plan", self.plan, "--request-id", request,
                    "--resumed-session-id", session, cwd=self.old)
        self.mark_fixture(6)
        differences = self.command_for_final_comparison()
        self.assertLessEqual(set(differences), {"mtime_ns"})
        self.assertLessEqual(set(differences.get("mtime_ns", [])), {"."})
        marked = self.mark_fixture(7)
        self.assert_cleanup(marked, True, linked=True)
        shown = json.loads(self.helper("run_record", "show", "--run", self.audit))
        self.assert_cleanup(shown, True, linked=True)
        for result in (marked, shown):
            self.assertRegex(result["cleanup_notice"]["backup_message"],
                             r"3\s*个[^。]*可经旧入口访问新项目")
        correct_entry = self.left / "retained-correct-entry"
        wrong_target = self.right / "wrong-target"
        wrong_target.mkdir()
        self.old.rename(correct_entry)
        CreateJunction(str(wrong_target), str(self.old))
        redirected = json.loads(self.helper("run_record", "show", "--run", self.audit))
        self.assert_cleanup(redirected, False)
        self.assertNotIn("可经旧入口访问新项目", redirected["cleanup_notice"]["backup_message"])
        self.old.rename(self.left / "retained-wrong-entry")
        correct_entry.rename(self.old)
        self.assertTrue(self.old.samefile(self.new))

        (self.backup / self.package_a_relative / "index.js").write_bytes(b"unique backup package bytes")
        stale = json.loads(self.helper("run_record", "show", "--run", self.audit))
        self.assert_cleanup(stale, False, linked=True)
        self.assertNotIn("可经旧入口访问新项目", stale["cleanup_notice"]["backup_message"])

    def test_pnpm_junction_manual_rename_rechecks_preserved_original(self):
        self.pnpm_fixture()
        self.copy_verified()
        self.prepare("manual")
        self.old.rename(self.backup)
        self.apply("manual")
        self.assertEqual(self.phase(), "linked")
        self.assert_pnpm_links(self.new)
        self.assertEqual((self.backup / self.package_b_relative / "index.js").read_bytes(), b"package b\n")

    def test_pnpm_junction_late_target_bytes_block_cutover(self):
        self.pnpm_fixture()
        self.copy_verified()
        self.prepare("auto")
        package = self.new / self.package_a_relative / "index.js"
        before = package.stat()
        package.write_bytes(b"modified!\n")
        os.utime(package, ns=(before.st_atime_ns, before.st_mtime_ns))
        self.apply("auto", success=False)
        self.assertFalse(os.path.lexists(self.backup))
        self.assertFalse(self.old.is_junction())
        self.assertEqual((self.old / self.package_a_relative / "index.js").read_bytes(), b"package a\n")
