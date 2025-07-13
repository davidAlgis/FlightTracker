#!/usr/bin/env python3
"""
flight_bot.py

A simple flight-price checker that scrapes Kayak once,
filters by max one-way/round-trip duration, and prints/notifies.
Returns a dict including departure and return dates.
"""

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
        max_duration_flight: float,
        driver_path: str = None,
    ):
        """
        Initialize FlightBot.

        :param departure: IATA code of departure airport
        :param destination: IATA code of destination airport
        :param dep_date: outbound date (YYYY-MM-DD)
        :param arrival_date: return date (YYYY-MM-DD)
        :param price_limit: price threshold in EUR
        :param max_duration_flight: max allowed duration in hours
        :param driver_path: optional path to geckodriver
        """
        self.departure = departure
        self.destination = destination
        self.dep_date = dep_date
        self.arrival_date = arrival_date
        self.price_limit = price_limit
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

        :param text: duration text
        :return: duration in hours
        """
        parts = text.split("h")
        hours = int(parts[0])
        mins = (
            int(parts[1].replace("min", "").strip())
            if "min" in parts[1]
            else 0
        )
        return hours + mins / 60.0

    def _get_current_price(self) -> dict:
        """
        Scrape each round-trip result’s price, airline, and durations,
        filter out any exceeding self.max_duration_flight, print each
        matching flight with its company, and return the cheapest as a dict.

        :return: dict with keys company, price, duration_out,
                 duration_return, dep_date, arrival_date,
                 departure_date, return_date
        """
        options = Options()
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

        records = []
        for result in results:
            # company name
            comp_div = result.find("div", class_="J0g6-operator-text")
            company = comp_div.get_text(strip=True) if comp_div else ""

            # price in EUR
            price_div = result.find("div", class_="e2GB-price-text")
            txt = price_div.get_text().strip() if price_div else ""
            digits = "".join(filter(str.isdigit, txt))
            if not digits:
                continue
            price_eur = int(digits)

            # durations
            leg_divs = result.find_all(
                "div", class_="xdW8 xdW8-mod-full-airport"
            )
            dur_texts = []
            for leg in leg_divs:
                dv = leg.find("div", class_="vmXl vmXl-mod-variant-default")
                if dv:
                    dur_texts.append(dv.get_text().strip())
            outward = dur_texts[0] if len(dur_texts) > 0 else ""
            ret = dur_texts[1] if len(dur_texts) > 1 else ""

            # filter by max duration
            if (
                outward
                and self._parse_duration_hours(outward)
                > self.max_duration_flight
            ):
                continue
            if (
                ret
                and self._parse_duration_hours(ret) > self.max_duration_flight
            ):
                continue

            # print and collect
            print(
                f"  Flight ({company}): €{price_eur}, Out: {outward}, Ret: {ret}"
            )
            records.append(
                {
                    "company": company,
                    "price": price_eur,
                    "duration_out": outward,
                    "duration_return": ret,
                }
            )

        if not records:
            return None  # type: ignore

        # pick cheapest
        best = min(records, key=lambda r: r["price"])
        # include the route dates
        best["dep_date"] = self.dep_date
        best["arrival_date"] = self.arrival_date
        # also add more descriptive keys
        best["departure_date"] = self.dep_date
        best["return_date"] = self.arrival_date
        return best

    def _show_notification(self, price: float):
        """
        Show a Windows 11 toast notification with the EUR price.

        :param price: price in EUR
        """
        title = "✈️ Flight Price Alert"
        message = (
            f"Price is €{price:.2f} for "
            f"{self.departure}→{self.destination} on {self.dep_date}"
        )
        self.notifier.show_toast(title, message, duration=10, threaded=True)

    def start(self) -> dict:
        """
        Check flight price once, notify if below limit, and return the best-record dict.

        :return: dict with flight info + dates, or None if no flight
        """
        print(
            f"Checking best price for {self.departure}→{self.destination} on {self.dep_date}…"
        )
        rec = self._get_current_price()
        if not rec:
            print("  ❌ No valid flights under max duration.")
            return None  # type: ignore

        print(f"  💰 Best price: €{rec['price']:.2f}")
        if rec["price"] <= self.price_limit:
            print("  ✅ Price is below limit! Showing notification…")
            self._show_notification(rec["price"])
        return rec
