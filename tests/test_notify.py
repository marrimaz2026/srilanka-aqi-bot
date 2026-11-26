"""Offline tests for the Telegram status message (nothing is sent)."""

import io
import json
import sys
import unittest
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import notify_telegram as tg  # noqa: E402

OK_LOG = "Checking 1 cities...\n  Colombo: AQI 62 (Moderate)\nNo serious changes - not posting.\n"
POSTED_LOG = ("Checking 1 cities...\n  Colombo: AQI 160 (Unhealthy)\n"
              "  Typefully quota: 3 used, 7 left (resets x)\n"
              "  Published via Typefully (draft 7)\nState saved.\n")


class FakeResp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class TestClassify(unittest.TestCase):
    def test_kinds(self):
        self.assertEqual(tg.classify("success", OK_LOG)[0], "ok")
        self.assertEqual(tg.classify("success", POSTED_LOG)[0], "posted")
        self.assertEqual(tg.classify("failure", OK_LOG)[0], "error")
        self.assertEqual(tg.classify("success", "ERROR: IQAIR_API_KEY is not set.")[0], "error")
        self.assertEqual(tg.classify("success", "No readings this run - nothing to do.")[0], "warning")
        self.assertEqual(tg.classify("success", "WARNING: only 0 Typefully post(s) left")[0], "warning")
        self.assertEqual(tg.classify("success", "DRY_RUN: state.json not updated.")[0], "dry_run")
        self.assertEqual(tg.classify("cancelled", "")[0], "cancelled")

    def test_normal_runs_are_silent_problems_are_loud(self):
        self.assertFalse(tg.classify("success", OK_LOG)[2])
        self.assertTrue(tg.classify("failure", "")[2])
        self.assertTrue(tg.classify("success", POSTED_LOG)[2])


class TestMessage(unittest.TestCase):
    def test_ok_message(self):
        _, text, _ = tg.build_message("success", OK_LOG, run_url="https://x/run/1")
        self.assertTrue(text.startswith("✅ AQI bot is working"))
        self.assertIn("Colombo: AQI 62 (Moderate)", text)
        self.assertTrue(text.endswith("Details: https://x/run/1"))

    def test_failure_names_step(self):
        _, text, _ = tg.build_message("failure", "", failed_step="Run bot")
        self.assertIn("Failed step: Run bot", text)
        self.assertIn("no bot output", text)

    def test_long_log_is_trimmed(self):
        log = "\n".join(f"line {i}" for i in range(500))
        _, text, _ = tg.build_message("success", log)
        self.assertNotIn("line 0\n", text)
        self.assertIn("line 499", text)
        self.assertLessEqual(len(text), tg.MAX_MESSAGE + 2)


class TestSend(unittest.TestCase):
    def test_sends_form_data(self):
        seen = {}

        def opener(req, timeout):
            seen["url"], seen["data"] = req.full_url, req.data.decode()
            return FakeResp(json.dumps({"ok": True}).encode())

        self.assertTrue(tg.send("TOKEN", "123", "hi", silent=True, opener=opener))
        self.assertEqual(seen["url"], "https://api.telegram.org/botTOKEN/sendMessage")
        self.assertIn("chat_id=123", seen["data"])
        self.assertIn("disable_notification=true", seen["data"])

    def test_http_error_does_not_raise_or_leak_token(self):
        def opener(req, timeout):
            raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {}, None)

        out = io.StringIO()
        orig, sys.stdout = sys.stdout, out
        try:
            self.assertFalse(tg.send("SECRET", "123", "hi", silent=False, opener=opener))
        finally:
            sys.stdout = orig
        self.assertNotIn("SECRET", out.getvalue())
        self.assertIn("401", out.getvalue())


if __name__ == "__main__":
    unittest.main()
