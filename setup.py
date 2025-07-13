#!/usr/bin/env python3
"""
setup.py

Creates a virtual environment in env/, installs the runtime
dependencies declared in pyproject.toml, and builds a one-file
executable with PyInstaller into build/ (icon: assets/flight_tracker.ico).

Usage (run from repository root):

    python setup.py
"""

from __future__ import annotations

import os
import subprocess
import sys
import venv
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent
ENV_DIR = ROOT_DIR / "env"
BUILD_DIR = ROOT_DIR / "build"
ICON_PATH = ROOT_DIR / "assets" / "flight_tracker.ico"
ENTRY_SCRIPT = ROOT_DIR / "flight_tracker" / "__main__.py"


def create_virtualenv() -> tuple[Path, Path]:
    """Ensure env/ exists and return (python, pip) paths inside it."""
    if not ENV_DIR.exists():
        venv.create(ENV_DIR, with_pip=True)

    if os.name == "nt":
        py = ENV_DIR / "Scripts" / "python.exe"
        pip = ENV_DIR / "Scripts" / "pip.exe"
    else:
        py = ENV_DIR / "bin" / "python"
        pip = ENV_DIR / "bin" / "pip"

    return py, pip


def _ensure_tomllib(pip_bin: Path) -> "module":
    """
    Provide a tomllib-compatible module (built-in on 3.11+; tomli otherwise).
    Installs tomli into the venv only if needed.
    """
    try:
        import tomllib  # Python ≥3.11
    except ModuleNotFoundError:
        subprocess.check_call([pip_bin, "install", "tomli"])
        import tomli as tomllib  # type: ignore
    return tomllib  # pyright: ignore[reportGeneralTypeIssues]


def install_requirements(pip_bin: Path) -> None:
    """Install dependencies listed in pyproject.toml (project.dependencies)."""
    tomllib = _ensure_tomllib(pip_bin)

    with open(ROOT_DIR / "pyproject.toml", "rb") as fh:
        deps = tomllib.load(fh).get("project", {}).get("dependencies", [])

    if deps:
        subprocess.check_call([pip_bin, "install", *deps])


def main() -> None:
    py_bin, pip_bin = create_virtualenv()

    # make sure pip inside venv is usable
    subprocess.check_call([py_bin, "-m", "ensurepip", "--upgrade"])

    install_requirements(pip_bin)

    # PyInstaller itself is a build-time dependency
    subprocess.check_call([pip_bin, "install", "pyinstaller"])

    BUILD_DIR.mkdir(exist_ok=True)

    subprocess.check_call(
        [
            py_bin,
            "-m",
            "PyInstaller",
            "--onefile",
            "--noconsole",
            "--name",
            "Flight Tracker",
            "--icon",
            str(ICON_PATH),
            "--distpath",
            str(BUILD_DIR),
            str(ENTRY_SCRIPT),
        ],
        cwd=ROOT_DIR,
    )

    print(f"\nExecutable built in: {BUILD_DIR}\n")


if __name__ == "__main__":
    main()
