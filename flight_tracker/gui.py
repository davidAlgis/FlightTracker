#!/usr/bin/env python3
"""
gui.py

GUI for the Flight Price Monitor. Three resizable zones:
1) Search config   2) Best-flight + graph   3) Status/progress
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

from flight_tracker.airport_from_distance import AirportFromDistance
from flight_tracker.country_to_airport import CountryToAirport
from flight_tracker.flight_bot import FlightBot
from flight_tracker.flight_record import FlightRecord
from flight_tracker.load_config import ConfigManager

matplotlib.use("TkAgg")


class FlightBotGUI(tk.Tk):
    """Tkinter GUI that collects parameters, launches FlightBot in bg, and displays results."""

    def __init__(self):
        super().__init__()
        self.title("Flight Price Monitor")
        self.resizable(True, True)
        self._configure_grid()
        self._create_config_frame()
        self._create_result_frame()
        self._create_status_panel()
        self._load_airport_names()
        self._init_state()
        self._load_saved_config()
        self._load_historic_best()
        self._plot_history()
        # auto-start if fully configured
        if self._fields_complete():
            self._on_start()

    def _configure_grid(self):
        self.columnconfigure(0, weight=1)
        self.columnconfigure(1, weight=1)
        self.rowconfigure(0, weight=1)
        self.rowconfigure(1, weight=0)

    def _create_config_frame(self):
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
        for i, (lbl, name, multi) in enumerate(fields):
            tk.Label(frame, text=lbl).grid(
                row=i, column=0, padx=5, pady=5, sticky="ne"
            )
            w = (
                tk.Text(frame, width=40, height=3)
                if multi
                else tk.Entry(frame, width=30)
            )
            w.grid(row=i, column=1, padx=5, pady=5, sticky="ew")
            w.bind("<KeyRelease>", lambda e: self._on_fields_changed())
            self.entries[name] = w

        tk.Button(frame, text="Start Monitoring", command=self._on_start).grid(
            row=len(fields), column=0, columnspan=2, pady=10
        )
        self.config_frame = frame

    def _create_result_frame(self):
        frame = tk.LabelFrame(self, text="Results")
        frame.grid(row=0, column=1, padx=10, pady=10, sticky="nsew")
        frame.rowconfigure(0, weight=0)
        frame.rowconfigure(1, weight=1)
        frame.columnconfigure(0, weight=1)

        # historic best
        hf = tk.LabelFrame(frame, text="Historic Best Flight")
        hf.grid(row=0, column=0, sticky="ew", padx=5, pady=(5, 2))
        self.historic_text = tk.Text(
            hf, state="disabled", height=5, wrap="word"
        )
        self.historic_text.pack(fill="both", expand=True, padx=5, pady=5)

        # graph
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
        df = pd.read_csv(AirportFromDistance.AIRPORTS_URL)
        self.code_to_name = {
            c: n for c, n in zip(df["iata_code"], df["name"]) if pd.notna(c)
        }

    def _init_state(self):
        self.resolved_airports = {}
        self.config_mgr = ConfigManager()
        self.record_mgr = FlightRecord()
        self._stop_event = threading.Event()
        self._monitor_thread = None
        # bind focus-out for airport fields
        for fld in ("departure", "destination"):
            self.entries[fld].bind(
                "<FocusOut>", lambda e, f=fld: self._pre_resolve_airports(f)
            )

    def _load_saved_config(self):
        saved = self.config_mgr.load()
        for k, w in self.entries.items():
            if k in saved:
                v = saved[k]
                if isinstance(w, tk.Text):
                    w.insert("1.0", v)
                else:
                    w.insert(0, v)
        for fld in ("departure", "destination"):
            ck = f"{fld}_codes"
            if ck in saved:
                self.resolved_airports[fld] = saved[ck]

    def _get_widget_value(self, w):
        return (
            w.get("1.0", END).strip()
            if isinstance(w, tk.Text)
            else w.get().strip()
        )

    def _fields_complete(self):
        dep = self._get_widget_value(self.entries["departure"])
        dest = self._get_widget_value(self.entries["destination"])
        dd = self._get_widget_value(self.entries["dep_date"])
        trip = self._get_widget_value(self.entries["trip_duration"])
        arrd = self._get_widget_value(self.entries["arrival_date"])
        md = self._get_widget_value(self.entries["max_duration_flight"])
        if not (dep and dest and dd and md):
            return False
        if not trip and not arrd:
            return False
        return True

    def _pre_resolve_airports(self, fld):
        w = self.entries[fld]
        raw = self._get_widget_value(w)
        if not raw:
            return
        pat = re.compile(r"^[A-Z]{3} - .+")
        if pat.match(raw):
            self.resolved_airports[fld] = [
                seg.split("-", 1)[0].strip() for seg in raw.split(",")
            ]
            return
        try:
            codes = self._resolve_airports(raw)
        except ValueError as e:
            messagebox.showerror("Invalid input", f"{fld}: {e}")
            return
        disp = [f"{c} - {self.code_to_name.get(c,'')}" for c in codes]
        if isinstance(w, tk.Text):
            w.delete("1.0", END)
            w.insert("1.0", ",".join(disp))
        else:
            w.delete(0, END)
            w.insert(0, ",".join(disp))
        self.resolved_airports[fld] = codes
        cfg = self.config_mgr.load()
        cfg[fld] = ",".join(disp)
        cfg[f"{fld}_codes"] = codes
        self.config_mgr.save(cfg)

    def _resolve_airports(self, txt):
        toks = [t.strip() for t in txt.split(",") if t.strip()]
        if all(len(t) == 3 and t.isalpha() and t.isupper() for t in toks):
            return toks
        if len(toks) == 2:
            city, ctry = toks
            d = simpledialog.askinteger(
                "Max Duration",
                f"Max transport duration (min) from {city}, {ctry}",
                minvalue=1,
            )
            if d is None:
                raise ValueError("Cancelled")
            return [
                c
                for c, _ in AirportFromDistance().get_airports(
                    f"{city}, {ctry}", d
                )
            ]
        return [c for c, _ in CountryToAirport().get_airports(txt)]

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
        self._start_monitoring()

    def _start_monitoring(self):
        deps = self.resolved_airports["departure"]
        dests = self.resolved_airports["destination"]
        depds = self._parse_dates(
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
                for d in depds
                for x in drs
            ]
        else:
            arrs = self._parse_dates(
                self._get_widget_value(self.entries["arrival_date"])
            )
            pairs = [
                (d.strftime("%Y-%m-%d"), r.strftime("%Y-%m-%d"))
                for d in depds
                for r in arrs
            ]
        params = {
            "max_duration_flight": float(
                self._get_widget_value(self.entries["max_duration_flight"])
            )
        }
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

    def _monitor_loop(self, deps, dests, pairs, params):
        while not self._stop_event.is_set():
            self.status_label.config(text="Status: checking flights...")
            self.progress.start()
            for dep, dest in itertools.product(deps, dests):
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
                    if rec:
                        mdate = datetime.now().strftime("%Y-%m-%d")
                        self.record_mgr.save_record(
                            date=mdate,
                            departure=dep,
                            destination=dest,
                            company=rec["company"],
                            duration_out=rec["duration_out"],
                            duration_return=rec["duration_return"],
                            price=rec["price"],
                        )
                        self._load_historic_best()
                        self._plot_history()
                if self._stop_event.is_set():
                    break
            self.progress.stop()
            self.status_label.config(text="Status: waiting")
            for _ in range(4 * 60):
                if self._stop_event.is_set():
                    break
                time.sleep(60)

        self.progress.stop()
        self.status_label.config(text="Status: idle")
        messagebox.showinfo("FlightBot", "Monitoring loop ended.")

    def _load_historic_best(self):
        path = self.record_mgr.path
        if not os.path.exists(path):
            return
        best = None
        with open(path, "r", encoding="utf-8") as f:
            for ln in f:
                try:
                    rec = json.loads(ln)
                except:
                    continue
                if best is None or rec["price"] < best["price"]:
                    best = rec
        if not best:
            return
        txt = (
            f"Date: {best['date']}\n"
            f"Route: {best['departure']} → {best['destination']}\n"
            f"Company: {best['company']}\n"
            f"Price: €{best['price']:.2f}\n"
            f"Outbound: {best['duration_out']}\n"
            f"Return: {best['duration_return']}\n"
        )
        self.historic_text.configure(state="normal")
        self.historic_text.delete("1.0", END)
        self.historic_text.insert(END, txt)
        self.historic_text.configure(state="disabled")

    def _plot_history(self):
        path = self.record_mgr.path
        if not os.path.exists(path):
            return
        ds, ps = [], []
        with open(path, "r", encoding="utf-8") as f:
            for ln in f:
                try:
                    rec = json.loads(ln)
                except:
                    continue
                d = datetime.strptime(rec["date"], "%Y-%m-%d")
                ds.append(d)
                ps.append(rec["price"])
        if not ds:
            return
        self.ax.clear()
        self.ax.plot_date(ds, ps, "-o")
        lo, hi = min(ds), max(ds)
        self.ax.set_xlim(lo - timedelta(days=1), hi + timedelta(days=1))
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
                raise ValueError("End date before start")
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
