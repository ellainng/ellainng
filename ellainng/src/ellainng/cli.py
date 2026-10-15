"""The permanent command entry points for personal utilities."""

import argparse
import json

from expose_interface import CommandError, Context, Interface, option

from . import __version__
from .errors import UtilityError
from .runtime import Runtime, validate_arguments


def json_object(value: str) -> dict:
    try:
        return validate_arguments(json.loads(value))
    except (json.JSONDecodeError, UtilityError) as error:
        raise argparse.ArgumentTypeError(f"Expected a finite JSON object: {error}") from error


def setup(context: Context):
    context.obj = Runtime()


def emit(value):
    print(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False))


app = Interface("ella", description="Discover, run, and schedule personal utilities.", setup=setup)
text = app.add_interface(Interface("text", description="Text utilities."))


@app.program
def version():
    """Show the installed application version."""
    print(__version__)


@app.program(name="list")
def list_utilities(context: Context):
    """List registered capability names without executing them."""
    for name in context.obj.names():
        print(name)


@app.program(name="inspect")
def inspect_utility(context: Context, name: str):
    """Show a capability's signature and structured argument contract."""
    try:
        emit(context.obj.describe(name))
    except UtilityError as error:
        raise CommandError(str(error)) from error


@app.program
@option("--arguments", type=json_object, default={}, help="Utility arguments as a JSON object.")
async def run(context: Context, name: str, arguments: dict | None = None):
    """Run one explicitly selected utility with validated arguments."""
    try:
        emit(await context.obj.invoke_async(name, arguments))
    except UtilityError as error:
        raise CommandError(str(error)) from error


@text.program
async def slugify(context: Context, value: str, separator: str = "-"):
    """Turn text into an ASCII slug."""
    try:
        emit(await context.obj.invoke_async("text.slugify", {"value": value, "separator": separator}))
    except UtilityError as error:
        raise CommandError(str(error)) from error


def main(argv=None):
    """Run the console interface."""
    app.main(argv)
