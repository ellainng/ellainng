"""Temporary configuration and state for every application test."""

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


class IsolatedStateTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        environment = patch.dict(os.environ, {
            "ELLAINNG_CONFIG": str(self.root / "moves.toml"),
            "ELLAINNG_STATE_DIR": str(self.root / "state"),
        })
        environment.start()
        self.addCleanup(environment.stop)
