#!/usr/bin/env python3
"""
GUI for the Flight Price Monitor. Divided into three resizable zones:

#1 (left): search configuration fields
#2 (right): best-flight and historical graph
#3 (bottom): status panel with current action label above the progress bar
"""

import itertools
import json
import os
import re
import threading
import time
import tkinter as tk
from datetime import datetime, timedelta
from tkinter import END, messagebox, simpledialog, ttk

import matplotlib
import pandas as pd

matplotlib.use("TkAgg")
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure

from flight_tracker.airport_from_distance import AirportFromDistance
from flight_tracker.country_to_airport import CountryToAirport
from flight_tracker.flight_bot import FlightBot
from flight_tracker.flight_record import FlightRecord
from flight_tracker.load_config import ConfigManager


class FlightBotGUI(tk.Tk):
    """Tkinter GUI that gathers parameters and runs FlightBot tasks."""

    def __init__(self):
        super().__init__()
        self.title("Flight Price Monitor")
        self.resizable(True, True)
        self.columnconfigure(0, weight=1)
        self.columnconfigure(1, weight=1)
        self.rowconfigure(0, weight=1)
        self.rowconfigure(1, weight=0)

        # Frame #1: configuration inputs
        self.config_frame = tk.LabelFrame(self, text="Search Configuration")
        self.config_frame.grid(
            row=0, column=0, padx=10, pady=10, sticky="nsew"
        )
        self.config_frame.columnconfigure(1, weight=1)

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
        ]

        self.entries = {}
        for idx, (label, name, multiline) in enumerate(fields):
            lbl = tk.Label(self.config_frame, text=label)
            lbl.grid(row=idx, column=0, padx=5, pady=5, sticky="ne")
            widget = (
                tk.Text(self.config_frame, width=40, height=3)
                if multiline
                else tk.Entry(self.config_frame, width=30)
            )
            widget.grid(row=idx, column=1, padx=5, pady=5, sticky="ew")
            widget.bind("<KeyRelease>", lambda ev: self._on_fields_changed())
            self.entries[name] = widget

        self.start_btn = tk.Button(
            self.config_frame, text="Start Monitoring", command=self._on_start
        )
        self.start_btn.grid(row=len(fields), column=0, columnspan=2, pady=10)

        # Frame #2: results (best flight + history)
        self.result_frame = tk.LabelFrame(self, text="Results")
        self.result_frame.grid(
            row=0, column=1, padx=10, pady=10, sticky="nsew"
        )
        self.result_frame.rowconfigure(0, weight=0)
        self.result_frame.rowconfigure(1, weight=1)
        self.result_frame.columnconfigure(0, weight=1)

        # Historic best-flight display
        self.historic_frame = tk.LabelFrame(
            self.result_frame, text="Historic Best Flight"
        )
        self.historic_frame.grid(
            row=0, column=0, sticky="ew", padx=5, pady=(5, 2)
        )
        self.historic_text = tk.Text(
            self.historic_frame, state="disabled", height=5, wrap="word"
        )
        self.historic_text.pack(fill="both", expand=True, padx=5, pady=5)

        # Price history graph
        self.graph_frame = tk.LabelFrame(
            self.result_frame, text="Price History"
        )
        self.graph_frame.grid(
            row=1, column=0, sticky="nsew", padx=5, pady=(2, 5)
        )
        self.graph_frame.rowconfigure(0, weight=1)
        self.graph_frame.columnconfigure(0, weight=1)
        self.figure = Figure(figsize=(5, 4), dpi=100)
        self.ax = self.figure.add_subplot(111)
        self.ax.set_xlabel("Monitoring Date")
        self.ax.set_ylabel("Price (€)")
        self.canvas = FigureCanvasTkAgg(self.figure, master=self.graph_frame)
        self.canvas.get_tk_widget().grid(row=0, column=0, sticky="nsew")

        # Frame #3: status panel
        self.status_panel = tk.LabelFrame(self, text="Status")
        self.status_panel.grid(
            row=1, column=0, columnspan=2, padx=10, pady=(0, 10), sticky="ew"
        )
        self.status_panel.columnconfigure(0, weight=1)
        self.status_label = tk.Label(self.status_panel, text="Status: idle")
        self.status_label.grid(
            row=0, column=0, padx=5, pady=(5, 0), sticky="w"
        )
        self.progress = ttk.Progressbar(
            self.status_panel, mode="indeterminate"
        )
        self.progress.grid(row=1, column=0, padx=5, pady=(0, 5), sticky="ew")

        # load airport-code → name
        airports_df = pd.read_csv(AirportFromDistance.AIRPORTS_URL)
        self.code_to_name = {
            code: name
            for code, name in zip(
                airports_df["iata_code"], airports_df["name"]
            )
            if pd.notna(code)
        }

        # state & managers
        self.resolved_airports = {}
        self.config_mgr = ConfigManager()
        self.record_mgr = FlightRecord()
        self._stop_event = threading.Event()
        self._monitor_thread = None
        self._current_best = None

        # bind focus-out for airports
        for field in ("departure", "destination"):
            self.entries[field].bind(
                "<FocusOut>",
                lambda ev, f=field: self._pre_resolve_airports(f),
            )

        # load saved config
        saved = self.config_mgr.load()
        for key, widget in self.entries.items():
            if key in saved:
                val = saved[key]
                if isinstance(widget, tk.Text):
                    widget.insert("1.0", val)
                else:
                    widget.insert(0, val)
        for side in ("departure", "destination"):
            ck = f"{side}_codes"
            if ck in saved:
                self.resolved_airports[side] = saved[ck]

        # initial historic display & graph
        self._load_historic_best()
        self._plot_history()
        if self._fields_complete():
            self._on_start()

    def _get_widget_value(self, widget):
        if isinstance(widget, tk.Text):
            return widget.get("1.0", END).strip()
        return widget.get().strip()

    def _pre_resolve_airports(self, field):
        w = self.entries[field]
        raw = self._get_widget_value(w)
        if not raw:
            return
        pat = re.compile(r"^[A-Z]{3} - .+?(?:,\s*[A-Z]{3} - .+?)*$")
        if pat.match(raw):
            codes = [seg.split("-", 1)[0].strip() for seg in raw.split(",")]
            self.resolved_airports[field] = codes
            return
        try:
            codes = self._resolve_airports(raw)
        except ValueError as e:
            messagebox.showerror("Invalid input", f"{field.title()}: {e}")
            return
        disp = [f"{c} - {self.code_to_name.get(c,'')}" for c in codes]
        if isinstance(w, tk.Text):
            w.delete("1.0", END)
            w.insert("1.0", ",".join(disp))
        else:
            w.delete(0, END)
            w.insert(0, ",".join(disp))
        self.resolved_airports[field] = codes
        cfg = self.config_mgr.load()
        cfg[field] = ",".join(disp)
        cfg[f"{field}_codes"] = codes
        self.config_mgr.save(cfg)

    def _resolve_airports(self, s):
        toks = [t.strip() for t in s.split(",") if t.strip()]
        if all(len(t) == 3 and t.isalpha() and t.isupper() for t in toks):
            return toks
        if len(toks) == 2:
            city, country = toks
            dur = simpledialog.askinteger(
                "Max Duration",
                f"Max transport duration (min) from {city}, {country}",
                minvalue=1,
            )
            if dur is None:
                raise ValueError("Cancelled")
            return [
                c
                for c, _ in AirportFromDistance().get_airports(
                    f"{city}, {country}", dur
                )
            ]
        return [c for c, _ in CountryToAirport().get_airports(s)]

    def _fields_complete(self):
        dep = self._get_widget_value(self.entries["departure"])
        dest = self._get_widget_value(self.entries["destination"])
        depd = self._get_widget_value(self.entries["dep_date"])
        trip = self._get_widget_value(self.entries["trip_duration"])
        arrd = self._get_widget_value(self.entries["arrival_date"])
        mdur = self._get_widget_value(self.entries["max_duration_flight"])
        plim = self._get_widget_value(self.entries["price_limit"])
        if not dep or not dest or not depd or not mdur or not plim:
            return False
        if not trip and not arrd:
            return False
        return True

    def _on_start(self):
        if not self._fields_complete():
            messagebox.showerror(
                "Missing fields", "Please complete all required fields."
            )
            return
        if self._monitor_thread and self._monitor_thread.is_alive():
            return
        self._stop_event.clear()
        self.status_label.config(text="Status: starting...")
        self.progress.start()
        self._current_best = None
        self.best_text = None  # not used here
        self._start_monitoring()

    def _start_monitoring(self):
        deps = self.resolved_airports.get("departure", [])
        dests = self.resolved_airports.get("destination", [])
        dep_dates = self._parse_dates(
            self._get_widget_value(self.entries["dep_date"])
        )
        trip = self._get_widget_value(self.entries["trip_duration"])
        if trip:
            drs = self._parse_durations(trip)
            date_pairs = [
                (
                    d.strftime("%Y-%m-%d"),
                    (d + timedelta(days=x)).strftime("%Y-%m-%d"),
                )
                for d in dep_dates
                for x in drs
            ]
        else:
            ret_dates = self._parse_dates(
                self._get_widget_value(self.entries["arrival_date"])
            )
            date_pairs = [
                (d.strftime("%Y-%m-%d"), r.strftime("%Y-%m-%d"))
                for d in dep_dates
                for r in ret_dates
            ]
        params = {
            "max_duration_flight": float(
                self._get_widget_value(self.entries["max_duration_flight"])
            ),
            "price_limit": int(
                self._get_widget_value(self.entries["price_limit"])
            ),
        }
        cfg = {k: self._get_widget_value(w) for k, w in self.entries.items()}
        cfg["departure_codes"] = deps
        cfg["destination_codes"] = dests
        cfg["max_duration_flight"] = params["max_duration_flight"]
        self.config_mgr.save(cfg)

        self._monitor_thread = threading.Thread(
            target=self._monitor_loop,
            args=(deps, dests, date_pairs, params),
            daemon=True,
        )
        self._monitor_thread.start()

    def _monitor_loop(self, deps, dests, date_pairs, params):
        while not self._stop_event.is_set():
            self.status_label.config(text="Status: checking flights...")
            self.progress.start()
            for dep, dest in itertools.product(deps, dests):
                for dd, rd in date_pairs:
                    if self._stop_event.is_set():
                        break
                    self.status_label.config(
                        text=f"Checking {dep}→{dest} on {dd} → {rd}"
                    )
                    bot = FlightBot(
                        departure=dep,
                        destination=dest,
                        dep_date=dd,
                        arrival_date=rd,
                        price_limit=params["price_limit"],
                        max_duration_flight=params["max_duration_flight"],
                    )
                    rec = bot.start()
                    if rec:
                        # save using today's date
                        monitor_date = datetime.now().strftime("%Y-%m-%d")
                        self.record_mgr.save_record(
                            date=monitor_date,
                            departure=dep,
                            destination=dest,
                            company=rec["company"],
                            duration_out=rec["duration_out"],
                            duration_return=rec["duration_return"],
                            price=rec["price"],
                        )
                        self._update_historic_best()
                        self._plot_history()
                if self._stop_event.is_set():
                    break
            self.progress.stop()
            self.status_label.config(text="Status: waiting")
            # sleep up to 4 hours
            for _ in range(4 * 60):
                if self._stop_event.is_set():
                    break
                time.sleep(60)

        self.progress.stop()
        self.status_label.config(text="Status: idle")
        messagebox.showinfo("FlightBot", "Monitoring loop ended.")

    def _load_historic_best(self):
        """Load the overall best record and display it."""
        path = self.record_mgr.path
        if not os.path.exists(path):
            return
        best_rec = None
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if best_rec is None or rec["price"] < best_rec["price"]:
                    best_rec = rec
        if not best_rec:
            return
        text = (
            f"Date: {best_rec['date']}\n"
            f"Route: {best_rec['departure']} → {best_rec['destination']}\n"
            f"Company: {best_rec['company']}\n"
            f"Price: €{best_rec['price']:.2f}\n"
            f"Outbound: {best_rec['duration_out']}\n"
            f"Return: {best_rec['duration_return']}\n"
        )
        self.historic_text.configure(state="normal")
        self.historic_text.delete("1.0", END)
        self.historic_text.insert(END, text)
        self.historic_text.configure(state="disabled")

    def _update_historic_best(self):
        """Reload and display best-flight after saving a new record."""
        self._load_historic_best()

    def _plot_history(self):
        """Load all records and plot price vs monitoring date with margins."""
        path = self.record_mgr.path
        if not os.path.exists(path):
            return
        dates, prices = [], []
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                d = datetime.strptime(rec["date"], "%Y-%m-%d")
                dates.append(d)
                prices.append(rec["price"])
        if not dates:
            return
        self.ax.clear()
        self.ax.plot_date(dates, prices, "-o")
        # scale axis from day before min to day after max
        min_d, max_d = min(dates), max(dates)
        self.ax.set_xlim(min_d - timedelta(days=1), max_d + timedelta(days=1))
        self.ax.set_xlabel("Monitoring Date")
        self.ax.set_ylabel("Price (€)")
        self.figure.autofmt_xdate()
        self.canvas.draw()

    def _parse_dates(self, s):
        parts = s.strip().split("-")
        if len(parts) == 3:
            return [datetime.strptime(s, "%Y-%m-%d")]
        if len(parts) == 6:
            st = "-".join(parts[:3])
            en = "-".join(parts[3:])
            d0 = datetime.strptime(st, "%Y-%m-%d")
            d1 = datetime.strptime(en, "%Y-%m-%d")
            if d1 < d0:
                raise ValueError("End date before start date")
            return [d0 + timedelta(days=i) for i in range((d1 - d0).days + 1)]
        raise ValueError("Invalid date format")

    def _parse_durations(self, s):
        parts = s.strip().split("-")
        if len(parts) == 1:
            return [int(parts[0])]
        if len(parts) == 2:
            lo, hi = int(parts[0]), int(parts[1])
            if hi < lo:
                raise ValueError("Invalid duration range")
            return list(range(lo, hi + 1))
        raise ValueError("Invalid duration format")


if __name__ == "__main__":
    app = FlightBotGUI()
    app.mainloop()
