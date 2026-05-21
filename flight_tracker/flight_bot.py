#!/usr/bin/env python3
"""
flight_bot.py

Automated Flight Price Checker using undetected-chromedriver.
ROBUST MODE: Uses text pattern recognition (Regex) instead of brittle CSS classes
to find prices and airlines even when Kayak changes their code.
"""

import re
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


class FlightBot:
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
        if not self._driver:
            return
        try:
            # Try multiple known cookie button selectors
            selectors = [
                "//button[contains(@class, 'RxNS') and contains(., 'refuser')]",  # Common Kayak
                "//button[contains(., 'Tout refuser')]",
                "//button[contains(., 'Refuser')]",
                "//button[contains(., 'Accept all')]",  # Fallback if only accept exists
            ]
            for xpath in selectors:
                btns = self._driver.find_elements(By.XPATH, xpath)
                for btn in btns:
                    if btn.is_displayed():
                        self._driver.execute_script(
                            "arguments[0].click();", btn
                        )
                        time.sleep(0.5)
                        return
        except Exception:
            pass

    def _handle_captcha(self):
        if not self._driver:
            return
        try:
            iframes = self._driver.find_elements(By.TAG_NAME, "iframe")
            for frame in iframes:
                try:
                    src = frame.get_attribute("src") or ""
                    if "recaptcha" in src:
                        self._driver.switch_to.frame(frame)
                        try:
                            checkbox = self._driver.find_element(
                                By.CLASS_NAME, "recaptcha-checkbox-border"
                            )
                            if checkbox.is_displayed():
                                print("DEBUG: Clicking ReCAPTCHA checkbox...")
                                checkbox.click()
                                time.sleep(2)
                        except NoSuchElementException:
                            pass
                        self._driver.switch_to.default_content()
                except Exception:
                    self._driver.switch_to.default_content()
        except Exception:
            pass

    def _get_current_price(self) -> dict:
        print(
            f"DEBUG: Launching browser for {self.departure}->{self.destination}..."
        )

        try:
            options = uc.ChromeOptions()
            options.add_argument("--disable-popup-blocking")
            # Force version 142 to match your system
            self._driver = uc.Chrome(
                options=options, use_subprocess=True, version_main=142
            )
            self._driver.set_window_size(1920, 1080)
        except Exception as e:
            print(f"DEBUG: Failed to start Chrome: {e}")
            self._offline = True
            return None

        try:
            if self._is_cancelled():
                return None

            self._driver.set_page_load_timeout(60)
            try:
                self._driver.get(self.url)
            except TimeoutException:
                pass

            # --- SECURITY LOOP ---
            for _ in range(25):
                if self._is_cancelled():
                    return None

                title = (
                    self._driver.title.lower() if self._driver.title else ""
                )

                # Check if we see flight results
                try:
                    # Look for ANY text containing price symbols to guess page loaded
                    body_text = self._driver.find_element(
                        By.TAG_NAME, "body"
                    ).text
                    if "€" in body_text and (
                        "vol" in title or "flight" in title or "kayak" in title
                    ):
                        break
                except:
                    pass

                # Handle blocks
                if not title or "security" in title or "challenge" in title:
                    self._dismiss_cookies()
                    self._handle_captcha()
                else:
                    self._dismiss_cookies()
                    time.sleep(1)

            try:
                self._driver.minimize_window()
            except:
                pass

            # --- PARSE RESULTS ---
            wait = WebDriverWait(self._driver, 15)
            try:
                # Wait for the main result list wrapper
                wait.until(
                    EC.presence_of_element_located(
                        (
                            By.CSS_SELECTOR,
                            "div[class*='result-item-container'], div[class*='list-wrapper']",
                        )
                    )
                )
            except TimeoutException:
                print("DEBUG: Timeout waiting for results.")
                return None

            items = self._driver.find_elements(
                By.CSS_SELECTOR, "div[class*='result-item-container']"
            )
            print(f"DEBUG: Found {len(items)} flights. Analyzing...")

            candidates = []

            for i, item in enumerate(items):
                if self._is_cancelled():
                    return None

                # Grab all text from the card once to do regex searches (Faster & Robust)
                card_text = item.text
                card_text_lower = card_text.lower()

                try:
                    # 1. ROBUST AIRLINE FINDER
                    company = "Unknown"

                    # Method A: Look for logo images with Alt text
                    try:
                        imgs = item.find_elements(By.CSS_SELECTOR, "img")
                        for img in imgs:
                            alt = img.get_attribute("alt")
                            if (
                                alt
                                and len(alt) > 2
                                and "logo" not in alt.lower()
                            ):
                                company = alt
                                break
                    except:
                        pass

                    # Method B: Fallback to text matching common airlines if known
                    # (Optional refinement could go here)

                    # Method C: Look for specific class if Method A failed
                    if company == "Unknown":
                        try:
                            # Try the operator text class again
                            op = item.find_element(
                                By.CSS_SELECTOR, "div[class*='operator-text']"
                            )
                            company = op.text.strip()
                        except:
                            pass

                    # 2. EXCLUSIONS
                    if self._excluded_airlines:
                        if any(
                            ex in company.lower()
                            for ex in self._excluded_airlines
                        ):
                            print(
                                f"  [Flight {i+1}] {company}: REJECTED (Excluded)"
                            )
                            continue

                    # 3. DURATIONS
                    # Find all patterns like "12h 30min" or "5h"
                    # Regex: \d{1,2}h\s*\d{0,2}
                    dur_matches = re.findall(
                        r"(\d{1,2})h\s*(\d{0,2})", card_text
                    )

                    outs = "0h"
                    ret = "0h"

                    if len(dur_matches) >= 1:
                        h, m = dur_matches[0]
                        outs = f"{h}h {m}min"
                    if len(dur_matches) >= 2:
                        h, m = dur_matches[1]
                        ret = f"{h}h {m}min"

                    if (
                        self._parse_duration_hours(outs)
                        > self.max_duration_flight
                    ):
                        # print(f"  [Flight {i+1}] REJECTED (Duration {outs})")
                        continue
                    if (
                        self._parse_duration_hours(ret)
                        > self.max_duration_flight
                    ):
                        continue

                    # 4. ROBUST PRICE FINDER
                    price_eur = None

                    # Regex to find price: Look for number followed by €
                    # e.g. "1 234 €", "1234 €", "1234€"
                    # We look for the smallest valid price on the card (often multiple prices shown)
                    price_matches = re.findall(r"(\d[\d\s]*)\s*€", card_text)
                    valid_prices = []

                    for p_str in price_matches:
                        # Clean spaces (1 200 -> 1200)
                        clean_p = (
                            p_str.replace(" ", "")
                            .replace("\u00a0", "")
                            .replace("\u202f", "")
                        )
                        if clean_p.isdigit():
                            val = int(clean_p)
                            if val > 10:  # Filter out garbage like "0 €"
                                valid_prices.append(val)

                    if valid_prices:
                        price_eur = min(
                            valid_prices
                        )  # Assume cheapest option on card

                    if not price_eur:
                        print(
                            f"  [Flight {i+1}] {company}: REJECTED (No price found in text)"
                        )
                        # print(f"    -> Card text dump: {card_text[:50]}...")
                        continue

                    # 5. BUY DIRECT VERIFICATION
                    if self.buy_direct:
                        # Strict mode: The Airline Name MUST appear in the "Provider" section
                        # or be the main button text.

                        # Use a simpler heuristic: look at the bottom line or button text
                        # "Select" usually implies direct or aggregator.
                        # "View Deal" usually implies 3rd party.

                        is_direct = False

                        # Check 1: Does the card explicitly say "sold by [Airline]"?
                        # Kayak text usually: "eDreams", "GoToGate", or "[Airline]" near price.

                        # We scan the text *near* the price we found.
                        # Since we have the whole card text, we check if Airline Name is present
                        # AND check if common OTA names are NOT present if we are strict.

                        # Check for common OTAs to reject
                        otas = [
                            "edreams",
                            "gotogate",
                            "mytrip",
                            "booking.com",
                            "kiwi",
                            "trip.com",
                            "bravofly",
                            "lastminute",
                        ]
                        found_ota = any(ota in card_text_lower for ota in otas)

                        if (
                            company.lower() in card_text_lower
                            and not found_ota
                        ):
                            # Likely direct if airline is mentioned and no OTA is mentioned
                            is_direct = True

                        # Check 2: Specific "Provider" element (if it exists)
                        try:
                            provs = item.find_elements(
                                By.CSS_SELECTOR, "div[class*='provider']"
                            )
                            for p in provs:
                                if company.lower() in p.text.lower():
                                    is_direct = True
                        except:
                            pass

                        if is_direct:
                            print(
                                f"  [Flight {i+1}] {company}: ACCEPTED (Direct) - {price_eur} EUR"
                            )
                            candidates.append(
                                {
                                    "company": company,
                                    "price": price_eur,
                                    "duration_out": outs,
                                    "duration_return": ret,
                                }
                            )
                        else:
                            print(
                                f"  [Flight {i+1}] {company}: REJECTED (3rd party or unverified)"
                            )
                    else:
                        print(
                            f"  [Flight {i+1}] {company}: ACCEPTED - {price_eur} EUR"
                        )
                        candidates.append(
                            {
                                "company": company,
                                "price": price_eur,
                                "duration_out": outs,
                                "duration_return": ret,
                            }
                        )

                except Exception as e:
                    print(f"  [Flight {i+1}] Error: {e}")
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
