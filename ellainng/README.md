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

Keep the `expose_interface` checkout beside this repository under `Projects/`.
The development source in `pyproject.toml` points to `../../expose_interface`
relative to this Python project, so `uv sync --locked` installs it editably.

Install the command into its own environment from the repository root:

```sh
uv tool install ./ellainng --with ../expose_interface
```

Both `ella` and `ellainng` invoke the same interface. Importing the package and
displaying help perform no utility execution or user configuration writes.

Hidden Moves is installed from PyPI. Until expose-interface is published, local
development and distribution checks use the neighboring source checkout. Wheel
metadata contains the ordinary `expose-interface==0.1.0` requirement; the uv source
override supplies the local package without embedding machine paths or private
GitHub URLs in the built distribution. Hosted CI also needs that source checkout
or an available package release before it can run.

## Development

```sh
uv sync --locked
uv run python -m unittest discover -s tests -v
uv build
uv run python scripts/check_distribution.py
```

The distribution check creates a temporary environment, installs the built wheel,
and the configured local expose_interface source, then verifies both commands
and module execution from outside the checkout. Pass `--expose-interface PATH`
to use a different checkout for this check. CI
runs the tests on Linux, macOS, and Windows and verifies the built distribution.
Releases and package publication are separate from source development.
