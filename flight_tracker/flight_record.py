#!/usr/bin/env python3
"""
flight_record.py

Lightweight persistence layer for the flight-price monitor.
Stores one JSON line per *hourly* scrape.

Each record contains
    • datetime          YYYY-MM-DD-HH (local time when scraped)
    • departure         IATA code
    • destination       IATA code
    • company           carrier(s)
    • duration_out      outbound duration (text)
    • duration_return   return duration (text)
    • price             float (€)
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Dict, Optional


def _default_store_file() -> str:
    """
    Return a user-writable path for *flight_records.jsonl*.

    * When running a **frozen** app (cx_Freeze, PyInstaller, …) we avoid
      writing into the installation directory (often read-only under
      *Program Files*).
      – Windows … ``%LOCALAPPDATA%\\flight_tracker\\flight_records.jsonl``
      – Linux/macOS … ``$XDG_DATA_HOME/flight_tracker/flight_records.jsonl``
        falling back to ``~/.local/share/flight_tracker/…``

    * In normal source runs we keep the file in the current working dir,
      preserving previous behaviour.
    """
    if getattr(sys, "frozen", False):  # inside bundled exe
        if os.name == "nt":
            root = os.getenv("LOCALAPPDATA") or Path.home()
        else:  # POSIX
            root = (
                os.getenv("XDG_DATA_HOME") or Path.home() / ".local" / "share"
            )
        store_dir = Path(root) / "flight_tracker"
        store_dir.mkdir(parents=True, exist_ok=True)
        return str(store_dir / "flight_records.jsonl")

    # developer run → relative file
    return "flight_records.jsonl"


class FlightRecord:
    """
    Simple JSON-lines store keeping only the *lowest* price per hour.
    """

    def __init__(self, path: str | None = None) -> None:
        self.path: str = path or _default_store_file()

        # guarantee that the file exists and is writable
        Path(self.path).touch(exist_ok=True)

    # ------------------------------------------------------------------ #
    # public API
    # ------------------------------------------------------------------ #
    def save_record(
        self,
        datetime_key: str,
        departure: str,
        destination: str,
        company: str,
        duration_out: str,
        duration_return: str,
        price: float,
    ) -> None:
        """
        Persist a scrape result.

        If an entry already exists for *datetime_key* it is replaced **only
        if** the new price is lower.
        """
        records: list[dict] = []
        existing_price: float | None = None

        with open(self.path, "r", encoding="utf-8") as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue

                if rec.get("datetime") == datetime_key:
                    existing_price = rec.get("price")
                else:
                    records.append(rec)

        # nothing to do if stored price is already cheaper (or equal)
        if existing_price is not None and existing_price <= price:
            return

        records.append(
            {
                "datetime": datetime_key,
                "departure": departure,
                "destination": destination,
                "company": company,
                "duration_out": duration_out,
                "duration_return": duration_return,
                "price": price,
            }
        )

        with open(self.path, "w", encoding="utf-8") as fh:
            for rec in records:
                fh.write(json.dumps(rec) + "\n")

    # ------------------------------------------------------------------ #
    def load_record(self, datetime_key: str) -> Optional[Dict]:
        """Return the record for *datetime_key* or ``None``."""
        if not Path(self.path).exists():
            return None

        with open(self.path, "r", encoding="utf-8") as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("datetime") == datetime_key:
                    return rec
        return None
