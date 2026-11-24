"""Keep late file edits safe during native NTFS and APFS cutover."""
import json
import os
from pathlib import Path
import subprocess
import sys
from threading import Event, Thread
from time import monotonic, sleep


SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "ai-project-migration" / "scripts"
PAUSE_FIXTURE = """
from pathlib import Path
from time import monotonic, sleep

def pause_until_released(waiting, resume):
    Path(waiting).touch()
    deadline = monotonic() + 60
    while not Path(resume).exists():
        if monotonic() >= deadline:
            raise RuntimeError('fixture was not released')
        sleep(0.01)
"""


def capture_stalled_progress(test, command, waiting, resume, marker):
    lines = []
    visible = Event()
    with subprocess.Popen([str(arg) for arg in command], cwd=test.audit.parent, env=test.env,
                          stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          text=True, encoding="utf-8", errors="replace") as process:
        def read_stderr():
            for line in process.stderr:
                lines.append(line)
                if marker in line:
                    visible.set()

        reader = Thread(target=read_stderr, daemon=True)
        reader.start()
        try:
            deadline = monotonic() + 30
            while not waiting.exists() and process.poll() is None and monotonic() < deadline:
                sleep(0.02)
            test.assertTrue(waiting.exists(), "".join(lines))
            test.assertTrue(visible.wait(max(0, deadline - monotonic())), "".join(lines))
            test.assertIsNone(process.poll(), "The progress must be readable before the CLI exits")
        finally:
            resume.touch()
            try:
                process.wait(timeout=90)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)
                raise
            finally:
                reader.join(timeout=5)
        output = process.stdout.read()
    test.assertEqual(process.returncode, 0, output + "\n" + "".join(lines))
    return output, "".join(lines)


