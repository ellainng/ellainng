"""Execution outcomes, privacy, and concurrent storage."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
from io import StringIO
import json
import os
from pathlib import Path
import sys
from unittest.mock import patch

from ellainng.cli import app
from ellainng.errors import UtilityError
from ellainng.history import History
from ellainng.runtime import Runtime
from support import IsolatedStateTests


class HistoryTests(IsolatedStateTests):
    def test_outcomes_and_secrets_are_not_stored(self):
        runtime = Runtime()
        secret = "SYNTHETIC_SECRET_CONTENT_12345"

        def echo(value: str) -> str:
            return value

        def fail(value: str) -> str:
            raise ValueError(value)

        runtime.register_python("test.echo", echo)
        runtime.register_python("test.fail", fail)
        self.assertEqual(runtime.invoke("test.echo", {"value": secret}), secret)
        with self.assertRaises(UtilityError):
            runtime.invoke("test.fail", {"value": secret})
        with self.assertRaises(UtilityError):
            runtime.invoke("test.echo", {"value": [secret]})
        runtime.register_executable("test.process", [sys.executable, "-c", "raise SystemExit(7)"])
        with self.assertRaises(UtilityError):
            runtime.invoke("test.process")
        rows = runtime.history.list()
        self.assertEqual(len(rows), 4)
        self.assertEqual(sum(row["status"] == "succeeded" for row in rows), 1)
        self.assertTrue(all(row["duration"] >= 0 for row in rows))
        self.assertTrue(all(row["started_at"].endswith("+00:00") for row in rows))
        self.assertNotIn(secret, json.dumps(rows))
        for path in runtime.history.directory.iterdir():
            self.assertNotIn(secret.encode(), path.read_bytes())
        self.assertEqual(runtime.history.show(rows[0]["id"]), rows[0])
        if os.name != "nt":
            self.assertEqual(runtime.history.path.stat().st_mode & 0o777, 0o600)

    def test_inspection_help_and_empty_history_do_not_create_database(self):
        runtime = Runtime()
        runtime.names()
        runtime.describe("text.slugify")
        runtime.effective_arguments("text.slugify")
        output = StringIO()
        with redirect_stdout(output):
            app.invoke(["history", "list"])
        self.assertEqual(json.loads(output.getvalue()), [])
        self.assertFalse(runtime.history.directory.exists())
        with self.assertRaises(UtilityError):
            runtime.history.show("missing")
        self.assertFalse(runtime.history.directory.exists())

    def test_storage_failure_prevents_execution(self):
        blocked = self.root / "blocked"
        blocked.write_text("ordinary file")
        calls = []

        def touch() -> str:
            calls.append(True)
            return "done"

        runtime = Runtime(state_dir=blocked)
        runtime.register_python("test.touch", touch)
        with self.assertRaisesRegex(UtilityError, "history"):
            runtime.invoke("test.touch")
        self.assertEqual(calls, [])

    def test_concurrent_runs_have_distinct_complete_records(self):
        def run(index):
            return Runtime().invoke("text.slugify", {"value": str(index)})

        with ThreadPoolExecutor(max_workers=4) as executor:
            self.assertEqual(list(executor.map(run, range(12))), [str(index) for index in range(12)])
        rows = History().list()
        self.assertEqual(len(rows), 12)
        self.assertEqual(len({row["id"] for row in rows}), 12)
        self.assertTrue(all(row["status"] == "succeeded" for row in rows))

    def test_cancellation_is_recorded_and_propagates(self):
        async def cancelled() -> str:
            raise asyncio.CancelledError()

        runtime = Runtime()
        runtime.register_python("test.cancelled", cancelled)
        with self.assertRaises(asyncio.CancelledError):
            asyncio.run(runtime.invoke_async("test.cancelled"))
        self.assertEqual(runtime.history.list()[0]["status"], "cancelled")

    def test_bounded_reads_and_read_errors(self):
        history = History()
        for limit in (0, 1001, True):
            with self.assertRaises(UtilityError):
                history.list(limit)
        history.directory.mkdir()
        history.path.write_bytes(b"invalid sqlite data")
        with self.assertRaisesRegex(UtilityError, "history"):
            history.list()

    def test_finished_operation_is_not_retried_when_history_completion_fails(self):
        runtime = Runtime()
        calls = []

        def count() -> int:
            calls.append(True)
            return len(calls)

        runtime.register_python("test.count", count)
        with patch.object(runtime.history, "finish", side_effect=UtilityError("unavailable")):
            with self.assertRaisesRegex(UtilityError, "already run"):
                runtime.invoke("test.count")
        self.assertEqual(calls, [True])
        self.assertEqual(runtime.history.list()[0]["status"], "running")
