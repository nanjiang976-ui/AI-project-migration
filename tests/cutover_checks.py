"""Keep late file edits safe during native NTFS and APFS cutover."""
import os


class CutoverChecks:
    def prepare_drift_check(self, mode):
        self.audit.mkdir()
        (self.old / "checked.txt").write_bytes(b"verified")
        for action in ("copy", "verify"):
            self.helper("tree_copy", action, self.old, self.new, "--audit", self.copy_audit)
        self.prepare(mode)

    def write_drift(self, root, data):
        path = root / "checked.txt"
        before = path.stat()
        # A content edit can preserve both file length and modification time.
        self.assertEqual(len(data), before.st_size)
        path.write_bytes(data)
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
        self.assertEqual(path.stat().st_mtime_ns, before.st_mtime_ns)

    def assert_cutover_blocks_drift(self, mode, *, edit_target):
        self.prepare_drift_check(mode)
        original = self.old
        if mode == "manual":
            self.old.rename(self.backup)
            original = self.backup
        self.write_drift(self.new if edit_target else original, b"modified")

        self.helper("cutover", "apply", "--plan", self.plan, "--mode", mode, success=False)

        self.assertFalse(self.old.is_symlink() or self.old.is_junction())
        self.assertEqual(os.path.lexists(self.old), mode == "auto")
        self.assertEqual(os.path.lexists(self.backup), mode == "manual")
        self.assertEqual((original / "checked.txt").read_bytes(), b"verified" if edit_target else b"modified")
        self.assertEqual((self.new / "checked.txt").read_bytes(), b"modified" if edit_target else b"verified")

    def test_automatic_cutover_blocks_late_source_edit(self):
        self.assert_cutover_blocks_drift("auto", edit_target=False)

    def test_automatic_cutover_requires_fresh_verification_after_target_edit(self):
        self.assert_cutover_blocks_drift("auto", edit_target=True)

        # Reconcile both sides to a new value so the initial manifest would still fail.
        self.write_drift(self.old, b"modified")
        self.helper("tree_copy", "verify", self.old, self.new, "--audit", self.copy_audit)
        self.helper("cutover", "apply", "--plan", self.plan, "--mode", "auto")
        self.assertTrue(self.old.is_symlink() or self.old.is_junction())
        self.assertTrue(self.old.samefile(self.new))
        self.assertEqual((self.backup / "checked.txt").read_bytes(), b"modified")

        # Running the same command after a successful cutover only checks the link.
        self.write_drift(self.new, b"new work")
        self.helper("cutover", "apply", "--plan", self.plan, "--mode", "auto")
        self.assertTrue(self.old.samefile(self.new))
        self.assertEqual((self.old / "checked.txt").read_bytes(), b"new work")
        self.assertEqual((self.backup / "checked.txt").read_bytes(), b"modified")

    def test_manual_cutover_blocks_late_backup_edit(self):
        self.assert_cutover_blocks_drift("manual", edit_target=False)

    def test_manual_cutover_blocks_late_target_edit(self):
        self.assert_cutover_blocks_drift("manual", edit_target=True)
