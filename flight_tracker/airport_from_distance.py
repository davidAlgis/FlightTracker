#!/usr/bin/env python3
"""
Approximate airport reachability by train without any API key.
Uses straight-line distance and an average train speed.
"""

import argparse
import sys
from math import atan2, cos, radians, sin, sqrt

import pandas as pd
from geopy.geocoders import Nominatim


class AirportFromDistance:
    """Find airports reachable within an approximate train travel time."""

    AIRPORTS_URL = "https://ourairports.com/data/airports.csv"
    AVERAGE_TRAIN_SPEED_KMH = 80  # assumed average train speed

    def __init__(self):
        """Load airport data and initialize the geolocator."""
        self.airports_df = pd.read_csv(self.AIRPORTS_URL)
        self.geolocator = Nominatim(user_agent="airport_distance_simple")

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
        Get all IATA airports reachable within max_duration minutes.

        :param city: Starting city name.
        :param max_duration: Maximum train travel time in minutes.
        :return: List of tuples (iata_code, airport_name).
        """
        lat, lon = self.detect_city(city)
        # Convert max_duration to max_distance using average speed
        max_distance = self.AVERAGE_TRAIN_SPEED_KMH * (max_duration / 60.0)
        subset = self.airports_df[self.airports_df["iata_code"].notna()]
        result = []
        for _, row in subset.iterrows():
            dist = self.haversine_distance(
                lat, lon, row["latitude_deg"], row["longitude_deg"]
            )
            if dist <= max_distance:
                result.append((row["iata_code"], row["name"]))
        return result


def main():
    """CLI entry point: parse args and print reachable airports."""
    parser = argparse.ArgumentParser(
        description=(
            "List IATA airports reachable by train from a starting city "
            "within a given duration (minutes), approximated by distance."
        )
    )
    parser.add_argument("city", help="Starting city name")
    parser.add_argument(
        "duration", type=float, help="Max travel time by train (minutes)"
    )
    args = parser.parse_args()

    finder = AirportFromDistance()
    try:
        airports = finder.get_airports(args.city, args.duration)
    except ValueError as e:
        sys.exit(str(e))

    for code, name in airports:
        print(f"{code} - {name}")


if __name__ == "__main__":
    main()
