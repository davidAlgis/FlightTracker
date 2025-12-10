#!/usr/bin/env python3
"""
flight_bot.py

Automated Flight Price Checker using undetected-chromedriver.
Includes specific fixes for:
1. Kayak's Cookie Banner (RxNS class)
2. Google ReCAPTCHA (switching frames to click 'recaptcha-checkbox-border')
"""

import threading
import time
from typing import Optional

import undetected_chromedriver as uc
from selenium.common.exceptions import (ElementClickInterceptedException,
                                        NoSuchElementException,
                                        TimeoutException)
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from win10toast import ToastNotifier


class FlightBot:
    """
    Scrapes Kayak using undetected-chromedriver.
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
        excluded_airlines: list[str] | None = None,
        buy_direct: bool = False,
    ):
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
        self.notifier = ToastNotifier()
        self.cancel_event = cancel_event
        self._driver = None
        self.buy_direct = buy_direct
        self._excluded_airlines = [
            s.strip().lower()
            for s in (excluded_airlines or [])
            if s and s.strip()
        ]

    def _is_cancelled(self) -> bool:
        return bool(self.cancel_event and self.cancel_event.is_set())

    def request_cancel(self) -> None:
        if self.cancel_event:
            self.cancel_event.set()
        self._quit_driver()

    def _quit_driver(self) -> None:
        if self._driver:
            try:
                self._driver.quit()
            except Exception:
                pass
            self._driver = None

    def _parse_duration_hours(self, text: str) -> float:
        try:
            parts = text.lower().split("h")
            hours = int(parts[0].strip())
            mins = 0
            if len(parts) > 1 and "min" in parts[1]:
                mins = int(parts[1].replace("min", "").strip())
            return hours + mins / 60.0
        except:
            return 999.9

    def _dismiss_cookies(self):
        """
        Click the 'Refuse' button using the user-identified class RxNS.
        """
        if not self._driver:
            return
        try:
            xpath_specific = "//button[contains(@class, 'RxNS') and (contains(., 'refuser') or contains(., 'Refuser'))]"
            btns = self._driver.find_elements(By.XPATH, xpath_specific)

            if not btns:
                xpath_generic = "//button[contains(., 'Tout refuser') or contains(., 'Refuser')]"
                btns = self._driver.find_elements(By.XPATH, xpath_generic)

            for btn in btns:
                if btn.is_displayed():
                    self._driver.execute_script("arguments[0].click();", btn)
                    time.sleep(1)
                    return
        except Exception:
            pass

    def _handle_captcha(self):
        """
        Detects and clicks the ReCAPTCHA 'I am not a robot' checkbox.
        Crucial: Must switch into the iframe to see the checkbox.
        """
        if not self._driver:
            return

        try:
            # 1. Find all iframes
            iframes = self._driver.find_elements(By.TAG_NAME, "iframe")
            for frame in iframes:
                try:
                    # Check if this frame looks like a captcha
                    src = frame.get_attribute("src") or ""
                    title = frame.get_attribute("title") or ""

                    if "recaptcha" in src or "recaptcha" in title.lower():
                        # 2. Switch context to the iframe
                        self._driver.switch_to.frame(frame)

                        # 3. Try to find the specific checkbox class
                        try:
                            checkbox = self._driver.find_element(
                                By.CLASS_NAME, "recaptcha-checkbox-border"
                            )
                            if checkbox.is_displayed():
                                print(
                                    "DEBUG: Found ReCAPTCHA checkbox. Clicking..."
                                )
                                checkbox.click()
                                time.sleep(2)  # Wait for verification
                        except NoSuchElementException:
                            pass

                        # 4. Switch back to main page
                        self._driver.switch_to.default_content()
                except Exception:
                    # Reset context if anything goes wrong in this frame
                    self._driver.switch_to.default_content()
        except Exception:
            pass

    def _get_current_price(self) -> dict:
        """
        Main logic using Undetected Chromedriver.
        """
        print(
            f"DEBUG: Starting stealth browser for {self.departure}->{self.destination}..."
        )

        try:
            options = uc.ChromeOptions()
            options.add_argument("--disable-popup-blocking")

            # Use version 142 to match your installed Chrome
            self._driver = uc.Chrome(
                options=options, use_subprocess=True, version_main=142
            )
            # Do NOT minimize immediately; let the page load visibly to pass bot checks
        except Exception as e:
            print(f"DEBUG: Failed to start Chrome: {e}")
            self._offline = True
            return None

        try:
            if self._is_cancelled():
                return None

            # 2. Navigate
            self._driver.set_page_load_timeout(60)
            try:
                self._driver.get(self.url)
            except TimeoutException:
                pass

            # 3. Security & Cookie Loop
            # We assume challenges might appear. We loop briefly to handle them.
            max_security_wait = 20
            for _ in range(max_security_wait):
                if self._is_cancelled():
                    return None

                # Check normal title
                title = self._driver.title.lower()
                if (
                    "kayak" in title
                    and "security" not in title
                    and "challenge" not in title
                ):
                    # If page looks loaded (title is correct), give it one last check for captcha overlays
                    # sometimes the captcha is an overlay on the main page
                    self._dismiss_cookies()
                    self._handle_captcha()
                    break

                # If blocked or loading, handle interruptions
                self._dismiss_cookies()
                self._handle_captcha()
                time.sleep(1)

            # Minimize only after we think we are past the checks
            try:
                self._driver.minimize_window()
            except:
                pass

            # 4. Wait for Results
            wait = WebDriverWait(self._driver, 15)
            try:
                wait.until(
                    EC.presence_of_element_located(
                        (
                            By.CSS_SELECTOR,
                            "div[class*='result-item-container']",
                        )
                    )
                )
            except TimeoutException:
                print(
                    "DEBUG: Timeout waiting for results. Possible anti-bot or no flights."
                )
                return None

            if self._is_cancelled():
                return None

            # 5. Parse Results
            items = self._driver.find_elements(
                By.CSS_SELECTOR, "div[class*='result-item-container']"
            )

            candidates = []

            for i, item in enumerate(items):
                if self._is_cancelled():
                    return None
                try:
                    # -- Airline --
                    try:
                        airline_el = item.find_element(
                            By.CSS_SELECTOR, "div[class*='operator-text']"
                        )
                        company = airline_el.text.strip()
                    except:
                        company = "Unknown"

                    # -- Exclusions --
                    if self._excluded_airlines:
                        if any(
                            ex in company.lower()
                            for ex in self._excluded_airlines
                        ):
                            continue

                    # -- Durations --
                    txt_lines = item.text.split("\n")
                    durs = [
                        t
                        for t in txt_lines
                        if "h " in t and ("min" in t or len(t) < 10)
                    ]
                    outs = durs[0] if len(durs) > 0 else "0h"
                    ret = durs[1] if len(durs) > 1 else "0h"

                    if (
                        self._parse_duration_hours(outs)
                        > self.max_duration_flight
                    ):
                        continue
                    if (
                        self._parse_duration_hours(ret)
                        > self.max_duration_flight
                    ):
                        continue

                    # -- Price Logic --
                    price_eur = None

                    if self.buy_direct:
                        # "Buy only from company" logic
                        # 1. Find the main button
                        btn = item.find_element(
                            By.CSS_SELECTOR,
                            "div[role='button'][class*='best'], div[class*='button-wrapper']",
                        )

                        # Case A: Main button IS the airline
                        if company.lower() in btn.text.lower():
                            raw_p = "".join(filter(str.isdigit, btn.text))
                            if raw_p:
                                price_eur = int(raw_p)

                        # Case B: Open dropdown
                        if not price_eur:
                            # Scroll & Click
                            self._driver.execute_script(
                                "arguments[0].scrollIntoView({block: 'center'});",
                                btn,
                            )
                            time.sleep(0.1)
                            # Try standard click first
                            try:
                                btn.click()
                            except ElementClickInterceptedException:
                                self._driver.execute_script(
                                    "arguments[0].click();", btn
                                )

                            time.sleep(1.5)  # Wait for anim

                            # Find the opened bucket
                            dropdowns = self._driver.find_elements(
                                By.CSS_SELECTOR,
                                "div[class*='rates-table-bucket'], div[class*='multibook-dropdown']",
                            )

                            visible_drop = None
                            for d in dropdowns:
                                if d.is_displayed():
                                    visible_drop = d
                                    break

                            if visible_drop:
                                lines = visible_drop.text.split("\n")
                                for idx, line in enumerate(lines):
                                    if (
                                        company.lower() in line.lower()
                                        or line.lower() in company.lower()
                                    ):
                                        # Look nearby for price
                                        nearby = lines[idx : idx + 3]
                                        for n in nearby:
                                            digs = "".join(
                                                filter(str.isdigit, n)
                                            )
                                            if (
                                                digs
                                                and len(digs) >= 2
                                                and ":" not in n
                                            ):
                                                price_eur = int(digs)
                                                break
                                    if price_eur:
                                        break
                    else:
                        # Standard Cheapest logic
                        try:
                            p_el = item.find_element(
                                By.CSS_SELECTOR, "div[class*='price-text']"
                            )
                            raw_p = "".join(filter(str.isdigit, p_el.text))
                            if raw_p:
                                price_eur = int(raw_p)
                        except:
                            pass

                    if price_eur:
                        candidates.append(
                            {
                                "company": company,
                                "price": price_eur,
                                "duration_out": outs,
                                "duration_return": ret,
                            }
                        )

                except Exception:
                    continue

            if not candidates:
                return None

            best = min(candidates, key=lambda x: x["price"])
            best.update(
                {
                    "dep_date": self.dep_date,
                    "arrival_date": self.arrival_date,
                    "departure_date": self.dep_date,
                    "return_date": self.arrival_date,
                }
            )

            print(
                f"DEBUG: Found best: {best['company']} - {best['price']} EUR"
            )
            return best

        except Exception as e:
            print(f"DEBUG: Critical error in scraping: {e}")
            return None
        finally:
            self._quit_driver()

    def start(self) -> dict:
        self._offline = False
        print(
            f"Checking {self.departure}->{self.destination} on {self.dep_date}..."
        )
        rec = self._get_current_price()
        if not rec:
            print("  No valid result found.")
            return None
        print(f"  Result: {rec['company']} {rec['price']} EUR")
        return rec

    def was_offline(self) -> bool:
        return getattr(self, "_offline", False)
