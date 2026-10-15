"""Installed entry point and import contracts."""

from contextlib import redirect_stderr, redirect_stdout
from importlib.metadata import distribution
from io import StringIO
from pathlib import Path
import os
import subprocess
import sys
import tempfile
import unittest

from ellainng import __version__
from ellainng.cli import app, main
from support import IsolatedStateTests


class PackageTests(IsolatedStateTests):
    def test_aliases_share_one_entry_point(self):
        entries = {entry.name: entry.value for entry in distribution("ellainng").entry_points}
        self.assertEqual(entries["ella"], "ellainng.cli:main")
        self.assertEqual(entries["ellainng"], entries["ella"])

    def test_help_and_version_exit_successfully(self):
        for arguments, expected in [(["--help"], "personal utilities"), (["version"], __version__)]:
            output = StringIO()
            with redirect_stdout(output), self.assertRaises(SystemExit) as result:
                main(arguments)
            self.assertEqual(result.exception.code, 0)
            self.assertIn(expected, output.getvalue())

    def test_invalid_input_exits_two(self):
        with redirect_stderr(StringIO()), self.assertRaises(SystemExit) as result:
            main(["unknown-command"])
        self.assertEqual(result.exception.code, 2)

    def test_module_help_and_import_do_not_create_user_files(self):
        with tempfile.TemporaryDirectory() as directory:
            environment = dict(os.environ, HOME=directory, USERPROFILE=directory,
                               XDG_CONFIG_HOME=directory, XDG_STATE_HOME=directory)
            for command in (["-c", "import ellainng; import ellainng.cli"],
                            ["-m", "ellainng", "--help"]):
                result = subprocess.run([sys.executable, *command], cwd=directory,
                                        env=environment, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_interface_can_be_inspected_without_execution(self):
        self.assertIn("version", app.format_help())


if __name__ == "__main__":
    unittest.main()
