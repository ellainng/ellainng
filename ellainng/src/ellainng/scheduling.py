"""Declarative jobs and explicit, ownership-checked macOS scheduling."""

from copy import deepcopy
import hashlib
import inspect
import os
from pathlib import Path
import plistlib
import re
import subprocess
import sys
from xml.parsers.expat import ExpatError

from hidden_moves import MoveError
from hidden_moves.adapters import CapabilityCatalog

from .errors import UtilityError
from .values import validate_arguments


def validate_schedules(schedules: dict, registrations: dict) -> None:
    if not isinstance(schedules, dict):
        raise UtilityError("schedules must be a table.")
    for name, entry in schedules.items():
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", name):
            raise UtilityError("Schedule names must contain 1–64 letters, digits, hyphens, or underscores.")
        if not isinstance(entry, dict) or set(entry) - {"move", "arguments", "preset", "at", "minutes"}:
            raise UtilityError(f"Schedule {name!r} has an invalid field.")
        move = entry.get("move")
        if not isinstance(move, str) or move not in {"text.slugify", *registrations}:
            raise UtilityError(f"Schedule {name!r} references an unknown move.")
        registration = registrations.get(move, {})
        if not registration.get("enabled", True):
            raise UtilityError(f"Schedule {name!r} references a disabled move; remove the schedule first.")
        if ("at" in entry) == ("minutes" in entry):
            raise UtilityError(f"Schedule {name!r} needs exactly one of at or minutes.")
        if "at" in entry and (not isinstance(entry["at"], str)
                              or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", entry["at"])):
            raise UtilityError("Daily times must use 24-hour HH:MM.")
        if "minutes" in entry and (type(entry["minutes"]) is not int
                                   or not 1 <= entry["minutes"] <= 35_791_394):
            raise UtilityError("Intervals must be positive whole minutes within launchd's integer range.")
        validate_arguments(entry.get("arguments", {}))
        if "preset" in entry and (not isinstance(entry["preset"], str)
                                   or entry["preset"] not in registration.get("presets", {})):
            raise UtilityError(f"Schedule {name!r} references an unknown preset.")


class Launchd:
    """A bounded launchctl boundary; exports never invoke launchctl or write files."""

    def __init__(self, runtime, *, agents_dir: Path | None = None, python: Path | None = None,
                 runner=None, platform: str | None = None, uid: int | None = None):
        self.runtime = runtime
        self.agents_dir = (agents_dir or Path.home() / "Library" / "LaunchAgents").expanduser().resolve()
        # Preserve venv symlinks: resolving python would select the base interpreter.
        self.python = (python or Path(sys.executable)).absolute()
        self.runner = runner or subprocess.run
        self.platform = sys.platform if platform is None else platform
        self.uid = uid

    def label(self, name: str) -> str:
        identity = f"{self.runtime.manifest.path}\0{name}".encode()
        return "io.github.ellainng.utility." + hashlib.sha256(identity).hexdigest()[:24]

    def path(self, name: str) -> Path:
        return self.agents_dir / (self.label(name) + ".plist")

    def definition(self, name: str) -> dict:
        try:
            return self.runtime.manifest.data["schedules"][name]
        except KeyError as error:
            raise UtilityError(f"Unknown schedule {name!r}.") from error

    def document(self, name: str) -> dict:
        definition = self.definition(name)
        logs = self.runtime.history.directory / "schedules"
        label = self.label(name)
        document = {
            "Label": label,
            "ProgramArguments": [str(self.python), "-m", "ellainng", "--config",
                                 str(self.runtime.manifest.path), "--state-dir",
                                 str(self.runtime.history.directory), "schedule", "execute", name],
            "WorkingDirectory": str(self.runtime.manifest.path.parent),
            "StandardOutPath": str(logs / (label + ".out.log")),
            "StandardErrorPath": str(logs / (label + ".err.log")),
            "Umask": 0o077,
            "RunAtLoad": False,
        }
        if "at" in definition:
            hour, minute = map(int, definition["at"].split(":"))
            document["StartCalendarInterval"] = {"Hour": hour, "Minute": minute}
        else:
            document["StartInterval"] = definition["minutes"] * 60
        return document

    def export(self, name: str) -> bytes:
        return plistlib.dumps(self.document(name), fmt=plistlib.FMT_XML, sort_keys=True)

    def _domain(self) -> str:
        if self.platform != "darwin":
            raise UtilityError("LaunchAgent installation and removal require macOS; use schedule export here.")
        return f"gui/{os.getuid() if self.uid is None else self.uid}"

    def _call(self, *arguments):
        try:
            return self.runner(["/bin/launchctl", *arguments], capture_output=True, text=True,
                               timeout=15, env=dict(os.environ, LC_ALL="C"))
        except (OSError, subprocess.TimeoutExpired) as error:
            raise UtilityError(f"Cannot run launchctl: {error}") from error

    def _loaded(self, name: str) -> bool:
        target = self._domain() + "/" + self.label(name)
        result = self._call("print", target)
        if result.returncode:
            diagnostic = (result.stdout + result.stderr).lower()
            if "could not find service" in diagnostic or "could not find specified service" in diagnostic:
                return False
            raise UtilityError(f"Cannot inspect launchd job {target}: {result.stderr.strip()}")
        # launchctl's diagnostic text is not a stable API. Fail closed if ownership
        # cannot be established from an exact origin path in the installed output.
        paths = re.findall(r"^\s*path = (.+?)\s*$", result.stdout, flags=re.MULTILINE)
        if len(paths) != 1 or Path(paths[0]) != self.path(name):
            raise UtilityError(f"Loaded label {self.label(name)!r} has an unverified origin; refusing changes.")
        return True

    def _installed(self, name: str) -> dict | None:
        path = self.path(name)
        if path.is_symlink():
            raise UtilityError(f"Refusing a symlink at the LaunchAgent path: {path}")
        try:
            content = path.read_bytes()
        except FileNotFoundError:
            return None
        except OSError as error:
            raise UtilityError(f"Cannot inspect LaunchAgent {path}: {error}") from error
        try:
            installed = plistlib.loads(content)
        except (plistlib.InvalidFileException, ValueError, TypeError, ExpatError) as error:
            raise UtilityError(f"Existing LaunchAgent is not a recognized plist: {path}") from error
        expected = self.document(name)
        identity = ("Label", "WorkingDirectory", "StandardOutPath", "StandardErrorPath")
        if not isinstance(installed, dict) or any(installed.get(key) != expected[key] for key in identity):
            raise UtilityError(f"Existing LaunchAgent is not owned by this configuration: {path}")
        arguments = installed.get("ProgramArguments")
        if (not isinstance(arguments, list) or len(arguments) != len(expected["ProgramArguments"])
                or not isinstance(arguments[0], str) or not Path(arguments[0]).is_absolute()
                or arguments[1:] != expected["ProgramArguments"][1:]):
            raise UtilityError(f"Existing LaunchAgent is not owned by this configuration: {path}")
        return installed

    def status(self, name: str) -> dict:
        self.definition(name)
        installed = self._installed(name)
        supported = self.platform == "darwin"
        return {"name": name, "label": self.label(name), "path": str(self.path(name)),
                "supported": supported, "installed": installed is not None,
                "loaded": self._loaded(name) if supported else None,
                "needs_reinstall": installed is not None and installed != self.document(name)}

    def install(self, name: str) -> dict:
        domain = self._domain()
        installed = self._installed(name)
        loaded = self._loaded(name)
        if loaded:
            if installed is None:
                raise UtilityError("Loaded job has no verified installed plist; refusing changes.")
            if installed != self.document(name):
                raise UtilityError("Installed schedule differs; uninstall it before installing the new definition.")
            return self.status(name)
        if installed is not None and installed != self.document(name):
            raise UtilityError("Installed schedule differs; uninstall it before installing the new definition.")
        path = self.path(name)
        created = False
        try:
            self.agents_dir.mkdir(parents=True, exist_ok=True)
            for key in ("StandardOutPath", "StandardErrorPath"):
                log = Path(self.document(name)[key])
                log.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
                if log.is_symlink():
                    raise UtilityError(f"Refusing a symlink at log path: {log}")
                descriptor = os.open(log, os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
                try:
                    if os.name != "nt" and os.fstat(descriptor).st_mode & 0o077:
                        raise UtilityError(f"Schedule log must have private permissions: {log}")
                finally:
                    os.close(descriptor)
            if installed is None:
                descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                created = True
                with os.fdopen(descriptor, "wb") as destination:
                    destination.write(self.export(name))
                    destination.flush()
                    os.fsync(destination.fileno())
            result = self._call("bootstrap", domain, str(path))
            if result.returncode:
                # Keep the plist when job state is ambiguous; never discard an
                # existing job's file after a failed scheduler operation.
                if (created and not self._loaded(name)
                        and self._installed(name) == self.document(name)):
                    path.unlink()
                raise UtilityError(f"launchctl bootstrap failed: {result.stderr.strip()}")
            return self.status(name)
        except OSError as error:
            raise UtilityError(f"Cannot install LaunchAgent {path}: {error}") from error

    def uninstall(self, name: str) -> None:
        self._domain()
        installed = self._installed(name)
        loaded = self._loaded(name)
        if installed is None:
            if loaded:
                raise UtilityError("Loaded job has no verified installed plist; refusing changes.")
            return
        if loaded:
            result = self._call("bootout", self._domain() + "/" + self.label(name))
            if result.returncode:
                raise UtilityError(f"launchctl bootout failed; plist preserved: {result.stderr.strip()}")
            if self._loaded(name):
                raise UtilityError("Job still appears loaded; plist preserved.")
        if self._installed(name) != installed:
            raise UtilityError("LaunchAgent changed during removal; file preserved.")
        try:
            self.path(name).unlink()
        except OSError as error:
            raise UtilityError(f"Job is unloaded but its plist could not be removed: {error}") from error


class Scheduler:
    """Schedule definitions reuse the interactive runtime and its history."""

    def __init__(self, runtime, *, backend: Launchd | None = None):
        self.runtime = runtime
        self.backend = backend or Launchd(runtime)

    def add(self, name: str, move: str, *, at: str | None = None, minutes: int | None = None,
            arguments: dict | None = None, preset: str | None = None):
        data = deepcopy(self.runtime.manifest.data)
        if name in data["schedules"]:
            raise UtilityError(f"Schedule {name!r} already exists.")
        entry = {"move": move, "arguments": validate_arguments({} if arguments is None else arguments)}
        if at is not None:
            entry["at"] = at
        if minutes is not None:
            entry["minutes"] = minutes
        if preset is not None:
            entry["preset"] = preset
        data["schedules"][name] = entry
        self.runtime.manifest.save(data)

    def remove(self, name: str):
        status = self.backend.status(name)
        if status["installed"] or status["loaded"]:
            raise UtilityError("Uninstall the LaunchAgent before removing its schedule definition.")
        data = deepcopy(self.runtime.manifest.data)
        del data["schedules"][name]
        self.runtime.manifest.save(data)

    def preflight(self, name: str):
        entry = self.backend.definition(name)
        arguments = self.runtime.effective_arguments(entry["move"], entry.get("arguments", {}),
                                                     preset=entry.get("preset"))
        try:
            CapabilityCatalog(self.runtime.moves, [entry["move"]])
            inspect.signature(self.runtime.moves.resolve(entry["move"])).bind(**arguments)
        except (MoveError, TypeError, ValueError) as error:
            raise UtilityError(f"Schedule {name!r} cannot bind its arguments: {error}") from error

    async def run_async(self, name: str):
        entry = self.backend.definition(name)
        return await self.runtime.invoke_async(entry["move"], entry.get("arguments", {}),
                                                preset=entry.get("preset"))

    def install(self, name: str):
        self.preflight(name)
        return self.backend.install(name)
