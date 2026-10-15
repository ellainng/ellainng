"""Persistent configuration, explicit loading, and argument precedence."""

from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
from io import StringIO
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import tomllib
import unittest
from unittest.mock import patch

from ellainng.cli import app, main
from ellainng.errors import UtilityError
from ellainng.manifest import Manifest, config_path, render_manifest, validate_manifest
from ellainng.runtime import Runtime
from support import IsolatedStateTests


class ManifestTests(IsolatedStateTests):
    def setUp(self):
        super().setUp()
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "moves.toml"
        self.environment = patch.dict(os.environ, {"ELLAINNG_CONFIG": str(self.path)})
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def manifest(self):
        manifest = Manifest.load(self.path)
        manifest.add("text.saved", {"kind": "python", "target": "ellainng.utilities.text:slugify",
                                    "defaults": {"separator": "_"},
                                    "presets": {"hyphens": {"separator": "-"}}})
        return manifest

    def test_precedence_and_separate_process_persistence(self):
        self.manifest()
        runtime = Runtime(config_path=self.path)
        self.assertEqual(runtime.invoke("text.saved", {"value": "Hello World"}), "hello_world")
        self.assertEqual(runtime.invoke("text.saved", {"value": "Hello World"}, preset="hyphens"),
                         "hello-world")
        self.assertEqual(runtime.invoke("text.saved", {"value": "Hello World", "separator": "_"},
                                        preset="hyphens"), "hello_world")
        result = subprocess.run([sys.executable, "-m", "ellainng", "run", "text.saved",
                                 "--arguments", '{"value":"Two Words"}'],
                                capture_output=True, text=True, cwd=self.directory.name)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), "two_words")
        self.assertEqual(runtime.effective_arguments("text.saved"), {"separator": "_"})
        with self.assertRaisesRegex(UtilityError, "No preset"):
            runtime.invoke("text.saved", preset="missing")

    def test_list_validate_and_learn_never_import_targets(self):
        manifest = Manifest.load(self.path)
        manifest.add("missing.tool", {"kind": "python", "target": "not_installed:execute"})
        with patch("ellainng.runtime.importlib.import_module", side_effect=AssertionError("imported")):
            self.assertIn("missing.tool", Runtime(config_path=self.path).names())
            output = StringIO()
            with redirect_stdout(output):
                app.invoke(["config", "validate"])
                app.invoke(["learn", "python", "another.tool", "also_missing:execute"])
            self.assertIn("Valid:", output.getvalue())
        with self.assertRaisesRegex(UtilityError, "Cannot load"):
            Runtime(config_path=self.path).describe("missing.tool")

    def test_enable_disable_remove_and_cli_writes(self):
        with redirect_stdout(StringIO()):
            app.invoke(["config", "init"])
            app.invoke(["learn", "python", "text.saved", "ellainng.utilities.text:slugify"])
            app.invoke(["defaults", "set", "text.saved", "--arguments", '{"separator":"_"}'])
            app.invoke(["preset", "set", "text.saved", "dashes", "--arguments", '{"separator":"-"}'])
            app.invoke(["disable", "text.saved"])
        with self.assertRaisesRegex(UtilityError, "disabled"):
            Runtime(config_path=self.path).invoke("text.saved", {"value": "Hello World"})
        with redirect_stdout(StringIO()):
            app.invoke(["enable", "text.saved"])
        self.assertEqual(Runtime(config_path=self.path).invoke("text.saved", {"value": "Hello World"}),
                         "hello_world")
        app.invoke(["remove", "text.saved"])
        self.assertNotIn("text.saved", Runtime(config_path=self.path).names())

    def test_stale_snapshot_lock_and_invalid_write_preserve_content(self):
        first = self.manifest()
        stale = Manifest.load(self.path)
        first.update("text.saved", enabled=False)
        original = self.path.read_bytes()
        with self.assertRaisesRegex(UtilityError, "changed"):
            stale.update("text.saved", enabled=True)
        lock = self.path.with_name("moves.toml.lock")
        lock.touch()
        with self.assertRaisesRegex(UtilityError, "locked"):
            first.update("text.saved", enabled=True)
        lock.unlink()
        with self.assertRaisesRegex(UtilityError, "null"):
            first.update("text.saved", defaults={"value": None})
        self.assertEqual(self.path.read_bytes(), original)

    def test_roundtrip_special_strings_and_nested_values(self):
        data = {"version": 1, "moves": {"text.saved": {
            "kind": "python", "target": "ellainng.utilities.text:slugify",
            "defaults": {"value": '"Hi"\n雪 😀\t\\\x7f', "nested": {"odd.key": [True, 1, 2.5]}}}},
            "schedules": {}}
        self.assertEqual(tomllib.loads(render_manifest(data).decode()), data)

    def test_invalid_manifests_are_rejected_without_import(self):
        valid = {"version": 1, "moves": {"text.saved": {
            "kind": "python", "target": "ellainng.utilities.text:slugify"}}, "schedules": {}}
        cases = [{"version": True}, {"version": 2}, {"version": 1, "unknown": True}]
        for field, value in (("target", "file.py"), ("enabled", 1), ("kind", "shell"),
                             ("defaults", []), ("presets", {"bad": []}), ("unknown", 1)):
            candidate = deepcopy(valid)
            candidate["moves"]["text.saved"][field] = value
            cases.append(candidate)
        cases.append({"version": 1, "moves": {"text.slugify": valid["moves"]["text.saved"]}})
        cases.append({"version": 1, "moves": {"registry": valid["moves"]["text.saved"]}})
        for data in cases:
            with self.subTest(data=data), self.assertRaises(UtilityError):
                validate_manifest(data)
        self.path.write_text('version = "unterminated')
        with self.assertRaisesRegex(UtilityError, "Invalid TOML"):
            Manifest.load(self.path)

    def test_explicit_config_wins_and_relative_resources_use_manifest_directory(self):
        explicit = Path(self.directory.name) / "other.toml"
        self.assertEqual(config_path(explicit), explicit.resolve())
        self.assertEqual(config_path(), self.path.resolve())
        manifest = Manifest.load(self.path)
        manifest.add("process.cwd", {"kind": "executable", "argv": [sys.executable, "-c",
                                     "import os; print(os.getcwd())"], "cwd": "."})
        observed = Runtime(config_path=self.path).invoke("process.cwd")["stdout"].strip()
        self.assertEqual(Path(observed).resolve(), self.path.parent.resolve())

    def test_invalid_json_does_not_import_or_write_and_missing_config_validation_fails(self):
        with redirect_stderr(StringIO()), self.assertRaises(SystemExit) as result:
            main(["run", "text.slugify", "--arguments", "[]"])
        self.assertEqual(result.exception.code, 2)
        self.assertFalse(self.path.exists())
        with redirect_stderr(StringIO()), self.assertRaises(SystemExit) as result:
            main(["config", "validate"])
        self.assertEqual(result.exception.code, 1)
        for arguments in ({"nested": {1: "bad"}}, {"tuple": (1, 2)}):
            with self.assertRaises(UtilityError):
                Runtime().invoke("text.slugify", arguments)


if __name__ == "__main__":
    unittest.main()
