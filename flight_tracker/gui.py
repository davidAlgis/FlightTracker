#!/usr/bin/env python3
"""
GUI for the Flight Price Monitor. Supports:
- Lists of IATA codes (e.g. DEL,BOM)
- City, Country inputs with transport-time lookup
- Country-only inputs listing all airports
- Date domains for departure and return dates
  (YYYY-MM-DD or YYYY-MM-DD-YYYY-MM-DD)
- Trip-duration domains (days, e.g. 3 or 3-7)
- Max one-way/return flight duration (hours)
- Confirmation of total number of monitoring tasks before starting
- Load and save of last search config to config.json
- Quiet, in-place resolution of airports to "CODE - Name" on field leave
- Multi-line text boxes for departure and destination fields
- Runs FlightBot tasks in a background thread
- Shows an indeterminate progress bar while tracking
"""

import itertools
import re
import threading
import tkinter as tk
from datetime import datetime, timedelta
from tkinter import END, messagebox, simpledialog, ttk

import pandas as pd

from flight_tracker.airport_from_distance import AirportFromDistance
from flight_tracker.country_to_airport import CountryToAirport
from flight_tracker.flight_bot import FlightBot
from flight_tracker.load_config import ConfigManager


class FlightBotGUI(tk.Tk):
    """Tkinter GUI that gathers parameters and starts FlightBot tasks."""

    def __init__(self):
        """Initialize window, fields, bindings, and load saved config."""
        super().__init__()
        self.title("Flight Price Monitor")
        self.geometry("900x600")
        self.resizable(False, False)

        fields = [
            (
                "Departure(s) (IATA, City, Country or Country)",
                "departure",
                True,
            ),
            (
                "Destination(s) (IATA, City, Country or Country)",
                "destination",
                True,
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
            ("Max Flight Duration (h)", "max_duration_flight", False),
            ("Price Limit (€)", "price_limit", False),
            ("Checking Interval (s)", "checking_interval", False),
            ("Total Duration (s)", "checking_duration", False),
        ]

        self.entries = {}
        for idx, (label, name, multiline) in enumerate(fields):
            lbl = tk.Label(self, text=label)
            lbl.grid(row=idx, column=0, padx=8, pady=4, sticky="ne")
            if multiline:
                widget = tk.Text(self, width=60, height=4)
            else:
                widget = tk.Entry(self, width=40)
            widget.grid(row=idx, column=1, padx=8, pady=4, sticky="w")
            self.entries[name] = widget

        airports_df = pd.read_csv(AirportFromDistance.AIRPORTS_URL)
        self.code_to_name = {
            code: name
            for code, name in zip(
                airports_df["iata_code"], airports_df["name"]
            )
            if pd.notna(code)
        }

        self.resolved_airports = {}
        for field in ("departure", "destination"):
            self.entries[field].bind(
                "<FocusOut>",
                lambda ev, f=field: self._pre_resolve_airports(f),
            )

        self.start_btn = tk.Button(
            self, text="Start Monitoring", command=self.start_monitor
        )
        self.start_btn.grid(row=len(fields), column=0, columnspan=2, pady=10)

        self.progress = ttk.Progressbar(self, mode="indeterminate")
        self.progress.grid(
            row=len(fields) + 1, column=0, columnspan=2, pady=4, sticky="ew"
        )

        self.config_mgr = ConfigManager()
        saved = self.config_mgr.load()
        for key, widget in self.entries.items():
            if key in saved:
                val = saved[key]
                if isinstance(widget, tk.Text):
                    widget.insert("1.0", val)
                else:
                    widget.insert(0, val)
        for side in ("departure", "destination"):
            codes_key = f"{side}_codes"
            if codes_key in saved:
                self.resolved_airports[side] = saved[codes_key]

    def _get_widget_value(self, widget):
        """Return trimmed string from Entry or Text widget."""
        if isinstance(widget, tk.Text):
            return widget.get("1.0", END).strip()
        return widget.get().strip()

    def _set_widget_value(self, widget, text):
        """Replace content of Entry or Text widget."""
        if isinstance(widget, tk.Text):
            widget.delete("1.0", END)
            widget.insert("1.0", text)
        else:
            widget.delete(0, END)
            widget.insert(0, text)

    def _pre_resolve_airports(self, field):
        """
        Resolve and replace airport entries with "CODE - Name" on focus out.
        Skips re-resolving if already in CODE - Name format.
        """
        widget = self.entries[field]
        raw = self._get_widget_value(widget)
        if not raw:
            return
        pattern = re.compile(r"^[A-Z]{3} - .+?(?:,\s*[A-Z]{3} - .+?)*$")
        if pattern.match(raw):
            codes = [seg.split("-", 1)[0].strip() for seg in raw.split(",")]
            self.resolved_airports[field] = codes
            return
        try:
            codes = self._resolve_airports(raw)
        except ValueError as e:
            messagebox.showerror("Invalid input", f"{field.title()}: {e}")
            return
        display = []
        for code in codes:
            name = self.code_to_name.get(code, "")
            display.append(f"{code} - {name}" if name else code)
        self._set_widget_value(widget, ",".join(display))
        self.resolved_airports[field] = codes
        cfg = self.config_mgr.load()
        cfg[field] = ",".join(display)
        cfg[f"{field}_codes"] = codes
        self.config_mgr.save(cfg)

    def _resolve_airports(self, input_str):
        """Convert input string to list of IATA codes."""
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
                raise ValueError("Operation cancelled")
            finder = AirportFromDistance()
            return [
                c
                for c, _ in finder.get_airports(f"{city}, {country}", duration)
            ]
        finder = CountryToAirport()
        return [c for c, _ in finder.get_airports(input_str)]

    def _parse_date_list(self, date_str):
        """Parse a date or date-range string into datetime list."""
        parts = date_str.strip().split("-")
        if len(parts) == 3:
            return [datetime.strptime(date_str, "%Y-%m-%d")]
        if len(parts) == 6:
            start = "-".join(parts[0:3])
            end = "-".join(parts[3:6])
            d0 = datetime.strptime(start, "%Y-%m-%d")
            d1 = datetime.strptime(end, "%Y-%m-%d")
            if d1 < d0:
                raise ValueError(f"End date {end} before start {start}")
            return [d0 + timedelta(days=i) for i in range((d1 - d0).days + 1)]
        raise ValueError("Invalid date format")

    def _parse_duration_list(self, dur_str):
        """Parse duration or range into list of ints."""
        parts = dur_str.strip().split("-")
        if len(parts) == 1:
            return [int(parts[0])]
        if len(parts) == 2:
            lo, hi = int(parts[0]), int(parts[1])
            if hi < lo:
                raise ValueError("Invalid duration range")
            return list(range(lo, hi + 1))
        raise ValueError("Invalid duration format")

    def start_monitor(self):
        """
        Collect inputs, confirm task count, save config, then
        start a background thread that runs FlightBots in sequence.
        """
        try:
            for side in ("departure", "destination"):
                if side not in self.resolved_airports:
                    self._pre_resolve_airports(side)
            deps = self.resolved_airports["departure"]
            dests = self.resolved_airports["destination"]
            dep_dates = self._parse_date_list(
                self._get_widget_value(self.entries["dep_date"])
            )
            trip = self._get_widget_value(self.entries["trip_duration"])
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
                    self._get_widget_value(self.entries["arrival_date"])
                )
                date_pairs = [
                    (d.strftime("%Y-%m-%d"), r.strftime("%Y-%m-%d"))
                    for d, r in itertools.product(dep_dates, ret_dates)
                ]
            params = {
                "max_duration_flight": float(
                    self._get_widget_value(self.entries["max_duration_flight"])
                ),
                "price_limit": int(
                    self._get_widget_value(self.entries["price_limit"])
                ),
                "checking_interval": int(
                    self._get_widget_value(self.entries["checking_interval"])
                ),
                "checking_duration": int(
                    self._get_widget_value(self.entries["checking_duration"])
                ),
            }
        except ValueError as e:
            messagebox.showerror("Invalid input", str(e))
            return

        total = len(deps) * len(dests) * len(date_pairs)
        if not messagebox.askyesno(
            "Confirm Tasks", f"{total} tasks will be started. Continue?"
        ):
            return

        cfg = {}
        for key, widget in self.entries.items():
            cfg[key] = self._get_widget_value(widget)
        cfg["departure_codes"] = deps
        cfg["destination_codes"] = dests
        cfg["max_duration_flight"] = params["max_duration_flight"]
        self.config_mgr.save(cfg)

        self.start_btn.config(state="disabled")
        self.progress.start()

        thread = threading.Thread(
            target=self._run_bots,
            args=(deps, dests, date_pairs, params),
            daemon=True,
        )
        thread.start()

    def _run_bots(self, deps, dests, date_pairs, params):
        """
        Run FlightBot.start() for each route/date in a background thread.
        """
        for dep, dest in itertools.product(deps, dests):
            for dd, rd in date_pairs:
                bot = FlightBot(
                    departure=dep,
                    destination=dest,
                    dep_date=dd,
                    arrival_date=rd,
                    price_limit=params["price_limit"],
                    checking_interval=params["checking_interval"],
                    checking_duration=params["checking_duration"],
                    max_duration_flight=params["max_duration_flight"],
                )
                bot.start()
        self.progress.stop()
        messagebox.showinfo(
            "FlightBot", "All monitoring tasks have completed."
        )
        self.quit()


if __name__ == "__main__":
    app = FlightBotGUI()
    app.mainloop()
