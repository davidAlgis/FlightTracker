#!/usr/bin/env python3
"""
Copy the contents of ./build/ into:
C:\\Users\\david\\AppData\\Local\\Programs\\FlightTracker

Behavior:
- Recursively copies all files and subdirectories from ./build to the target.
- Creates destination directories as needed.
- Overwrites existing files at the destination.
- Shows a simple console loading bar while copying files.
- After copying, launches 'flight_tracker.exe' from the destination folder
  if the file exists (non-blocking).
- Prints a short summary and exits with an error code:
    0 = success
    1 = partial success (some files failed)
    2 = fatal error (source not found or other unrecoverable error)

Notes:
- Paths are fixed to match the request. If you want to override, you can
  pass two optional arguments: cpy_build.py <src_dir> <dest_dir>.
- Only ASCII characters are used in comments and output.
"""

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Iterable, Tuple

DEFAULT_SRC = Path("./build").resolve()
DEFAULT_DEST = Path(r"C:\Users\david\AppData\Local\Programs\FlightTracker")


def _validate_paths(src: Path, dest: Path) -> Tuple[bool, str]:
    """
    Validate source and destination paths before copying.

    Returns:
        (ok, message) where ok indicates whether validation passed.
    """
    if not src.exists():
        return False, f"Source does not exist: {src}"
    if not src.is_dir():
        return False, f"Source is not a directory: {src}"
    try:
        dest.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        return False, f"Failed to create destination '{dest}': {exc}"
    return True, "OK"


def _walk_files(src: Path) -> Iterable[Path]:
    """
    Yield all file paths under 'src' recursively.
    """
    for root, _dirnames, filenames in os.walk(src):
        for name in filenames:
            yield Path(root) / name


def _print_progress(done: int, total: int, width: int = 40) -> None:
    """
    Print an in-place ASCII progress bar: [####.....] 42% (123/456)

    Args:
        done: number of files processed
        total: total number of files
        width: bar width in characters
    """
    total = max(1, int(total))
    done = min(int(done), total)
    frac = done / total
    filled = int(round(width * frac))
    bar = "#" * filled + "." * (width - filled)
    percent = int(frac * 100)
    msg = f"[{bar}] {percent:3d}% ({done}/{total})"
    # Print carriage return without newline to stay on one line
    sys.stdout.write("\r" + msg)
    sys.stdout.flush()
    if done >= total:
        # Move to next line when finished
        sys.stdout.write("\n")
        sys.stdout.flush()


def _copy_tree_with_progress(src: Path, dest: Path) -> Tuple[int, int, int]:
    """
    Recursively copy files from src to dest with a progress bar.

    Returns:
        (files_copied, dirs_created, failures)
    """
    # Pre-scan to count files
    all_files = list(_walk_files(src))
    total_files = len(all_files)

    files_copied = 0
    dirs_created = 0
    failures = 0

    # Ensure directories and copy files, updating the bar
    # We loop by directory to preserve your original structure counters
    for root, dirnames, filenames in os.walk(src):
        rel = Path(root).relative_to(src)
        dest_dir = dest.joinpath(rel)

        try:
            dest_dir.mkdir(parents=True, exist_ok=True)
            dirs_created += 1
        except Exception as exc:
            print(f"\n[ERROR] Could not create directory: {dest_dir} ({exc})")
            failures += 1
            # Continue to try other entries
            continue

        for name in filenames:
            src_path = Path(root) / name
            dest_path = dest_dir / name
            try:
                shutil.copy2(src_path, dest_path)
                files_copied += 1
            except Exception as exc:
                print(
                    f"\n[ERROR] Failed to copy '{src_path}' -> '{dest_path}': {exc}"
                )
                failures += 1
            finally:
                # Update progress after each file attempt
                _print_progress(files_copied, total_files)

    # If there were zero files, still render an empty bar and newline
    if total_files == 0:
        _print_progress(1, 1)

    return files_copied, dirs_created, failures


def _launch_flight_tracker(dest: Path) -> None:
    """
    Launch 'flight_tracker.exe' from the destination folder if it exists.
    Non-blocking; prints a message if not found or if starting fails.
    """
    exe_path = dest / "Flight Tracker.exe"
    if not exe_path.exists():
        print(
            "[INFO] 'Flight Tracker.exe' not found in destination; not launching."
        )
        return
    try:
        # Start without waiting; set cwd so relative resources resolve correctly.
        subprocess.Popen([str(exe_path)], cwd=str(dest), shell=False)
        print("[INFO] Launched Flight Tracker.exe.")
    except Exception as exc:
        print(f"[WARN] Could not launch Flight Tracker.exe: {exc}")


def main() -> int:
    """
    Entry point. Parses optional arguments and performs the copy, with progress,
    then launches flight_tracker.exe if present.
    """
    parser = argparse.ArgumentParser(
        description="Copy ./build to C:\\Users\\david\\AppData\\Local\\Programs\\FlightTracker and launch the app"
    )
    parser.add_argument(
        "src",
        nargs="?",
        default=str(DEFAULT_SRC),
        help="Source directory (default: ./build)",
    )
    parser.add_argument(
        "dest",
        nargs="?",
        default=str(DEFAULT_DEST),
        help=r"Destination directory (default: C:\Users\david\AppData\Local\Programs\FlightTracker)",
    )
    args = parser.parse_args()

    src = Path(args.src).resolve()
    dest = Path(args.dest).resolve()

    print(f"[INFO] Source:      {src}")
    print(f"[INFO] Destination: {dest}")

    ok, msg = _validate_paths(src, dest)
    if not ok:
        print(f"[FATAL] {msg}")
        return 2

    print("[INFO] Copying files...")
    files_copied, dirs_created, failures = _copy_tree_with_progress(src, dest)

    print(f"[INFO] Directories ensured: {dirs_created}")
    print(f"[INFO] Files copied:        {files_copied}")

    # Launch the application if present (always attempt, regardless of failures)
    _launch_flight_tracker(dest)

    if failures == 0:
        print("[INFO] Done with no errors.")
        return 0
    else:
        print(f"[WARN] Completed with {failures} error(s).")
        return 1


if __name__ == "__main__":
    sys.exit(main())
