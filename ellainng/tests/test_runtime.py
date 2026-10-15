"""Real catalog validation and literal subprocess execution."""

import asyncio
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
from pathlib import Path
import sys
import tempfile
import unittest

from ellainng.cli import app, main
from ellainng.errors import UtilityError
from ellainng.runtime import Runtime
from support import IsolatedStateTests


class RuntimeTests(IsolatedStateTests):
    def test_schema_rejects_invalid_inputs_before_execution(self):
        calls = []

        def double(value: int) -> int:
            calls.append(value)
            return value * 2

        runtime = Runtime()
        runtime.register_python("numbers.double", double)
        for arguments in ({}, {"value": "2"}, {"value": True}, {"value": 2, "extra": 1},
                          {"value": float("nan")}):
            with self.assertRaises(UtilityError):
                runtime.invoke("numbers.double", arguments)
        self.assertEqual(calls, [])
        self.assertEqual(runtime.invoke("numbers.double", {"value": 2}), 4)

    def test_async_results_and_inspection(self):
        calls = []

        async def repeat(value: str) -> str:
            calls.append(value)
            return value * 2

        runtime = Runtime()
        runtime.register_python("text.repeat", repeat)
        self.assertTrue(runtime.describe("text.repeat")["is_async"])
        self.assertEqual(calls, [])
        self.assertEqual(asyncio.run(runtime.invoke_async("text.repeat", {"value": "ha"})), "haha")

    def test_builtin_and_fresh_registry(self):
        runtime = Runtime()
        self.assertEqual(runtime.invoke("text.slugify", {"value": "  Café & Hello, World! "}),
                         "cafe-hello-world")
        self.assertEqual(runtime.invoke("text.slugify", {"value": "!!!"}), "")
        runtime.register_python("text.other", lambda: None)
        self.assertNotIn("text.other", Runtime().names())
        with self.assertRaises(UtilityError):
            runtime.invoke("text.slugify", {"value": "abc", "separator": "/"})

    def test_subprocess_arguments_and_working_directory_are_literal(self):
        runtime = Runtime()
        with tempfile.TemporaryDirectory() as directory:
            runtime.register_executable("process.echo", [sys.executable, "-c",
                "import json, os, sys; print(json.dumps([os.getcwd(), sys.argv[1:]]))"],
                cwd=Path(directory))
            arguments = ["two words", "'quoted'", '"double"', "$(touch NEVER)", ";exit 9"]
            result = runtime.invoke("process.echo", {"args": arguments})
            observed_cwd, observed_args = json.loads(result["stdout"])
            self.assertEqual(Path(observed_cwd).resolve(), Path(directory).resolve())
            self.assertEqual(observed_args, arguments)
            self.assertEqual(result["returncode"], 0)
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_executable_failures_and_invalid_registration(self):
        for argv in ([], "echo hi", [1], [""], ["a\0b"]):
            with self.assertRaises(UtilityError):
                Runtime().register_executable("bad.command", argv)
        for timeout in (0, -1, True, float("inf")):
            with self.assertRaises(UtilityError):
                Runtime().register_executable("bad.command", ["unused"], timeout=timeout)
        for name, argv, timeout, message in (
            ("missing", ["ellainng-test-missing-executable"], 1, "Cannot start"),
            ("failure", [sys.executable, "-c", "raise SystemExit(7)"], 1, "exit status 7"),
            ("timeout", [sys.executable, "-c", "import time; time.sleep(5)"], 0.05, "timeout"),
        ):
            runtime = Runtime()
            runtime.register_executable(f"process.{name}", argv, timeout=timeout)
            with self.assertRaisesRegex(UtilityError, message):
                runtime.invoke(f"process.{name}")

    def test_cli_and_python_results_agree(self):
        value = "Hello from Ella"
        for arguments in (["run", "text.slugify", "--arguments", json.dumps({"value": value})],
                          ["text", "slugify", value]):
            output = StringIO()
            with redirect_stdout(output):
                app.invoke(arguments)
            self.assertEqual(json.loads(output.getvalue()), "hello-from-ella")

    def test_invalid_json_is_usage_error_and_unknown_name_is_command_error(self):
        for payload in ("[1]", "{", '{"value": NaN}'):
            with redirect_stderr(StringIO()), self.assertRaises(SystemExit) as result:
                main(["run", "text.slugify", "--arguments", payload])
            self.assertEqual(result.exception.code, 2)
        with redirect_stderr(StringIO()), self.assertRaises(SystemExit) as result:
            main(["run", "missing.utility"])
        self.assertEqual(result.exception.code, 1)


if __name__ == "__main__":
    unittest.main()
