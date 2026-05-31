#!/usr/bin/env python3
"""
setup.py

Automates the safe deletion of old build environments, creates a fresh
virtual environment, installs fixed runtime dependencies directly from
pyproject.toml, and freezes the app into a standalone bundle using PyInstaller.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
import venv
from pathlib import Path

# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #
ROOT_DIR = Path(__file__).resolve().parent
ENV_DIR = ROOT_DIR / "env"
BUILD_DIR = ROOT_DIR / "build"
ASSETS_DIR = ROOT_DIR / "assets"

ICON_PATH = ASSETS_DIR / "flight_tracker.ico"


# --------------------------------------------------------------------------- #
# Hard Clean Helper
# --------------------------------------------------------------------------- #
def force_purge_directory(target_dir: Path, description: str) -> None:
    """Forcefully wipes a directory from the file system to clear build caches."""
    if target_dir.exists():
        print(
            f"[CLEANUP] Removing old {description} folder to prevent package pollution..."
        )
        for attempt in range(5):
            try:
                shutil.rmtree(target_dir)
                print(f"[CLEANUP] Successfully purged {description}.")
                return
            except OSError as e:
                print(
                    f"[WARN] File lock encountered on {description}. Retrying in 2 seconds... ({e})"
                )
                time.sleep(2)
        print(
            f"[ERROR] Could not delete {target_dir}. Please close any open apps or editors using it."
        )
        sys.exit(1)


# --------------------------------------------------------------------------- #
# Virtual-environment helpers
# --------------------------------------------------------------------------- #
def create_virtualenv() -> tuple[Path, Path]:
    """Create a pristine virtual environment."""
    if not ENV_DIR.exists():
        print("[ENV] Creating a fresh virtual environment...")
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
        import tomllib
    except ModuleNotFoundError:
        subprocess.check_call([pip_bin, "install", "tomli"])
        import tomli as tomllib
    return tomllib


def install_requirements(pip_bin: Path) -> None:
    """Install packages directly from the pinned definitions in pyproject.toml."""
    tomllib = _ensure_tomllib(pip_bin)

    with open(ROOT_DIR / "pyproject.toml", "rb") as fh:
        deps = tomllib.load(fh).get("project", {}).get("dependencies", [])

    if deps:
        print(
            "[PIP] Installing code dependencies directly from pyproject.toml..."
        )
        subprocess.check_call([pip_bin, "install", *deps])


def build_with_pyinstaller(py_bin: Path, pip_bin: Path) -> None:
    """Freeze the app cleanly into a directory bundle using PyInstaller."""
    # Strict validation check to ensure icon file is present before compilation
    if not ICON_PATH.exists():
        print(f"[ERROR] Icon target file asset not found at path location: {ICON_PATH}")
        print("Please verify the target .ico file is present inside your assets/ dir.")
        sys.exit(1)

    print("[FREEZE] Installing PyInstaller compilation toolset...")
    subprocess.check_call([pip_bin, "install", "pyinstaller"])

    # Generate a temporary root-level bootstrap launcher file
    bootstrap_py = ROOT_DIR / "entry_bootstrap.py"
    print("[FREEZE] Generating temporary root bootstrap launcher...")
    bootstrap_py.write_text(
        "from flight_tracker.__main__ import main\n"
        "if __name__ == '__main__':\n"
        "    main()\n",
        encoding="utf-8",
    )

    print(
        "[FREEZE] Compiling standalone application executable bundle via PyInstaller..."
    )
    
    # Separated flags and parameters to avoid path string coercion interpretation faults
    cmd = [
        str(py_bin),
        "-m",
        "PyInstaller",
        "--noconsole",                           # No terminal window display popup
        "--name", "Flight Tracker",              # Target binary compilation name
        "--paths", str(ROOT_DIR),                # Explicitly map workspace base folder paths
        "--icon", str(ICON_PATH),                # Standalone window binary shell icon
        "--add-data", f"{ASSETS_DIR}{os.path.pathsep}assets",  # Pack local images asset filesystem mirror
        "--distpath", str(ROOT_DIR / 'dist'),    # Output staging target
        "--workpath", str(ROOT_DIR / 'build_tmp'), # Isolation directory workspace
        "--hidden-import=flight_tracker",
        "--hidden-import=tkinter",
        "--hidden-import=matplotlib",
        "--hidden-import=numpy",
        "--hidden-import=pandas",
        "--hidden-import=bs4",
        "--hidden-import=selenium",
        "--hidden-import=pystray",
        "--hidden-import=PIL",
        "--hidden-import=plyer",                 # Include plyer engine explicitly
        str(bootstrap_py),                       # Target the root-level bootstrap script
    ]

    try:
        subprocess.check_call(cmd)
    except subprocess.CalledProcessError as e:
        print(f"[ERROR] PyInstaller compilation failed: {e}")
        sys.exit(1)
    finally:
        # Guarantee removal of the temporary bootstrap script
        if bootstrap_py.exists():
            print("[FREEZE] Removing temporary root bootstrap launcher...")
            bootstrap_py.unlink()

    # Move the compilation build from dist/Flight Tracker directly to build/
    dist_app_dir = ROOT_DIR / "dist" / "Flight Tracker"
    if dist_app_dir.exists():
        shutil.move(str(dist_app_dir), str(BUILD_DIR))

    # Programmatic cleanup of auxiliary compiler leftover structures
    shutil.rmtree(ROOT_DIR / "dist", ignore_errors=True)
    shutil.rmtree(ROOT_DIR / "build_tmp", ignore_errors=True)
    spec_file = ROOT_DIR / "Flight Tracker.spec"
    if spec_file.exists():
        spec_file.unlink()


# --------------------------------------------------------------------------- #
# Main Execution Chain
# --------------------------------------------------------------------------- #
def main() -> None:
    # 1. Clear old dirty cache directories completely
    force_purge_directory(ENV_DIR, "virtual environment (./env)")
    force_purge_directory(BUILD_DIR, "production build (./build)")

    # 2. Run clean environment generation and build compilation
    py_bin, pip_bin = create_virtualenv()
    subprocess.check_call([py_bin, "-m", "ensurepip", "--upgrade"])
    install_requirements(pip_bin)
    build_with_pyinstaller(py_bin, pip_bin)

    print(
        f"\n[SUCCESS] Clean PyInstaller binary built! Available in: {BUILD_DIR}\n"
    )


if __name__ == "__main__":
    main()