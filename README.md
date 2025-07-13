
# FlightTracker

A small tool to track flight prices over time.

![](assets/screenshot.jpeg)

## Features

- GUI to configure routes, dates and maximum travel time  
- Automatically finds airports by country name or city+max transport time  
- Checks flight prices on Kayak and records daily minimums  
- Shows history graph and best-ever flight  
- Runs in the system tray with notifications  

## Installation

```bash
pip install .
````

## Running

```bash
python -m flight_tracker
```

Or generate an executable with ``setup.py``.

## Usage

1. Enter departure and destination airports (IATA codes, country or city+max transport time).
2. Choose date or date range, trip duration, and max flight duration.
3. The app runs in the tray, checks prices periodically, and pops up notifications.
4. View history and best prices in the GUI.

