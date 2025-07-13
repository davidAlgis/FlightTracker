#!/usr/bin/env python3
"""
flight_bot.py

A simple flight-price checker that scrapes Kayak once,
filters by max one-way/round-trip duration, and returns
the cheapest available flight under that duration.
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
    Scrapes Kayak for a single round-trip, filters by maximum
    duration, and identifies the cheapest flight.
    """

    def __init__(
        self,
        departure: str,
        destination: str,
        dep_date: str,
        arrival_date: str,
        max_duration_flight: float,
        driver_path: str = None,
    ):
        """
        :param departure: IATA code of departure airport
        :param destination: IATA code of destination airport
        :param dep_date: outbound date (YYYY-MM-DD)
        :param arrival_date: return date (YYYY-MM-DD)
        :param max_duration_flight: maximum allowed duration in hours
        :param driver_path: optional geckodriver path
        """
        self.departure = departure
        self.destination = destination
        self.dep_date = dep_date
        self.arrival_date = arrival_date
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
        Convert '18h 55min' → hours as float.
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
        Scrape each result’s price, airline, and durations;
        filter out any exceeding max_duration_flight, print each,
        and return the cheapest as a dict with route dates.
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

        # reject cookie banner if present
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
        candidates = []

        for result in results:
            # airline
            comp = result.find("div", class_="J0g6-operator-text")
            company = comp.get_text(strip=True) if comp else ""

            # price in €
            pdiv = result.find("div", class_="e2GB-price-text")
            txt = pdiv.get_text().strip() if pdiv else ""
            digits = "".join(filter(str.isdigit, txt))
            if not digits:
                continue
            price_eur = int(digits)

            # durations
            legs = result.find_all("div", class_="xdW8 xdW8-mod-full-airport")
            outs = ""
            ret = ""
            if legs:
                dtexts = [
                    leg.find("div", class_="vmXl vmXl-mod-variant-default")
                    .get_text()
                    .strip()
                    for leg in legs
                    if leg.find("div", class_="vmXl vmXl-mod-variant-default")
                ]
                if dtexts:
                    outs = dtexts[0]
                    if len(dtexts) > 1:
                        ret = dtexts[1]

            # filter by duration
            if (
                outs
                and self._parse_duration_hours(outs) > self.max_duration_flight
            ):
                continue
            if (
                ret
                and self._parse_duration_hours(ret) > self.max_duration_flight
            ):
                continue

            print(
                f"  Flight ({company}): €{price_eur}, Out: {outs}, Ret: {ret}"
            )
            candidates.append(
                {
                    "company": company,
                    "price": price_eur,
                    "duration_out": outs,
                    "duration_return": ret,
                }
            )

        if not candidates:
            return None  # type: ignore

        best = min(candidates, key=lambda r: r["price"])
        best.update(
            {
                "dep_date": self.dep_date,
                "arrival_date": self.arrival_date,
                "departure_date": self.dep_date,
                "return_date": self.arrival_date,
            }
        )
        return best

    def start(self) -> dict:
        """
        Run one check, notify if you like, and return the best flight dict.
        """
        print(
            f"Checking best price for {self.departure}→{self.destination} on {self.dep_date}…"
        )
        rec = self._get_current_price()
        if not rec:
            print("  ❌ No valid flights under max duration.")
            return None  # type: ignore

        print(f"  💰 Best price: €{rec['price']:.2f}")
        return rec
