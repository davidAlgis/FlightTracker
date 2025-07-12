# flight_bot.py

import time

from bs4 import BeautifulSoup
from selenium import webdriver
from win10toast import ToastNotifier


class FlightBot:
    """
    A simple flight-price monitor that checks Kayak periodically
    and shows a Windows 11 toast notification when the price
    drops below a threshold.
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
        self.num_checks = max(
            1, int(self.checking_duration / self.checking_interval)
        )
        self.url = (
            f"https://www.kayak.co.in/flights/"
            f"{self.departure}-{self.destination}/"
            f"{self.dep_date}/{self.arrival_date}?sort=bestflight_a"
        )
        self.driver_path = driver_path

        # Initialize the Windows toast notifier
        self.notifier = ToastNotifier()

    def _get_current_price(self):
        """Launches Chrome, scrapes the best price on the page, returns it as int (or None)."""
        driver = (
            webdriver.Chrome(self.driver_path)
            if self.driver_path
            else webdriver.Chrome()
        )
        driver.get(self.url)
        # wait for results to load
        time.sleep(30)
        page_source = driver.page_source
        driver.quit()

        soup = BeautifulSoup(page_source, "html.parser")
        prices = []
        for span in soup.find_all(
            "span", class_="js-label js-price _itL _ibU _ibV _idj _kKW"
        ):
            text = span.get_text().strip()
            digits = "".join(filter(str.isdigit, text))
            if digits:
                prices.append(int(digits))
        return min(prices) if prices else None

    def _show_notification(self, price: int):
        """Show a Windows 11 toast notification."""
        title = "✈️ Flight Price Alert"
        message = f"Price dropped to ₹{price} for {self.departure}→{self.destination} on {self.dep_date}"
        # duration is in seconds
        self.notifier.show_toast(title, message, duration=10, threaded=True)

    def start(self):
        """Run the periodic check. Stops as soon as price ≤ limit."""
        for check_num in range(1, self.num_checks + 1):
            print(
                f"[{check_num}/{self.num_checks}] Checking price for "
                f"{self.departure}→{self.destination} on {self.dep_date}…"
            )
            price = self._get_current_price()
            if price is None:
                print("  ❌ Failed to find any prices. Retrying later…")
            else:
                print(f"  💰 Current price: ₹{price}")
                if price <= self.price_limit:
                    print("  ✅ Price is below limit! Showing notification…")
                    self._show_notification(price)
                    print("  🔔 Notification sent. Stopping monitor.")
                    return
            if check_num < self.num_checks:
                time.sleep(self.checking_interval)
        print("Finished all checks; price never dropped below threshold.")
