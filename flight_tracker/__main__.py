#!/usr/bin/env python3
"""
Executable entry point for the Flight Tracker GUI.
"""

from flight_tracker.gui import FlightBotGUI


def main() -> None:
    """Launch the Flight Tracker application."""
    app = FlightBotGUI()
    app.mainloop()


if __name__ == "__main__":
    main()
