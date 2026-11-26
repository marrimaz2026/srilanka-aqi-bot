#!/usr/bin/env python3
"""Send a short Telegram message about the last bot run.

Runs as the last step of the GitHub Actions workflow, even if an earlier step
failed, so you always know whether the bot is working.

Uses only the Python standard library (no pip packages), so it still works if
"Install dependencies" failed.

Environment variables (set by the workflow):
    TELEGRAM_BOT_TOKEN  - from @BotFather (GitHub secret). If missing: do nothing.
    TELEGRAM_CHAT_ID    - your chat id (GitHub secret). If missing: do nothing.
    TELEGRAM_NOTIFY     - "all" (default): a message every run
                          "important": only posts, warnings and errors
    JOB_STATUS          - "success", "failure" or "cancelled"
    FAILED_STEP         - name of the step that failed (if any)
    RUN_URL             - link to this run on GitHub
    LOG_FILE            - the bot's output (default: bot.log)
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable

MAX_LOG_LINES = 12
MAX_MESSAGE = 3500          # Telegram allows 4096; keep a margin


def classify(job_status: str, log: str) -> tuple[str, str, bool]:
    """Return (kind, headline, important) for this run.

    important=True means the message makes a sound on your phone;
    False sends it silently.
    """
    if job_status == "cancelled":
        return "cancelled", "⚪ AQI bot run was cancelled", True
    if job_status != "success" or "ERROR:" in log:
        return "error", "❌ AQI bot FAILED", True
    if "Published via Typefully" in log or "Typefully accepted draft" in log:
        return "posted", "📢 AQI bot posted to X", True
    if "WARNING:" in log:
        return "warning", "⚠️ AQI bot warning", True
    if "No readings this run" in log:
        return "warning", "⚠️ AQI bot got no data from IQAir", True
    if "DRY_RUN" in log:
        return "dry_run", "🧪 AQI bot dry run OK", False
    return "ok", "✅ AQI bot is working", False


def useful_lines(log: str) -> list[str]:
    """Pick the interesting lines from the bot's output."""
    keep = []
    for raw in log.splitlines():
        line = raw.strip()
        if not line or line.startswith("---"):
            continue
        keep.append(line)
    return keep[-MAX_LOG_LINES:]


def build_message(job_status: str, log: str, failed_step: str = "",
                  run_url: str = "") -> tuple[str, str, bool]:
    kind, headline, important = classify(job_status, log)
    parts = [headline]
    if kind == "error" and failed_step:
        parts.append(f"Failed step: {failed_step}")
    lines = useful_lines(log)
    if lines:
        parts.append("\n".join(lines))
    elif kind == "error":
        parts.append("(no bot output - open the run on GitHub to see why)")
    if run_url:
        parts.append(f"Details: {run_url}")
    text = "\n\n".join(parts)
    if len(text) > MAX_MESSAGE:
        text = text[:MAX_MESSAGE] + "\n…"
    return kind, text, important


def send(token: str, chat_id: str, text: str, silent: bool,
         opener: Callable = urllib.request.urlopen) -> bool:
    data = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": text,
        "disable_notification": "true" if silent else "false",
        "disable_web_page_preview": "true",
    }).encode()
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        with opener(urllib.request.Request(url, data=data), timeout=20) as resp:
            ok = json.loads(resp.read().decode() or "{}").get("ok", False)
    except urllib.error.HTTPError as exc:
        # Don't print the exception itself: the URL contains the bot token.
        hint = {400: " (check TELEGRAM_CHAT_ID, and send your bot a message first)",
                401: " (check TELEGRAM_BOT_TOKEN)",
                403: " (press Start in your bot's chat first)"}.get(exc.code, "")
        print(f"Telegram: HTTP {exc.code}{hint}")
        return False
    except Exception as exc:  # network problems etc.
        print(f"Telegram: could not send ({type(exc).__name__})")
        return False
    print("Telegram: message sent." if ok else "Telegram: not sent (unexpected reply).")
    return bool(ok)


def main() -> int:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        print("Telegram not set up (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID missing) - skipping.")
        return 0

    log_path = Path(os.getenv("LOG_FILE", "bot.log"))
    try:
        log = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        log = ""

    kind, text, important = build_message(
        os.getenv("JOB_STATUS", "failure"), log,
        os.getenv("FAILED_STEP", ""), os.getenv("RUN_URL", ""))

    mode = os.getenv("TELEGRAM_NOTIFY", "all").strip().lower() or "all"
    if mode == "important" and kind in ("ok", "dry_run"):
        print("Telegram: normal run, TELEGRAM_NOTIFY=important - not sending.")
        return 0

    send(token, chat_id, text, silent=not important)
    return 0  # never fail the workflow because of Telegram


if __name__ == "__main__":
    sys.exit(main())
