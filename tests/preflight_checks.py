"""Check preflight with isolated fixtures on native NTFS and APFS hosts."""
import base64
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import unittest


SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "ai-project-migration" / "scripts"


class PreflightChecks:
    def run_preflight(self, *args, success=True, env=None):
        process = self.preflight_process([sys.executable, "-B", "-X", "utf8",
                                          SCRIPTS / "preflight.py", *args], env=env)
        self.assertEqual(process.returncode, 0 if success else 1, self.preflight_diagnostic(process))
        return self.preflight_report(process)

    def preflight_process(self, args, *, env=None):
        # The macOS detach scenario removes the target volume and its directories.
        return subprocess.run([str(arg) for arg in args], cwd=self.old.parent,
                              env=self.env if env is None else env, capture_output=True,
                              stdin=subprocess.DEVNULL, timeout=60)

    def preflight_diagnostic(self, process):
        return f"rc={process.returncode}\n{process.stdout!r}\n{process.stderr!r}"

    def preflight_report(self, process):
        return json.loads(process.stdout.decode("utf-8-sig"))

    def preflight_status(self, report, name):
        return next(check["status"] for check in report["checks"] if check["name"] == name)

    def preflight_snapshot(self, *parents):
        return {str(path): (path.stat().st_mtime_ns, None if path.is_dir() else path.read_bytes())
                for parent in parents for path in [parent, *parent.rglob("*")]}

    def test_preflight_native_runtime(self):
        if os.name == "nt":
            commands = [["powershell.exe", "-NoProfile", "-NonInteractive", "-File", SCRIPTS / "preflight.ps1"]]
            if shutil.which("pwsh.exe"):
                commands.append(["pwsh.exe", "-NoProfile", "-NonInteractive", "-File", SCRIPTS / "preflight.ps1"])
        else:
            commands = [["/bin/sh", SCRIPTS / "preflight.sh"]]
        for command in commands:
            with self.subTest(shell=command[0]):
                result = self.preflight_process(command)
                self.assertEqual(result.returncode, 0, self.preflight_diagnostic(result))
                report = self.preflight_report(result)
                for check in ("system", "terminal", "python"):
                    self.assertEqual(self.preflight_status(report, check), "passed", report)
                executable = Path(report["python"]["path"])
                self.assertTrue(executable.is_absolute() and executable.is_file(), report)
                check = self.preflight_process([executable, "-I", "-S", "-B", "-c",
                                                "import sys; assert sys.implementation.name == 'cpython' "
                                                "and sys.version_info >= (3, 12)"])
                self.assertEqual(check.returncode, 0, self.preflight_diagnostic(check))

    @unittest.skipUnless(os.name == "nt", "Windows Python alias fixture")
    def test_preflight_windows_skips_store_alias_and_reports_old_python(self):
        # Replace discovery in the calling PowerShell scope. No real registry,
        # Store app, launcher, executable, or package manager is changed.
        command = r"""
function Get-Command {
    param($Name, $CommandType, [switch]$All, $ErrorAction)
    [pscustomobject]@{Source = "$env:MIGRATION_FIXTURE_ROOT\WindowsApps\python.exe"}
    if ($env:MIGRATION_FIXTURE_HAS_OLD_PYTHON -eq 'yes') {
        [pscustomobject]@{Source = "$env:MIGRATION_FIXTURE_ROOT\old\python.exe"}
    }
}
function Get-ChildItem {
    param($LiteralPath, [switch]$Directory, $Filter, $ErrorAction)
}
function Get-Item {
    param($LiteralPath, $ErrorAction)
    if ($LiteralPath -like '*WindowsApps*') {
        [IO.File]::AppendAllText($env:MIGRATION_FIXTURE_TRACE, 'alias;')
    }
    [pscustomobject]@{
        PSIsContainer = $false
        FullName = 'Invoke-OldFixturePython'
        VersionInfo = [pscustomobject]@{ProductName = 'Python'}
    }
}
function Invoke-OldFixturePython {
    [IO.File]::AppendAllText($env:MIGRATION_FIXTURE_TRACE, 'old;')
    $global:LASTEXITCODE = 3
}
& $env:MIGRATION_FIXTURE_ENTRY
exit $LASTEXITCODE
"""
        encoded = base64.b64encode(command.encode("utf-16le")).decode("ascii")
        trace = self.new.parent / "probe-invocations.txt"
        env = dict(self.env, MIGRATION_FIXTURE_ENTRY=str(SCRIPTS / "preflight.ps1"),
                   MIGRATION_FIXTURE_ROOT=str(self.new.parent), MIGRATION_FIXTURE_TRACE=str(trace))
        args = ["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded]
        process = self.preflight_process(args, env=dict(env, MIGRATION_FIXTURE_HAS_OLD_PYTHON="no"))
        self.assertEqual(process.returncode, 1, self.preflight_diagnostic(process))
        output = self.preflight_report(process)
        self.assertEqual(self.preflight_status(output, "python"), "missing", output)
        self.assertEqual(self.preflight_status(output, "volumes"), "pending", output)
        self.assertEqual(self.preflight_status(output, "git"), "pending", output)
        self.assertFalse(trace.exists())
        process = self.preflight_process(args, env=dict(env, MIGRATION_FIXTURE_HAS_OLD_PYTHON="yes"))
        self.assertEqual(process.returncode, 1, self.preflight_diagnostic(process))
        output = self.preflight_report(process)
        self.assertEqual(self.preflight_status(output, "python"), "incompatible", output)
        self.assertIsNone(output["python"])
        self.assertEqual(trace.read_text(encoding="utf-8"), "old;")

    def test_preflight_plain_folder_passes_without_git_or_writes(self):
        empty_path = self.new.parent / "empty-path"
        empty_path.mkdir()
        no_git = dict(self.env, PATH=str(empty_path))
        # Preserve the test source spelling and use a Unicode destination.
        destination = self.new.with_name("目标 [x]'例")
        before = self.preflight_snapshot(self.old, self.new.parent)
        report = self.run_preflight("--source", self.old, "--target", destination, env=no_git)
        self.assertEqual(self.preflight_status(report, "volumes"), "passed", report)
        self.assertEqual(self.preflight_status(report, "git"), "not_applicable", report)
        self.assertEqual(before, self.preflight_snapshot(self.old, self.new.parent))

    def test_preflight_git_project_needs_callable_git(self):
        self.git(self.old, "init", "-q")
        # Keep this fixture stable while comparing its complete Git metadata.
        self.git(self.old, "config", "--local", "maintenance.auto", "false")
        self.git(self.old, "config", "--local", "gc.auto", "0")
        self.git(self.old, "add", ".")
        self.git(self.old, "commit", "-qm", "fixture")
        empty_path = self.new.parent / "empty-path"
        empty_path.mkdir()
        before = self.preflight_snapshot(self.old, self.new.parent)
        args = ("--source", self.old, "--target", self.new)
        blocked = self.run_preflight(*args, success=False, env=dict(self.env, PATH=str(empty_path)))
        self.assertEqual(self.preflight_status(blocked, "git"), "missing", blocked)
        passed = self.run_preflight(*args)
        self.assertEqual(self.preflight_status(passed, "git"), "passed", passed)
        self.assertEqual(before, self.preflight_snapshot(self.old, self.new.parent))

    def test_preflight_rejects_git_without_worktree_command_support(self):
        (self.old / ".git").mkdir()
        # Simulate an installed Git that accepts the arguments but ignores -z.
        # Invoking the real preflight CLI still exercises its blocking exit code.
        code = """
import os
from pathlib import Path
import sys
from unittest.mock import patch

sys.path.insert(0, sys.argv[1])
import git_check
import preflight

source = Path(sys.argv[2])
fake_git = source.parent / 'fixture-git'
answers = {
    ('--version',): b'git version 2.35.0\\n',
    ('rev-parse', '--show-toplevel'): os.fsencode(source) + b'\\n',
    ('rev-parse', '--path-format=absolute', '--git-common-dir'): os.fsencode(source / '.git') + b'\\n',
    ('worktree', 'list', '--porcelain', '-z'): b'worktree fixture\\n\\n',
}

def git(root, *args, program, timeout):
    assert root == source and program == fake_git
    return answers[args]

sys.argv = ['preflight.py', '--source', str(source), '--target', sys.argv[3]]
with patch.object(git_check, 'git_program', return_value=fake_git), patch.object(git_check, 'git', new=git):
    sys.exit(preflight.main())
"""
        process = self.preflight_process([sys.executable, "-B", "-X", "utf8", "-c", code,
                                          SCRIPTS, self.old, self.new])
        self.assertEqual(process.returncode, 1, self.preflight_diagnostic(process))
        report = self.preflight_report(process)
        self.assertEqual(self.preflight_status(report, "git"), "incompatible", report)

    @unittest.skipUnless(sys.platform == "darwin", "Apple Git dispatcher fixture")
    def test_preflight_apple_git_does_not_run_unbacked_dispatcher(self):
        self.git(self.old, "init", "-q")
        env = dict(self.env, PATH="/usr/bin:/bin", DEVELOPER_DIR=str(self.new.parent / "no-apple-tools"))
        report = self.run_preflight("--source", self.old, "--target", self.new, success=False, env=env)
        self.assertEqual(self.preflight_status(report, "git"), "missing", report)

    def test_preflight_blocks_invalid_destination_without_writes(self):
        before = self.preflight_snapshot(self.old, self.new.parent)
        target = self.new.parent / "missing-parent" / self.new.name
        report = self.run_preflight("--source", self.old, "--target", target, success=False)
        self.assertEqual(self.preflight_status(report, "volumes"), "blocked", report)
        self.assertEqual(before, self.preflight_snapshot(self.old, self.new.parent))

    def test_preflight_resume_uses_backup_and_preserves_checkpoint(self):
        self.helper("run_record", "start", self.old, self.new, "--run", self.audit,
                    "--host", "generic", "--allow-same-volume")
        self.old.rename(self.backup)
        self.new.mkdir()
        before = self.preflight_snapshot(self.backup, self.new, self.audit)
        report = self.run_preflight("--resume", "--source", self.backup, "--target", self.new)
        self.assertEqual(self.preflight_status(report, "volumes"), "passed", report)
        self.assertEqual(self.preflight_status(report, "git"), "not_applicable", report)
        report = self.run_preflight("--resume", "--target", self.new)
        self.assertEqual(self.preflight_status(report, "volumes"), "passed", report)
        self.assertEqual(before, self.preflight_snapshot(self.backup, self.new, self.audit))
        self.assertFalse(self.old.exists())
