#!/usr/bin/env python3
"""One-time helper: print every Sri Lankan city IQAir knows about.

Usage:
    IQAIR_API_KEY=your_key python list_cities.py

Uses 1 call for the province list plus 1 call per province, waiting 13 seconds
between calls (about 2 minutes total). Copy the exact spellings into CITIES
in aqi_bot.py.
"""

from __future__ import annotations

import os
import sys
import time

import requests

COUNTRY = "Sri Lanka"
BASE = "https://api.airvisual.com/v2"
WAIT = 13
RATE_LIMIT_WAIT = 60


def call(endpoint: str, params: dict) -> list | None:
    for attempt in (1, 2):
        try:
            payload = requests.get(f"{BASE}/{endpoint}", params=params, timeout=30).json()
        except (requests.RequestException, ValueError) as exc:
            print(f"  ! request failed ({type(exc).__name__})")
            return None
        if payload.get("status") == "success":
            return payload.get("data") or []
        data = payload.get("data")
        message = data.get("message", "unknown error") if isinstance(data, dict) else "unknown error"
        if message == "call_limit_reached" and attempt == 1:
            print(f"  ! call limit reached - waiting {RATE_LIMIT_WAIT}s and retrying once")
            time.sleep(RATE_LIMIT_WAIT)
            continue
        print(f"  ! API error: {message}")
        return None
    return None


def main() -> int:
    key = os.getenv("IQAIR_API_KEY")
    if not key:
        print("Set IQAIR_API_KEY first, e.g.  IQAIR_API_KEY=abc123 python list_cities.py")
        return 1

    states = call("states", {"country": COUNTRY, "key": key})
    if not states:
        print("Could not get the province list.")
        return 1

    names = [s["state"] for s in states if "state" in s]
    print(f"IQAir has {len(names)} provinces for {COUNTRY}. Fetching cities "
          f"(~{len(names) * WAIT}s)...\n")

    for province in names:
        time.sleep(WAIT)
        cities = call("cities", {"state": province, "country": COUNTRY, "key": key})
        print(f'Province: "{province}"')
        if cities:
            for c in cities:
                print(f'    ("{c.get("city")}", "{province}"),')
        else:
            print("    (no cities returned)")
        print()

    print("Copy the lines you want into CITIES in aqi_bot.py.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
