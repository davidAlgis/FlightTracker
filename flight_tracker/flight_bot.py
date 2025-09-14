#!/usr/bin/env python3
"""
flight_bot.py

A simple flight-price checker that scrapes Kayak once,
filters by max one-way/round-trip duration, and returns
the cheapest available flight under that duration.

Immediate-cancel support:
- Stores a handle to the live WebDriver.
- request_cancel() sets the cancel event and quits the driver from another thread.
- Short timeouts and polling loops avoid long blocking calls.
"""

import time
from typing import Optional

from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.firefox.options import Options
from win10toast import ToastNotifier


class FlightBot:
    """
    Scrapes Kayak for a single round-trip, filters by maximum duration,
    and identifies the cheapest flight.

    Cancellation:
    - Provide a threading.Event as cancel_event (or call request_cancel()).
    - If cancelled, the driver is quit immediately and the run returns None.
    """

    def __init__(
        self,
        departure: str,
        destination: str,
        dep_date: str,
        arrival_date: str,
        max_duration_flight: float,
        driver_path: str = None,
        cancel_event=None,
    ):
        """
        :param departure: IATA code of departure airport
        :param destination: IATA code of destination airport
        :param dep_date: outbound date (YYYY-MM-DD)
        :param arrival_date: return date (YYYY-MM-DD)
        :param max_duration_flight: maximum allowed duration in hours
        :param driver_path: optional geckodriver path
        :param cancel_event: optional threading.Event for cooperative cancel
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
        self.cancel_event = cancel_event
        self._driver: Optional[webdriver.Firefox] = None

    # ------------------------------------------------------------------ #
    # Cancellation helpers
    # ------------------------------------------------------------------ #
    def _is_cancelled(self) -> bool:
        """Return True if a cancel_event is present and set."""
        return bool(self.cancel_event and self.cancel_event.is_set())

    def request_cancel(self) -> None:
        """
        External hard-cancel:
        - Set cancel flag.
        - Quit the live WebDriver immediately if present.
        """
        try:
            if self.cancel_event is not None:
                self.cancel_event.set()
        except Exception:
            pass
        try:
            if self._driver is not None:
                self._driver.quit()
                self._driver = None
        except Exception:
            # Ignore driver errors during shutdown
            self._driver = None

    # ------------------------------------------------------------------ #
    # Internal utilities
    # ------------------------------------------------------------------ #
    def _parse_duration_hours(self, text: str) -> float:
        """Convert '18h 55min' -> hours as float."""
        parts = text.split("h")
        hours = int(parts[0])
        mins = (
            int(parts[1].replace("min", "").strip())
            if "min" in parts[1]
            else 0
        )
        return hours + mins / 60.0

    def _quit_driver(self) -> None:
        """Safely quit and clear the WebDriver if it exists."""
        drv = self._driver
        self._driver = None
        if drv is None:
            return
        try:
            drv.quit()
        except Exception:
            pass

    def _poll_sleep(self, total_seconds: float, step: float = 0.25) -> bool:
        """
        Sleep up to total_seconds in small steps, returning False early if cancelled.
        :return: True if full duration elapsed, False if cancelled.
        """
        elapsed = 0.0
        while elapsed < total_seconds:
            if self._is_cancelled():
                return False
            time.sleep(step)
            elapsed += step
        return True

    def _dismiss_cookies_if_present(self) -> None:
        """
        Try to click a 'Tout refuser' button if present, without blocking long.
        Uses short retries so cancellation can interrupt quickly.
        """
        if self._driver is None:
            return
        xpath = "//button[.//div[text()='Tout refuser']]"
        for _ in range(20):  # ~5s total at 0.25s per loop
            if self._is_cancelled() or self._driver is None:
                return
            try:
                btn = self._driver.find_element(By.XPATH, xpath)
                try:
                    btn.click()
                except Exception:
                    pass
                return
            except Exception:
                time.sleep(0.25)

    # ------------------------------------------------------------------ #
    def _get_current_price(self) -> dict:
        """
        Scrape each result's price, airline, and durations; return the cheapest dict.

        Offline-safe:
        - If a network/navigation error occurs (e.g., DNS not found), mark the run
          as offline and return None without raising.
        """
        from selenium.common.exceptions import (TimeoutException,
                                                WebDriverException)

        options = Options()
        options.add_argument("--headless")

        # Reset offline flag for this call
        self._offline = False

        try:
            self._driver = (
                webdriver.Firefox(
                    executable_path=self.driver_path, options=options
                )
                if self.driver_path
                else webdriver.Firefox(options=options)
            )
        except WebDriverException:
            # Driver could not start (rare); treat as offline-ish failure
            self._driver = None
            self._offline = True
            return None  # type: ignore

        html = ""
        try:
            if self._is_cancelled():
                self._quit_driver()
                return None  # type: ignore

            try:
                if self._driver is not None:
                    self._driver.set_page_load_timeout(8)
                    self._driver.set_script_timeout(8)
                    self._driver.get(self.url)
            except TimeoutException:
                try:
                    if self._driver is not None:
                        self._driver.execute_script("window.stop();")
                except Exception:
                    pass
            except WebDriverException:
                # E.g., about:neterror dnsNotFound when offline
                self._offline = True
                self._quit_driver()
                return None  # type: ignore

            self._dismiss_cookies_if_present()
            if self._is_cancelled():
                self._quit_driver()
                return None  # type: ignore

            # Poll briefly for content (cancellable)
            for _ in range(32):
                if self._is_cancelled():
                    self._quit_driver()
                    return None  # type: ignore
                try:
                    if self._driver is not None:
                        html = self._driver.page_source or ""
                    if "Fxw9-result-item-container" in html:
                        break
                except Exception:
                    pass
                time.sleep(0.25)

        finally:
            self._quit_driver()

        if self._is_cancelled():
            return None  # type: ignore
        if not html:
            # No DOM fetched; if we did not explicitly mark offline, just no result
            return None  # type: ignore

        soup = BeautifulSoup(html, "html.parser")
        results = soup.find_all("div", class_="Fxw9-result-item-container")
        candidates = []

        for result in results:
            if self._is_cancelled():
                return None  # type: ignore

            comp = result.find("div", class_="J0g6-operator-text")
            company = comp.get_text(strip=True) if comp else ""

            pdiv = result.find("div", class_="e2GB-price-text")
            txt = pdiv.get_text().strip() if pdiv else ""
            digits = "".join(filter(str.isdigit, txt))
            if not digits:
                continue
            price_eur = int(digits)

            legs = result.find_all("div", class_="xdW8 xdW8-mod-full-airport")
            outs = ""
            ret = ""
            if legs:
                dtexts = []
                for leg in legs:
                    cell = leg.find(
                        "div", class_="vmXl vmXl-mod-variant-default"
                    )
                    if cell:
                        dtexts.append(cell.get_text().strip())
                if dtexts:
                    outs = dtexts[0]
                    if len(dtexts) > 1:
                        ret = dtexts[1]

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
        Run one check and return the best flight dict.

        Never raises on offline; sets self._offline True and returns None.
        """
        # Clear offline flag for this run
        self._offline = False

        print(
            f"Checking best price for {self.departure}->{self.destination} "
            f"on {self.dep_date}..."
        )
        if self._is_cancelled():
            return None  # type: ignore

        try:
            rec = self._get_current_price()
        except Exception:
            # Any unexpected scraper error: be conservative and treat as no result
            self._offline = False
            return None  # type: ignore

        if not rec:
            if getattr(self, "_offline", False):
                print("  Offline: will retry later.")
            else:
                print("  No valid flights under max duration or cancelled.")
            return None  # type: ignore

        print(f"  Best price: EUR {rec['price']:.2f}")
        return rec

    def was_offline(self) -> bool:
        """Return True if the last start() detected a network/offline error."""
        return bool(getattr(self, "_offline", False))
