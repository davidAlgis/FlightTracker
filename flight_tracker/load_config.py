#!/usr/bin/env python3
"""
Module to load and save FlightTracker search configuration to config.json.
"""

import json
import os

DEFAULT_CONFIG_FILE = "config.json"


class ConfigManager:
    """
    Manage loading and saving of the FlightTracker GUI configuration.
    """

    def __init__(self, path=DEFAULT_CONFIG_FILE):
        """
        Initialize the ConfigManager.

        :param path: Path to the JSON config file.
        """
        self.path = path
        self.config = {}

    def load(self):
        """
        Load configuration from the JSON file.

        :return: Dict of configuration, or empty dict if file is missing or invalid.
        """
        if not os.path.exists(self.path):
            return {}
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                self.config = json.load(f)
        except (IOError, json.JSONDecodeError):
            # If file is unreadable or contains invalid JSON, return empty config
            self.config = {}
        return self.config

    def save(self, config):
        """
        Save the given configuration dict to the JSON file.

        :param config: Dict of configuration to save.
        """
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(config, f, indent=4)
            self.config = config
        except IOError:
            # If file cannot be written, ignore silently
            pass
