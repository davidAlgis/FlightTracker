# flighttracker/__main__.py

from .gui import FlightBotGUI

def main():
    """
    Entry point for the flighttracker package.
    Starts the Tkinter-based Flight Price Monitor GUI.
    """
    app = FlightBotGUI()
    app.mainloop()

if __name__ == "__main__":
    main()