class CutoverChecks:
    def prepare_drift_check(self, mode):
        self.audit.mkdir()
        (self.old / "checked.txt").write_bytes(b"verified")
        for action in ("copy", "verify"):
            self.helper("tree_copy", action, self.old, self.new, "--audit", self.copy_audit)
        return self.prepare(mode)

    def offline_fixture_process(self, *args, code=None):
        script = SCRIPTS / "cutover.py"
        entry = [script] if code is None else ["-c", code, script]
        return subprocess.run([sys.executable, "-B", "-X", "utf8", *map(str, [*entry, *args])],
                              cwd=self.audit.parent, env=self.env, stdin=subprocess.DEVNULL,
                              capture_output=True, text=True, encoding="utf-8", timeout=90)

    def test_cutover_progress_is_visible_during_a_real_scan_and_preserves_stdout(self):
        self.assertIn("--progress", self.prepare_drift_check("auto"))
        waiting, resume = self.audit / "waiting", self.audit / "resume"
        code = PAUSE_FIXTURE + """
import runpy
import sys
from unittest.mock import patch

script, waiting, resume, *args = sys.argv[1:]
sys.path.insert(0, str(Path(script).parent))
if sys.platform == 'darwin':
    import mac_tree as tree
else:
    import tree_copy as tree

snapshot = tree.snapshot

def held_snapshot(*args, **options):
    if not Path(waiting).exists():
        pause_until_released(waiting, resume)
    return snapshot(*args, **options)

sys.argv = [script, *args]
with patch.object(tree, 'snapshot', held_snapshot):
    runpy.run_path(script, run_name='__main__')
"""
        args = ("apply", "--plan", self.plan, "--mode", "auto")
        output, progress = capture_stalled_progress(
            self, [sys.executable, "-B", "-X", "utf8", "-c", code,
                   SCRIPTS / "cutover.py", waiting, resume, *args, "--progress"],
            waiting, resume, "进度：重新读取原件并计算哈希",
        )
        self.assertIn("进度：旧入口已通过核验", progress)
        replay = self.offline_fixture_process(*args)
        self.assertEqual((replay.returncode, replay.stderr), (0, ""))
        self.assertEqual(replay.stdout, output)
        self.assertTrue(self.old.samefile(self.new))
        self.assertEqual(list(self.audit.glob("cutover-diagnostic-*.json")), [])

    def test_cutover_progress_diagnoses_drift_and_preserves_error_if_diagnostic_cannot_save(self):
        self.prepare_drift_check("auto")
        self.write_drift(self.old, b"modified")
        args = ("apply", "--plan", self.plan, "--mode", "auto")
        legacy = self.offline_fixture_process(*args)
        failed = self.offline_fixture_process(*args, "--progress")
        self.assertEqual((legacy.returncode, failed.returncode), (1, 1))
        self.assertEqual(failed.stdout, legacy.stdout)
        self.assertNotIn("进度：", legacy.stderr)
        self.assertIn(legacy.stderr.strip(), failed.stderr)
        self.assertFalse(self.backup.exists())
        report, = self.audit.glob("cutover-diagnostic-*.json")
        observed = json.loads(report.read_text(encoding="utf-8"))
        self.assertEqual((observed["scope"], observed["stage"], observed["source"]),
                         ("directory", "compare-source", str(self.old)))
        self.assertEqual(observed["status"]["phase"], "ready")
        self.assertIn("checked.txt", observed["error"])
        self.assertIn(str(report), failed.stderr)

        blocked_report = self.audit / ("cutover-diagnostic-" + "0" * 32 + ".json")
        blocked_report.write_bytes(b"keep original file")
        code = """
import runpy
import sys
from pathlib import Path
from unittest.mock import patch
from uuid import UUID

script = sys.argv[1]
sys.path.insert(0, str(Path(script).parent))
sys.argv = sys.argv[1:]
with patch('uuid.uuid4', return_value=UUID(int=0)):
    runpy.run_path(script, run_name='__main__')
"""
        failed_again = self.offline_fixture_process(*args, "--progress", code=code)
        self.assertEqual((failed_again.returncode, failed_again.stdout), (1, failed.stdout))
        self.assertIn("诊断未能保存", failed_again.stderr)
        self.assertIn(legacy.stderr.strip(), failed_again.stderr)
        self.assertEqual(blocked_report.read_bytes(), b"keep original file")
        self.assertEqual(set(self.audit.glob("cutover-diagnostic-*.json")), {report, blocked_report})

    def test_cutover_progress_diagnoses_link_failure_after_rename_and_can_resume(self):
        self.prepare_drift_check("auto")
        code = """
import runpy
import sys
from pathlib import Path
from unittest.mock import patch

script = sys.argv[1]
sys.path.insert(0, str(Path(script).parent))
sys.argv = sys.argv[1:]
link_function = '_winapi.CreateJunction' if sys.platform == 'win32' else 'os.symlink'
with patch(link_function, side_effect=OSError('fixture link denied')):
    runpy.run_path(script, run_name='__main__')
"""
        args = ("apply", "--plan", self.plan, "--mode", "auto", "--progress")
        failed = self.offline_fixture_process(*args, code=code)
        self.assertEqual((failed.returncode, failed.stdout), (1, ""))
        self.assertIn("fixture link denied", failed.stderr)
        report, = self.audit.glob("cutover-diagnostic-*.json")
        observed = json.loads(report.read_text(encoding="utf-8"))
        self.assertEqual(observed["stage"], "link")
        self.assertEqual(observed["status"]["phase"], "renamed")
        self.assertIn("fixture link denied", observed["error"])
        self.assertFalse(os.path.lexists(self.old))
        self.assertEqual((self.backup / "checked.txt").read_bytes(), b"verified")
        resumed = self.offline_fixture_process(*args)
        self.assertEqual(resumed.returncode, 0, resumed.stderr)
        self.assertTrue(self.old.samefile(self.new))
        self.assertEqual(list(self.audit.glob("cutover-diagnostic-*.json")), [report])

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
