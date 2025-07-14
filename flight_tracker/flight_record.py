#!/usr/bin/env python3
"""
Module to record and retrieve minimal flight data per hour in JSON lines.

Each record contains:
- datetime (YYYY-MM-DD-HH)
- departure IATA code
- destination IATA code
- airline/company name
- outbound duration (e.g. "18h 55min")
- return duration (e.g. "26h 10min")
- price (float)
"""

import json
import os
from typing import Dict, Optional


class FlightRecord:
    """Manage appending and loading minimal-hourly flight records."""

    def __init__(self, path: str = "flight_records.jsonl"):
        """
        Initialize the FlightRecord manager.

        :param path: File path for JSON lines storage.
        """
        self.path = path
        if not os.path.exists(self.path):
            with open(self.path, "w", encoding="utf-8"):
                pass

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
        Save or update the minimal flight record for a given hour.
        Only overwrite if the new price is lower than any existing
        record for that datetime_key (YYYY-MM-DD-HH).

        :param datetime_key: Date and hour string in YYYY-MM-DD-HH format.
        :param departure: Departure airport IATA code.
        :param destination: Destination airport IATA code.
        :param company: Airline or company name.
        :param duration_out: Outbound flight duration.
        :param duration_return: Return flight duration.
        :param price: Price in euros.
        """
        records = []
        existing_price = None

        with open(self.path, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("datetime") == datetime_key:
                    existing_price = rec.get("price")
                else:
                    records.append(rec)

        # if an existing record is cheaper or equal, do nothing
        if existing_price is not None and existing_price <= price:
            return

        # otherwise append new (first or cheaper) record
        new_rec = {
            "datetime": datetime_key,
            "departure": departure,
            "destination": destination,
            "company": company,
            "duration_out": duration_out,
            "duration_return": duration_return,
            "price": price,
        }
        records.append(new_rec)

        with open(self.path, "w", encoding="utf-8") as f:
            for rec in records:
                f.write(json.dumps(rec) + "\n")

    def load_record(self, datetime_key: str) -> Optional[Dict]:
        """
        Load the flight record for a given hour.

        :param datetime_key: Date and hour string in YYYY-MM-DD-HH format.
        :return: The record dict, or None if not found.
        """
        if not os.path.exists(self.path):
            return None

        with open(self.path, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("datetime") == datetime_key:
                    return rec
        return None
