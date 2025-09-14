#!/usr/bin/env python3
"""
GUI for the Flight Price Monitor. Divided into three resizable zones:

#1 (left): search configuration fields
#2 (right): best-flight and historical graph
#3 (bottom): status panel with current action label above the progress bar

Closes to system tray instead of exiting; right-click tray icon to restore or quit.

Changes for instant cancel:
- Keep a reference to the current FlightBot and call request_cancel() on Cancel.
- Do not auto-start after any user cancel; require explicit Start click.
- Poll waits at 1s to allow fast cancel during idle.
"""

import itertools
import json
import os
import random
import re
import threading
import time
import tkinter as tk
from datetime import datetime, timedelta
from tkinter import END, messagebox, simpledialog, ttk

import matplotlib
import pandas as pd
import pystray
from matplotlib.backends.backend_tkagg import (FigureCanvasTkAgg,
                                               NavigationToolbar2Tk)
from matplotlib.figure import Figure
from PIL import Image, ImageDraw
from win10toast import ToastNotifier

from flight_tracker.airport_from_distance import AirportFromDistance
from flight_tracker.country_to_airport import CountryToAirport
from flight_tracker.flight_bot import FlightBot
from flight_tracker.flight_record import FlightRecord
from flight_tracker.load_config import ConfigManager

matplotlib.use("TkAgg")


