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

## Capabilities

```sh
uv run ella list
uv run ella inspect text.slugify
uv run ella run text.slugify --arguments '{"value":"Café & Hello, World!"}'
uv run ella text slugify 'Café & Hello, World!'
```

`run` validates structured arguments using Hidden Moves, awaits async functions,
and prints a JSON result. Incorrect inputs exit with an error before calling the
utility. Inspection describes the callable without invoking it.

Ordinary functions and literal executable argument arrays can also be registered
from Python:

```python
from ellainng.runtime import Runtime

def repeat(value: str, count: int = 2) -> str:
    return value * count

runtime = Runtime()
runtime.register_python("text.repeat", repeat)
print(runtime.invoke("text.repeat", {"value": "ha", "count": 3}))

runtime.register_executable("git.status", ["git", "status", "--short"], timeout=30)
result = runtime.invoke("git.status", {"args": ["--branch"]})
print(result["stdout"])
```

Each runtime has its own registry. Executables use `shell=False`, append supplied
`args` literally, inherit the caller's environment, and return stdout/stderr on
success. A missing program, nonzero exit, or timeout is a command failure. Async
Python callers can await `runtime.invoke_async(...)`.

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
