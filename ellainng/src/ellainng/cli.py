"""The permanent command entry points for personal utilities."""

from expose_interface import Interface

from . import __version__

app = Interface("ella", description="Discover, run, and schedule personal utilities.")


@app.program
def version():
    """Show the installed application version."""
    print(__version__)


def main(argv=None):
    """Run the console interface."""
    app.main(argv)
