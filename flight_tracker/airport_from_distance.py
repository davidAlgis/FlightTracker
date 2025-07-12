# airport_from_distance.py

#!/usr/bin/env python3
"""
Module providing AirportFromDistance, a class to find all IATA airports
reachable by train (approximated) from a given city within a specified duration.
Only scheduled, international airports are returned.
"""

import sys
from math import atan2, cos, radians, sin, sqrt

import pandas as pd
from geopy.geocoders import Nominatim


class AirportFromDistance:
    """Find scheduled international airports reachable within an approximate train time."""

    AIRPORTS_URL = "https://ourairports.com/data/airports.csv"
    AVERAGE_TRAIN_SPEED_KMH = 80  # assumed average train speed

    def __init__(self):
        """Load airport data and initialize the geolocator."""
        self.airports_df = pd.read_csv(self.AIRPORTS_URL)
        self.geolocator = Nominatim(user_agent="airport_distance")

    def detect_city(self, city):
        """
        Geocode a city name to latitude and longitude.

        :param city: Name of the city to geocode.
        :return: Tuple (latitude, longitude).
        :raises ValueError: If the city cannot be geocoded.
        """
        location = self.geolocator.geocode(city)
        if not location:
            raise ValueError(f"Could not geocode city: {city}")
        return location.latitude, location.longitude

    def haversine_distance(self, lat1, lon1, lat2, lon2):
        """
        Calculate great-circle distance between two points (km).

        :return: Distance in kilometers.
        """
        R = 6371  # Earth radius in km
        dlat = radians(lat2 - lat1)
        dlon = radians(lon2 - lon1)
        a = (
            sin(dlat / 2) ** 2
            + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
        )
        c = 2 * atan2(sqrt(a), sqrt(1 - a))
        return R * c

    def get_airports(self, city, max_duration):
        """
        Get all IATA airports reachable within max_duration minutes,
        filtered to only scheduled, international airports.

        :param city: Starting city name.
        :param max_duration: Maximum train travel time in minutes.
        :return: List of tuples (iata_code, airport_name).
        """
        lat, lon = self.detect_city(city)
        max_distance = self.AVERAGE_TRAIN_SPEED_KMH * (max_duration / 60.0)

        subset = self.airports_df[
            self.airports_df["iata_code"].notna()
            & (
                self.airports_df["scheduled_service"].fillna("").str.lower()
                == "yes"
            )
            & (self.airports_df["type"] == "large_airport")
        ]

        valid = []
        for _, row in subset.iterrows():
            dist = self.haversine_distance(
                lat,
                lon,
                row["latitude_deg"],
                row["longitude_deg"],
            )
            if dist <= max_distance:
                valid.append((row["iata_code"], row["name"]))

        return valid
