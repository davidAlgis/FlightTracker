#!/usr/bin/env python3
import time

from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.firefox.options import Options
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from win10toast import ToastNotifier


class FlightBot:
    """
    A simple flight-price checker that scrapes Kayak once,
    filters by max one-way/round-trip duration, and prints/notifies.
    """

    def __init__(
        self,
        departure: str,
        destination: str,
        dep_date: str,
        arrival_date: str,
        price_limit: int,
        checking_interval: int,
        checking_duration: int,
        max_duration_flight: float,
        driver_path: str = None,
    ):
        self.departure = departure
        self.destination = destination
        self.dep_date = dep_date
        self.arrival_date = arrival_date
        self.price_limit = price_limit
        self.checking_interval = checking_interval
        self.checking_duration = checking_duration
        self.max_duration_flight = max_duration_flight
        self.url = (
            f"https://www.kayak.fr/flights/"
            f"{departure}-{destination}/"
            f"{dep_date}/{arrival_date}?sort=bestflight_a"
        )
        self.driver_path = driver_path
        self.notifier = ToastNotifier()

    def _parse_duration_hours(self, text: str) -> float:
        """
        Convert a string like '18h 55min' into hours as float.
        """
        parts = text.split("h")
        hours = int(parts[0])
        mins = (
            int(parts[1].replace("min", "").strip())
            if "min" in parts[1]
            else 0
        )
        return hours + mins / 60.0

    def _get_current_price(self) -> float:
        """
        Launch headless Firefox, reject cookie popup if present, scrape each
        round-trip result’s price and durations, print them, and return the lowest price.
        """
        options = Options()
        # options.headless = True
        options.add_argument("--headless")
        driver = (
            webdriver.Firefox(
                executable_path=self.driver_path, options=options
            )
            if self.driver_path
            else webdriver.Firefox(options=options)
        )
        driver.get(self.url)

        # reject cookies banner if present
        try:
            btn = WebDriverWait(driver, 10).until(
                EC.element_to_be_clickable(
                    (By.XPATH, "//button[.//div[text()='Tout refuser']]")
                )
            )
            btn.click()
        except Exception:
            pass

        time.sleep(30)
        html = driver.page_source
        driver.quit()

        soup = BeautifulSoup(html, "html.parser")
        results = soup.find_all("div", class_="Fxw9-result-item-container")

        valid_prices = []
        for result in results:
            # extract price
            price_div = result.find("div", class_="e2GB-price-text")
            txt = price_div.get_text().strip() if price_div else ""
            digits = "".join(filter(str.isdigit, txt))
            if not digits:
                continue
            price_eur = int(digits)

            # extract durations
            leg_divs = result.find_all(
                "div", class_="xdW8 xdW8-mod-full-airport"
            )
            durations = []
            for leg in leg_divs:
                dv = leg.find("div", class_="vmXl vmXl-mod-variant-default")
                if dv:
                    durations.append(dv.get_text().strip())
            outward = durations[0] if len(durations) > 0 else "N/A"
            ret = durations[1] if len(durations) > 1 else "N/A"

            # filter by max_duration_flight
            ok = True
            if (
                outward
                and self._parse_duration_hours(outward)
                > self.max_duration_flight
            ):
                ok = False
            if (
                ret
                and self._parse_duration_hours(ret) > self.max_duration_flight
            ):
                ok = False
            if not ok:
                continue

            print(
                f"  Flight: €{price_eur}, Outbound: {outward}, Return: {ret}"
            )
            valid_prices.append(price_eur)

        return min(valid_prices) if valid_prices else None  # type: ignore

    def _show_notification(self, price: float):
        """
        Show a Windows 11 toast notification with the EUR price.
        """
        title = "✈️ Flight Price Alert"
        message = (
            f"Price is €{price:.2f} for "
            f"{self.departure}→{self.destination} on {self.dep_date}"
        )
        self.notifier.show_toast(title, message, duration=10, threaded=True)

    def start(self):
        """
        Check flight price once, print filtered results, and notify if below limit.
        """
        print(
            f"Checking best price for {self.departure}→{self.destination} on {self.dep_date}…"
        )
        price = self._get_current_price()
        if price is None:
            print("  ❌ No valid flights under max duration.")
            return

        print(f"  💰 Best price: €{price:.2f}")
        if price <= self.price_limit:
            print("  ✅ Price is below limit! Showing notification…")
            self._show_notification(price)
