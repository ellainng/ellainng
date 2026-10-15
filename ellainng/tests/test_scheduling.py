"""Portable exports and simulated launchd lifecycle; never operate on real jobs."""

import asyncio
from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
from io import StringIO
import json
from pathlib import Path
import plistlib
import subprocess
import sys
from unittest.mock import patch

from ellainng.cli import app, main
from ellainng.errors import UtilityError
from ellainng.manifest import Manifest, validate_manifest
from ellainng.runtime import Runtime
from ellainng.scheduling import Launchd, Scheduler
from support import IsolatedStateTests


class FakeLaunchctl:
    def __init__(self):
        self.loaded_path = None
        self.fail_bootstrap = False
        self.fail_bootout = False
        self.fail_inspection = False
        self.calls = []

    def __call__(self, argv, **kwargs):
        self.calls.append(argv)
        command = argv[1]
        code, output, error = 0, "", ""
        if command == "print":
            if self.fail_inspection:
                code, error = 1, "Operation not permitted"
            elif self.loaded_path is None:
                code, error = 113, "Could not find service in domain for user gui: 501"
            else:
                output = f"gui/501/example = {{\n\tpath = {self.loaded_path}\n\tstate = waiting\n}}\n"
        elif command == "bootstrap":
            if self.fail_bootstrap:
                code, error = 5, "Bootstrap failed"
            else:
                self.loaded_path = Path(argv[3])
        elif command == "bootout":
            if self.fail_bootout:
                code, error = 5, "Bootout failed"
            else:
                self.loaded_path = None
        else:
            raise AssertionError(f"Unexpected scheduler command: {argv}")
        return subprocess.CompletedProcess(argv, code, output, error)


