#!/usr/bin/env python3
"""
Module providing CountryToAirport, a class to map a country name or ISO code
to its IATA airport codes and names using OurAirports data.
"""

import sys

import pandas as pd


class CountryToAirport:
    """Map a country name or 2-letter ISO code to its IATA airports."""

    COUNTRIES_URL = "https://ourairports.com/data/countries.csv"
    AIRPORTS_URL = "https://ourairports.com/data/airports.csv"

    def __init__(self):
        """Load country and airport data from OurAirports."""
        self.countries_df = pd.read_csv(self.COUNTRIES_URL)
        self.airports_df = pd.read_csv(self.AIRPORTS_URL)

    def detect_country(self, country_input):
        """
        Detect the ISO code and official name for a given country input.

        :param country_input: Partial country name or 2-letter ISO code.
        :return: Tuple (code, name).
        :raises ValueError: If no matching country is found.
        """
        code = country_input.strip()
        # If not a valid 2-letter code, match by name
        if (
            len(code) != 2
            or code.upper() not in self.countries_df["code"].values
        ):
            mask = self.countries_df["name"].str.contains(
                code, case=False, na=False
            )
            matches = self.countries_df[mask]
            if matches.empty:
                raise ValueError(
                    f"No country found matching '{country_input}'"
                )
            code = matches.iloc[0]["code"]
            name = matches.iloc[0]["name"]
        else:
            code = code.upper()
            name = self.countries_df.loc[
                self.countries_df["code"] == code, "name"
            ].iloc[0]

        return code, name

    def get_airports(self, country_input):
        """
        Get all IATA airport codes and names for the given country.

        :param country_input: Partial country name or 2-letter ISO code.
        :return: List of tuples (iata_code, airport_name).
        """
        code, name = self.detect_country(country_input)
        # Debug: show detected country code and name
        print(f"Detected country: {name} ({code})", file=sys.stderr)

        subset = self.airports_df[
            (self.airports_df["iso_country"] == code)
            & self.airports_df["iata_code"].notna()
        ]

        airports = [
            (row["iata_code"], row["name"]) for _, row in subset.iterrows()
        ]
        return airports
