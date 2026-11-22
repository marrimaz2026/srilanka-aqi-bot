#!/usr/bin/env python3
"""Sri Lanka air quality bot (serious alerts only).

Checks the US AQI for several Sri Lankan cities every hour using the IQAir
(AirVisual) API. It posts to X (through Typefully) only for SERIOUS changes:

  * a city reaches Unhealthy (151+) or worse          -> alert ⬆️
  * a city already at Unhealthy+ changes level         -> alert ⬆️/⬇️
  * a city drops back below 151 ("all clear")          -> update ⬇️

Changes between Good / Moderate / Unhealthy for Sensitive Groups are recorded
in state.json but never posted. At most one "worse" post and one "better"
post are sent per day, to stay close to Typefully's free plan.

Environment variables:
    IQAIR_API_KEY            - IQAir API key (required)
    TYPEFULLY_API_KEY        - Typefully API key (required unless DRY_RUN)
    TYPEFULLY_SOCIAL_SET_ID  - optional; only needed if your Typefully account
                               has more than one connected account/social set
    DRY_RUN=true             - print posts instead of sending them
                               (state.json is NOT updated)
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, NamedTuple
from zoneinfo import ZoneInfo

import requests

# ---------------------------------------------------------------------------
# Cities to monitor: (city, province) exactly as IQAir spells them.
# Run list_cities.py to see every name IQAir has for Sri Lanka.
# Keep this list at 7 cities or fewer to stay well inside IQAir's free plan.
# ---------------------------------------------------------------------------
CITIES: list[tuple[str, str]] = [
    ("Colombo", "Western"),
    # To add more cities later, remove the "# " at the start of a line:
    # ("Kandy", "Central"),
    # ("Nuwara Eliya", "Central"),
    # ("Galle", "Southern"),
    # ("Jaffna", "Northern"),
    # ("Anuradhapura", "North Central"),
    # ("Puttalam", "North Western"),
]

# --- Alert settings --------------------------------------------------------
SERIOUS_LEVEL = 3            # index in LEVELS: 3 = "Unhealthy" (AQI 151+)
# Daily limit (Sri Lanka time): at most ONE "worse" post and ONE "better" post
# per day. Changes that are blocked by the limit are checked again next hour,
# and go out the next day if they are still true.

# --- Other settings --------------------------------------------------------
COUNTRY = "Sri Lanka"
API_URL = "https://api.airvisual.com/v2/city"
SECONDS_BETWEEN_CALLS = 13      # keeps us under 5 calls/minute
RATE_LIMIT_WAIT = 60            # wait after "call_limit_reached", then retry once
STATE_FILE = Path(__file__).resolve().with_name("state.json")
TIMEZONE = ZoneInfo("Asia/Colombo")
MAX_POST_LENGTH = 270           # X limit is 280; leave a small safety margin
HASHTAGS = "#SriLanka #AirQuality"
DATA_CREDIT = "Data: IQAir"
ARROW_WORSE = "⬆️"
ARROW_BETTER = "⬇️"
TYPEFULLY_API = "https://api.typefully.com/v2"


class Level(NamedTuple):
    max_aqi: float
    name: str
    emoji: str
    advice: str


# US AQI scale, best to worst. The list index is the "level number".
LEVELS: list[Level] = [
    Level(50, "Good", "🟢", "Enjoy the fresh air!"),
    Level(100, "Moderate", "🟡",
          "Very sensitive people: go easy on heavy outdoor exercise."),
    Level(150, "Unhealthy for Sensitive Groups", "🟠",
          "Kids, elderly & people with asthma/heart disease: limit time outdoors."),
    Level(200, "Unhealthy", "🔴",
          "Everyone: limit outdoor activity and consider a mask."),
    Level(300, "Very Unhealthy", "🟣",
          "Avoid outdoor activity. Keep windows closed."),
    Level(float("inf"), "Hazardous", "🟤",
          "Stay indoors. Use an air purifier if you can."),
]
LEVEL_BY_NAME = {lvl.name: i for i, lvl in enumerate(LEVELS)}


def get_level(aqi: int) -> int:
    """Return the level index (0 = Good ... 5 = Hazardous) for a US AQI value."""
    for i, lvl in enumerate(LEVELS):
        if aqi <= lvl.max_aqi:
            return i
    return len(LEVELS) - 1


# ---------------------------------------------------------------------------
# X character counting (simplified version of X's "weighted length" rules):
# most Latin/basic characters count 1, everything else (incl. emoji) counts 2,
# and multi-code-point emoji (flags, variation selectors, ZWJ) count 2 total.
# ---------------------------------------------------------------------------
_LIGHT_RANGES = [(0, 4351), (8192, 8205), (8208, 8223), (8242, 8247)]


def _is_light(cp: int) -> bool:
    return any(lo <= cp <= hi for lo, hi in _LIGHT_RANGES)


def x_length(text: str) -> int:
    total = 0
    cps = [ord(c) for c in text]
    i = 0
    while i < len(cps):
        cp = cps[i]
        if 0x1F1E6 <= cp <= 0x1F1FF and i + 1 < len(cps) and 0x1F1E6 <= cps[i + 1] <= 0x1F1FF:
            total += 2          # flag = two regional indicators = one emoji
            i += 2
            continue
        if cp == 0xFE0F or 0x1F3FB <= cp <= 0x1F3FF:
            i += 1              # variation selector / skin tone: part of previous emoji
            continue
        if cp == 0x200D:
            i += 2              # zero-width joiner glues the next emoji on
            continue
        total += 1 if _is_light(cp) else 2
        i += 1
    return total


# ---------------------------------------------------------------------------
# IQAir
# ---------------------------------------------------------------------------
def fetch_city_aqi(city: str, province: str, api_key: str,
                   get: Callable = requests.get,
                   sleep: Callable[[float], None] = time.sleep) -> int | None:
    """Return the current US AQI for a city, or None if it should be skipped."""
    params = {"city": city, "state": province, "country": COUNTRY, "key": api_key}
    for attempt in (1, 2):
        try:
            resp = get(API_URL, params=params, timeout=30)
            payload = resp.json()
        except (requests.RequestException, ValueError) as exc:
            # Don't print the exception text: it can contain the URL with the key.
            print(f"  ! {city}: request failed ({type(exc).__name__}) - skipping")
            return None

        data = payload.get("data") if isinstance(payload, dict) else None
        data = data if isinstance(data, dict) else {}

        if payload.get("status") == "success":
            if data.get("country") != COUNTRY:
                print(f"  ! {city}: result is for {data.get('country')!r}, not {COUNTRY} - skipping")
                return None
            try:
                return int(data["current"]["pollution"]["aqius"])
            except (KeyError, TypeError, ValueError):
                print(f"  ! {city}: no AQI value in response - skipping")
                return None

        message = data.get("message", "unknown error")
        if message == "call_limit_reached" and attempt == 1:
            print(f"  ! {city}: call limit reached - waiting {RATE_LIMIT_WAIT}s and retrying once")
            sleep(RATE_LIMIT_WAIT)
            continue
        print(f"  ! {city}: API error '{message}' - skipping")
        return None
    return None


def fetch_all(cities: list[tuple[str, str]], api_key: str,
              get: Callable = requests.get,
              sleep: Callable[[float], None] = time.sleep) -> dict[str, int]:
    """Fetch every city, waiting between calls. Returns {city: aqi} for successes."""
    readings: dict[str, int] = {}
    for n, (city, province) in enumerate(cities):
        if n > 0:
            sleep(SECONDS_BETWEEN_CALLS)
        aqi = fetch_city_aqi(city, province, api_key, get=get, sleep=sleep)
        if aqi is not None:
            readings[city] = aqi
            print(f"  {city}: AQI {aqi} ({LEVELS[get_level(aqi)].name})")
    return readings


# ---------------------------------------------------------------------------
# Deciding what to post
# ---------------------------------------------------------------------------
def is_serious(level: int | None) -> bool:
    return level is not None and level >= SERIOUS_LEVEL


def evaluate(readings: dict[str, int], state: dict, now: datetime) -> tuple[list[dict], dict]:
    """Work out which cities need an alert, and the new state.

    Returns (alerts, new_state). new_state records every city's current level
    (posted or not), plus which of today's two post slots are used.
    """
    cities = {k: dict(v) for k, v in state.get("cities", {}).items()}
    today = now.date().isoformat()
    daily = dict(state.get("daily") or {})
    if daily.get("date") != today:
        daily = {"date": today, "worse": False, "better": False}

    alerts: list[dict] = []
    for city, aqi in readings.items():
        level = get_level(aqi)
        prev = LEVEL_BY_NAME.get((cities.get(city) or {}).get("level"))

        if prev == level:
            continue  # same level: nothing to do

        # Only serious changes are posted: into, within, or out of Unhealthy+.
        post_worthy = is_serious(level) or is_serious(prev)

        if post_worthy:
            direction = "worse" if prev is None or level > prev else "better"
            if daily.get(direction):
                # Today's post for this direction is already used. Leave this
                # city's state alone so the change is checked again later.
                print(f"  {city}: {direction} change held back (today's '{direction}' post already used)")
                continue
            alerts.append({"city": city, "aqi": aqi, "level": level,
                           "prev_level": prev, "direction": direction})

        cities[city] = {"level": LEVELS[level].name, "aqi": aqi,
                        "updated_at": now.isoformat(timespec="minutes")}

    for a in alerts:
        daily[a["direction"]] = True

    return alerts, {"cities": dict(sorted(cities.items())), "daily": daily}


def format_city_line(change: dict) -> str:
    lvl = LEVELS[change["level"]]
    line = f"{lvl.emoji} {change['city']}: AQI {change['aqi']} ({lvl.name})"
    prev = change["prev_level"]
    if prev is not None:
        line += f" {ARROW_WORSE if change['level'] > prev else ARROW_BETTER}"
    return line


def build_posts(changes: list[dict], now: datetime) -> list[str]:
    """Build one post, or a thread if it doesn't fit in MAX_POST_LENGTH."""
    changes = sorted(changes, key=lambda c: c["aqi"], reverse=True)
    header = f"🇱🇰 Sri Lanka air quality update ({now.strftime('%I:%M %p')} SL time)"
    worst = max(c["level"] for c in changes)
    footer = f"{LEVELS[worst].advice}\n{DATA_CREDIT}\n{HASHTAGS}"
    blocks = [header, *(format_city_line(c) for c in changes), footer]

    posts: list[str] = []
    current = ""
    for block in blocks:
        candidate = f"{current}\n{block}" if current else block
        if x_length(candidate) <= MAX_POST_LENGTH:
            current = candidate
        else:
            posts.append(current)
            current = block
    posts.append(current)
    return posts


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------
def load_state(path: Path | None = None) -> dict:
    path = path or STATE_FILE
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        print("  ! state.json is unreadable - treating this as a first run")
        return {}


