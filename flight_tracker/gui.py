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
        """
        Load IATA->airport-name map from OurAirports CSV.

        If the download fails (e.g., no internet), do not crash:
        - keep an empty map so the GUI stays usable,
        - update the status label softly,
        - schedule a retry in 60 seconds via Tkinter after().
        Any pending retry is canceled once loading succeeds.
        """
        import urllib.error

        retry_ms = 60_000  # 1 minute

        try:
            df = pd.read_csv(AirportFromDistance.AIRPORTS_URL)
        except Exception:
            # Ensure the map exists, even if empty
            if not hasattr(self, "code_to_name") or self.code_to_name is None:
                self.code_to_name = {}

            # Soft status hint; ignore if status_label not ready yet
            try:
                self.status_label.config(
                    text="Status: offline, retrying in 1 min"
                )
            except Exception:
                pass

            # Schedule a single retry if none is pending
            def _retry():
                self._airport_retry_id = None
                self._load_airport_names()

            if (
                not hasattr(self, "_airport_retry_id")
                or self._airport_retry_id is None
            ):
                try:
                    self._airport_retry_id = self.after(retry_ms, _retry)
                except Exception:
                    # If after() is not available yet, try again on next call path
                    self._airport_retry_id = None
            return

        # Success path: build the code->name map
        self.code_to_name = {
            c: n for c, n in zip(df["iata_code"], df["name"]) if pd.notna(c)
        }

        # Cancel any pending retry now that data is loaded
        if (
            hasattr(self, "_airport_retry_id")
            and self._airport_retry_id is not None
        ):
            try:
                self.after_cancel(self._airport_retry_id)
            except Exception:
                pass
            self._airport_retry_id = None

        # Optional: refresh status
        try:
            self.status_label.config(text="Status: data loaded")
        except Exception:
            pass

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
        Continuous monitoring with quiet offline handling.

        Random mode uses Discounted Thompson Sampling + Additive Surrogate to
        propose candidates in small daily batches. Archive forgets exponentially
        across days and is retroactively bootstrapped from flight_records.jsonl.
        """
        from datetime import datetime

        _ = self._get_weights()  # keep existing lazy-load (no-op)

        random_mode = bool(params.get("random_mode"))
        window_start = params.get("window_start")
        window_end = params.get("window_end")
        durations = params.get("durations") or []
        samples_per_sweep = 10 if random_mode else 0

        OFFLINE_WAIT_SEC = 60

        # TS/surrogate archive (persistent)
        arch = self._archive_load()

        # NEW: retroactive, incremental bootstrap from existing JSONL
        self._archive_bootstrap_from_records(arch)

        today_str = datetime.now().strftime("%Y-%m-%d")
        self._archive_decay(arch, today_str)
        self._archive_save(arch)

        try:
            while not self._stop_event.is_set():
                self.status_label.config(text="Status: checking flights...")
                self.progress.start()

                if random_mode:
                    deps_pool = list(deps)
                    dests_pool = list(dests)

                    # Build date pool in the current window based on durations
                    dates_pool = []
                    if window_start and window_end and durations:
                        latest_dep_all = [
                            (window_end - timedelta(days=int(d))).date()
                            for d in durations
                        ]
                        latest_dep = min(latest_dep_all) if latest_dep_all else window_end.date()
                        num_days = max(
                            0, (latest_dep - window_start.date()).days + 1
                        )
                        for i in range(num_days):
                            dates_pool.append(
                                (window_start + timedelta(days=i)).strftime("%Y-%m-%d")
                            )

                    proposals = self._propose_batch_ts_additive(
                        arch=arch,
                        deps_pool=deps_pool,
                        dests_pool=dests_pool,
                        dates_pool=dates_pool,
                        durations=list(map(int, durations)) if durations else [],
                        q=max(1, samples_per_sweep),
                        random_floor_frac=0.10,
                        beam_k=20,
                    )

                    for dep, dest, dd, rd in proposals:
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
                            max_duration_flight=params["max_duration_flight"],
                            cancel_event=self._stop_event,
                        )
                        self._current_bot = bot

                        prev_best = self._get_global_best_price()
                        rec = bot.start()
                        is_offline = bool(getattr(bot, "_offline", False))
                        self._current_bot = None
                        if self._stop_event.is_set():
                            break

                        if is_offline:
                            try:
                                self.status_label.config(
                                    text="Status: offline, retrying in 60s"
                                )
                            except Exception:
                                pass
                            for _s in range(OFFLINE_WAIT_SEC):
                                if self._stop_event.is_set():
                                    break
                                time.sleep(1)
                            break

                        key = self._archive_key(dep, dest, dd, rd)

                        if not rec:
                            gb = self._get_global_best_price()
                            y = (gb * 1.10) if (gb is not None and gb > 0) else 1.0
                            self._archive_add_observation(arch, key, float(y), today_str)
                            self._archive_save(arch)
                            continue

                        price = float(rec["price"])
                        timestamp = datetime.now().strftime("%Y-%m-%d-%H")
                        self.record_mgr.save_record(
                            timestamp,
                            dep,
                            dest,
                            rec["company"],
                            rec["duration_out"],
                            rec["duration_return"],
                            price,
                            rec.get("dep_date"),
                            rec.get("arrival_date"),
                        )

                        self._archive_add_observation(arch, key, price, today_str)
                        self._archive_save(arch)

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
                        if old_rec and price > float(old_rec["price"]) * 1.1:
                            diff = price - float(old_rec["price"])
                            pct = diff / float(old_rec["price"]) * 100.0
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
                            self._current_bot = bot
                            prev_best = self._get_global_best_price()
                            rec = bot.start()
                            is_offline = bool(getattr(bot, "_offline", False))
                            self._current_bot = None
                            if self._stop_event.is_set():
                                break

                            if is_offline:
                                try:
                                    self.status_label.config(
                                        text="Status: offline, retrying in 60s"
                                    )
                                except Exception:
                                    pass
                                for _s in range(OFFLINE_WAIT_SEC):
                                    if self._stop_event.is_set():
                                        break
                                    time.sleep(1)
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
                                rec.get("dep_date"),
                                rec.get("arrival_date"),
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
                self.status_label.config(text="Status: continuing...")

        finally:
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
        """
        Read flight_records.jsonl and show the single cheapest record ever found.
        Works with both legacy daily records (key date) and the new hourly records
        (key datetime). Stores a click-through link when dates are available.
        Now also displays the trip dates (dep_date -> arrival_date) when present.
        """
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
                        "dep_date": rec.get("dep_date"),
                        "arrival_date": rec.get("arrival_date"),
                    }

        if best is None:
            return

        dd = best.get("dep_date")
        rd = best.get("arrival_date")
        trip_line = f"Trip: {dd} -> {rd}\n" if dd and rd else ""

        text = (
            f"Date/Hour: {best['ts']}\n"
            f"Route: {best['departure']} -> {best['destination']}\n"
            f"{trip_line}"
            f"Company: {best['company']}\n"
            f"Price: EUR {best['price']:.2f}\n"
            f"Outbound: {best['duration_out']}\n"
            f"Return:   {best['duration_ret']}\n"
            f"(Click to open search)"
        )
        self.historic_text.configure(state="normal")
        self.historic_text.delete("1.0", END)
        self.historic_text.insert(END, text)
        self.historic_text.configure(state="disabled")

        # Store link payload and bind click
        if best.get("departure") and best.get("destination") and dd and rd:
            self._historic_best_link = (
                best["departure"],
                best["destination"],
                dd,
                rd,
            )
        else:
            self._historic_best_link = None

        if not hasattr(self, "_historic_click_bound"):
            self.historic_text.bind("<Button-1>", self._on_historic_click)
            self._historic_click_bound = True

    def _open_kayak_search(
        self, dep: str, dest: str, dep_date: str, arrival_date: str
    ) -> None:
        """
        Open the Kayak search URL for the given parameters in the default browser.
        """
        import webbrowser

        url = (
            f"https://www.kayak.fr/flights/"
            f"{dep}-{dest}/"
            f"{dep_date}/{arrival_date}?sort=bestflight_a"
        )
        try:
            webbrowser.open(url, new=2)
        except Exception:
            messagebox.showerror(
                "Open URL failed", "Could not open the browser for Kayak."
            )

    def _on_historic_click(self, event) -> None:
        """
        Click handler for the Historic Best Flight text panel.
        Opens the stored Kayak URL when date info is available.
        """
        link = getattr(self, "_historic_best_link", None)
        if not link:
            messagebox.showinfo(
                "No dates available",
                "This best record does not contain departure/return dates.",
            )
            return
        dep, dest, dd, rd = link
        self._open_kayak_search(dep, dest, dd, rd)

    def _plot_history(self) -> None:
        """
        Plot ONE dot per calendar day: the best (lowest) price recorded that day.

        We first aggregate all hourly records into a daily_best map, then plot the
        per-day minima only. Hover/click still show the full details of that day's
        best record, including dep/arr dates when available.
        """
        import matplotlib.dates as mdates  # local import

        if not os.path.exists(self.record_mgr.path):
            return

        from datetime import datetime as _dt

        # 1) Aggregate: keep only the best (lowest) price per day, with details.
        daily_best: dict[str, dict] = {}
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
                    ts = _dt.strptime(ts_str, fmt)
                    price_val = float(rec["price"])
                except (ValueError, TypeError):
                    continue

                day_key = ts.strftime("%Y-%m-%d")
                prev = daily_best.get(day_key)
                if prev is None or price_val < prev.get("price", float("inf")):
                    daily_best[day_key] = {
                        "date": day_key,
                        "price": price_val,
                        "departure": rec.get("departure", ""),
                        "destination": rec.get("destination", ""),
                        "company": rec.get("company", ""),
                        "duration_out": rec.get("duration_out", ""),
                        "duration_return": rec.get("duration_return", ""),
                        "dep_date": rec.get("dep_date"),
                        "arrival_date": rec.get("arrival_date"),
                    }

        if not daily_best:
            return

        # 2) Build per-day series (sorted by date) → one point per day.
        days_sorted = sorted(daily_best.keys())
        times = [_dt.strptime(d, "%Y-%m-%d") for d in days_sorted]
        prices = [daily_best[d]["price"] for d in days_sorted]

        # 3) Plot.
        self.ax.clear()
        line_list = self.ax.plot_date(times, prices, "-o", picker=5)
        self._line = line_list[0]

        # Recreate the annotation on the fresh axes so it can be shown and picked
        if hasattr(self, "_point_annotation"):
            try:
                self._point_annotation.remove()
            except Exception:
                pass
        self._point_annotation = self.ax.annotate(
            text="",
            xy=(0, 0),
            xytext=(10, 10),
            textcoords="offset points",
            bbox=dict(boxstyle="round", fc="white", ec="black", lw=0.5),
            arrowprops=dict(arrowstyle="->"),
            visible=False,
            zorder=10,
            picker=True,
        )
        try:
            self._point_annotation.get_bbox_patch().set_picker(True)
        except Exception:
            pass

        # 4) Save data for hover/click handlers.
        self._plot_times = times
        self._plot_prices = prices
        self._plot_days = days_sorted  # <— one entry per plotted point
        self._daily_best = daily_best  # details keyed by YYYY-MM-DD
        self._annotation_link = None  # (dep, dest, dep_date, arrival_date)

        self.ax.set_xlabel("Monitoring date")
        self.ax.set_ylabel("Price (EUR)")
        self.figure.autofmt_xdate()

        # Ensure handlers are connected once
        if not hasattr(self, "_hover_cid"):
            self._hover_cid = self.canvas.mpl_connect(
                "motion_notify_event", self._on_motion
            )
        if not hasattr(self, "_pick_cid"):
            self._pick_cid = self.canvas.mpl_connect(
                "pick_event", self._on_pick
            )

        # NEW: bind F12 once to open the TS archive popup
        if not hasattr(self, "_ts_debug_bound") or not getattr(self, "_ts_debug_bound", False):
            try:
                self.bind("<F12>", self._show_ts_archive_popup)
                self._ts_debug_bound = True
            except Exception:
                # If binding fails for any reason, do not crash the UI.
                self._ts_debug_bound = False

        self.canvas.draw_idle()

    def _on_motion(self, event) -> None:
        """
        Hover handler: when the mouse is near a plotted point, show an annotation
        bubble with the BEST flight of that calendar day (min price) and details.
        Now also displays the trip dates (dep_date -> arrival_date) when available.
        """
        import matplotlib.dates as mdates
        import numpy as np

        if not hasattr(self, "_line") or event.inaxes is not self.ax:
            if self._point_annotation.get_visible():
                self._point_annotation.set_visible(False)
                self._annotation_link = None
                self.canvas.draw_idle()
            return

        xdata = self._line.get_xdata()
        ydata = self._line.get_ydata()
        if xdata is None or ydata is None or len(xdata) == 0:
            return

        def _x_to_num(x):
            try:
                return float(x)
            except Exception:
                try:
                    return mdates.date2num(x)
                except Exception:
                    try:
                        return mdates.date2num(
                            np.datetime64(x)
                            .astype("datetime64[ns]")
                            .astype(object)
                        )
                    except Exception:
                        return None

        threshold_px = 8
        min_dist2 = (threshold_px + 1) ** 2
        nearest_idx = None

        for i in range(len(xdata)):
            xn = _x_to_num(xdata[i])
            if xn is None:
                continue
            try:
                yn = float(ydata[i])
            except Exception:
                continue
            px_py = self.ax.transData.transform(
                np.array([xn, yn], dtype=float)
            )
            px, py = (
                (px_py[0], px_py[1])
                if px_py.ndim == 1
                else (px_py[0, 0], px_py[0, 1])
            )

            dx = px - event.x
            dy = py - event.y
            d2 = dx * dx + dy * dy
            if d2 < min_dist2:
                min_dist2 = d2
                nearest_idx = i

        if nearest_idx is None:
            if self._point_annotation.get_visible():
                self._point_annotation.set_visible(False)
                self._annotation_link = None
                self.canvas.draw_idle()
            return

        day_key = (
            self._plot_days[nearest_idx]
            if hasattr(self, "_plot_days")
            else None
        )
        best = (
            self._daily_best.get(day_key, {})
            if hasattr(self, "_daily_best")
            else {}
        )

        if best:
            dd = best.get("dep_date")
            rd = best.get("arrival_date")
            trip_line = f"Trip: {dd} -> {rd}\n" if dd and rd else ""
            text = (
                f"Date: {best.get('date','')}\n"
                f"{trip_line}"
                f"Best of day: EUR {best.get('price', 0):.2f}\n"
                f"Route: {best.get('departure','')} -> {best.get('destination','')}\n"
                f"Company: {best.get('company','')}\n"
                f"Outbound: {best.get('duration_out','')}\n"
                f"Return:   {best.get('duration_return','')}"
            )
            dep = best.get("departure")
            dest = best.get("destination")
            self._annotation_link = (
                (dep, dest, dd, rd) if (dep and dest and dd and rd) else None
            )
        else:
            ts_str = mdates.num2date(_x_to_num(xdata[nearest_idx])).strftime(
                "%Y-%m-%d %H:%M"
            )
            try:
                y_val = float(ydata[nearest_idx])
            except Exception:
                y_val = ydata[nearest_idx]
            text = f"{ts_str}\nEUR {y_val:.2f}"
            self._annotation_link = None

        self._point_annotation.xy = (xdata[nearest_idx], ydata[nearest_idx])
        self._point_annotation.set_text(text)
        self._point_annotation.set_visible(True)
        self.canvas.draw_idle()

    def _on_pick(self, event) -> None:
        """
        Pick handler.
        - Clicking on a data point shows the tooltip (existing behavior).
        - Clicking on the visible annotation bubble opens the Kayak URL for that day
          when we have dep/arr dates stored.
        """
        from datetime import datetime as _dt

        import matplotlib.dates as mdates
        import numpy as np

        # Click on annotation bubble or its bbox -> open Kayak if we have a link
        if getattr(event, "artist", None) is not None:
            if event.artist is self._point_annotation or (
                hasattr(self._point_annotation, "get_bbox_patch")
                and event.artist is self._point_annotation.get_bbox_patch()
            ):
                link = getattr(self, "_annotation_link", None)
                if link:
                    dep, dest, dd, rd = link
                    self._open_kayak_search(dep, dest, dd, rd)
                return

        # Otherwise, treat as clicking on a plotted point (line pick)
        if hasattr(event, "artist") and event.ind:
            ind = event.ind[0]
            xdata, ydata = event.artist.get_data()
            x, y = xdata[ind], ydata[ind]

            ts = None
            if isinstance(x, (float, np.floating, int, np.integer)):
                try:
                    ts = mdates.num2date(float(x))
                except Exception:
                    ts = None
            elif isinstance(x, _dt):
                ts = x
            else:
                try:
                    ts = mdates.num2date(mdates.date2num(x))
                except Exception:
                    ts = None

            if ts is not None:
                ts_str = ts.strftime("%Y-%m-%d %H:%M")
                try:
                    y_val = float(y)
                except Exception:
                    y_val = y
                text = f"{ts_str}\nEUR {y_val:.2f}"
            else:
                try:
                    y_val = float(y)
                except Exception:
                    y_val = y
                text = f"EUR {y_val:.2f}"

            self._point_annotation.xy = (x, y)
            self._point_annotation.set_text(text)
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

    def _weights_path(self) -> str:
        """
        Return the path where adaptive sampling weights are stored.
        Lives next to config.json by default as weights.json.
        """
        cfg_path = getattr(self.config_mgr, "path", "config.json")
        root = os.path.dirname(os.path.abspath(cfg_path))
        return os.path.join(root, "weights.json")

    def _load_weights(self) -> dict:
        """
        Load weights from disk. Structure:
        {
            "dep_airports": { "CDG": 0.5, "ORY": 0.5, ... },
            "dest_airports": { "JFK": 0.33, "EWR": 0.33, "LGA": 0.34, ... },
            "dates": { "2026-02-01": 0.01, ... }
        }

        Stored values are non-negative "raw" weights. When sampling over a pool,
        we normalize to probabilities. Unseen keys default to 1.0.
        """
        path = self._weights_path()
        try:
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                    # Basic shape guard
                    if not isinstance(data, dict):
                        raise ValueError
                    for k in ("dep_airports", "dest_airports", "dates"):
                        if k not in data or not isinstance(data[k], dict):
                            data[k] = {}
                    return data
        except Exception:
            pass
        return {"dep_airports": {}, "dest_airports": {}, "dates": {}}

    def _save_weights(self) -> None:
        """Persist current weights to disk."""
        try:
            path = self._weights_path()
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(self._weights, fh, indent=2)
        except Exception:
            # Silent failure is acceptable; this is a heuristic aid.
            pass

    def _get_weights(self) -> dict:
        """
        Lazy-init accessor so we do not need to modify __init__.
        """
        if not hasattr(self, "_weights") or self._weights is None:
            self._weights = self._load_weights()
        return self._weights

    def _init_weights_for_category(
        self, cat: str, candidates: list[str]
    ) -> None:
        """
        Ensure raw weights exist for all 'candidates' in category 'cat'.
        Unseen keys start at 1.0 so that a fresh pool is uniform when normalized.
        """
        w = self._get_weights()[cat]
        for key in candidates:
            if key not in w:
                w[key] = 1.0

    def _normalize_probs(self, cat: str, pool: list[str]) -> list[float]:
        """
        Build a probability vector over 'pool' from raw weights in category 'cat'.
        If every weight is zero or missing, fallback to uniform.
        """
        w = self._get_weights()[cat]
        vals = [float(w.get(k, 1.0)) for k in pool]
        s = sum(vals)
        if s <= 0.0 or not all(v >= 0.0 for v in vals):
            n = max(1, len(pool))
            return [1.0 / n] * len(pool)
        return [v / s for v in vals]

    def _choose_weighted(self, cat: str, pool: list[str]) -> str:
        """
        Draw one key from 'pool' according to normalized weights in category 'cat'.
        """
        import random as _rnd

        self._init_weights_for_category(cat, pool)
        probs = self._normalize_probs(cat, pool)
        r = _rnd.random()
        acc = 0.0
        for key, p in zip(pool, probs):
            acc += p
            if r <= acc:
                return key
        return pool[-1]  # numerical fallback

    def _adjust_weight(
        self, cat: str, selected: str, delta: float, pool: list[str]
    ) -> None:
        """
        Gently adjust probabilities for one category within 'pool', then renormalize.

        Strategy (slow + stable):
          - Work on positive raw weights and update the *selected* item
            multiplicatively, others unchanged (the renormalization naturally
            shifts the rest the opposite way).
          - Scale the step by the pool size so large pools evolve very slowly.
          - After normalization, blend a tiny fraction back toward uniform to
            avoid runaway concentration and keep exploration alive.

        :param cat: weight category key ("dep_airports", "dest_airports", "dates")
        :param selected: the chosen key inside 'pool' to up/down weight
        :param delta: signed update signal in [-1.0, 1.0] (from caller)
                      positive => reward; negative => penalty
        :param pool: the list of keys available for this draw
        """
        if len(pool) <= 1 or selected not in pool:
            return

        # Ensure weights exist and are positive
        wcat = self._get_weights()[cat]
        self._init_weights_for_category(cat, pool)

        # Base step sizes (intentionally small; scaled by pool size below)
        BASE_GOOD = 0.25  # multiplicative step numerator for reward
        BASE_BAD = 0.35  # multiplicative step numerator for penalty (larger)
        # Tiny smoothing toward uniform each update
        SMOOTH_BACK = 0.01

        n = len(pool)
        eps = 1e-6

        # Current raw weights and probabilities
        raw = [float(wcat.get(k, 1.0)) for k in pool]
        s = sum(raw)
        if s <= 0.0:
            raw = [1.0] * n
            s = float(n)
        probs = [r / s for r in raw]

        i_sel = pool.index(selected)
        p_sel = probs[i_sel]

        # Compute a very small multiplicative factor for the selected key.
        # Scale by pool size so larger pools evolve slower.
        if delta > 0.0:
            step = (BASE_GOOD / n) * min(1.0, delta)
            mult = 1.0 + step
        elif delta < 0.0:
            step = (BASE_BAD / n) * min(1.0, abs(delta))
            mult = max(0.0, 1.0 - step)
        else:
            mult = 1.0

        # Apply multiplicative change to the selected item's *raw* weight
        raw[i_sel] = max(eps, raw[i_sel] * mult)

        # Renormalize to probabilities
        s2 = sum(raw)
        probs2 = [r / s2 for r in raw]

        # Smooth slightly toward uniform to prevent runaway concentration
        uniform = 1.0 / n
        probs3 = [
            (1.0 - SMOOTH_BACK) * p + SMOOTH_BACK * uniform for p in probs2
        ]

        # Final normalization (guards against numeric drift)
        s3 = sum(probs3)
        if s3 <= 0.0:
            probs3 = [uniform] * n
        else:
            probs3 = [max(eps, p / s3) for p in probs3]

        # Write back as raw weights (we store probabilities; they will be re-normalized on use)
        for key, p in zip(pool, probs3):
            wcat[key] = p

        self._save_weights()

    def _ensure_date_weights(self, date_keys: list[str]) -> None:
        """
        Ensure raw weights exist for all 'date_keys' under category 'dates'.
        """
        self._init_weights_for_category("dates", date_keys)

    def _update_adaptive_after_result(
        self,
        dep_key: str,
        dest_key: str,
        date_key: str,
        deps_pool: list[str],
        dests_pool: list[str],
        dates_pool: list[str],
        result_price: float | None,
        prev_best_price: float | None,
    ) -> None:
        """
        Update weights using the *historical percentile* of the found price.

        Reward tiers (lower price = better):
          • All-time best ever  ................. strongest reward
          • Top 1% of all recorded prices ...... very strong reward
          • Top 10% ............................. strong reward
          • Top 50% (<= median) ................. mild reward
          • > 50% ............................... tiny penalty
          • >= 90% (worst decile) ............... small penalty
          • No valid result ..................... mild penalty

        Notes
        -----
        - Updates remain *slow*: `_adjust_weight` scales by pool size and blends
          back toward uniform each step, so even strong rewards move weights
          gently.
        - If we don't have enough history yet (fewer than 5 prices), we fall
          back to the previous "near previous best" heuristic to avoid noisy jumps.
        """
        import json
        import os

        # 1) No-result path: tiny penalty, applied to the three families.
        if result_price is None:
            for cat, key, pool in (
                ("dep_airports", dep_key, deps_pool),
                ("dest_airports", dest_key, dests_pool),
                ("dates", date_key, dates_pool),
            ):
                self._adjust_weight(cat, key, -1.0, pool)
            return

        # 2) Load historical prices to compute percentiles.
        prices: list[float] = []
        try:
            if os.path.exists(self.record_mgr.path):
                with open(self.record_mgr.path, "r", encoding="utf-8") as fh:
                    for line in fh:
                        try:
                            rec = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        try:
                            prices.append(float(rec["price"]))
                        except Exception:
                            continue
        except Exception:
            prices = []

        # 3) If insufficient history, fall back to the older ratio logic (slow).
        if len(prices) < 5 or (
            prev_best_price is None or prev_best_price <= 0
        ):
            ratio = (
                float(result_price) / float(prev_best_price)
                if prev_best_price and prev_best_price > 0
                else 1.0
            )
            NEAR_THRESH = 1.02  # within +2% of prev best is "good"
            WORSE_THRESH = 1.50  # >= +50% worse is "bad"

            if ratio <= NEAR_THRESH:
                # Reward scaled by closeness to previous best
                closeness = max(
                    0.0,
                    min(
                        1.0, (NEAR_THRESH - ratio) / (NEAR_THRESH - 1.0 + 1e-9)
                    ),
                )
                signal = 0.25 + 0.75 * closeness  # [0.25, 1.0]
            elif ratio >= WORSE_THRESH:
                signal = -1.0
            else:
                return  # neutral zone

            for cat, key, pool in (
                ("dep_airports", dep_key, deps_pool),
                ("dest_airports", dest_key, dests_pool),
                ("dates", date_key, dates_pool),
            ):
                self._adjust_weight(cat, key, signal, pool)
            return

        # 4) Percentile-based tiers over the *historical* distribution.
        prices_sorted = sorted(p for p in prices if p > 0)
        N = len(prices_sorted)
        hist_min = prices_sorted[0]

        # Percentile rank of current result among historical prices (lower is better).
        # percent_rank in (0,1]; e.g., 0.10 means in the best 10% historically.
        rank_count = sum(1 for p in prices_sorted if p <= result_price)
        percent_rank = rank_count / N

        # Decile threshold for penalty
        p90 = prices_sorted[int(0.9 * (N - 1))]

        # Stronger rewards for better percentiles; still "slow" via _adjust_weight.
        if result_price < hist_min - 1e-9:
            # New all-time low
            signal = 1.0
        elif percent_rank <= 0.01:
            signal = 0.85
        elif percent_rank <= 0.10:
            signal = 0.60
        elif percent_rank <= 0.50:
            signal = 0.35
        else:
            # Above median: slight penalty, harsher if in worst decile
            signal = -0.60 if result_price >= p90 else -0.15

        for cat, key, pool in (
            ("dep_airports", dep_key, deps_pool),
            ("dest_airports", dest_key, dests_pool),
            ("dates", date_key, dates_pool),
        ):
            self._adjust_weight(cat, key, signal, pool)

    def _archive_path(self) -> str:
        """
        Return path for TS/surrogate archive JSON.
        Lives next to config.json as 'ts_archive.json'.
        """
        cfg_path = getattr(self.config_mgr, "path", "config.json")
        root = os.path.dirname(os.path.abspath(cfg_path))
        return os.path.join(root, "ts_archive.json")

    def _archive_load(self) -> dict:
        """
        Load discounted per-arm statistics for Thompson Sampling and the
        additive surrogate.

        File schema:
        {
          "gamma": 0.98,
          "stats": {
             "DEP|DEST|YYYY-MM-DD|YYYY-MM-DD": {
                 "mu": float,
                 "var": float,
                 "n": float,
                 "last_date": "YYYY-MM-DD"
             },
             ...
          }
        }
        """
        path = self._archive_path()
        try:
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                    if isinstance(data, dict) and "stats" in data:
                        if "gamma" not in data or not (0.0 < float(data["gamma"]) < 1.0):
                            data["gamma"] = 0.98
                        if not isinstance(data["stats"], dict):
                            data["stats"] = {}
                        return data
        except Exception:
            pass
        return {"gamma": 0.98, "stats": {}}


    def _archive_save(self, arch: dict) -> None:
        """
        Persist TS/surrogate archive to disk safely.
        """
        try:
            path = self._archive_path()
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(arch, fh, indent=2)
            os.replace(tmp, path)
        except Exception:
            pass


    def _archive_key(self, dep: str, dest: str, dep_date: str, ret_date: str) -> str:
        """
        Build a unique key for a (dep, dest, dep_date, ret_date) arm.
        """
        return f"{dep}|{dest}|{dep_date}|{ret_date}"

    def _archive_decay(self, arch: dict, today: str) -> None:
        """
        Apply day-wise exponential forgetting to all arms based on 'last_date'.
        If last_date < today, multiply (mu, var, n) by gamma**delta_days.
        """
        from datetime import datetime as _dt

        fmt = "%Y-%m-%d"
        try:
            t_today = _dt.strptime(today, fmt)
        except Exception:
            return

        gamma = float(arch.get("gamma", 0.98))
        stats = arch.get("stats", {})
        for k, s in list(stats.items()):
            try:
                last = s.get("last_date")
                if not last:
                    s["last_date"] = today
                    continue
                t_last = _dt.strptime(last, fmt)
                delta = (t_today - t_last).days
                if delta <= 0:
                    continue
                factor = gamma ** float(delta)
                s["mu"] = float(s.get("mu", 0.0)) * factor
                s["var"] = float(s.get("var", 1.0)) * factor
                s["n"] = float(s.get("n", 0.0)) * factor
                s["last_date"] = today
            except Exception:
                # If any entry is malformed, drop it defensively.
                stats.pop(k, None)
        arch["stats"] = stats

    def _archive_add_observation(self, arch: dict, key: str, y: float, today: str) -> None:
        """
        Discounted online update of per-arm statistics.
        Uses an EWMA for mean and variance (Bessel-free), and a discounted 'n'.

        If the arm is new, initialize with mu=y, var=1.0 (unit noise), n=1.0.
        """
        gamma = float(arch.get("gamma", 0.98))
        stats = arch.setdefault("stats", {})
        s = stats.get(key, {"mu": float(y), "var": 1.0, "n": 1.0, "last_date": today})

        # Apply same-day decay to keep consistency (no-op if last_date == today).
        self._archive_decay(arch, today)
        mu_prev = float(s.get("mu", float(y)))
        var_prev = float(s.get("var", 1.0))
        n_prev = float(s.get("n", 0.0))

        alpha = 1.0 - gamma  # EWMA step
        mu_new = gamma * mu_prev + alpha * float(y)
        # EWMA variance around evolving mean
        var_new = gamma * var_prev + alpha * (float(y) - mu_new) ** 2
        n_new = gamma * n_prev + 1.0

        s.update({"mu": mu_new, "var": max(1e-6, var_new), "n": n_new, "last_date": today})
        stats[key] = s
        arch["stats"] = stats

    def _fit_additive_surrogate(self, arch: dict) -> tuple[dict, dict, dict]:
        """
        Fit a very light additive surrogate:
            f(dep, dest, date) ~= alpha(dep) + beta(dest) + gamma(date)

        We use each arm's discounted mean as a target, weighted by its discounted n.
        Returns (alpha_map, beta_map, gamma_map) as dictionaries of scores.
        Unseen items simply do not appear and will be treated as 0 in scoring.
        """
        import numpy as np

        stats = arch.get("stats", {})
        if not stats:
            return {}, {}, {}

        deps, dests, dates = [], [], []
        y_list, w_list = [], []

        for k, s in stats.items():
            try:
                dep, dest, dep_date, _ret = k.split("|")
                deps.append(dep)
                dests.append(dest)
                dates.append(dep_date)
                y_list.append(float(s.get("mu", 0.0)))
                w_list.append(max(0.0, float(s.get("n", 0.0))))
            except Exception:
                continue

        if not y_list:
            return {}, {}, {}

        uniq_dep = sorted(set(deps))
        uniq_dest = sorted(set(dests))
        uniq_date = sorted(set(dates))

        idx_dep = {d: i for i, d in enumerate(uniq_dep)}
        idx_dest = {d: i for i, d in enumerate(uniq_dest)}
        idx_date = {d: i for i, d in enumerate(uniq_date)}

        m = len(y_list)
        p = len(uniq_dep) + len(uniq_dest) + len(uniq_date)

        # Design matrix for ridge on additive effects: [I_dep | I_dest | I_date]
        X = np.zeros((m, p), dtype=float)
        y = np.array(y_list, dtype=float)
        w = np.array(w_list, dtype=float)

        for r, (dep, dest, date) in enumerate(zip(deps, dests, dates)):
            X[r, idx_dep[dep]] = 1.0
            X[r, len(uniq_dep) + idx_dest[dest]] = 1.0
            X[r, len(uniq_dep) + len(uniq_dest) + idx_date[date]] = 1.0

        # Weighted ridge: (X^T W X + lambda I)^{-1} X^T W y
        # Use small ridge to stabilize (lambda=1e-3).
        lam = 1e-3
        W = np.diag(w) if np.all(w >= 0.0) else np.eye(m)
        XtW = X.T @ W
        A = XtW @ X + lam * np.eye(p)
        b = XtW @ y
        try:
            coef = np.linalg.solve(A, b)
        except np.linalg.LinAlgError:
            coef = np.linalg.lstsq(A, b, rcond=None)[0]

        alpha = {
            d: float(coef[idx_dep[d]]) for d in uniq_dep
        }
        beta = {
            d: float(coef[len(uniq_dep) + idx_dest[d]]) for d in uniq_dest
        }
        gamma = {
            d: float(coef[len(uniq_dep) + len(uniq_dest) + idx_date[d]]) for d in uniq_date
        }
        return alpha, beta, gamma

    def _propose_batch_ts_additive(
        self,
        arch: dict,
        deps_pool: list[str],
        dests_pool: list[str],
        dates_pool: list[str],
        durations: list[int],
        q: int,
        random_floor_frac: float = 0.10,
        beam_k: int = 20,
    ) -> list[tuple[str, str, str, str]]:
        """
        Propose up to q (dep, dest, dep_date, ret_date) candidates using:
          - Thompson Sampling on previously seen arms (exploit + explore via variance)
          - Additive surrogate + beam search to score unseen arms
          - A small random floor to never fully discard options

        Returns a deduplicated list of tuples (dep, dest, dd, rd).
        """
        import heapq
        import numpy as np
        from datetime import datetime as _dt, timedelta as _td

        def _ret_date(dd: str, dur: int) -> str:
            try:
                d0 = _dt.strptime(dd, "%Y-%m-%d")
                return (d0 + _td(days=int(dur))).strftime("%Y-%m-%d")
            except Exception:
                return dd

        # 1) Thompson on seen arms
        seen_candidates = []
        stats = arch.get("stats", {})
        for k, s in stats.items():
            try:
                dep, dest, dd, rd = k.split("|")
            except Exception:
                continue
            mu = float(s.get("mu", 0.0))
            var = max(1e-6, float(s.get("var", 1.0)))
            n = max(0.0, float(s.get("n", 0.0)))
            # Posterior sampling: Normal(mu, var/(n+1)) as a pragmatic choice
            post_var = var / (n + 1.0)
            sample = np.random.normal(loc=mu, scale=max(1e-6, np.sqrt(post_var)))
            seen_candidates.append((sample, (dep, dest, dd, rd)))

        seen_candidates.sort(key=lambda t: t[0])  # minimize sample
        q_seen = int(q * 0.6)
        picked = [cand for _s, cand in seen_candidates[:q_seen]]

        # 2) Additive surrogate for unseen
        alpha, beta, gamma = self._fit_additive_surrogate(arch)

        # Rank each factor; take top-k for beam
        def _topk(dct: dict, k: int, universe: list[str]) -> list[str]:
            if not dct:
                # If no learned scores yet, fall back to uniform beam from pool
                return universe[: min(k, len(universe))]
            items = [(dct.get(x, 0.0), x) for x in universe]
            # Lower score is better (we model prices), so sort ascending
            items.sort(key=lambda t: t[0])
            return [x for _score, x in items[: min(k, len(items))]]

        top_dep = _topk(alpha, beam_k, deps_pool)
        top_dest = _topk(beta, beam_k, dests_pool)
        top_dates = _topk(gamma, beam_k, dates_pool)

        # Beam search combinations and score with additive surrogate
        heap = []
        for d in top_dep:
            a_score = alpha.get(d, 0.0)
            for b in top_dest:
                b_score = beta.get(b, 0.0)
                # choose a small set of durations for each (d,b)
                for dd in top_dates:
                    g_score = gamma.get(dd, 0.0)
                    # choose 2 representative durations: min and median to diversify
                    if not durations:
                        dur_list = [0]
                    else:
                        durs_sorted = sorted(set(int(x) for x in durations))
                        mid = durs_sorted[len(durs_sorted) // 2]
                        dur_list = [durs_sorted[0], mid] if len(durs_sorted) > 1 else [durs_sorted[0]]
                    for dur in dur_list:
                        rd = _ret_date(dd, dur)
                        key = self._archive_key(d, b, dd, rd)
                        if key in stats:
                            # already in TS set; skip to avoid duplication
                            continue
                        score = a_score + b_score + g_score
                        heapq.heappush(heap, (score, (d, b, dd, rd)))

        q_sur = int(q * 0.3)
        surrogate_pick = []
        while heap and len(surrogate_pick) < q_sur:
            _s, cand = heapq.heappop(heap)
            surrogate_pick.append(cand)

        picked.extend(surrogate_pick)

        # 3) Random floor
        q_rand = max(1, int(q * random_floor_frac))
        rng = random.Random()
        rand_added = 0
        tries = 0
        # precompute dates x durations into return dates
        dd_all = list(dates_pool)
        while rand_added < q_rand and tries < q_rand * 20:
            tries += 1
            if not deps_pool or not dests_pool or not dd_all or not durations:
                break
            d = rng.choice(deps_pool)
            b = rng.choice(dests_pool)
            dd = rng.choice(dd_all)
            dur = rng.choice(durations)
            rd = _ret_date(dd, dur)
            cand = (d, b, dd, rd)
            if cand not in picked:
                picked.append(cand)
                rand_added += 1

        # Deduplicate and cap to q
        seen = set()
        out = []
        for dep, dest, dd, rd in picked:
            tup = (dep, dest, dd, rd)
            if tup in seen:
                continue
            seen.add(tup)
            out.append(tup)
            if len(out) >= q:
                break
        return out



    def _show_ts_archive_popup(self, event=None):
        """
        Open a simple, non-technical popup that explains what has been learned so far.

        The popup shows:
          - A short help text in plain English.
          - A small chart of "how many trip options were updated each day".
          - The current "Top departures", "Top destinations", and "Top departure dates"
            ranked by their typical recent price (lower is better).
          - The "Top trip options" (route + dates) by typical recent price.

        Notes:
          - "Typical recent price" means the system averages prices while giving more
            importance to recent checks. This helps follow changing prices.
          - "Number of recent checks" is how many times we recently looked at that
            item (also weighted to favor recent checks).
          - "Price spread" is how much the price tends to bounce around: smaller
            means more stable, larger means more variable.
        """
        import json
        import os
        from collections import defaultdict
        from datetime import datetime as _dt
        import tkinter as _tk
        from tkinter import ttk as _ttk
        from matplotlib.figure import Figure
        from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

        # Load archive from disk
        arch_path = self._archive_path()
        if not os.path.exists(arch_path):
            _tk.messagebox.showinfo(
                "Learning Overview",
                "No data yet. Let the monitor run a bit, then try again."
            )
            return

        try:
            with open(arch_path, "r", encoding="utf-8") as fh:
                arch = json.load(fh)
        except Exception:
            _tk.messagebox.showerror(
                "Learning Overview",
                "Could not read the learning file (ts_archive.json)."
            )
            return

        stats = arch.get("stats", {})
        forget_rate = arch.get("gamma", 0.98)  # closer to 1.0 = slower forgetting

        # Aggregate helpful summaries (weighted by "recent checks")
        dep_scores = defaultdict(list)
        dest_scores = defaultdict(list)
        date_scores = defaultdict(list)
        updates_per_day = defaultdict(int)

        for key, s in stats.items():
            try:
                dep, dest, dd, _rd = key.split("|")
            except ValueError:
                continue

            typical = float(s.get("mu", 0.0))      # typical recent price estimate
            checks = float(s.get("n", 0.0))        # recent checks weight
            spread = float(s.get("var", 0.0))      # price variability estimate

            dep_scores[dep].append((typical, checks))
            dest_scores[dest].append((typical, checks))
            date_scores[dd].append((typical, checks))

            last = s.get("last_date")
            if isinstance(last, str):
                updates_per_day[last] += 1

        def _reduce(bucket):
            # Compute weighted average "typical recent price" per item
            out = []
            for k, lst in bucket.items():
                wsum = sum(n for _mu, n in lst)
                avg = (sum(mu * n for mu, n in lst) / wsum) if wsum > 0 else 0.0
                out.append((avg, wsum, k))
            out.sort(key=lambda t: t[0])  # lower is better
            return out

        dep_top = _reduce(dep_scores)[:10]
        dest_top = _reduce(dest_scores)[:10]
        date_top = _reduce(date_scores)[:10]

        # Build list of top trip options (by typical recent price)
        trips = []
        for key, s in stats.items():
            typical = float(s.get("mu", 0.0))
            checks = float(s.get("n", 0.0))
            spread = float(s.get("var", 0.0))
            trips.append((typical, checks, spread, key))
        trips.sort(key=lambda t: t[0])
        trips_top = trips[:20]

        # Create popup
        win = _tk.Toplevel(self)
        win.title("Learning Overview (press F12 to open)")
        win.geometry("980x760")
        win.transient(self)
        win.grab_set()

        # Header with plain-language help
        header = _tk.LabelFrame(win, text="What you are seeing")
        header.pack(fill="x", padx=10, pady=8)

        help_txt = (
            "This page shows how the app learns which routes and dates look promising.\n"
            "- Typical recent price: an average that gives more weight to recent checks.\n"
            "- Number of recent checks: how many times we looked at it recently.\n"
            "- Price spread: how much it tends to vary (smaller = more stable).\n"
            "Nothing is ever fully discarded: the app still tries new options regularly."
        )
        _tk.Label(header, text=help_txt, justify="left").pack(anchor="w", padx=8, pady=6)

        # Archive summary line
        summary = _tk.Frame(win)
        summary.pack(fill="x", padx=10, pady=(0, 8))
        _tk.Label(
            summary,
            text=f"Saved options: {len(stats)}    Forgetting speed: {forget_rate} (closer to 1.0 = slower)"
        ).pack(anchor="w")

        # Plot updates per day
        plot_frame = _tk.LabelFrame(win, text="How many options were updated each day")
        plot_frame.pack(fill="both", padx=10, pady=(0, 10), expand=False)

        if updates_per_day:
            try:
                days_sorted = sorted(updates_per_day.keys())
                xs = [_dt.strptime(d, "%Y-%m-%d") for d in days_sorted]
                ys = [updates_per_day[d] for d in days_sorted]

                fig = Figure(figsize=(7.5, 2.6), dpi=100)
                ax = fig.add_subplot(111)
                ax.plot_date(xs, ys, "-o")
                ax.set_xlabel("Day")
                ax.set_ylabel("Options updated")
                fig.autofmt_xdate()

                canvas = FigureCanvasTkAgg(fig, master=plot_frame)
                canvas.draw()
                canvas.get_tk_widget().pack(fill="x", padx=8, pady=6)
            except Exception:
                _tk.Label(plot_frame, text="Chart could not be drawn.").pack(padx=10, pady=8)
        else:
            _tk.Label(plot_frame, text="No updates recorded yet.").pack(padx=10, pady=8)

        # Paned area for tables
        paned = _ttk.Panedwindow(win, orient="vertical")
        paned.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        # Factors table (departures, destinations, dates)
        fac_frame = _tk.LabelFrame(paned, text="Good places and dates (ranked by typical recent price)")
        paned.add(fac_frame, weight=1)

        cols = ("typical_price", "recent_checks", "code_or_date", "type")
        tree = _ttk.Treeview(fac_frame, columns=cols, show="headings", height=10)
        headings = (
            "Typical recent price (EUR)",
            "Number of recent checks",
            "Code or date",
            "Category",
        )
        for c, h in zip(cols, headings):
            tree.heading(c, text=h)
            tree.column(c, width=190 if c == "typical_price" else 160, anchor="center")
        tree.pack(fill="both", expand=True, padx=6, pady=6)

        def _fmt_eur(x):
            try:
                return f"{float(x):.2f}"
            except Exception:
                return str(x)

        for avg, n, code in dep_top:
            tree.insert("", "end", values=(_fmt_eur(avg), f"{n:.2f}", code, "Departure"))
        for avg, n, code in dest_top:
            tree.insert("", "end", values=(_fmt_eur(avg), f"{n:.2f}", code, "Destination"))
        for avg, n, code in date_top:
            tree.insert("", "end", values=(_fmt_eur(avg), f"{n:.2f}", code, "Departure date"))

        # Trips table (top specific options)
        trips_frame = _tk.LabelFrame(paned, text="Top trip options by typical recent price")
        paned.add(trips_frame, weight=2)

        a_cols = ("typical_price", "recent_checks", "price_spread", "from", "to", "dep_date", "ret_date")
        a_tree = _ttk.Treeview(trips_frame, columns=a_cols, show="headings", height=12)

        a_headings = (
            "Typical recent price (EUR)",
            "Number of recent checks",
            "Price spread",
            "From",
            "To",
            "Departure",
            "Return",
        )
        for c, h in zip(a_cols, a_headings):
            a_tree.heading(c, text=h)
            a_tree.column(c, width=170 if c in ("typical_price", "recent_checks", "price_spread") else 120, anchor="center")
        a_tree.pack(fill="both", expand=True, padx=6, pady=6)

        for typical, checks, spread, key in trips_top:
            try:
                dep, dest, dd, rd = key.split("|")
            except ValueError:
                dep = dest = dd = rd = "?"
            a_tree.insert(
                "",
                "end",
                values=(
                    _fmt_eur(typical),
                    f"{checks:.2f}",
                    f"{spread:.4f}",
                    dep,
                    dest,
                    dd,
                    rd,
                ),
            )

        # Close button
        btn = _tk.Button(win, text="Close", command=win.destroy)
        btn.pack(pady=8)


    def _archive_bootstrap_from_records(self, arch: dict) -> None:
        """
        Retroactively and incrementally feed the Thompson/surrogate archive from
        'flight_records.jsonl'. We process records in chronological order and
        update per-arm stats using the record's calendar day as the observation
        date (enables discounted forgetting over time).

        The method is incremental: it remembers the last processed timestamp
        ('last_bootstrap_ts') and only ingests newer records on subsequent calls.

        Notes
        -----
        - An arm key is DEP|DEST|dep_date|arrival_date. Records missing either
          dep_date or arrival_date are skipped.
        - Timestamp field may be 'datetime' (YYYY-MM-DD-HH) or 'date' (YYYY-MM-DD).
        - Price must parse to float and be positive.
        """
        import json
        import os
        from datetime import datetime as _dt

        path = getattr(self.record_mgr, "path", "flight_records.jsonl")
        if not os.path.exists(path):
            return

        # Load last processed timestamp (string comparable due to fixed formatting).
        last_ts = arch.get("last_bootstrap_ts", "")

        # Collect eligible rows
        rows: list[tuple[str, str, str, str, str, float]] = []
        # tuple: (ts_iso, dep, dest, dep_date, arrival_date, price)

        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue

                ts_str = rec.get("datetime") or rec.get("date")
                if not ts_str:
                    continue

                # Normalize timestamp string to YYYY-MM-DD-HH for ordering
                # If only a date is present, use hour "00".
                try:
                    if len(ts_str.split("-")) == 4:
                        # Already YYYY-MM-DD-HH
                        ts_norm = ts_str
                    else:
                        # Parse as date, reformat with HH=00
                        t = _dt.strptime(ts_str, "%Y-%m-%d")
                        ts_norm = t.strftime("%Y-%m-%d-00")
                except Exception:
                    continue

                # Incremental ingestion: only newer than last_ts
                if last_ts and not (ts_norm > last_ts):
                    continue

                dep = rec.get("departure")
                dest = rec.get("destination")
                dd = rec.get("dep_date")
                rd = rec.get("arrival_date")
                price = rec.get("price", None)

                if not (dep and dest and dd and rd):
                    continue
                try:
                    price_f = float(price)
                    if not (price_f > 0.0):
                        continue
                except Exception:
                    continue

                rows.append((ts_norm, dep, dest, dd, rd, price_f))

        if not rows:
            return

        # Sort by timestamp ascending for correct day-wise discounting behavior
        rows.sort(key=lambda r: r[0])

        # Feed archive one by one, using the record's calendar day as the update day
        for ts_norm, dep, dest, dd, rd, price_f in rows:
            # Extract the calendar day "YYYY-MM-DD" from normalized ts
            day = ts_norm[:10]
            key = self._archive_key(dep, dest, dd, rd)
            # Update the archive with the observation at 'day'
            self._archive_add_observation(arch, key, price_f, day)

        # Remember the last processed timestamp
        arch["last_bootstrap_ts"] = rows[-1][0]
    def _records_last_day(self) -> str | None:
        """
        Return the last calendar day 'YYYY-MM-DD' present in flight_records.jsonl,
        or None if unavailable. Safe utility, not required by the bootstrap.
        """
        import json
        import os
        from datetime import datetime as _dt

        path = getattr(self.record_mgr, "path", "flight_records.jsonl")
        if not os.path.exists(path):
            return None

        last_day = None
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                    ts_str = rec.get("datetime") or rec.get("date")
                    if not ts_str:
                        continue
                    # Normalize to date
                    if len(ts_str.split("-")) == 4:
                        day = ts_str[:10]
                    else:
                        _dt.strptime(ts_str, "%Y-%m-%d")  # validate
                        day = ts_str
                    last_day = day
                except Exception:
                    continue
        return last_day


if __name__ == "__main__":
    app = FlightBotGUI()
    app.mainloop()
