"""The permanent command entry points for personal utilities."""

import argparse
from functools import wraps
import json
from pathlib import Path

from expose_interface import CommandError, Context, Interface, option

from . import __version__
from .errors import UtilityError
from .manifest import config_path
from .runtime import Runtime, validate_arguments


def json_object(value: str) -> dict:
    try:
        return validate_arguments(json.loads(value))
    except (json.JSONDecodeError, UtilityError) as error:
        raise argparse.ArgumentTypeError(f"Expected a finite JSON object: {error}") from error


def setup(context: Context, config: Path | None = None, state_dir: Path | None = None):
    context.obj = Runtime(config_path=config, state_dir=state_dir)


def expected(function):
    """Translate expected storage/registration failures at the CLI boundary."""
    @wraps(function)
    def invoke(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except UtilityError as error:
            raise CommandError(str(error)) from error
    return invoke


def emit(value):
    print(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False))


app = Interface("ella", description="Discover, run, and schedule personal utilities.", setup=setup)
text = app.add_interface(Interface("text", description="Text utilities."))
config = app.add_interface(Interface("config", description="Manage the persistent manifest."))
learn = app.add_interface(Interface("learn", description="Explicitly trust and register utilities."))
defaults = app.add_interface(Interface("defaults", description="Save default arguments."))
presets = app.add_interface(Interface("preset", description="Save named argument presets."))
history = app.add_interface(Interface("history", description="Inspect private execution metadata."))


@app.program
def version():
    """Show the installed application version."""
    print(__version__)


@app.program(name="list")
@expected
def list_utilities(context: Context):
    """List registered capability names without executing them."""
    for name in context.obj.names():
        entry = context.obj.manifest.data["moves"].get(name, {})
        print(name + ("\t(disabled)" if not entry.get("enabled", True) else ""))


@app.program(name="inspect")
def inspect_utility(context: Context, name: str):
    """Show a capability's signature and structured argument contract."""
    try:
        emit(context.obj.describe(name))
    except UtilityError as error:
        raise CommandError(str(error)) from error


@app.program
@option("--arguments", type=json_object, default={}, help="Utility arguments as a JSON object.")
async def run(context: Context, name: str, arguments: dict | None = None,
              preset: str | None = None):
    """Run one explicitly selected utility with validated arguments."""
    try:
        emit(await context.obj.invoke_async(name, arguments, preset=preset))
    except UtilityError as error:
        raise CommandError(str(error)) from error


@text.program
async def slugify(context: Context, value: str, separator: str = "-"):
    """Turn text into an ASCII slug."""
    try:
        emit(await context.obj.invoke_async("text.slugify", {"value": value, "separator": separator}))
    except UtilityError as error:
        raise CommandError(str(error)) from error


@app.program(name="arguments")
@expected
@option("--arguments", type=json_object, default={})
def effective_arguments(context: Context, name: str, arguments: dict | None = None,
                        preset: str | None = None):
    """Inspect merged callable defaults, saved defaults, preset, and explicit inputs."""
    emit(context.obj.effective_arguments(name, arguments, preset=preset))


@config.program(name="path")
def show_config_path(context: Context):
    """Show the selected configuration path without reading or creating it."""
    print(config_path(context.obj.config_path))


@config.program(name="init")
@expected
def init_config(context: Context):
    """Create a private manifest, refusing to replace an existing file."""
    manifest = context.obj.manifest
    if manifest.path.exists():
        raise UtilityError(f"Configuration already exists: {manifest.path}")
    manifest.save(manifest.data)
    print(manifest.path)


@config.program(name="validate")
@expected
def validate_config(context: Context):
    """Validate the manifest without importing any registered Python target."""
    manifest = context.obj.manifest
    if not manifest.path.is_file():
        raise UtilityError(f"Configuration does not exist: {manifest.path}")
    print(f"Valid: {manifest.path}")


@learn.program(name="python")
@expected
def learn_python(context: Context, name: str, target: str):
    """Trust an installed module:attribute reference without importing or running it."""
    context.obj.manifest.add(name, {"kind": "python", "target": target})
    print(name)


def json_argv(value: str) -> list:
    try:
        parsed = json.loads(value)
        if not isinstance(parsed, list) or not parsed or any(not isinstance(part, str) for part in parsed):
            raise ValueError("expected a nonempty array of strings")
        return parsed
    except (json.JSONDecodeError, ValueError) as error:
        raise argparse.ArgumentTypeError(str(error)) from error


@learn.program(name="executable")
@expected
@option("--argv", type=json_argv, required=True, help="Literal command array as JSON.")
def learn_executable(context: Context, name: str, argv: list,
                     cwd: Path | None = None, timeout: float = 60):
    """Trust a literal executable/interpreter command; never evaluate a shell string."""
    entry = {"kind": "executable", "argv": argv, "timeout": timeout}
    if cwd is not None:
        entry["cwd"] = str(cwd)
    context.obj.manifest.add(name, entry)
    print(name)


@app.program
@expected
def enable(context: Context, name: str):
    """Enable one saved registration."""
    context.obj.manifest.update(name, enabled=True)


@app.program
@expected
def disable(context: Context, name: str):
    """Disable one saved registration."""
    context.obj.manifest.update(name, enabled=False)


@app.program
@expected
def remove(context: Context, name: str):
    """Remove one saved registration."""
    context.obj.manifest.update(name, remove=True)


@defaults.program(name="set")
@expected
@option("--arguments", type=json_object, required=True)
def set_defaults(context: Context, name: str, arguments: dict):
    """Replace a registration's saved argument defaults."""
    context.obj.manifest.update(name, defaults=arguments)


@presets.program(name="set")
@expected
@option("--arguments", type=json_object, required=True)
def set_preset(context: Context, name: str, preset: str, arguments: dict):
    """Create or replace one named preset."""
    context.obj.manifest.update(name, defaults=arguments, preset=preset)


@history.program(name="list")
@expected
def list_history(context: Context, limit: int = 20):
    """Show recent runs without argument, output, or exception contents."""
    emit(context.obj.history.list(limit))


@history.program(name="show")
@expected
def show_history(context: Context, run_id: str):
    """Show one run's name, UTC time, duration, and outcome."""
    emit(context.obj.history.show(run_id))


def main(argv=None):
    """Run the console interface."""
    app.main(argv)