def save_state(state: dict, path: Path | None = None) -> None:
    path = path or STATE_FILE
    path.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Typefully (publishes to X for you, no X API credits needed)
# ---------------------------------------------------------------------------
class TypefullyError(Exception):
    pass


class TypefullyClient:
    def __init__(self, api_key: str, social_set_id: str | None = None,
                 session: requests.Session | None = None,
                 sleep: Callable[[float], None] = time.sleep):
        self.session = session or requests.Session()
        self.session.headers.update({"Authorization": f"Bearer {api_key}",
                                     "Content-Type": "application/json"})
        self.social_set_id = social_set_id
        self.sleep = sleep

    def _request(self, method: str, path: str, **kwargs) -> dict:
        try:
            resp = self.session.request(method, TYPEFULLY_API + path, timeout=30, **kwargs)
        except requests.RequestException as exc:
            raise TypefullyError(f"{method} {path}: {type(exc).__name__}") from None
        if resp.status_code >= 400:
            hint = {401: " (check TYPEFULLY_API_KEY)",
                    402: " (account paused or plan limit reached)",
                    403: " (the key needs permission to publish)",
                    429: " (rate limited)"}.get(resp.status_code, "")
            raise TypefullyError(f"{method} {path}: HTTP {resp.status_code}{hint}: {resp.text[:300]}")
        return resp.json() if resp.content else {}

    def resolve_social_set(self) -> str:
        if self.social_set_id:
            return self.social_set_id
        results = self._request("GET", "/social-sets").get("results", [])
        if len(results) == 1:
            self.social_set_id = str(results[0]["id"])
            return self.social_set_id
        listing = ", ".join(f"{r.get('id')} (@{r.get('username')})" for r in results) or "none"
        raise TypefullyError("Can't pick a Typefully social set automatically. "
                             f"Found: {listing}. Set the TYPEFULLY_SOCIAL_SET_ID secret.")

    def quota_remaining(self) -> int | None:
        """Posts left this month, or None if Typefully doesn't report a limit."""
        info = self._request("GET", f"/social-sets/{self.resolve_social_set()}")
        quota = info.get("publishing_quota") or {}
        remaining = quota.get("remaining")
        if remaining is not None:
            print(f"  Typefully quota: {quota.get('used')} used, {remaining} left "
                  f"(resets {quota.get('resets_at')})")
        return remaining

    def publish_thread(self, posts: list[str], wait_seconds: int = 60) -> str:
        """Publish now on X (a thread if several posts). Returns the draft id."""
        sid = self.resolve_social_set()
        body = {"platforms": {"x": {"enabled": True, "posts": [{"text": t} for t in posts]}},
                "publish_at": "now"}
        draft = self._request("POST", f"/social-sets/{sid}/drafts", json=body)
        draft_id = draft.get("id")
        if draft_id is None:
            raise TypefullyError(f"Unexpected response: {str(draft)[:300]}")

        # Publishing is asynchronous: check until it's done (or give up waiting).
        waited = 0
        while True:
            if draft.get("status") == "error":
                raise TypefullyError(f"Typefully reported a publishing error for draft {draft_id}")
            if draft.get("status") == "published" or draft.get("publish_state") == "finished":
                print(f"  Published via Typefully (draft {draft_id})")
                return str(draft_id)
            if waited >= wait_seconds:
                # Accepted but not confirmed yet. Don't treat it as a failure,
                # otherwise the next run would post the same alert twice.
                print(f"  Typefully accepted draft {draft_id}; still publishing.")
                return str(draft_id)
            self.sleep(5)
            waited += 5
            draft = self._request("GET", f"/social-sets/{sid}/drafts/{draft_id}")


