"""Versioned declarative registrations with atomic, checked persistence."""

from copy import deepcopy
import json
import keyword
import math
import os
from pathlib import Path
import sys
import tempfile
import tomllib

from hidden_moves import MoveError, MoveSpec, Registry

from .errors import UtilityError
from .values import validate_arguments


def config_path(explicit: Path | None = None) -> Path:
    if explicit is not None:
        return Path(explicit).expanduser().resolve()
    if os.environ.get("ELLAINNG_CONFIG"):
        return Path(os.environ["ELLAINNG_CONFIG"]).expanduser().resolve()
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    elif sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "ellainng" / "moves.toml"


def target_parts(target: str) -> tuple[str, list[str]]:
    if not isinstance(target, str) or target.count(":") != 1:
        raise UtilityError("Python targets must use installed module:attribute references.")
    module, attribute = target.split(":")
    components = [*module.split("."), *attribute.split(".")]
    if any(not part.isidentifier() or keyword.iskeyword(part) for part in components):
        raise UtilityError("Python targets must use installed module:attribute references.")
    return module, attribute.split(".")


def validate_manifest(data: dict) -> None:
    if not isinstance(data, dict) or set(data) - {"version", "moves", "schedules"}:
        raise UtilityError("Manifest accepts only version, moves, and schedules.")
    if type(data.get("version")) is not int or data["version"] != 1:
        raise UtilityError("Manifest version must be 1.")
    entries = data.get("moves", {})
    if not isinstance(entries, dict):
        raise UtilityError("moves must be a table keyed by quoted dotted names.")
    registry = Registry()
    registry.register(MoveSpec("slugify", lambda: None, namespace="text"))
    for name, entry in entries.items():
        try:
            namespace, _, leaf = name.rpartition(".")
            registry.register(MoveSpec(leaf, lambda: None, namespace=namespace or None))
        except (MoveError, ValueError, TypeError, AttributeError) as error:
            raise UtilityError(f"Invalid or conflicting registration {name!r}: {error}") from error
        if not isinstance(entry, dict):
            raise UtilityError(f"Registration {name!r} must be a table.")
        common = {"kind", "enabled", "defaults", "presets"}
        kind = entry.get("kind")
        allowed = common | ({"target"} if kind == "python" else {"argv", "cwd", "timeout"})
        if kind not in ("python", "executable") or set(entry) - allowed:
            raise UtilityError(f"Registration {name!r} has an unknown kind or field.")
        if type(entry.get("enabled", True)) is not bool:
            raise UtilityError(f"Registration {name!r}: enabled must be true or false.")
        if kind == "python":
            target_parts(entry.get("target"))
        else:
            argv = entry.get("argv")
            if (not isinstance(argv, list) or not argv or not argv[0]
                    or any(not isinstance(part, str) or "\0" in part for part in argv)):
                raise UtilityError(f"Registration {name!r}: argv must be a nonempty string array.")
            timeout = entry.get("timeout", 60)
            if (isinstance(timeout, bool) or not isinstance(timeout, (float, int))
                    or not math.isfinite(timeout) or timeout <= 0):
                raise UtilityError(f"Registration {name!r}: timeout must be positive and finite.")
            if "cwd" in entry and (not isinstance(entry["cwd"], str) or not entry["cwd"]
                                   or "\0" in entry["cwd"]):
                raise UtilityError(f"Registration {name!r}: cwd must be a nonempty path string.")
        validate_arguments(entry.get("defaults", {}))
        presets = entry.get("presets", {})
        if not isinstance(presets, dict):
            raise UtilityError(f"Registration {name!r}: presets must be a table.")
        for preset, arguments in presets.items():
            if not preset:
                raise UtilityError("Preset names must not be empty.")
            validate_arguments(arguments)
    from .scheduling import validate_schedules

    validate_schedules(data.get("schedules", {}), entries)


