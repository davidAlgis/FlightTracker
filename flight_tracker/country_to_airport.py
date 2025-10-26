import sys
import pandas as pd
import ssl
import urllib.request

class CountryToAirport:
    """Map a country name or 2-letter ISO code to its international IATA airports."""
    COUNTRIES_URL = "https://ourairports.com/data/countries.csv"
    AIRPORTS_URL = "https://ourairports.com/data/airports.csv"

    def __init__(self):
        """Load country and airport data from OurAirports."""
        # Create an unverified SSL context
        context = ssl._create_unverified_context()

        try:
            # Use the unverified context to download the CSV files
            with urllib.request.urlopen(self.COUNTRIES_URL, context=context) as response:
                self.countries_df = pd.read_csv(response)
            with urllib.request.urlopen(self.AIRPORTS_URL, context=context) as response:
                self.airports_df = pd.read_csv(response)
        except Exception as e:
            raise ValueError(f"Could not load country or airport data: {e}")

    def detect_country(self, country_input):
        """
        Detect the ISO code and official name for a given country input.
        :param country_input: Partial country name or 2-letter ISO code.
        :return: Tuple (code, name).
        :raises ValueError: If no matching country is found.
        """
        code = country_input.strip()
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
        Get all IATA airport codes and names for the given country,
        filtered to only scheduled, international airports.
        :param country_input: Partial country name or 2-letter ISO code.
        :return: List of tuples (iata_code, airport_name).
        """
        code, name = self.detect_country(country_input)
        # DEBUG: show detected country code and name
        print(f"Detected country: {name} ({code})", file=sys.stderr)
        subset = self.airports_df[
            (self.airports_df["iso_country"] == code)
            & self.airports_df["iata_code"].notna()
            & (
                self.airports_df["scheduled_service"].fillna("").str.lower()
                == "yes"
            )
            & (self.airports_df["type"] == "large_airport")
        ]
        airports = [
            (row["iata_code"], row["name"]) for _, row in subset.iterrows()
        ]
        return airports