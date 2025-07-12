#!/usr/bin/env python3
import time

from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from win10toast import ToastNotifier


class FlightBot:
    """
    A simple flight-price checker that scrapes Kayak once.
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
        driver_path: str = None,
    ):
        self.departure = departure
        self.destination = destination
        self.dep_date = dep_date
        self.arrival_date = arrival_date
        self.price_limit = price_limit
        self.checking_interval = checking_interval
        self.checking_duration = checking_duration
        self.url = (
            f"https://www.kayak.fr/flights/"
            f"{self.departure}-{self.destination}/"
            f"{self.dep_date}/{self.arrival_date}?sort=bestflight_a"
        )
        self.driver_path = driver_path
        self.notifier = ToastNotifier()

    def _get_current_price(self) -> float:
        """
        Launch Chrome, reject cookie popup if present, scrape each
        round-trip result’s price and its outbound/return durations,
        print them, and return the lowest price in EUR.
        """
        options = webdriver.ChromeOptions()
        options.add_experimental_option("excludeSwitches", ["enable-logging"])
        driver = (
            webdriver.Chrome(self.driver_path, options=options)
            if self.driver_path
            else webdriver.Chrome(options=options)
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

        all_prices = []
        for result in results:
            # price
            price_div = result.find("div", class_="e2GB-price-text")
            txt = price_div.get_text().strip() if price_div else ""
            digits = "".join(filter(str.isdigit, txt))
            if not digits:
                continue
            price_eur = int(digits)

            # durations: outbound then return
            leg_divs = result.find_all(
                "div", class_="xdW8 xdW8-mod-full-airport"
            )
            durations = []
            for leg in leg_divs:
                dur_div = leg.find(
                    "div", class_="vmXl vmXl-mod-variant-default"
                )
                if dur_div:
                    durations.append(dur_div.get_text().strip())
            outward = durations[0] if len(durations) > 0 else "N/A"
            ret = durations[1] if len(durations) > 1 else "N/A"

            print(
                f"  Flight: €{price_eur}, "
                f"Outbound: {outward}, Return: {ret}"
            )
            all_prices.append(price_eur)

        return min(all_prices) if all_prices else None  # type: ignore

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
        Check flight price once, print it in EUR, and notify if below limit.
        """
        print(
            f"Checking best price for "
            f"{self.departure}→{self.destination} on {self.dep_date}…"
        )
        price = self._get_current_price()
        if price is None:
            print("  ❌ Failed to find any prices.")
            return

        print(f"  💰 Best price: €{price:.2f}")
        if price <= self.price_limit:
            print("  ✅ Price is below limit! Showing notification…")
            self._show_notification(price)
