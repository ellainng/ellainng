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

Install the command into its own environment from the repository root:

```sh
uv tool install ./ellainng
```

Both `ella` and `ellainng` invoke the same interface. Importing the package and
displaying help perform no utility execution or user configuration writes.

Both `hidden-moves==0.1.0` and `expose-interface==0.1.0` are installed from PyPI.
Development, CI, and built-wheel installation use these published distributions;
no neighboring source checkout or GitHub authorization is required.

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

## Persistent catalog

Select a manifest with `ella --config /path/to/moves.toml ...`, then
`ELLAINNG_CONFIG`, or the default shown by `ella config path`. macOS uses
`~/Library/Application Support/ellainng/moves.toml`; Linux uses
`${XDG_CONFIG_HOME:-~/.config}/ellainng/moves.toml`; Windows uses
`%APPDATA%/ellainng/moves.toml`.

```sh
uv run ella config init
uv run ella learn python text.slug ellainng.utilities.text:slugify
uv run ella defaults set text.slug --arguments '{"separator":"_"}'
uv run ella preset set text.slug hyphens --arguments '{"separator":"-"}'
uv run ella arguments text.slug --preset hyphens --arguments '{"value":"Hello World"}'
uv run ella run text.slug --preset hyphens --arguments '{"value":"Hello World"}'
uv run ella disable text.slug
uv run ella enable text.slug
uv run ella remove text.slug
uv run ella config validate
```

Saved defaults override callable defaults; a selected preset overrides saved
defaults; explicit arguments override the preset. `arguments` previews that
merge without calling the utility. `inspect` and `arguments` may import the
explicitly selected target to inspect its schema. Python imports execute trusted
code, so only register targets you trust. Registration, listing, validation, and
help do not import those targets. A fresh CLI invocation reads a fresh snapshot.

Register an executable or a script with an explicit interpreter:

```sh
uv run ella learn executable git.status --argv '["git","status","--short"]' --cwd .
uv run ella learn executable files.organize --argv '["python3","./organize.py"]' --timeout 30
uv run ella run git.status --arguments '{"args":["--branch"]}'
```

Relative paths and working directories resolve beside the manifest. Bare
executable names use the caller's PATH. No shell expression is evaluated. Choose
absolute interpreter/program paths when the environment needs to be reproducible.
Registering a new name does not install a new top-level shell executable; use
`ella run NAME` for saved utilities.

See [config/example.toml](config/example.toml) for the version 1 format. Dotted
move names are quoted TOML keys. Built-in names and conflicting namespace paths
cannot be replaced by saved registrations. Writes validate the whole manifest,
detect stale snapshots, serialize concurrent command writers with a sidecar lock,
and atomically replace the file. The managed writer normalizes formatting and
comments; back up hand-edited comments separately. TOML has no null value: omit
those saved keys and pass null explicitly at invocation when needed. Credentials
belong in the caller's environment, outside the manifest.

## Execution history

```sh
uv run ella history list --limit 10
uv run ella history show RUN_ID
```

Each runtime invocation records a unique run ID, capability name, UTC start time,
elapsed seconds, outcome, and error class. Argument values, environment values,
output, and exception messages are excluded. Input failures reaching the runtime
are recorded once; CLI parsing failures are rejected before runtime setup and
create no record. A process killed before completion can leave a `running` row.

Select storage with the global `--state-dir PATH` option or `ELLAINNG_STATE_DIR`.
Defaults are `~/Library/Application Support/ellainng/state` on macOS,
`${XDG_STATE_HOME:-~/.local/state}/ellainng` on Linux, and
`%LOCALAPPDATA%/ellainng/state` on Windows. The database is created privately on
the first run. Listing, inspection, help, and empty history reads create no state.
Storage must be writable before a utility starts. A completion-storage failure
reports that an operation has already run and never retries the operation.

## Scheduling

Save a definition and review its export before installing it:

```sh
uv run ella schedule add daily-slug text.slugify --at 02:00 --arguments '{"value":"Hello World"}'
uv run ella schedule add frequent-slug text.slugify --minutes 30 --arguments '{"value":"Hello World"}'
uv run ella schedule list
uv run ella schedule show daily-slug
uv run ella schedule run daily-slug
uv run ella schedule export daily-slug
```

Add `--preset NAME` to select a saved move preset. Definitions and exports are
portable; native installation is currently available on macOS:

```sh
uv run ella schedule install daily-slug
uv run ella schedule status daily-slug
uv run ella schedule uninstall daily-slug
uv run ella schedule remove daily-slug
```

Installation writes an application-owned plist in `~/Library/LaunchAgents` and
bootstraps it in the current user's GUI session. Existing unrelated files and
loaded labels are refused. Removal verifies the plist's command/configuration
identity and the loaded job's origin before acting. An unrecognized launchctl
diagnostic format also prevents removal. Schedule timing or interpreter changes
require explicit uninstall/reinstall. Uninstall keeps the definition, history,
and logs; remove deletes only an uninstalled definition. Referenced move/preset
registrations must remain valid until their schedule is removed.

The plist preserves the installed interpreter path, explicit config/state paths,
and argument boundaries. It does not copy the interactive shell's environment or
credentials. Use absolute executable/interpreter paths for background scripts;
launchd's PATH, available credentials, and permissions may differ from a terminal.
Moving the configuration or removing the installed environment breaks that job's
command; uninstall before moving either.

Before installation, preflight checks capability exposure and Python argument
binding without executing the utility. Type validation is performed by the
catalog on every actual run. Hidden Moves currently provides validation through
invocation rather than a standalone public argument-validation method.

Background execution uses the same presets, catalog, and SQLite history as
interactive invocation. Private status logs live in the state directory's
`schedules/` folder; ordinary Python stdout/stderr, returned values, and exception
messages are suppressed by the background command. Utility code remains trusted
code and can explicitly write its own output or files. Logs are retained until
you remove them; automatic rotation is not included in this initial version.

Daily times use the Mac's local calendar. A daily job missed during sleep can
run on wake; power-off can miss a daily run. Interval firings can be skipped
during sleep or while the job is already running. Neither mode promises
continuous execution. See Apple's [scheduling guide](https://developer.apple.com/library/archive/documentation/MacOSX/Conceptual/BPSystemStartup/Chapters/ScheduledJobs.html)
and the installed `launchd.plist(5)`/`launchctl(1)` manuals for platform behavior.
Development verifies plist export and lifecycle with temporary files and a
simulated launchctl. A real personal job requires a separately selected utility
and explicit activation.

## Development

```sh
uv sync --locked
uv run python -m unittest discover -s tests -v
uv build
uv run python scripts/check_distribution.py
```

The distribution check creates a temporary environment, installs the built wheel,
and verifies both commands, module execution, and the complete behavior suite
against the installed wheel from outside the checkout. CI
runs the tests on Linux, macOS, and Windows and verifies the built distribution.
Releases and package publication are separate from source development.
