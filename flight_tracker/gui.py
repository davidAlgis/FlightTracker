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
"""

import itertools
import threading
import tkinter as tk
from datetime import datetime, timedelta
from tkinter import messagebox, simpledialog

from flight_tracker.airport_from_distance import AirportFromDistance
from flight_tracker.country_to_airport import CountryToAirport
from flight_tracker.flight_bot import FlightBot
from flight_tracker.load_config import ConfigManager


class FlightBotGUI(tk.Tk):
    """
    A Tkinter GUI that collects routes, dates (or date ranges),
    and optional trip-duration ranges, then starts FlightBot
    instances for every combination. Remembers last inputs.
    """

    def __init__(self):
        """Initialize window, form fields, and load saved configuration."""
        super().__init__()
        self.title("Flight Price Monitor")
        self.resizable(False, False)

        fields = [
            (
                "Departure(s) (IATA list, City, Country or Country)",
                "departure",
                False,
            ),
            (
                "Destination(s) (IATA list, City, Country or Country)",
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
            (
                "Trip Duration (days)\n(e.g. 3 or 3-7)",
                "trip_duration",
                False,
            ),
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

        start_btn = tk.Button(
            self, text="Start Monitoring", command=self.start_monitor
        )
        start_btn.grid(row=len(fields), column=0, columnspan=2, pady=10)

        # Load and apply saved configuration
        self.config_mgr = ConfigManager()
        saved = self.config_mgr.load()
        for key, entry in self.entries.items():
            if key in saved:
                entry.insert(0, str(saved[key]))

    def _resolve_airports(self, input_str):
        """
        Resolve an input string into a list of IATA codes.
        Handles:
          - Comma-separated IATA lists (e.g. "DEL,BOM")
          - "City, Country" → AirportFromDistance lookup
          - Country-only → CountryToAirport lookup
        """
        tokens = [t.strip() for t in input_str.split(",") if t.strip()]
        if all(len(t) == 3 and t.isalpha() and t.isupper() for t in tokens):
            return tokens
        if len(tokens) == 2:
            city, country = tokens
            prompt = f"Max transport duration (min) from {city}, {country}"
            duration = simpledialog.askinteger(
                "Max Duration", prompt, minvalue=1
            )
            if duration is None:
                raise ValueError("Operation cancelled by user")
            finder = AirportFromDistance()
            return [
                code
                for code, _ in finder.get_airports(
                    f"{city}, {country}", duration
                )
            ]
        finder = CountryToAirport()
        return [code for code, _ in finder.get_airports(input_str)]

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
            days = (d1 - d0).days
            return [d0 + timedelta(days=i) for i in range(days + 1)]
        raise ValueError(
            f"Invalid date format '{date_str}'. Use YYYY-MM-DD or "
            "YYYY-MM-DD-YYYY-MM-DD"
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
            deps = self._resolve_airports(
                self.entries["departure"].get().strip()
            )
            dests = self._resolve_airports(
                self.entries["destination"].get().strip()
            )
            dep_dates = self._parse_date_list(
                self.entries["dep_date"].get().strip()
            )
            trip_dur_str = self.entries["trip_duration"].get().strip()
            if trip_dur_str:
                durations = self._parse_duration_list(trip_dur_str)
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
        confirm = messagebox.askyesno(
            "Confirm Tasks",
            f"{total_tasks} monitoring tasks will be started. Continue?",
        )
        if not confirm:
            return

        # Save current search configuration
        config = {key: self.entries[key].get().strip() for key in self.entries}
        self.config_mgr.save(config)

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

        # messagebox.showinfo(
        #     "FlightBot",
        #     "Monitoring started for all routes and date pairs in the background.",
        # )
        self.quit()


if __name__ == "__main__":
    app = FlightBotGUI()
    app.mainloop()