class SchedulingTests(IsolatedStateTests):
    def setUp(self):
        super().setUp()
        self.path = self.root / "catalog with spaces $literal" / "moves.toml"
        self.runtime = Runtime(config_path=self.path)
        self.runner = FakeLaunchctl()
        self.backend = Launchd(self.runtime, agents_dir=self.root / "Launch Agents",
                               runner=self.runner, platform="darwin", uid=501)
        self.scheduler = Scheduler(self.runtime, backend=self.backend)
        self.scheduler.add("daily-slug", "text.slugify", at="02:30",
                           arguments={"value": "Hello 'World' & $literal"})

    def test_export_roundtrips_without_files_or_native_calls(self):
        document = plistlib.loads(self.backend.export("daily-slug"))
        self.assertEqual(document["StartCalendarInterval"], {"Hour": 2, "Minute": 30})
        self.assertEqual(document["ProgramArguments"][4], str(self.path.resolve()))
        self.assertEqual(document["ProgramArguments"][-3:], ["schedule", "execute", "daily-slug"])
        self.assertEqual(document["Umask"], 0o077)
        self.assertFalse(document["RunAtLoad"])
        self.assertFalse(self.backend.agents_dir.exists())
        self.assertFalse(self.runtime.history.directory.exists())
        self.assertEqual(self.runner.calls, [])
        self.scheduler.add("frequent", "text.slugify", minutes=30, arguments={"value": "hi"})
        self.assertEqual(plistlib.loads(self.backend.export("frequent"))["StartInterval"], 1800)

    def test_install_status_uninstall_and_definition_removal(self):
        status = self.scheduler.install("daily-slug")
        self.assertTrue(status["installed"])
        self.assertTrue(status["loaded"])
        with self.assertRaisesRegex(UtilityError, "Uninstall"):
            self.scheduler.remove("daily-slug")
        original = self.backend.path("daily-slug").read_bytes()
        self.scheduler.install("daily-slug")
        self.assertEqual(self.backend.path("daily-slug").read_bytes(), original)
        self.backend.uninstall("daily-slug")
        self.assertFalse(self.backend.path("daily-slug").exists())
        self.assertIsNone(self.runner.loaded_path)
        self.assertIn("daily-slug", Manifest.load(self.path).data["schedules"])
        self.scheduler.remove("daily-slug")
        self.assertNotIn("daily-slug", Manifest.load(self.path).data["schedules"])
        self.assertTrue(any(call[1:3] == ["bootstrap", "gui/501"] for call in self.runner.calls))
        self.assertTrue(any(call[1] == "bootout" for call in self.runner.calls))

    def test_bootstrap_failure_rolls_back_only_new_plist(self):
        self.runner.fail_bootstrap = True
        with self.assertRaisesRegex(UtilityError, "bootstrap failed"):
            self.scheduler.install("daily-slug")
        self.assertFalse(self.backend.path("daily-slug").exists())
        self.assertIn("daily-slug", Manifest.load(self.path).data["schedules"])

    def test_uninstall_failure_and_unrelated_files_are_preserved(self):
        self.scheduler.install("daily-slug")
        path = self.backend.path("daily-slug")
        original = path.read_bytes()
        self.runner.fail_bootout = True
        with self.assertRaisesRegex(UtilityError, "preserved"):
            self.backend.uninstall("daily-slug")
        self.assertEqual(path.read_bytes(), original)
        path.write_bytes(plistlib.dumps({"Label": "another-application"}))
        for operation in (self.backend.install, self.backend.uninstall):
            with self.assertRaisesRegex(UtilityError, "not owned"):
                operation("daily-slug")
        self.assertEqual(plistlib.loads(path.read_bytes()), {"Label": "another-application"})

    def test_loaded_origin_conflicts_and_query_failures_prevent_changes(self):
        self.runner.loaded_path = self.root / "another.plist"
        with self.assertRaisesRegex(UtilityError, "unverified"):
            self.scheduler.install("daily-slug")
        self.assertFalse(self.backend.path("daily-slug").exists())
        self.runner.loaded_path = None
        self.runner.fail_inspection = True
        with self.assertRaisesRegex(UtilityError, "Cannot inspect"):
            self.scheduler.install("daily-slug")
        self.assertFalse(self.backend.path("daily-slug").exists())

    def test_changed_schedule_requires_explicit_reinstallation(self):
        self.scheduler.install("daily-slug")
        data = deepcopy(self.runtime.manifest.data)
        data["schedules"]["daily-slug"]["at"] = "03:45"
        self.runtime.manifest.save(data)
        self.assertTrue(self.backend.status("daily-slug")["needs_reinstall"])
        with self.assertRaisesRegex(UtilityError, "differs"):
            self.scheduler.install("daily-slug")
        self.backend.uninstall("daily-slug")
        self.scheduler.install("daily-slug")
        self.assertFalse(self.backend.status("daily-slug")["needs_reinstall"])

    def test_non_macos_install_fails_and_export_still_works(self):
        backend = Launchd(self.runtime, agents_dir=self.root / "Other Agents",
                          runner=self.runner, platform="linux")
        self.assertIn(b"StartCalendarInterval", backend.export("daily-slug"))
        with self.assertRaisesRegex(UtilityError, "require macOS"):
            backend.install("daily-slug")
        self.assertFalse(backend.status("daily-slug")["supported"])
        self.assertEqual(self.runner.calls, [])

    def test_scheduled_run_and_launchd_execution_share_history_without_logging_values(self):
        self.assertEqual(asyncio.run(self.scheduler.run_async("daily-slug")), "hello-world-literal")
        self.assertEqual(self.runtime.history.list()[0]["status"], "succeeded")
        output = StringIO()
        with redirect_stdout(output):
            app.invoke(["--config", str(self.path), "schedule", "execute", "daily-slug"])
        self.assertEqual(json.loads(output.getvalue()), {"schedule": "daily-slug", "status": "succeeded"})
        self.assertNotIn("literal", output.getvalue())
        self.assertEqual(len(self.runtime.history.list()), 2)

    def test_invalid_schedule_structure_and_references_preserve_manifest(self):
        original = self.path.read_bytes()
        for name, move, options in (
            ("bad/name", "text.slugify", {"at": "02:00"}),
            ("bad", "missing.move", {"at": "02:00"}),
            ("bad", "text.slugify", {}),
            ("bad", "text.slugify", {"at": "25:00"}),
            ("bad", "text.slugify", {"at": "02:00", "minutes": 2}),
            ("bad", "text.slugify", {"minutes": True}),
            ("bad", "text.slugify", {"minutes": 0}),
            ("bad", "text.slugify", {"minutes": 2, "preset": "missing"}),
        ):
            with self.subTest(options=options), self.assertRaises(UtilityError):
                self.scheduler.add(name, move, **options)
        self.assertEqual(self.path.read_bytes(), original)

    def test_preflight_and_runtime_reject_invalid_arguments_before_utility_execution(self):
        self.scheduler.add("missing-input", "text.slugify", at="12:00")
        with self.assertRaisesRegex(UtilityError, "cannot bind"):
            self.scheduler.install("missing-input")
        self.scheduler.add("wrong-type", "text.slugify", minutes=3, arguments={"value": 42})
        with self.assertRaises(UtilityError):
            asyncio.run(self.scheduler.run_async("wrong-type"))
        self.assertEqual(self.runtime.history.list()[0]["status"], "failed")
        self.assertFalse(self.backend.agents_dir.exists())

    def test_virtual_environment_interpreter_path_is_preserved(self):
        interpreter = self.root / "venv" / "bin" / "python"
        backend = Launchd(self.runtime, python=interpreter, platform="linux")
        self.assertEqual(backend.document("daily-slug")["ProgramArguments"][0], str(interpreter))

    def test_interpreter_upgrade_can_uninstall_the_previously_owned_job(self):
        self.scheduler.install("daily-slug")
        newer = Launchd(self.runtime, agents_dir=self.backend.agents_dir,
                        python=self.root / "new environment" / "python",
                        runner=self.runner, platform="darwin", uid=501)
        self.assertTrue(newer.status("daily-slug")["needs_reinstall"])
        newer.uninstall("daily-slug")
        self.assertFalse(newer.path("daily-slug").exists())

    def test_malformed_plist_is_preserved_with_expected_error(self):
        path = self.backend.path("daily-slug")
        path.parent.mkdir()
        invalid = b'<?xml version="1.0"?><plist><dict>'
        path.write_bytes(invalid)
        with self.assertRaisesRegex(UtilityError, "recognized plist"):
            self.backend.uninstall("daily-slug")
        self.assertEqual(path.read_bytes(), invalid)

    def test_schedule_presets_and_referenced_registration_protection(self):
        manifest = self.runtime.manifest
        manifest.add("text.saved", {"kind": "python", "target": "ellainng.utilities.text:slugify",
                                    "defaults": {"separator": "_"},
                                    "presets": {"hyphens": {"separator": "-"}}})
        self.scheduler.add("preset-job", "text.saved", minutes=10, preset="hyphens",
                           arguments={"value": "Hello World"})
        self.assertEqual(asyncio.run(self.scheduler.run_async("preset-job")), "hello-world")
        for options in ({"remove": True}, {"enabled": False}):
            with self.assertRaises(UtilityError):
                manifest.update("text.saved", **options)

    def test_background_failure_reports_no_argument_or_exception_contents(self):
        self.scheduler.add("bad-input", "text.slugify", minutes=10,
                           arguments={"value": "PRIVATE_SAMPLE", "separator": "PRIVATE_SAMPLE"})
        output, errors = StringIO(), StringIO()
        with redirect_stdout(output), redirect_stderr(errors), self.assertRaises(SystemExit) as result:
            main(["--config", str(self.path), "schedule", "execute", "bad-input"])
        self.assertEqual(result.exception.code, 1)
        self.assertNotIn("PRIVATE_SAMPLE", output.getvalue() + errors.getvalue())
        self.assertEqual(json.loads(output.getvalue())["status"], "failed")

    def test_cli_schedule_export_and_usage(self):
        output = StringIO()
        with redirect_stdout(output):
            app.invoke(["--config", str(self.path), "schedule", "export", "daily-slug"])
        self.assertEqual(plistlib.loads(output.getvalue().encode())["Label"],
                         self.backend.label("daily-slug"))
        with redirect_stderr(StringIO()), self.assertRaises(SystemExit) as result:
            main(["--config", str(self.path), "schedule", "add", "bad", "text.slugify", "--minutes", "0"])
        self.assertEqual(result.exception.code, 1)
