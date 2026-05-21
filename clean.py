#!/usr/bin/env python3
"""
clean.py

Wipes the development workspace clean by forcefully purging the virtual
environment, the standalone build folder, and the mypy cache.
"""

import os
import shutil
import sys
import time
from pathlib import Path


def main() -> None:
    # Resolve the root directory relative to where this script lives
    ROOT_DIR = Path(__file__).resolve().parent

    targets = {
        "Virtual Environment (./env)": ROOT_DIR / "env",
        "Production Build (./build)": ROOT_DIR / "build",
        "Mypy Cache (./.mypy_cache)": ROOT_DIR / ".mypy_cache",
    }

    print("====================================================")
    print("[CLEAN] Starting total project workspace scrub...")
    print("====================================================\n")

    any_failures = False

    for description, path in targets.items():
        if not path.exists():
            print(f"[INFO] {description} is already missing. Skipping.")
            continue

        print(f"[PURGING] {description}...")
        success = False

        # Retry loop to handle stubborn Windows OS file-locks
        for attempt in range(5):
            try:
                shutil.rmtree(path)
                print(f"[SUCCESS] Cleaned {description}.")
                success = True
                break
            except OSError as e:
                if attempt < 4:
                    print(
                        f"[WARN] File lock hit on {path.name}. Retrying in 1.5 seconds... ({e})"
                    )
                    time.sleep(1.5)
                else:
                    print(
                        f"[ERROR] Failed to delete {description} after multiple attempts."
                    )
                    print(f"        Reason: {e}")

        if not success:
            any_failures = True

    print("\n====================================================")
    print("[CLEAN] Routine finished.")
    if any_failures:
        print("[WARN] Some items could not be completely removed.")
        print(
            "       Ensure your application, terminal, or IDE is not actively using them."
        )
        sys.exit(1)
    else:
        print("[OK] Your workspace is completely clean and fresh!")
        sys.exit(0)


if __name__ == "__main__":
    main()
