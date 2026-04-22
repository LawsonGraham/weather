"""City → IANA timezone mapping for the 11 US markets we trade.

Used by the live strategy's per-city local-hour gate (STRATEGY.md §3
entry filter #5). Peak temperature is a function of local solar time,
not UTC — a 16:00-local floor gives every city the same relative
position in its peak window, unlike a fixed UTC hour which would
translate to different local times per zone.
"""
from __future__ import annotations

CITY_TO_TZ: dict[str, str] = {
    "New York City": "America/New_York",
    "Atlanta":       "America/New_York",
    "Miami":         "America/New_York",
    "Chicago":       "America/Chicago",
    "Dallas":        "America/Chicago",
    "Houston":       "America/Chicago",
    "Austin":        "America/Chicago",
    "Denver":        "America/Denver",
    "Seattle":       "America/Los_Angeles",
    "Los Angeles":   "America/Los_Angeles",
    "San Francisco": "America/Los_Angeles",
}

# Station-code → IANA timezone. Station codes are the ICAO identifier
# minus the leading "K" (LGA, ATL, MIA, ...). Used by lib.weather.hrrr
# and any other module that needs per-station local time but only has
# the station code (not the full city name).
STATION_TZ: dict[str, str] = {
    "LGA": "America/New_York",
    "NYC": "America/New_York",
    "ATL": "America/New_York",
    "MIA": "America/New_York",
    "ORD": "America/Chicago",
    "DAL": "America/Chicago",
    "HOU": "America/Chicago",
    "AUS": "America/Chicago",
    "DEN": "America/Denver",
    "SEA": "America/Los_Angeles",
    "LAX": "America/Los_Angeles",
    "SFO": "America/Los_Angeles",
}
