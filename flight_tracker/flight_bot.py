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
    A simple flight-price checker that scrapes Kayak once,
    converts the price from INR to EUR, and prints it.
    """

    # Approximate conversion rate INR→EUR
    EXCHANGE_RATE = 0.012

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
        Launch Chrome, reject cookie popup if present, scrape all detected prices,
        interpret values in EUR if marked with '€' or convert from INR otherwise,
        print the list of detected EUR prices, and return the lowest price.
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
        eur_prices = []
        for div in soup.find_all("div", class_="e2GB-price-text"):
            txt = div.get_text().strip()
            digits = "".join(filter(str.isdigit, txt))
            if not digits:
                continue
            amount = int(digits)
            if "€" in txt:
                eur_prices.append(amount)
            else:
                eur_prices.append(round(amount * self.EXCHANGE_RATE, 2))

        if not eur_prices:
            return None  # type: ignore

        # show all detected EUR prices
        print(
            "  Detected prices: " + ", ".join(f"€{p:.2f}" for p in eur_prices)
        )

        return min(eur_prices)

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
        if price <= self.price_limit * self.EXCHANGE_RATE:
            print("  ✅ Price is below limit! Showing notification…")
            self._show_notification(price)
