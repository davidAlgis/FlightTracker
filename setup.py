#!/usr/bin/env python3
"""
setup.py

Creates (or re-uses) a virtual environment in ./env, installs runtime
dependencies from pyproject.toml, then freezes the GUI application with
cx_Freeze into ./build.

 • Executable ........... Flight Tracker.exe   (Win32 GUI, no console)
 • Icon ................. assets/flight_tracker.ico
 • Extra files .......... entire assets/ folder copied beside the exe
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import venv
from pathlib import Path

# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #
ROOT_DIR = Path(__file__).resolve().parent
ENV_DIR = ROOT_DIR / "env"
BUILD_DIR = ROOT_DIR / "build"
ASSETS_DIR = ROOT_DIR / "assets"

ENTRY_PY = ROOT_DIR / "flight_tracker" / "__main__.py"
ICON_PATH = ASSETS_DIR / "flight_tracker.ico"


# --------------------------------------------------------------------------- #
# Virtual-environment helpers
# --------------------------------------------------------------------------- #
def create_virtualenv() -> tuple[Path, Path]:
    """Return absolute paths to (python, pip) in ./env, creating it if needed."""
    if not ENV_DIR.exists():
        venv.create(ENV_DIR, with_pip=True)

    if os.name == "nt":
        py = ENV_DIR / "Scripts" / "python.exe"
        pip = ENV_DIR / "Scripts" / "pip.exe"
    else:
        py = ENV_DIR / "bin" / "python"
        pip = ENV_DIR / "bin" / "pip"

    return py, pip


def _ensure_tomllib(pip_bin: Path):
    """Import tomllib or install tomli for Python < 3.11."""
    try:
        import tomllib  # Python ≥ 3.11
    except ModuleNotFoundError:
        subprocess.check_call([pip_bin, "install", "tomli"])
        import tomli as tomllib  # type: ignore
    return tomllib  # pyright: ignore[reportGeneralTypeIssues]


def install_requirements(pip_bin: Path) -> None:
    """Install `[project] dependencies` from pyproject.toml into the venv."""
    tomllib = _ensure_tomllib(pip_bin)

    with open(ROOT_DIR / "pyproject.toml", "rb") as fh:
        deps = tomllib.load(fh).get("project", {}).get("dependencies", [])

    if deps:
        subprocess.check_call([pip_bin, "install", *deps])


# --------------------------------------------------------------------------- #
# cx_Freeze build
# --------------------------------------------------------------------------- #
def build_with_cx_freeze(py_bin: Path, pip_bin: Path) -> None:
    """Write a tiny helper setup script for cx_Freeze and run it."""
    subprocess.check_call([pip_bin, "install", "cx_Freeze~=7.1"])

    with tempfile.TemporaryDirectory() as tmp:
        freeze_setup = Path(tmp) / "freeze_setup.py"
        freeze_setup.write_text(
            f"""
from __future__ import annotations
import os
import sys
from pathlib import Path
from cx_Freeze import setup, Executable

# Make the project root importable so 'import flight_tracker' succeeds.
ROOT = Path(r"{ROOT_DIR}")
sys.path.insert(0, str(ROOT))

ASSETS    = ROOT / "assets"
BUILD_DIR = ROOT / "build"

build_exe_options = {{
    "packages": ["flight_tracker"],                   # collect the package
    "excludes": [],
    "include_files": [(ASSETS, "assets")],            # copy assets/ beside exe
    "build_exe": BUILD_DIR,                           # output directory
}}

base = "Win32GUI" if os.name == "nt" else None

exe = Executable(
    script=str(Path(r"{ENTRY_PY}")),
    target_name="Flight Tracker.exe" if os.name == "nt" else "flight_tracker",
    base=base,
    icon=str(Path(r"{ICON_PATH}")),
)

setup(
    name="Flight Tracker",
    version="1.0.0",
    description="Stand-alone flight price monitor",
    options={{"build_exe": build_exe_options}},
    executables=[exe],
)
"""
        )

        subprocess.check_call([py_bin, str(freeze_setup), "build"])


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main() -> None:
    py_bin, pip_bin = create_virtualenv()

    # guarantee pip inside the venv is usable
    subprocess.check_call([py_bin, "-m", "ensurepip", "--upgrade"])

    install_requirements(pip_bin)
    build_with_cx_freeze(py_bin, pip_bin)

    # remove cx_Freeze temporary folders (optional tidy-up)
    for temp_dir in BUILD_DIR.glob("**/temp"):
        shutil.rmtree(temp_dir, ignore_errors=True)

    print(f"\nExecutable available in: {BUILD_DIR}\n")


if __name__ == "__main__":
    main()
