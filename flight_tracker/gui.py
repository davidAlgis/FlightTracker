#!/usr/bin/env python3
"""
GUI for the Flight Price Monitor. Supports:
- Lists of IATA codes (e.g. DEL,BOM)
- City, Country inputs with transport-time lookup
- Country-only inputs listing all airports
- Date domains for departure and return dates (YYYY-MM-DD or YYYY-MM-DD-YYYY-MM-DD)
- Trip-duration domains (days, e.g. 3 or 3-7)
- Confirmation of total number of monitoring tasks before starting
- Load and save of last search config to config.json
- Quiet, in-place resolution of airports to "CODE - Name" on field leave,
  without re-resolving already-resolved entries.
"""

import itertools
import re
import threading
import tkinter as tk
from datetime import datetime, timedelta
from tkinter import END, messagebox, simpledialog

import pandas as pd

from flight_tracker.airport_from_distance import AirportFromDistance
from flight_tracker.country_to_airport import CountryToAirport
from flight_tracker.flight_bot import FlightBot
from flight_tracker.load_config import ConfigManager


class FlightBotGUI(tk.Tk):
    """
    A Tkinter GUI that collects routes, dates (or date ranges),
    and optional trip-duration ranges, then starts FlightBot
    instances for every combination. Remembers last inputs
    and quietly resolves airport codes to names in entries.
    """

    def __init__(self):
        """Initialize window, form fields, load saved config, bind events."""
        super().__init__()
        self.title("Flight Price Monitor")
        self.resizable(False, False)

        fields = [
            (
                "Departure(s) (IATA, City, Country or Country)",
                "departure",
                False,
            ),
            (
                "Destination(s) (IATA, City, Country or Country)",
                "destination",
                False,
            ),
            (
                "Departure Date(s)\n(YYYY-MM-DD or YYYY-MM-DD-YYYY-MM-DD)",
                "dep_date",
                False,
            ),
            (
                "Return Date(s)\n(YYYY-MM-DD or YYYY-MM-DD-YYYY-MM-DD)",
                "arrival_date",
                False,
            ),
            ("Trip Duration (days)\n(e.g. 3 or 3-7)", "trip_duration", False),
            ("Price Limit (₹)", "price_limit", False),
            ("Checking Interval (s)", "checking_interval", False),
            ("Total Duration (s)", "checking_duration", False),
        ]

        self.entries = {}
        for idx, (label, name, is_pass) in enumerate(fields):
            lbl = tk.Label(self, text=label)
            lbl.grid(row=idx, column=0, padx=8, pady=4, sticky="e")
            ent = tk.Entry(self, width=40, show="*" if is_pass else "")
            ent.grid(row=idx, column=1, padx=8, pady=4)
            self.entries[name] = ent

        # load airport names mapping
        airports_df = pd.read_csv(AirportFromDistance.AIRPORTS_URL)
        self.code_to_name = {
            code: name
            for code, name in zip(
                airports_df["iata_code"], airports_df["name"]
            )
            if pd.notna(code)
        }

        # bind focus-out to resolve airports quietly
        self.resolved_airports = {}
        for field in ("departure", "destination"):
            self.entries[field].bind(
                "<FocusOut>",
                lambda ev, f=field: self._pre_resolve_airports(f),
            )

        start_btn = tk.Button(
            self, text="Start Monitoring", command=self.start_monitor
        )
        start_btn.grid(row=len(fields), column=0, columnspan=2, pady=10)

        # load and apply saved configuration
        self.config_mgr = ConfigManager()
        saved = self.config_mgr.load()
        for key, entry in self.entries.items():
            if key in saved:
                entry.insert(0, str(saved[key]))
        for side in ("departure", "destination"):
            codes_key = f"{side}_codes"
            if codes_key in saved:
                self.resolved_airports[side] = saved[codes_key]

    def _pre_resolve_airports(self, field_name):
        """
        Quietly resolve and replace airport codes in the given field when it loses focus.
        If the field already contains "CODE - Name" entries, parse them without re-resolving.
        City,country inputs will prompt once for duration; country-only is silent.
        """
        raw = self.entries[field_name].get().strip()
        if not raw:
            return

        # if already in "CODE - Name" format, just parse codes
        display_pattern = re.compile(
            r"^[A-Z]{3} - .+?(?:,\s*[A-Z]{3} - .+?)*$"
        )
        if display_pattern.match(raw):
            codes = [seg.split("-", 1)[0].strip() for seg in raw.split(",")]
            self.resolved_airports[field_name] = codes
            return

        # otherwise, perform resolution
        try:
            codes = self._resolve_airports(raw)
        except ValueError as e:
            messagebox.showerror("Invalid input", f"{field_name.title()}: {e}")
            return

        # build display strings "CODE - Name"
        display = []
        for code in codes:
            name = self.code_to_name.get(code, "")
            display.append(f"{code} - {name}" if name else code)

        # replace entry text with resolved display
        self.entries[field_name].delete(0, END)
        self.entries[field_name].insert(0, ",".join(display))

        # save codes for later use
        self.resolved_airports[field_name] = codes
        cfg = self.config_mgr.load()
        cfg[field_name] = ",".join(display)
        cfg[f"{field_name}_codes"] = codes
        self.config_mgr.save(cfg)

    def _resolve_airports(self, input_str):
        """
        Resolve an input string into a list of IATA codes.
        Handles:
          - Comma-separated IATA lists (e.g. "DEL,BOM")
          - "City, Country" → AirportFromDistance lookup (prompts duration)
          - Country-only → CountryToAirport lookup (silent)
        """
        tokens = [t.strip() for t in input_str.split(",") if t.strip()]
        if all(len(t) == 3 and t.isalpha() and t.isupper() for t in tokens):
            return tokens
        if len(tokens) == 2:
            city, country = tokens
            duration = simpledialog.askinteger(
                "Max Duration",
                f"Max transport duration (min) from {city}, {country}",
                minvalue=1,
            )
            if duration is None:
                raise ValueError("Operation cancelled by user")
            finder = AirportFromDistance()
            return [
                c
                for c, _ in finder.get_airports(f"{city}, {country}", duration)
            ]
        finder = CountryToAirport()
        return [c for c, _ in finder.get_airports(input_str)]

    def _parse_date_list(self, date_str):
        """
        Parse a date or date-range string into a list of datetime.
        Accepts "YYYY-MM-DD" or "YYYY-MM-DD-YYYY-MM-DD".
        """
        parts = date_str.strip().split("-")
        if len(parts) == 3:
            return [datetime.strptime(date_str, "%Y-%m-%d")]
        if len(parts) == 6:
            start = "-".join(parts[0:3])
            end = "-".join(parts[3:6])
            d0 = datetime.strptime(start, "%Y-%m-%d")
            d1 = datetime.strptime(end, "%Y-%m-%d")
            if d1 < d0:
                raise ValueError(f"End date {end} is before start {start}")
            return [d0 + timedelta(days=i) for i in range((d1 - d0).days + 1)]
        raise ValueError(
            f"Invalid date format '{date_str}'. Use YYYY-MM-DD or YYYY-MM-DD-YYYY-MM-DD"
        )

    def _parse_duration_list(self, dur_str):
        """
        Parse a duration or duration-range string into list of ints.
        Accepts "N" or "N-M".
        """
        parts = dur_str.strip().split("-")
        if len(parts) == 1:
            return [int(parts[0])]
        if len(parts) == 2:
            lo, hi = int(parts[0]), int(parts[1])
            if hi < lo:
                raise ValueError(f"Invalid duration range '{dur_str}'")
            return list(range(lo, hi + 1))
        raise ValueError(f"Invalid duration format '{dur_str}'. Use N or N-M")

    def start_monitor(self):
        """
        Gather inputs, build lists of departures, destinations,
        and (departure, return) date pairs, then confirm the total
        number of tasks with the user before spawning FlightBot threads.
        """
        try:
            for side in ("departure", "destination"):
                if side not in self.resolved_airports:
                    self._pre_resolve_airports(side)
            deps = self.resolved_airports["departure"]
            dests = self.resolved_airports["destination"]

            dep_dates = self._parse_date_list(
                self.entries["dep_date"].get().strip()
            )
            trip = self.entries["trip_duration"].get().strip()
            if trip:
                durations = self._parse_duration_list(trip)
                date_pairs = [
                    (
                        d.strftime("%Y-%m-%d"),
                        (d + timedelta(days=dur)).strftime("%Y-%m-%d"),
                    )
                    for d in dep_dates
                    for dur in durations
                ]
            else:
                ret_dates = self._parse_date_list(
                    self.entries["arrival_date"].get().strip()
                )
                date_pairs = [
                    (d.strftime("%Y-%m-%d"), r.strftime("%Y-%m-%d"))
                    for d, r in itertools.product(dep_dates, ret_dates)
                ]

            params = {
                name: int(self.entries[name].get().strip())
                for name in (
                    "price_limit",
                    "checking_interval",
                    "checking_duration",
                )
            }
        except ValueError as e:
            messagebox.showerror("Invalid input", str(e))
            return

        total_tasks = len(deps) * len(dests) * len(date_pairs)
        if not messagebox.askyesno(
            "Confirm Tasks",
            f"{total_tasks} monitoring tasks will be started. Continue?",
        ):
            return

        cfg = {}
        for key, entry in self.entries.items():
            cfg[key] = entry.get().strip()
        cfg["departure_codes"] = self.resolved_airports["departure"]
        cfg["destination_codes"] = self.resolved_airports["destination"]
        self.config_mgr.save(cfg)

        # for dep_airport, dest_airport in itertools.product(deps, dests):
        #     for dep_date, ret_date in date_pairs:
        #         bot = FlightBot(
        #             departure=dep_airport,
        #             destination=dest_airport,
        #             dep_date=dep_date,
        #             arrival_date=ret_date,
        #             price_limit=params["price_limit"],
        #             checking_interval=params["checking_interval"],
        #             checking_duration=params["checking_duration"],
        #         )
        #         thread = threading.Thread(target=bot.start, daemon=True)
        #         thread.start()

        self.quit()


if __name__ == "__main__":
    app = FlightBotGUI()
    app.mainloop()
