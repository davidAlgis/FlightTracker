#!/usr/bin/env python3
"""
GUI for the Flight Price Monitor. Divided into three resizable zones:

#1 (left): search configuration fields
#2 (right): best‐flight and historical graph
#3 (bottom): status panel with current action label above the progress bar

Now also sends Windows toast notifications when:
- A new all‐time low price is detected.
- A flight’s price has jumped by more than 10% compared to the minimal price 3 days ago.
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
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure
from win10toast import ToastNotifier

from flight_tracker.airport_from_distance import AirportFromDistance
from flight_tracker.country_to_airport import CountryToAirport
from flight_tracker.flight_bot import FlightBot
from flight_tracker.flight_record import FlightRecord
from flight_tracker.load_config import ConfigManager

matplotlib.use("TkAgg")


class FlightBotGUI(tk.Tk):
    """Tkinter GUI that gathers parameters, runs FlightBot tasks in a background thread,
    filters out poor airports after first sweep, displays best‐flight and history,
    and issues toast notifications on new lows or large price jumps."""

    def __init__(self):
        super().__init__()
        self.title("Flight Price Monitor")
        self.resizable(True, True)
        self._configure_grid()
        self._create_config_frame()
        self._create_result_frame()
        self._create_status_panel()
        self._load_airport_names()

        # state & managers
        self.resolved_airports = {}
        self.config_mgr = ConfigManager()
        self.record_mgr = FlightRecord()
        self.notifier = ToastNotifier()
        self.best_prices = {}  # (dep, dest) → best price found in this session
        self._first_pass = True  # only filter after the initial sweep
        self._stop_event = threading.Event()
        self._monitor_thread = None

        # bind focus‐out for airports to resolve them
        for field in ("departure", "destination"):
            self.entries[field].bind(
                "<FocusOut>",
                lambda ev, f=field: self._pre_resolve_airports(f),
            )

        self._load_saved_config()
        self._load_historic_best()
        self._plot_history()

        # auto‐start if fields are already complete
        if self._fields_complete():
            self._on_start()

    def _configure_grid(self):
        """Configure the main window’s grid layout."""
        self.columnconfigure(0, weight=1)
        self.columnconfigure(1, weight=1)
        self.rowconfigure(0, weight=1)
        self.rowconfigure(1, weight=0)

    def _create_config_frame(self):
        """Left panel: search configuration."""
        frame = tk.LabelFrame(self, text="Search Configuration")
        frame.grid(row=0, column=0, padx=10, pady=10, sticky="nsew")
        frame.columnconfigure(1, weight=1)

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
        ]

        self.entries = {}
        for idx, (label, name, multiline) in enumerate(fields):
            tk.Label(frame, text=label).grid(
                row=idx, column=0, padx=5, pady=5, sticky="ne"
            )
            widget = (
                tk.Text(frame, width=40, height=3)
                if multiline
                else tk.Entry(frame, width=30)
            )
            widget.grid(row=idx, column=1, padx=5, pady=5, sticky="ew")
            widget.bind("<KeyRelease>", lambda ev: self._on_fields_changed())
            self.entries[name] = widget

        tk.Button(frame, text="Start Monitoring", command=self._on_start).grid(
            row=len(fields), column=0, columnspan=2, pady=10
        )
        self.config_frame = frame

    def _create_result_frame(self):
        """Right panel: historic best flight and price history graph."""
        frame = tk.LabelFrame(self, text="Results")
        frame.grid(row=0, column=1, padx=10, pady=10, sticky="nsew")
        frame.rowconfigure(0, weight=0)
        frame.rowconfigure(1, weight=1)
        frame.columnconfigure(0, weight=1)

        # Historic best-flight display
        hf = tk.LabelFrame(frame, text="Historic Best Flight")
        hf.grid(row=0, column=0, sticky="ew", padx=5, pady=(5, 2))
        self.historic_text = tk.Text(
            hf, state="disabled", height=5, wrap="word"
        )
        self.historic_text.pack(fill="both", expand=True, padx=5, pady=5)

        # Price history graph
        gf = tk.LabelFrame(frame, text="Price History")
        gf.grid(row=1, column=0, sticky="nsew", padx=5, pady=(2, 5))
        gf.rowconfigure(0, weight=1)
        gf.columnconfigure(0, weight=1)

        self.figure = Figure(figsize=(5, 4), dpi=100)
        self.ax = self.figure.add_subplot(111)
        self.ax.set_xlabel("Monitoring Date")
        self.ax.set_ylabel("Price (€)")
        canvas = FigureCanvasTkAgg(self.figure, master=gf)
        canvas.get_tk_widget().grid(row=0, column=0, sticky="nsew")
        self.canvas = canvas

        self.result_frame = frame

    def _create_status_panel(self):
        """Bottom panel: current action label and progress bar."""
        sp = tk.LabelFrame(self, text="Status")
        sp.grid(
            row=1, column=0, columnspan=2, padx=10, pady=(0, 10), sticky="ew"
        )
        sp.columnconfigure(0, weight=1)

        self.status_label = tk.Label(sp, text="Status: idle")
        self.status_label.grid(
            row=0, column=0, padx=5, pady=(5, 0), sticky="w"
        )

        self.progress = ttk.Progressbar(sp, mode="indeterminate")
        self.progress.grid(row=1, column=0, padx=5, pady=(0, 5), sticky="ew")

        self.status_panel = sp

    def _load_airport_names(self):
        """Load mapping IATA→airport name from OurAirports."""
        df = pd.read_csv(AirportFromDistance.AIRPORTS_URL)
        self.code_to_name = {
            c: n for c, n in zip(df["iata_code"], df["name"]) if pd.notna(c)
        }

    def _load_saved_config(self):
        """Restore previous inputs and codes from config.json."""
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

    def _get_widget_value(self, w):
        if isinstance(w, tk.Text):
            return w.get("1.0", END).strip()
        return w.get().strip()

    def _fields_complete(self):
        dep = self._get_widget_value(self.entries["departure"])
        dest = self._get_widget_value(self.entries["destination"])
        dd = self._get_widget_value(self.entries["dep_date"])
        trip = self._get_widget_value(self.entries["trip_duration"])
        arr = self._get_widget_value(self.entries["arrival_date"])
        mdur = self._get_widget_value(self.entries["max_duration_flight"])
        if not (dep and dest and dd and mdur):
            return False
        if not trip and not arr:
            return False
        return True

    def _on_fields_changed(self):
        """Auto‐start/stop when required fields are filled/cleared."""
        alive = self._monitor_thread and self._monitor_thread.is_alive()
        if self._fields_complete() and not alive:
            self._on_start()
        elif not self._fields_complete() and alive:
            self._stop_event.set()
            self.progress.stop()
            self.status_label.config(text="Status: idle")
            messagebox.showinfo(
                "FlightBot", "Monitoring stopped (fields changed)."
            )

    def _pre_resolve_airports(self, field):
        """Resolve free‐form airport input into CODE – Name on focus‐out."""
        w = self.entries[field]
        raw = self._get_widget_value(w)
        if not raw:
            return
        if re.match(r"^[A-Z]{3} - .+", raw):
            codes = [seg.split("-", 1)[0].strip() for seg in raw.split(",")]
            self.resolved_airports[field] = codes
            return
        try:
            codes = self._resolve_airports(raw)
        except ValueError as e:
            messagebox.showerror("Invalid input", f"{field}: {e}")
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

    def _resolve_airports(self, txt):
        toks = [t.strip() for t in txt.split(",") if t.strip()]
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
        return [c for c, _ in CountryToAirport().get_airports(txt)]

    def _on_start(self):
        """Validate and begin monitoring in background thread."""
        if not self._fields_complete():
            messagebox.showerror(
                "Missing fields", "Please complete all required fields."
            )
            return
        if self._monitor_thread and self._monitor_thread.is_alive():
            return
        self._stop_event.clear()
        self.status_label.config(text="Status: starting…")
        self.progress.start()
        self._start_monitoring()

    def _start_monitoring(self):
        deps = self.resolved_airports.get("departure", [])
        dests = self.resolved_airports.get("destination", [])
        dep_ds = self._parse_dates(
            self._get_widget_value(self.entries["dep_date"])
        )
        trip = self._get_widget_value(self.entries["trip_duration"])
        if trip:
            drs = self._parse_durations(trip)
            pairs = [
                (
                    d.strftime("%Y-%m-%d"),
                    (d + timedelta(days=x)).strftime("%Y-%m-%d"),
                )
                for d in dep_ds
                for x in drs
            ]
        else:
            arr_ds = self._parse_dates(
                self._get_widget_value(self.entries["arrival_date"])
            )
            pairs = [
                (d.strftime("%Y-%m-%d"), r.strftime("%Y-%m-%d"))
                for d in dep_ds
                for r in arr_ds
            ]

        params = {
            "max_duration_flight": float(
                self._get_widget_value(self.entries["max_duration_flight"])
            ),
        }

        # save config
        cfg = {k: self._get_widget_value(w) for k, w in self.entries.items()}
        cfg["departure_codes"] = deps
        cfg["destination_codes"] = dests
        cfg["max_duration_flight"] = params["max_duration_flight"]
        self.config_mgr.save(cfg)

        self._monitor_thread = threading.Thread(
            target=self._monitor_loop,
            args=(deps, dests, pairs, params),
            daemon=True,
        )
        self._monitor_thread.start()

    def _get_global_best_price(self):
        """Return the lowest price ever recorded across all dates, or None."""
        best = None
        path = self.record_mgr.path
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    p = rec.get("price")
                    if best is None or p < best:
                        best = p
        return best

    def _monitor_loop(self, deps, dests, pairs, params):
        """Background loop: check each route×date, record & notify, then filter."""
        while not self._stop_event.is_set():
            self.status_label.config(text="Status: checking flights…")
            self.progress.start()

            for dep, dest in itertools.product(deps, dests):
                best_for_pair = None
                for dd, rd in pairs:
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
                        max_duration_flight=params["max_duration_flight"],
                    )
                    rec = bot.start()
                    if not rec:
                        continue

                    price = rec["price"]
                    # track session‐best for this pair
                    if best_for_pair is None or price < best_for_pair:
                        best_for_pair = price

                    # compare to global historic best before saving today’s record
                    today = datetime.now().strftime("%Y-%m-%d")
                    global_before = self._get_global_best_price()
                    three_days = (datetime.now() - timedelta(days=3)).strftime(
                        "%Y-%m-%d"
                    )
                    old_rec = self.record_mgr.load_record(three_days)

                    # save/update today's minimal
                    self.record_mgr.save_record(
                        date=today,
                        departure=dep,
                        destination=dest,
                        company=rec["company"],
                        duration_out=rec["duration_out"],
                        duration_return=rec["duration_return"],
                        price=price,
                    )

                    # notify on new all‐time low
                    if global_before is None or price < global_before:
                        self.notifier.show_toast(
                            "New All-Time Low!",
                            f"{dep}→{dest} on {dd}: €{price:.2f}",
                            duration=10,
                            threaded=True,
                        )

                    # notify on >10% jump vs 3-day old record
                    if old_rec and price > old_rec["price"] * 1.1:
                        diff = price - old_rec["price"]
                        pct = (diff / old_rec["price"]) * 100
                        self.notifier.show_toast(
                            "Price Jump Alert",
                            f"{dep}→{dest} jumped €{diff:.2f} (+{pct:.0f}%) vs 3 days ago",
                            duration=10,
                            threaded=True,
                        )

                    # update display & history
                    self._load_historic_best()
                    self._plot_history()

                if best_for_pair is not None:
                    self.best_prices[(dep, dest)] = best_for_pair
                if self._stop_event.is_set():
                    break

            # after the very first pass, prune poor airports once
            if self._first_pass:
                self._filter_airports()
                self._first_pass = False

            self.progress.stop()
            self.status_label.config(text="Status: waiting")
            # wait up to 4 hours, checking for stop
            for _ in range(4 * 60):
                if self._stop_event.is_set():
                    break
                time.sleep(60)

        self.progress.stop()
        self.status_label.config(text="Status: idle")
        messagebox.showinfo("FlightBot", "Monitoring loop ended.")

    def _filter_airports(self):
        """
        Remove any airport whose all pairings are ≥20% above overall best,
        unless it ever appeared in a saved daily-best record.
        """
        if not self.best_prices:
            return

        overall = min(self.best_prices.values())
        protected = set()
        if os.path.exists(self.record_mgr.path):
            with open(self.record_mgr.path, "r", encoding="utf-8") as f:
                for line in f:
                    try:
                        rec = json.loads(line)
                        protected.add(rec["departure"])
                        protected.add(rec["destination"])
                    except json.JSONDecodeError:
                        continue

        deps = self.resolved_airports.get("departure", [])
        dests = self.resolved_airports.get("destination", [])

        drop_deps = [
            d
            for d in deps
            if d not in protected
            and all(
                self.best_prices.get((d, x), float("inf")) >= 1.2 * overall
                for x in dests
            )
        ]
        drop_dests = [
            x
            for x in dests
            if x not in protected
            and all(
                self.best_prices.get((d, x), float("inf")) >= 1.2 * overall
                for d in deps
            )
        ]

        self.resolved_airports["departure"] = [
            d for d in deps if d not in drop_deps
        ]
        self.resolved_airports["destination"] = [
            x for x in dests if x not in drop_dests
        ]

        for field in ("departure", "destination"):
            codes = self.resolved_airports[field]
            text = ",".join(
                f"{c} - {self.code_to_name.get(c,'')}" for c in codes
            )
            w = self.entries[field]
            if isinstance(w, tk.Text):
                w.delete("1.0", END)
                w.insert("1.0", text)
            else:
                w.delete(0, END)
                w.insert(0, text)

    def _load_historic_best(self):
        """Load the all-time best record and display it."""
        path = self.record_mgr.path
        if not os.path.exists(path):
            return
        best = None
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if best is None or rec["price"] < best["price"]:
                    best = rec
        if not best:
            return
        text = (
            f"Date: {best['date']}\n"
            f"Route: {best['departure']} → {best['destination']}\n"
            f"Company: {best['company']}\n"
            f"Price: €{best['price']:.2f}\n"
            f"Outbound: {best['duration_out']}\n"
            f"Return: {best['duration_return']}\n"
        )
        self.historic_text.configure(state="normal")
        self.historic_text.delete("1.0", END)
        self.historic_text.insert(END, text)
        self.historic_text.configure(state="disabled")

    def _plot_history(self):
        """Plot price vs monitoring date, with a one-day margin."""
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
        lo, hi = min(dates), max(dates)
        self.ax.set_xlim(lo - timedelta(days=1), hi + timedelta(days=1))
        self.ax.set_xlabel("Monitoring Date")
        self.ax.set_ylabel("Price (€)")
        self.figure.autofmt_xdate()
        self.canvas.draw()

    def _parse_dates(self, s):
        """Parse 'YYYY-MM-DD' or 'YYYY-MM-DD-YYYY-MM-DD' → list of datetimes."""
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
        """Parse 'N' or 'N-M' → list of ints."""
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
