import threading
import tkinter as tk
from tkinter import messagebox

from .flight_bot import FlightBot


class FlightBotGUI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Flight Price Monitor")
        self.resizable(False, False)

        # Fields: (Label text, attribute name, is_password)
        fields = [
            ("Departure (e.g. DEL)", "departure", False),
            ("Destination (e.g. BOM)", "destination", False),
            ("Departure Date (YYYY-MM-DD)", "dep_date", False),
            ("Return Date (YYYY-MM-DD)", "arrival_date", False),
            ("Price Limit (₹)", "price_limit", False),
            ("Sender Email", "sender_email", False),
            ("Sender Password", "sender_password", True),
            ("Receiver Email", "receiver_email", False),
            ("Checking Interval (s)", "checking_interval", False),
            ("Total Duration (s)", "checking_duration", False),
        ]

        self.entries = {}
        for idx, (label_text, var_name, is_pass) in enumerate(fields):
            lbl = tk.Label(self, text=label_text)
            lbl.grid(row=idx, column=0, padx=8, pady=4, sticky="e")
            ent = tk.Entry(self, width=30, show="*" if is_pass else "")
            ent.grid(row=idx, column=1, padx=8, pady=4)
            self.entries[var_name] = ent

        start_btn = tk.Button(
            self, text="Start Monitoring", command=self.start_monitor
        )
        start_btn.grid(row=len(fields), column=0, columnspan=2, pady=10)

    def start_monitor(self):
        """Collects user inputs, validates them, and starts the FlightBot."""
        try:
            params = {}
            for name, entry in self.entries.items():
                val = entry.get().strip()
                if name in (
                    "price_limit",
                    "checking_interval",
                    "checking_duration",
                ):
                    params[name] = int(val)
                else:
                    params[name] = val
        except ValueError:
            messagebox.showerror(
                "Invalid input",
                "Please enter numeric values for price limit, interval, and duration.",
            )
            return

        bot = FlightBot(
            departure=params["departure"],
            destination=params["destination"],
            dep_date=params["dep_date"],
            arrival_date=params["arrival_date"],
            price_limit=params["price_limit"],
            checking_interval=params["checking_interval"],
            checking_duration=params["checking_duration"],
        )

        thread = threading.Thread(target=bot.start, daemon=True)
        thread.start()
        messagebox.showinfo(
            "FlightBot", "Monitoring started in the background."
        )
        self.quit()


if __name__ == "__main__":
    app = FlightBotGUI()
    app.mainloop()
