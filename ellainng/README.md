# ellainng

A personal utility environment built with
[expose_interface](https://github.com/outside-labs/expose_interface) and
[Hidden Moves](https://github.com/outside-labs/hidden_moves).

The repository's root README is the GitHub profile. This directory contains the
installable application, its configuration examples, and its tests.

## Installation

Python 3.14 or newer is required. From this directory:

```sh
uv sync --locked
uv run ella --help
uv run ellainng version
uv run python -m ellainng --help
```

Install the command into its own environment from the repository checkout:

```sh
uv tool install ./ellainng
```

Both `ella` and `ellainng` invoke the same interface. Importing the package and
displaying help perform no utility execution or user configuration writes.

Hidden Moves is installed from PyPI. Until expose-interface is published, its
dependency uses a specific reviewed GitHub revision. Installation therefore
requires Git and authorization to access that source repository while it is private.

## Development

```sh
uv sync --locked
uv run python -m unittest discover -s tests -v
uv build
uv run python scripts/check_distribution.py
```

The distribution check creates a temporary environment, installs the built wheel,
and verifies both commands and module execution from outside the checkout. CI
runs the tests on Linux, macOS, and Windows and verifies the built distribution.
Releases and package publication are separate from source development.
