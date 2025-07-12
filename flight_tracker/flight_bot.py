import time

from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from win10toast import ToastNotifier


class FlightBot:
    """
    A simple flight-price monitor that checks Kayak once
    and prints the best price.
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
        """
        Launch Chrome, reject cookies if prompted, scrape the best price on the page,
        and return it as int (or None).
        """
        options = webdriver.ChromeOptions()
        options.add_experimental_option("excludeSwitches", ["enable-logging"])
        driver = (
            webdriver.Chrome(self.driver_path, options=options)
            if self.driver_path
            else webdriver.Chrome(options=options)
        )
        driver.get(self.url)
        try:
            button = WebDriverWait(driver, 10).until(
                EC.element_to_be_clickable(
                    (By.XPATH, "//button[.//div[text()='Reject all']]")
                )
            )
            button.click()
        except Exception:
            pass

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
        """
        Check the current best flight price once and print the result.
        """
        print(
            f"Checking best price for "
            f"{self.departure}→{self.destination} on {self.dep_date}…"
        )
        try:
            price = self._get_current_price()
        except Exception as e:
            print(f"  ❌ Error fetching price: {e}")
            return

        if price is None:
            print("  ❌ Failed to find any prices.")
        else:
            print(f"  💰 Best price: ₹{price}")
