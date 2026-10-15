"""Application policy around an explicit Hidden Moves registry."""

import asyncio
from collections.abc import Callable
from functools import cached_property
import importlib
import inspect
import math
from pathlib import Path
import subprocess
import time
from typing import Any

from hidden_moves import MoveError, Moves
from hidden_moves.adapters import CapabilityCatalog

from .errors import UtilityError
from .history import History
from .utilities.text import slugify
from .values import validate_arguments


class Runtime:
    """A fresh personal registry; registration never calls a utility."""

    def __init__(self, *, config_path: Path | None = None, state_dir: Path | None = None):
        self.moves = Moves()
        self.config_path = config_path
        self.history = History(state_dir)
        self._loaded = set()
        self.register_python("text.slugify", slugify)

    @cached_property
    def manifest(self):
        from .manifest import Manifest

        return Manifest.load(self.config_path)

    def _prepare(self, name: str, *, allow_disabled: bool = False):
        from .manifest import target_parts

        entry = self.manifest.data["moves"].get(name)
        if entry is None:
            return
        if not allow_disabled and not entry.get("enabled", True):
            raise UtilityError(f"Utility {name!r} is disabled.")
        if name in self._loaded:
            return
        try:
            if entry["kind"] == "python":
                module, attributes = target_parts(entry["target"])
                function = importlib.import_module(module)
                for attribute in attributes:
                    function = getattr(function, attribute)
                self.register_python(name, function)
            else:
                base = self.manifest.path.parent
                argv = list(entry["argv"])
                if "/" in argv[0] or "\\" in argv[0]:
                    argv[0] = str((base / Path(argv[0]).expanduser()).resolve())
                cwd = (base / Path(entry.get("cwd", ".")).expanduser()).resolve()
                self.register_executable(name, argv, cwd=cwd, timeout=entry.get("timeout", 60))
        except UtilityError:
            raise
        except Exception as error:
            raise UtilityError(f"Cannot load utility {name!r}: {error}") from error
        self._loaded.add(name)

    def register_python(self, name: str, function: Callable) -> None:
        namespace, _, leaf = name.rpartition(".")
        try:
            self.moves.learn(function, name=leaf, namespace=namespace or None)
        except (MoveError, TypeError, ValueError) as error:
            raise UtilityError(f"Cannot register {name!r}: {error}") from error

    def register_executable(self, name: str, argv: list[str], *, cwd: Path | None = None,
                            timeout: float = 60.0) -> None:
        """Register a literal command array; invocation appends optional ``args``."""
        if (not isinstance(argv, list) or not argv
                or any(not isinstance(part, str) or "\0" in part for part in argv)
                or not argv[0]):
            raise UtilityError("argv must be a nonempty array of strings without NUL characters.")
        if (isinstance(timeout, bool) or not isinstance(timeout, (int, float))
                or not math.isfinite(timeout) or timeout <= 0):
            raise UtilityError("Executable timeout must be a positive finite number of seconds.")
        command = tuple(argv)

        def execute(args: list[str] | None = None) -> dict[str, Any]:
            """Run the registered executable, preserving each argument literally."""
            try:
                completed = subprocess.run([*command, *(args or [])], cwd=cwd, shell=False,
                                           timeout=timeout, capture_output=True, text=True,
                                           encoding="utf-8", errors="replace")
            except subprocess.TimeoutExpired as error:
                raise UtilityError(f"Executable {name!r} exceeded its {timeout:g}s timeout.") from error
            except (OSError, ValueError) as error:
                raise UtilityError(f"Cannot start executable {name!r}: {error}") from error
            if completed.returncode:
                raise UtilityError(f"Executable {name!r} failed with exit status {completed.returncode}.")
            return {"returncode": completed.returncode,
                    "stdout": completed.stdout, "stderr": completed.stderr}

        self.register_python(name, execute)

    def names(self) -> list[str]:
        return sorted({spec.qualified_name for spec in self.moves.moves()}
                      | self.manifest.data["moves"].keys())

    def describe(self, name: str) -> dict:
        self._prepare(name, allow_disabled=True)
        try:
            return {**self.moves.explain(name),
                    "registration": self.manifest.data["moves"].get(name)}
        except MoveError as error:
            raise UtilityError(str(error)) from error

    def effective_arguments(self, name: str, arguments: dict | None = None,
                            *, preset: str | None = None) -> dict:
        supplied = validate_arguments({} if arguments is None else arguments)
        self._prepare(name)
        definition = self.describe(name)
        properties = (definition.get("input_schema") or {}).get("properties", {})
        effective = {key: value["default"] for key, value in properties.items() if "default" in value}
        entry = self.manifest.data["moves"].get(name, {})
        effective.update(entry.get("defaults", {}))
        if preset is not None:
            presets = entry.get("presets", {})
            if preset not in presets:
                raise UtilityError(f"No preset {preset!r} for {name!r}.")
            effective.update(presets[preset])
        effective.update(supplied)
        return validate_arguments(effective)

    async def invoke_async(self, name: str, arguments: dict | None = None,
                           *, preset: str | None = None):
        run_id = self.history.start(name)
        started = time.monotonic()
        try:
            result = await self._invoke_async(name, arguments, preset=preset)
        except BaseException as error:
            status = "cancelled" if isinstance(error, (asyncio.CancelledError, KeyboardInterrupt)) else "failed"
            try:
                self.history.finish(run_id, time.monotonic() - started, status=status,
                                    error_type=type(error).__name__)
            except UtilityError as storage_error:
                raise UtilityError(f"Run {run_id} failed and its history could not be updated.") from storage_error
            raise
        try:
            self.history.finish(run_id, time.monotonic() - started, status="succeeded")
        except UtilityError as error:
            raise UtilityError(f"Run {run_id} completed, but its history could not be updated; "
                               "the operation has already run.") from error
        return result

    async def _invoke_async(self, name: str, arguments: dict | None = None,
                            *, preset: str | None = None):
        supplied = self.effective_arguments(name, arguments, preset=preset)
        try:
            catalog = CapabilityCatalog(self.moves, [name])
            result = catalog.invoke(name, supplied)
            if inspect.isawaitable(result):
                result = await result
            return catalog.serialize_result(name, result)
        except UtilityError:
            raise
        except Exception as error:
            raise UtilityError(f"Utility {name!r} failed ({type(error).__name__}): {error}") from error

    def invoke(self, name: str, arguments: dict | None = None, *, preset: str | None = None):
        """Invoke from synchronous code; async callers use ``invoke_async``."""
        return asyncio.run(self.invoke_async(name, arguments, preset=preset))