class FlightBotGUI(tk.Tk):
    """Tkinter GUI for configuring and running FlightBot with system-tray support."""

    def __init__(self):
        super().__init__()
        self.title("Flight Price Monitor")
        self.resizable(True, True)

        icon_path = self._asset_path("flight_tracker.ico")
        try:
            self.iconbitmap(icon_path)
        except tk.TclError:
            pass

        self._configure_grid()
        self._create_config_frame()
        self._create_result_frame()
        self._create_status_panel()
        self._load_airport_names()

        # state and managers
        self.resolved_airports = {}
        self.config_mgr = ConfigManager()
        self.record_mgr = FlightRecord()
        self.notifier = ToastNotifier()
        self.best_prices = {}
        self._first_pass = True
        self._stop_event = threading.Event()
        self._monitor_thread = None
        self._current_bot: FlightBot | None = None  # NEW: live bot reference

        # After any cancel, require explicit Start click (no auto-start)
        self._allow_auto_start = True

        # tray icon
        self._create_tray_icon()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        # bind focus-out for automatic airport resolution
        for field in ("departure", "destination"):
            self.entries[field].bind(
                "<FocusOut>", lambda ev, f=field: self._pre_resolve_airports(f)
            )

        self._load_saved_config()

        if os.path.exists("config.json"):
            self.withdraw()
            self.tray_icon.visible = True

        self._load_historic_best()
        self._plot_history()

        if self._allow_auto_start and self._fields_complete():
            self._on_start()

    def _configure_grid(self):
        """Configure main window grid for two columns and two rows."""
        self.columnconfigure(0, weight=1)
        self.columnconfigure(1, weight=1)
        self.rowconfigure(0, weight=1)
        self.rowconfigure(1, weight=0)

    def _create_config_frame(self):
        """Left-hand panel for search configuration."""
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
            ("Departure Date (YYYY-MM-DD)", "dep_date", False),
            ("Return Date (YYYY-MM-DD)", "arrival_date", False),
            ("Trip Duration (days) (e.g. 7 or 25-35)", "trip_duration", False),
            ("Max Flight Duration (h)", "max_duration_flight", False),
        ]

        self.entries = {}
        for idx, (lbl_txt, name, multiline) in enumerate(fields):
            tk.Label(frame, text=lbl_txt).grid(
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

        self.start_button = tk.Button(
            frame, text="Start Monitoring", command=self._on_start
        )
        self.start_button.grid(
            row=len(fields), column=0, columnspan=2, pady=10
        )
        self.config_frame = frame

    def _create_result_frame(self) -> None:
        """Create the right-hand panel with historic-best info and an interactive graph."""
        frame = tk.LabelFrame(self, text="Results")
        frame.grid(row=0, column=1, padx=10, pady=10, sticky="nsew")
        frame.rowconfigure(0, weight=0)
        frame.rowconfigure(1, weight=1)
        frame.columnconfigure(0, weight=1)

        hbf = tk.LabelFrame(frame, text="Historic Best Flight")
        hbf.grid(row=0, column=0, sticky="ew", padx=5, pady=(5, 2))
        self.historic_text = tk.Text(
            hbf, height=5, wrap="word", state="disabled"
        )
        self.historic_text.pack(fill="both", expand=True, padx=5, pady=5)

        gf = tk.LabelFrame(frame, text="Price History")
        gf.grid(row=1, column=0, sticky="nsew", padx=5, pady=(2, 5))
        gf.rowconfigure(0, weight=1)
        gf.columnconfigure(0, weight=1)

        self.figure = Figure(figsize=(5, 4), dpi=100)
        self.ax = self.figure.add_subplot(111)
        self.ax.set_xlabel("Monitoring timestamp")
        self.ax.set_ylabel("Price (EUR)")

        self.canvas = FigureCanvasTkAgg(self.figure, master=gf)
        self.canvas.draw()
        self.canvas.get_tk_widget().grid(row=0, column=0, sticky="nsew")

        toolbar = NavigationToolbar2Tk(self.canvas, gf, pack_toolbar=False)
        toolbar.update()
        toolbar.grid(row=1, column=0, sticky="ew")

        self._point_annotation = self.ax.annotate(
            text="",
            xy=(0, 0),
            xytext=(10, 10),
            textcoords="offset points",
            bbox=dict(boxstyle="round", fc="yellow", ec="black", lw=0.5),
            arrowprops=dict(arrowstyle="->"),
            visible=False,
        )

        self.canvas.mpl_connect("pick_event", self._on_pick)
        self.result_frame = frame

    def _create_status_panel(self):
        """Bottom panel with current status label and indeterminate progress bar."""
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

        self.cancel_button = tk.Button(
            sp,
            text="Cancel Monitoring",
            command=self._on_cancel,
            state="disabled",
        )
        self.cancel_button.grid(
            row=2, column=0, padx=5, pady=(0, 8), sticky="e"
        )

        self.status_panel = sp

    def _load_airport_names(self):
        """Load IATA->airport-name map from OurAirports CSV."""
        df = pd.read_csv(AirportFromDistance.AIRPORTS_URL)
        self.code_to_name = {
            c: n for c, n in zip(df["iata_code"], df["name"]) if pd.notna(c)
        }

    def _load_saved_config(self):
        """Restore last inputs and resolved codes from config.json."""
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
        """Get trimmed string from Entry or Text widget."""
        if isinstance(w, tk.Text):
            return w.get("1.0", END).strip()
        return w.get().strip()

    def _fields_complete(self):
        """
        Return True if required fields have values.
        Required:
          - departure, destination, dep_date, max_duration_flight
          - at least one of: trip_duration (single or range) OR arrival_date (single)
        """
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
        """
        Stop monitoring if fields become incomplete.
        Do NOT auto-start if fields become complete, unless explicitly allowed.
        After a user cancel, auto-start is disabled until user clicks Start.
        """
        alive = self._monitor_thread and self._monitor_thread.is_alive()
        if not self._fields_complete() and alive:
            self._stop_event.set()
            # Hard-cancel the running bot/driver immediately
            if self._current_bot is not None:
                self._current_bot.request_cancel()
            self.progress.stop()
            self.status_label.config(text="Status: cancelling...")
            self.cancel_button.config(state="disabled")
            self._wait_for_cancel()
        # No auto-start on field change when _allow_auto_start is False.
        if self._fields_complete() and not alive and self._allow_auto_start:
            pass

    def _pre_resolve_airports(self, field):
        """Resolve freeform airport input into IATA codes and display CODE - Name."""
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
        """Convert comma-list or City/Country or Country to IATA codes."""
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
        """Start the background monitoring thread if not already running."""
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
        self.start_button.config(state="disabled")
        self.cancel_button.config(state="normal")
        self._start_monitoring()

    def _on_cancel(self):
        """Request cancellation of the current monitoring loop immediately."""
        if not self._monitor_thread or not self._monitor_thread.is_alive():
            return
        # Disable future auto-starts until user clicks Start again
        self._allow_auto_start = False
        self._stop_event.set()
        # Hard-cancel the running bot/driver immediately
        if self._current_bot is not None:
            self._current_bot.request_cancel()
        self.progress.stop()
        self.status_label.config(text="Status: cancelling...")
        self.cancel_button.config(state="disabled")
        self._wait_for_cancel()

    def _wait_for_cancel(self):
        """Poll for monitor thread completion and restore UI when done."""
        if self._monitor_thread and self._monitor_thread.is_alive():
            self.after(100, self._wait_for_cancel)
            return
        self._monitor_thread = None
        self._current_bot = None
        self.status_label.config(text="Status: idle")
        self.start_button.config(state="normal")
        self.cancel_button.config(state="disabled")

    def _start_monitoring(self):
        """
        Gather parameters, validate dates, save config, and launch the monitor loop.

        This version validates the Departure Date and Return Date with clear,
        user-visible errors instead of letting ValueError bubble up into Tkinter.
        On validation failure, it restores the UI state and returns immediately.
        """
        deps = self.resolved_airports.get("departure", [])
        dests = self.resolved_airports.get("destination", [])

        # Validate departure date
        try:
            dep_dt = self._parse_date_single(
                self._get_widget_value(self.entries["dep_date"])
            )
        except ValueError as e:
            messagebox.showerror("Invalid Departure Date", str(e))
            self.progress.stop()
            self.status_label.config(text="Status: idle")
            self.start_button.config(state="normal")
            self.cancel_button.config(state="disabled")
            return

        # Validate return date only if provided
        arr_field_val = self._get_widget_value(self.entries["arrival_date"])
        arr_dt = None
        if arr_field_val:
            try:
                arr_dt = self._parse_date_single(arr_field_val)
            except ValueError as e:
                messagebox.showerror("Invalid Return Date", str(e))
                self.progress.stop()
                self.status_label.config(text="Status: idle")
                self.start_button.config(state="normal")
                self.cancel_button.config(state="disabled")
                return

        trip_str = self._get_widget_value(self.entries["trip_duration"])
        random_mode = bool(trip_str)

        durations = None
        if random_mode:
            # Trip duration is provided: parse and validate the date window length
            try:
                durations = self._parse_durations(trip_str)
            except ValueError as e:
                messagebox.showerror("Invalid Trip Duration", str(e))
                self.progress.stop()
                self.status_label.config(text="Status: idle")
                self.start_button.config(state="normal")
                self.cancel_button.config(state="disabled")
                return

            if arr_dt is None:
                messagebox.showerror(
                    "Invalid input",
                    "Return Date is required when Trip Duration is provided.",
                )
                self.progress.stop()
                self.status_label.config(text="Status: idle")
                self.start_button.config(state="normal")
                self.cancel_button.config(state="disabled")
                return

            window_days = (arr_dt - dep_dt).days
            max_trip = max(durations)
            if window_days < max_trip:
                messagebox.showerror(
                    "Invalid window",
                    (
                        "Date window is too short for the maximum trip duration "
                        f"({window_days} days window < {max_trip} days)."
                    ),
                )
                self.progress.stop()
                self.status_label.config(text="Status: idle")
                self.start_button.config(state="normal")
                self.cancel_button.config(state="disabled")
                return

        params = {
            "max_duration_flight": float(
                self._get_widget_value(self.entries["max_duration_flight"])
            ),
            "random_mode": random_mode,
            "window_start": dep_dt,
            "window_end": arr_dt if arr_dt else dep_dt,
            "durations": durations,
        }

        # Save config (store entered strings and resolved codes)
        cfg = {k: self._get_widget_value(w) for k, w in self.entries.items()}
        cfg["departure_codes"] = deps
        cfg["destination_codes"] = dests
        cfg["max_duration_flight"] = params["max_duration_flight"]
        self.config_mgr.save(cfg)

        # Exhaustive mode keeps the single pair; random mode uses None sentinel
        if random_mode:
            pairs = None
        else:
            pairs = [
                (
                    dep_dt.strftime("%Y-%m-%d"),
                    (arr_dt or dep_dt).strftime("%Y-%m-%d"),
                )
            ]

        self._monitor_thread = threading.Thread(
            target=self._monitor_loop,
            args=(deps, dests, pairs, params),
            daemon=True,
        )
        self._monitor_thread.start()

    def _get_global_best_price(self):
        """Return the best price ever recorded, or None if no records."""
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
        """
        Check flights either exhaustively for a single (dep,ret) pair
        or randomly sample (dep date, duration, airport pair) inside the window.
        Records results hourly, notifies on new lows or large jumps, then filters airports.
        """
        from datetime import datetime

        random_mode = bool(params.get("random_mode"))
        window_start = params.get("window_start")
        window_end = params.get("window_end")
        durations = params.get("durations") or []
        samples_per_sweep = 10 if random_mode else 0

        try:
            while not self._stop_event.is_set():
                self.status_label.config(text="Status: checking flights...")
                self.progress.start()

                if random_mode:
                    for _ in range(samples_per_sweep):
                        if self._stop_event.is_set():
                            break
                        if not deps or not dests or not durations:
                            continue

                        dep = random.choice(deps)
                        dest = random.choice(dests)
                        dur_days = int(random.choice(durations))

                        latest_dep = window_end - timedelta(days=dur_days)
                        if latest_dep < window_start:
                            continue

                        delta_days = (latest_dep - window_start).days
                        dep_dt = window_start + timedelta(
                            days=random.randint(0, delta_days)
                        )
                        ret_dt = dep_dt + timedelta(days=dur_days)

                        dd = dep_dt.strftime("%Y-%m-%d")
                        rd = ret_dt.strftime("%Y-%m-%d")

                        self.status_label.config(
                            text=f"Checking {dep}->{dest} on {dd} -> {rd}"
                        )
                        bot = FlightBot(
                            departure=dep,
                            destination=dest,
                            dep_date=dd,
                            arrival_date=rd,
                            max_duration_flight=params["max_duration_flight"],
                            cancel_event=self._stop_event,
                        )
                        self._current_bot = bot  # expose for hard cancel
                        rec = bot.start()
                        self._current_bot = None
                        if self._stop_event.is_set():
                            break
                        if not rec:
                            continue

                        price = rec["price"]
                        timestamp = datetime.now().strftime("%Y-%m-%d-%H")
                        self.record_mgr.save_record(
                            timestamp,
                            dep,
                            dest,
                            rec["company"],
                            rec["duration_out"],
                            rec["duration_return"],
                            price,
                        )

                        global_prev = self._get_global_best_price()
                        if global_prev is None or price < global_prev:
                            self.notifier.show_toast(
                                "New All-Time Low!",
                                f"{dep}->{dest} on {dd}: EUR {price:.2f}",
                                duration=10,
                                threaded=True,
                            )

                        three_days_ago = (
                            datetime.now() - timedelta(days=3)
                        ).strftime("%Y-%m-%d-%H")
                        old_rec = self.record_mgr.load_record(three_days_ago)
                        if old_rec and price > old_rec["price"] * 1.1:
                            diff = price - old_rec["price"]
                            pct = diff / old_rec["price"] * 100
                            self.notifier.show_toast(
                                "Price Jump Alert",
                                f"{dep}->{dest} jumped EUR {diff:.2f} (+{pct:.0f}%) vs 3 days ago",
                                duration=10,
                                threaded=True,
                            )

                        best_for_pair = self.best_prices.get((dep, dest))
                        if best_for_pair is None or price < best_for_pair:
                            self.best_prices[(dep, dest)] = price

                        self._load_historic_best()
                        self._plot_history()

                else:
                    dep_ret_pairs = pairs or []
                    for dep, dest in itertools.product(deps, dests):
                        best_for_pair = None
                        for dd, rd in dep_ret_pairs:
                            if self._stop_event.is_set():
                                break
                            self.status_label.config(
                                text=f"Checking {dep}->{dest} on {dd} -> {rd}"
                            )
                            bot = FlightBot(
                                departure=dep,
                                destination=dest,
                                dep_date=dd,
                                arrival_date=rd,
                                max_duration_flight=params[
                                    "max_duration_flight"
                                ],
                                cancel_event=self._stop_event,
                            )
                            self._current_bot = bot  # expose for hard cancel
                            rec = bot.start()
                            self._current_bot = None
                            if self._stop_event.is_set():
                                break
                            if not rec:
                                continue

                            price = rec["price"]
                            if best_for_pair is None or price < best_for_pair:
                                best_for_pair = price

                            timestamp = datetime.now().strftime("%Y-%m-%d-%H")
                            self.record_mgr.save_record(
                                timestamp,
                                dep,
                                dest,
                                rec["company"],
                                rec["duration_out"],
                                rec["duration_return"],
                                price,
                            )

                            global_prev = self._get_global_best_price()
                            if global_prev is None or price < global_prev:
                                self.notifier.show_toast(
                                    "New All-Time Low!",
                                    f"{dep}->{dest} on {dd}: EUR {price:.2f}",
                                    duration=10,
                                    threaded=True,
                                )

                            three_days_ago = (
                                datetime.now() - timedelta(days=3)
                            ).strftime("%Y-%m-%d-%H")
                            old_rec = self.record_mgr.load_record(
                                three_days_ago
                            )
                            if old_rec and price > old_rec["price"] * 1.1:
                                diff = price - old_rec["price"]
                                pct = diff / old_rec["price"] * 100
                                self.notifier.show_toast(
                                    "Price Jump Alert",
                                    f"{dep}->{dest} jumped EUR {diff:.2f} (+{pct:.0f}%) vs 3 days ago",
                                    duration=10,
                                    threaded=True,
                                )

                            self._load_historic_best()
                            self._plot_history()

                        if best_for_pair is not None:
                            self.best_prices[(dep, dest)] = best_for_pair
                        if self._stop_event.is_set():
                            break

                self.progress.stop()
                self.status_label.config(text="Status: waiting")
                # Wait 30 minutes with 1s polling to allow instant cancel
                for _ in range(30 * 60):
                    if self._stop_event.is_set():
                        break
                    time.sleep(1)

        finally:
            # Ensure no dangling bot handle
            self._current_bot = None

        self.progress.stop()
        self.status_label.config(text="Status: idle")
        messagebox.showinfo("FlightBot", "Monitoring loop ended.")

    def _filter_airports(self):
        """
        Remove airports whose all pair prices are >=20% above overall best,
        except those that ever appeared in a daily-best record.
        Save updated codes and display text back to config.
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

        display = {}
        for field in ("departure", "destination"):
            codes = self.resolved_airports[field]
            text = ",".join(
                f"{c} - {self.code_to_name.get(c,'')}" for c in codes
            )
            display[field] = text
            w = self.entries[field]
            if isinstance(w, tk.Text):
                w.delete("1.0", END)
                w.insert("1.0", text)
            else:
                w.delete(0, END)
                w.insert(0, text)

        cfg = self.config_mgr.load()
        cfg["departure_codes"] = self.resolved_airports["departure"]
        cfg["destination_codes"] = self.resolved_airports["destination"]
        cfg["departure"] = display["departure"]
        cfg["destination"] = display["destination"]
        self.config_mgr.save(cfg)

    # Historic-best panel & history graph
    def _load_historic_best(self) -> None:
        """Show the single cheapest record ever found (supports legacy schema too)."""
        if not os.path.exists(self.record_mgr.path):
            return

        best = None
        with open(self.record_mgr.path, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue

                ts_key = "datetime" if "datetime" in rec else "date"
                if ts_key not in rec or "price" not in rec:
                    continue

                try:
                    price_val = float(rec["price"])
                except (TypeError, ValueError):
                    continue

                if best is None or price_val < best["price"]:
                    best = {
                        "ts": rec[ts_key],
                        "departure": rec.get("departure", ""),
                        "destination": rec.get("destination", ""),
                        "company": rec.get("company", ""),
                        "price": price_val,
                        "duration_out": rec.get("duration_out", ""),
                        "duration_ret": rec.get("duration_return", ""),
                    }

        if best is None:
            return

        text = (
            f"Date/Hour: {best['ts']}\n"
            f"Route: {best['departure']} -> {best['destination']}\n"
            f"Company: {best['company']}\n"
            f"Price: EUR {best['price']:.2f}\n"
            f"Outbound: {best['duration_out']}\n"
            f"Return:   {best['duration_ret']}\n"
        )
        self.historic_text.configure(state="normal")
        self.historic_text.delete("1.0", END)
        self.historic_text.insert(END, text)
        self.historic_text.configure(state="disabled")

    def _plot_history(self) -> None:
        """Plot all stored prices versus their timestamp (pickable points)."""
        if not os.path.exists(self.record_mgr.path):
            return

        times = []
        prices = []

        with open(self.record_mgr.path, "r", encoding="utf-8") as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue

                ts_str = rec.get("datetime") or rec.get("date")
                if ts_str is None or "price" not in rec:
                    continue

                try:
                    fmt = (
                        "%Y-%m-%d-%H"
                        if len(ts_str.split("-")) == 4
                        else "%Y-%m-%d"
                    )
                    ts = datetime.strptime(ts_str, fmt)
                    price_val = float(rec["price"])
                except (ValueError, TypeError):
                    continue

                times.append(ts)
                prices.append(price_val)

        if not times:
            return

        self.ax.clear()
        self.ax.plot_date(times, prices, "-o", picker=5)
        self.ax.set_xlabel("Monitoring timestamp")
        self.ax.set_ylabel("Price (EUR)")
        self.figure.autofmt_xdate()
        self.canvas.draw()

    def _on_pick(self, event) -> None:
        """Show the (timestamp, price) of a clicked data point in a tooltip."""
        if hasattr(event, "artist") and event.ind:
            ind = event.ind[0]
            xdata, ydata = event.artist.get_data()
            x, y = xdata[ind], ydata[ind]
            ts_str = matplotlib.dates.num2date(x).strftime("%Y-%m-%d %H:%M")
            self._point_annotation.xy = (x, y)
            self._point_annotation.set_text(f"{ts_str}\nEUR {y:.2f}")
            self._point_annotation.set_visible(True)
            self.canvas.draw_idle()

    def _parse_date_single(self, s: str) -> datetime:
        """
        Parse a single date in 'YYYY-MM-DD' format into a datetime.

        Raises:
            ValueError: with a precise message if the string does not match the
            expected format or if it is not a real calendar date (e.g., day out
            of range for the given month).
        """
        s = s.strip()

        # First validate the format explicitly.
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
            raise ValueError(f"'{s}' does not match YYYY-MM-DD.")

        try:
            return datetime.strptime(s, "%Y-%m-%d")
        except ValueError as e:
            # Preserve the underlying reason (e.g., "day is out of range for month").
            raise ValueError(f"'{s}' is not a valid calendar date: {e}") from e

    def _parse_durations(self, s):
        """Parse 'N' or 'N-M' into a list of integer durations."""
        parts = s.strip().split("-")
        if len(parts) == 1:
            return [int(parts[0])]
        if len(parts) == 2:
            lo, hi = int(parts[0]), int(parts[1])
            if hi < lo:
                raise ValueError("Invalid duration range")
            return list(range(lo, hi + 1))
        raise ValueError("Invalid duration format")

    def _on_close(self):
        """Hide the window and show the tray icon instead of exiting."""
        self.withdraw()
        self.tray_icon.visible = True

    def _restore(self, icon, item):
        """Restore the window from the system tray."""
        self.deiconify()
        self.tray_icon.visible = False

    def _quit_app(self, icon, item):
        """Stop the tray icon and exit the application."""
        icon.stop()
        self.destroy()

    def _create_tray_icon(self):
        """Create a tray icon using the project assets or a fallback."""
        icon_path = self._asset_path("flight_tracker.ico")
        if os.path.exists(icon_path):
            img = Image.open(icon_path)
        else:
            img = Image.new("RGB", (16, 16), "white")
            d = ImageDraw.Draw(img)
            d.rectangle((2, 2, 13, 13), fill="black")

        menu = pystray.Menu(
            pystray.MenuItem("Restore", self._restore),
            pystray.MenuItem("Quit", self._quit_app),
        )
        icon = pystray.Icon("FlightBot", img, "FlightBot", menu)
        threading.Thread(target=icon.run, daemon=True).start()
        self.tray_icon = icon

    def _asset_path(self, *parts: str) -> str:
        """Return an absolute path inside the assets/ folder for dev and frozen apps."""
        import sys

        if getattr(sys, "frozen", False):
            root = os.path.dirname(sys.executable)
        else:
            root = os.path.dirname(os.path.dirname(__file__))
        return os.path.join(root, "assets", *parts)


if __name__ == "__main__":
    app = FlightBotGUI()
    app.mainloop()