def _toml_value(value) -> str:
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False).replace("\x7f", "\\u007f")
    if type(value) is bool:
        return "true" if value else "false"
    if type(value) in (int, float) and (not isinstance(value, float) or math.isfinite(value)):
        return repr(value)
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    if isinstance(value, dict):
        return "{ " + ", ".join(f"{_toml_value(key)} = {_toml_value(child)}"
                                for key, child in value.items()) + " }"
    raise UtilityError("TOML cannot store null or non-JSON values; omit that key instead.")


def render_manifest(data: dict) -> bytes:
    data = deepcopy(data)
    validate_manifest(data)
    data.setdefault("moves", {})
    data.setdefault("schedules", {})
    lines = ["version = 1"]
    for section in ("moves", "schedules"):
        lines.extend(["", f"[{section}]"])
        for name, entry in sorted(data.get(section, {}).items()):
            lines.extend(["", f"[{section}.{_toml_value(name)}]"])
            lines.extend(f"{key} = {_toml_value(value)}" for key, value in sorted(entry.items()))
    try:
        content = ("\n".join(lines) + "\n").encode("utf-8")
        # Validate the writer before replacing any user file.
        if tomllib.loads(content.decode()) != data:
            raise UtilityError("Manifest values cannot round-trip through TOML.")
        return content
    except (UnicodeError, tomllib.TOMLDecodeError) as error:
        raise UtilityError(f"Cannot represent manifest as TOML: {error}") from error


class Manifest:
    """A configuration snapshot; stale snapshots cannot replace newer content."""

    def __init__(self, path: Path, data: dict, original: bytes | None):
        self.path, self.data, self._original = path, data, original

    @classmethod
    def load(cls, path: Path | None = None):
        destination = config_path(path)
        try:
            content = destination.read_bytes()
        except FileNotFoundError:
            return cls(destination, {"version": 1, "moves": {}, "schedules": {}}, None)
        except OSError as error:
            raise UtilityError(f"Cannot read configuration {destination}: {error}") from error
        try:
            data = tomllib.loads(content.decode("utf-8"))
        except (UnicodeError, tomllib.TOMLDecodeError) as error:
            raise UtilityError(f"Invalid TOML configuration {destination}: {error}") from error
        validate_manifest(data)
        data.setdefault("moves", {})
        data.setdefault("schedules", {})
        return cls(destination, data, content)

    def save(self, data: dict) -> None:
        data = deepcopy(data)
        data.setdefault("moves", {})
        data.setdefault("schedules", {})
        content = render_manifest(data)
        lock = self.path.with_name(self.path.name + ".lock")
        temporary = None
        acquired = False
        try:
            self.path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
            with lock.open("xb"):
                acquired = True
            def current():
                try:
                    return self.path.read_bytes()
                except FileNotFoundError:
                    return None
            if current() != self._original:
                raise UtilityError("Configuration changed since it was read; retry with a fresh snapshot.")
            with tempfile.NamedTemporaryFile(dir=self.path.parent, delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            if current() != self._original:
                raise UtilityError("Configuration changed during the update; retry.")
            os.replace(temporary, self.path)
            temporary = None
            self.data, self._original = data, content
        except FileExistsError as error:
            raise UtilityError(f"Configuration is locked by another writer: {lock}") from error
        except OSError as error:
            raise UtilityError(f"Cannot write configuration {self.path}: {error}") from error
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
            if acquired:
                lock.unlink(missing_ok=True)

    def add(self, name: str, entry: dict) -> None:
        if name in self.data["moves"]:
            raise UtilityError(f"Registration {name!r} already exists.")
        data = deepcopy(self.data)
        data["moves"][name] = entry
        self.save(data)

    def update(self, name: str, *, enabled: bool | None = None, remove: bool = False,
               defaults: dict | None = None, preset: str | None = None) -> None:
        data = deepcopy(self.data)
        if name not in data["moves"]:
            raise UtilityError(f"No saved registration named {name!r}.")
        if remove:
            del data["moves"][name]
        elif enabled is not None:
            data["moves"][name]["enabled"] = enabled
        elif preset is not None:
            data["moves"][name].setdefault("presets", {})[preset] = defaults
        elif defaults is not None:
            data["moves"][name]["defaults"] = defaults
        self.save(data)
