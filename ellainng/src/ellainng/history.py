"""Private execution metadata; argument and output contents are never stored."""

from contextlib import contextmanager
from datetime import datetime, timezone
import os
from pathlib import Path
import sqlite3
import sys
import uuid

from .errors import UtilityError


def state_path(explicit: Path | None = None) -> Path:
    if explicit is not None:
        return Path(explicit).expanduser().resolve()
    if os.environ.get("ELLAINNG_STATE_DIR"):
        return Path(os.environ["ELLAINNG_STATE_DIR"]).expanduser().resolve()
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "ellainng" / "state"
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return base / "ellainng" / "state"
    return Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state")) / "ellainng"


class History:
    """SQLite metadata with short transactions for concurrent utility processes."""

    def __init__(self, directory: Path | None = None):
        self.directory = state_path(directory)
        self.path = self.directory / "history.sqlite3"

    @contextmanager
    def _connection(self, *, write: bool = False):
        connection = None
        try:
            if self.path.is_symlink():
                raise UtilityError(f"History database must not be a symlink: {self.path}")
            if write:
                self.directory.mkdir(parents=True, mode=0o700, exist_ok=True)
                try:
                    descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                except FileExistsError:
                    pass
                else:
                    os.close(descriptor)
                if os.name != "nt" and self.path.stat().st_mode & 0o077:
                    raise UtilityError(f"Execution history database must have private permissions: {self.path}")
                connection = sqlite3.connect(self.path, timeout=5)
            else:
                connection = sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True, timeout=5)
            connection.row_factory = sqlite3.Row
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1) or (not write and version != 1):
                raise UtilityError(f"Unsupported history database version: {version}")
            if write:
                with connection:
                    connection.execute("""CREATE TABLE IF NOT EXISTS runs (
                        id TEXT PRIMARY KEY, name TEXT NOT NULL, started_at TEXT NOT NULL,
                        duration REAL, status TEXT NOT NULL, error_type TEXT
                    )""")
                    connection.execute("PRAGMA user_version = 1")
                    yield connection
            else:
                yield connection
        except (sqlite3.Error, OSError) as error:
            raise UtilityError(f"Cannot access execution history {self.path}: {error}") from error
        finally:
            if connection is not None:
                connection.close()

    def start(self, name: str) -> str:
        if not isinstance(name, str) or not name:
            raise UtilityError("A utility name is required.")
        run_id = uuid.uuid4().hex
        started = datetime.now(timezone.utc).isoformat()
        with self._connection(write=True) as connection:
            connection.execute("INSERT INTO runs (id, name, started_at, status) VALUES (?, ?, ?, ?)",
                               (run_id, name, started, "running"))
        return run_id

    def finish(self, run_id: str, duration: float, *, status: str, error_type: str | None = None):
        if status not in ("succeeded", "failed", "cancelled"):
            raise UtilityError("Invalid execution status.")
        with self._connection(write=True) as connection:
            cursor = connection.execute("UPDATE runs SET duration=?, status=?, error_type=? WHERE id=?",
                                        (duration, status, error_type, run_id))
            if cursor.rowcount != 1:
                raise UtilityError(f"No history record for run {run_id!r}.")

    def list(self, limit: int = 20) -> list[dict]:
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise UtilityError("History limit must be an integer from 1 to 1000.")
        if not self.path.exists():
            return []
        with self._connection() as connection:
            return [dict(row) for row in connection.execute(
                "SELECT * FROM runs ORDER BY started_at DESC, id DESC LIMIT ?", (limit,))]

    def show(self, run_id: str) -> dict:
        row = None
        if self.path.exists():
            with self._connection() as connection:
                row = connection.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        if row is None:
            raise UtilityError(f"Unknown run ID {run_id!r}.")
        return dict(row)
