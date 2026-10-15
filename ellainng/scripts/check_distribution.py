"""Install and exercise the built wheel outside the checkout."""

import argparse
from pathlib import Path
import subprocess
import sys
import tempfile
import tomllib
import zipfile


def main():
    project = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expose-interface", type=Path,
                        help="Local expose_interface checkout; defaults to the configured uv source.")
    options = parser.parse_args()
    with (project / "pyproject.toml").open("rb") as source:
        metadata = tomllib.load(source)
    configured = metadata.get("tool", {}).get("uv", {}).get("sources", {}).get("expose-interface", {})
    local_source = options.expose_interface
    if local_source is None and "path" in configured:
        local_source = project / configured["path"]
    requirements = []
    if local_source is not None:
        local_source = local_source.expanduser().resolve()
        if not (local_source / "pyproject.toml").is_file():
            parser.error(f"Local expose_interface checkout is missing: {local_source}")
        requirements.append(str(local_source))
    wheels = list((project / "dist").glob("ellainng-*.whl"))
    if len(wheels) != 1:
        raise SystemExit("Build exactly one ellainng wheel before checking the distribution.")
    wheel = wheels[0]
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        if not all(name.startswith(("ellainng/", "ellainng-")) for name in names):
            raise SystemExit("The wheel contains unexpected top-level files.")
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        environment = root / "environment"
        subprocess.run(["uv", "venv", "--python", sys.executable, str(environment)], check=True)
        scripts = environment / ("Scripts" if sys.platform == "win32" else "bin")
        python = scripts / ("python.exe" if sys.platform == "win32" else "python")
        subprocess.run(["uv", "pip", "install", "--python", str(python),
                        str(wheel), *requirements], check=True)
        for alias in ("ella", "ellainng"):
            command = scripts / (alias + ".exe" if sys.platform == "win32" else alias)
            result = subprocess.run([str(command), "--help"], cwd=root, check=True,
                                    capture_output=True, text=True)
            assert "personal utilities" in result.stdout, result.stdout
        subprocess.run([str(python), "-m", "ellainng", "version"], cwd=root, check=True)
        subprocess.run([str(python), "-m", "unittest", "discover", "-s", str(project / "tests"), "-q"],
                       cwd=root, check=True)
    print("Installed wheel verified.")


if __name__ == "__main__":
    main()