def make_publisher() -> TypefullyClient:
    key = os.getenv("TYPEFULLY_API_KEY")
    if not key:
        raise TypefullyError("Missing secret: TYPEFULLY_API_KEY")
    return TypefullyClient(key, os.getenv("TYPEFULLY_SOCIAL_SET_ID") or None)


def is_dry_run() -> bool:
    return os.getenv("DRY_RUN", "").strip().lower() in ("1", "true", "yes", "on")


# ---------------------------------------------------------------------------
def main() -> int:
    dry_run = is_dry_run()
    api_key = os.getenv("IQAIR_API_KEY")
    if not api_key:
        print("ERROR: IQAIR_API_KEY is not set.")
        return 1

    publisher = None
    if not dry_run:
        try:
            publisher = make_publisher()
        except TypefullyError as exc:
            print(f"ERROR: {exc}")
            return 1

    state = load_state()
    if not state:
        print("No saved state - first run. Recording levels; only cities already "
              "at Unhealthy (151+) or worse will be posted.")

    print(f"Checking {len(CITIES)} cities...")
    readings = fetch_all(CITIES, api_key)
    if not readings:
        print("No readings this run - nothing to do.")
        return 0

    now = datetime.now(TIMEZONE)
    alerts, new_state = evaluate(readings, state, now)

    if not alerts:
        print("No serious changes - not posting.")
        if dry_run:
            print("DRY_RUN: state.json not updated.")
        elif new_state != state:
            save_state(new_state)
            print("State updated (no post needed).")
        return 0

    posts = build_posts(alerts, now)

    if dry_run:
        print(f"\nDRY_RUN: would post {len(posts)} post(s):")
        for i, text in enumerate(posts, 1):
            print(f"\n--- post {i}/{len(posts)} ({x_length(text)} chars) ---\n{text}")
        print("\nDRY_RUN: nothing sent, state.json not updated.")
        return 0

    try:
        remaining = publisher.quota_remaining()
        if remaining is not None and remaining < len(posts):
            # Out of free posts. Record the levels anyway so a pile of old
            # alerts isn't posted late when the quota resets.
            print(f"WARNING: only {remaining} Typefully post(s) left this month, need "
                  f"{len(posts)}. Skipping this alert.")
            save_state(new_state)
            return 0
        publisher.publish_thread(posts)
    except TypefullyError as exc:
        print(f"ERROR: posting failed ({exc}). State not saved - will retry next run.")
        return 1

    save_state(new_state)
    print("State saved.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
