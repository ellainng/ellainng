"""Application policy around an explicit Hidden Moves registry."""

import asyncio
from collections.abc import Callable
import inspect
import json
import math
from pathlib import Path
import subprocess
from typing import Any

from hidden_moves import MoveError, Moves
from hidden_moves.adapters import CapabilityCatalog

from .errors import UtilityError
from .utilities.text import slugify


def validate_arguments(arguments: dict) -> dict:
    """Require a finite JSON object before selecting or importing a capability."""
    if not isinstance(arguments, dict) or not all(isinstance(key, str) for key in arguments):
        raise UtilityError("Arguments must be a JSON object with string keys.")
    try:
        # Round-tripping also detaches the caller's mutable containers.
        return json.loads(json.dumps(arguments, allow_nan=False))
    except (ValueError, TypeError, OverflowError) as error:
        raise UtilityError("Arguments must contain finite JSON values.") from error


class Runtime:
    """A fresh personal registry; registration never calls a utility."""

    def __init__(self):
        self.moves = Moves()
        self.register_python("text.slugify", slugify)

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
        return sorted(spec.qualified_name for spec in self.moves.moves())

    def describe(self, name: str) -> dict:
        try:
            return self.moves.explain(name)
        except MoveError as error:
            raise UtilityError(str(error)) from error

    async def invoke_async(self, name: str, arguments: dict | None = None):
        supplied = validate_arguments({} if arguments is None else arguments)
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

    def invoke(self, name: str, arguments: dict | None = None):
        """Invoke from synchronous code; async callers use ``invoke_async``."""
        return asyncio.run(self.invoke_async(name, arguments))
