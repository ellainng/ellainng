"""Install and exercise the built wheel outside the checkout."""

from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile


def main():
    project = Path(__file__).resolve().parents[1]
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
        subprocess.run(["uv", "pip", "install", "--python", str(python), str(wheel)], check=True)
        for alias in ("ella", "ellainng"):
            command = scripts / (alias + ".exe" if sys.platform == "win32" else alias)
            result = subprocess.run([str(command), "--help"], cwd=root, check=True,
                                    capture_output=True, text=True)
            assert "personal utilities" in result.stdout, result.stdout
        subprocess.run([str(python), "-m", "ellainng", "version"], cwd=root, check=True)
    print("Installed wheel verified.")


if __name__ == "__main__":
    main()
