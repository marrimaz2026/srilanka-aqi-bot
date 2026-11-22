"""Offline tests: fake AQI values, fake IQAir responses, fake Typefully.

Run with:  python -m unittest discover -s tests -v
"""

import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import aqi_bot as bot  # noqa: E402

NOW = datetime(2026, 11, 22, 15, 0, tzinfo=bot.TIMEZONE)
LONG_AGO = (NOW - timedelta(days=1)).isoformat()


def state_of(**levels):
    return {"cities": {c: {"level": n, "aqi": 0, "last_alert_at": LONG_AGO}
                       for c, n in levels.items()}}


# ---------------------------------------------------------------- fakes
class FakePublisher:
    def __init__(self, remaining=10, fail=False):
        self.remaining, self.fail, self.sent = remaining, fail, []

    def quota_remaining(self):
        return self.remaining

    def publish_thread(self, posts):
        if self.fail:
            raise bot.TypefullyError("Typefully is down")
        self.sent.append(posts)
        return "d1"


class FakeHTTPResp:
    def __init__(self, status, payload):
        self.status_code, self.payload = status, payload
        self.content = b"x"
        self.text = str(payload)

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, responses):
        self.responses, self.requests, self.headers = list(responses), [], {}

    def request(self, method, url, **kw):
        self.requests.append((method, url, kw.get("json")))
        return self.responses.pop(0)


class FakeResp:
    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


def ok(aqi, country="Sri Lanka"):
    return FakeResp({"status": "success",
                     "data": {"country": country, "current": {"pollution": {"aqius": aqi}}}})


def fail(msg):
    return FakeResp({"status": "fail", "data": {"message": msg}})


# ---------------------------------------------------------------- tests
class TestLevels(unittest.TestCase):
    def test_boundaries(self):
        cases = {0: 0, 50: 0, 51: 1, 100: 1, 101: 2, 150: 2, 151: 3,
                 200: 3, 201: 4, 300: 4, 301: 5, 999: 5}
        for aqi, lvl in cases.items():
            self.assertEqual(bot.get_level(aqi), lvl, aqi)


class TestSeriousOnly(unittest.TestCase):
    def test_non_serious_changes_not_posted_but_recorded(self):
        st = state_of(Colombo="Good", Kandy="Unhealthy for Sensitive Groups")
        alerts, new = bot.evaluate({"Colombo": 80, "Kandy": 140 - 50}, st, NOW)
        self.assertEqual(alerts, [])
        self.assertEqual(new["cities"]["Colombo"]["level"], "Moderate")
        self.assertEqual(new["cities"]["Kandy"]["level"], "Moderate")

    def test_level_up_into_unhealthy(self):
        alerts, _ = bot.evaluate({"Colombo": 160},
                                 state_of(Colombo="Unhealthy for Sensitive Groups"), NOW)
        self.assertEqual(bot.format_city_line(alerts[0]), "🔴 Colombo: AQI 160 (Unhealthy) ⬆️")

    def test_worse_within_serious(self):
        alerts, _ = bot.evaluate({"Colombo": 230}, state_of(Colombo="Unhealthy"), NOW)
        self.assertEqual(bot.format_city_line(alerts[0]), "🟣 Colombo: AQI 230 (Very Unhealthy) ⬆️")

    def test_level_down_all_clear(self):
        alerts, _ = bot.evaluate({"Galle": 95}, state_of(Galle="Unhealthy"), NOW)
        self.assertEqual(bot.format_city_line(alerts[0]), "🟡 Galle: AQI 95 (Moderate) ⬇️")

    def test_no_change(self):
        st = state_of(Galle="Unhealthy")
        alerts, new = bot.evaluate({"Galle": 190}, st, NOW)
        self.assertEqual(alerts, [])
        self.assertEqual(new["cities"]["Galle"]["level"], "Unhealthy")

    def test_first_run_posts_only_serious_cities(self):
        readings = {"Colombo": 170, "Kandy": 60, "Jaffna": 120, "Galle": 320}
        alerts, new = bot.evaluate(readings, {}, NOW)
        self.assertEqual(sorted(a["city"] for a in alerts), ["Colombo", "Galle"])
        for a in alerts:
            self.assertNotIn("⬆️", bot.format_city_line(a))
        self.assertEqual(len(new["cities"]), 4)  # everyone recorded

    def test_daily_limit_one_worse_one_better(self):
        st = state_of(Colombo="Moderate", Galle="Moderate", Kandy="Unhealthy")
        # 10:00 - Colombo gets worse -> uses today's "worse" post
        a1, st = bot.evaluate({"Colombo": 160}, st, NOW)
        self.assertEqual([a["city"] for a in a1], ["Colombo"])
        # 11:00 - Galle gets worse -> held back (worse already used today)
        a2, st = bot.evaluate({"Galle": 170}, st, NOW + timedelta(hours=1))
        self.assertEqual(a2, [])
        self.assertEqual(st["cities"]["Galle"]["level"], "Moderate")  # not recorded
        # 12:00 - Kandy improves -> today's "better" post is still free
        a3, st = bot.evaluate({"Kandy": 90}, st, NOW + timedelta(hours=2))
        self.assertEqual([a["city"] for a in a3], ["Kandy"])
        # 13:00 - Colombo improves -> held back (better already used today)
        a4, st = bot.evaluate({"Colombo": 80}, st, NOW + timedelta(hours=3))
        self.assertEqual(a4, [])
        # Next day: Galle is still bad -> posted
        a5, _ = bot.evaluate({"Galle": 170}, st, NOW + timedelta(days=1))
        self.assertEqual([a["city"] for a in a5], ["Galle"])

    def test_same_run_worse_and_better_share_one_post(self):
        st = state_of(Colombo="Moderate", Kandy="Unhealthy")
        alerts, new = bot.evaluate({"Colombo": 160, "Kandy": 90}, st, NOW)
        self.assertEqual(len(alerts), 2)
        self.assertEqual(len(bot.build_posts(alerts, NOW)), 1)
        self.assertTrue(new["daily"]["worse"] and new["daily"]["better"])

    def test_flapping_is_limited(self):
        st = state_of(Colombo="Unhealthy for Sensitive Groups")
        posts = 0
        for hour, aqi in enumerate([151, 149, 152, 148, 153, 147]):
            alerts, st = bot.evaluate({"Colombo": aqi}, st, NOW + timedelta(hours=hour))
            posts += bool(alerts)
        self.assertEqual(posts, 2)  # one worse + one better, then nothing more today

    def test_failed_city_keeps_old_state(self):
        st = state_of(Colombo="Good", Jaffna="Unhealthy")
        _, new = bot.evaluate({"Colombo": 160}, st, NOW)
        self.assertEqual(new["cities"]["Jaffna"]["level"], "Unhealthy")


class TestPosts(unittest.TestCase):
    def test_several_cities_one_post_sorted_worst_first(self):
        st = state_of(Colombo="Moderate", Kandy="Unhealthy", Galle="Unhealthy")
        alerts, _ = bot.evaluate({"Colombo": 165, "Kandy": 45, "Galle": 240}, st, NOW)
        posts = bot.build_posts(alerts, NOW)
        self.assertEqual(len(posts), 1)
        lines = posts[0].split("\n")
        self.assertEqual(lines[0], "🇱🇰 Sri Lanka air quality update (03:00 PM SL time)")
        self.assertTrue(lines[1].startswith("🟣 Galle: AQI 240"))
        self.assertTrue(lines[2].startswith("🔴 Colombo: AQI 165"))
        self.assertTrue(lines[3].startswith("🟢 Kandy: AQI 45"))
        self.assertEqual(lines[4], bot.LEVELS[4].advice)
        self.assertEqual(lines[5], "Data: IQAir")
        self.assertEqual(lines[6], "#SriLanka #AirQuality")
        self.assertNotIn("http", posts[0])

    def test_long_post_splits_into_thread(self):
        names = ["Colombo", "Kandy", "Nuwara Eliya", "Galle", "Jaffna",
                 "Anuradhapura", "Puttalam"]
        readings = {c: 201 + i for i, c in enumerate(names)}
        alerts, _ = bot.evaluate(readings, {}, NOW)
        posts = bot.build_posts(alerts, NOW)
        self.assertGreater(len(posts), 1)
        for p in posts:
            self.assertLessEqual(bot.x_length(p), bot.MAX_POST_LENGTH)
        self.assertTrue(posts[0].startswith("🇱🇰"))
        self.assertTrue(posts[-1].endswith("#SriLanka #AirQuality"))

    def test_x_length_counts_emoji_as_two(self):
        self.assertEqual(bot.x_length("abc"), 3)
        self.assertEqual(bot.x_length("🇱🇰"), 2)
        self.assertEqual(bot.x_length("⬆️"), 2)


class TestTypefully(unittest.TestCase):
    def test_publish_thread_body_and_polling(self):
        s = FakeSession([
            FakeHTTPResp(200, {"results": [{"id": 42, "username": "me"}]}),
            FakeHTTPResp(201, {"id": 7, "status": "draft", "publish_state": "in_progress"}),
            FakeHTTPResp(200, {"id": 7, "status": "published", "publish_state": "finished"}),
        ])
        c = bot.TypefullyClient("k", session=s, sleep=lambda x: None)
        c.publish_thread(["one", "two"])
        method, url, body = s.requests[1]
        self.assertEqual((method, url), ("POST", bot.TYPEFULLY_API + "/social-sets/42/drafts"))
        self.assertEqual(body["publish_at"], "now")
        self.assertEqual(body["platforms"]["x"]["posts"], [{"text": "one"}, {"text": "two"}])
        self.assertEqual(s.headers["Authorization"], "Bearer k")

    def test_publish_error_status_raises(self):
        s = FakeSession([FakeHTTPResp(201, {"id": 7, "status": "error"})])
        c = bot.TypefullyClient("k", social_set_id="42", session=s, sleep=lambda x: None)
        with self.assertRaises(bot.TypefullyError):
            c.publish_thread(["x"])

    def test_http_error_raises(self):
        s = FakeSession([FakeHTTPResp(402, {"detail": "paid plan"})])
        c = bot.TypefullyClient("k", social_set_id="42", session=s)
        with self.assertRaises(bot.TypefullyError):
            c.publish_thread(["x"])

    def test_quota(self):
        s = FakeSession([FakeHTTPResp(200, {"publishing_quota":
                                            {"used": 7, "remaining": 3, "resets_at": "x"}})])
        self.assertEqual(bot.TypefullyClient("k", "42", session=s).quota_remaining(), 3)

    def test_two_social_sets_needs_id(self):
        s = FakeSession([FakeHTTPResp(200, {"results": [{"id": 1}, {"id": 2}]})])
        with self.assertRaises(bot.TypefullyError):
            bot.TypefullyClient("k", session=s).resolve_social_set()


class TestIQAir(unittest.TestCase):
    def test_success(self):
        self.assertEqual(bot.fetch_city_aqi("Colombo", "Western", "k",
                                            get=lambda *a, **k: ok(77)), 77)

    def test_call_limit_waits_and_retries_once(self):
        responses = iter([fail("call_limit_reached"), ok(55)])
        waits = []
        aqi = bot.fetch_city_aqi("Colombo", "Western", "k",
                                 get=lambda *a, **k: next(responses), sleep=waits.append)
        self.assertEqual((aqi, waits), (55, [60]))

    def test_other_error_skips(self):
        self.assertIsNone(bot.fetch_city_aqi("Nowhere", "Western", "k",
                                             get=lambda *a, **k: fail("city_not_found")))

    def test_wrong_country_skipped(self):
        self.assertIsNone(bot.fetch_city_aqi("Colombo", "Western", "k",
                                             get=lambda *a, **k: ok(80, "India")))

    def test_fetch_all_waits_13s_between_calls(self):
        waits = []
        bot.fetch_all(bot.CITIES, "k", get=lambda *a, **k: ok(40), sleep=waits.append)
        self.assertEqual(waits, [13] * (len(bot.CITIES) - 1))


class TestMainFlow(unittest.TestCase):
    def run_main(self, publisher, readings, initial_state=None):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "state.json"
            if initial_state:
                bot.save_state(initial_state, path)
            orig = (bot.STATE_FILE, bot.fetch_all, bot.make_publisher)
            bot.STATE_FILE = path
            bot.fetch_all = lambda cities, key: readings
            bot.make_publisher = lambda: publisher
            os.environ["IQAIR_API_KEY"] = "fake"
            os.environ.pop("DRY_RUN", None)
            try:
                code = bot.main()
                saved = bot.load_state(path)
            finally:
                bot.STATE_FILE, bot.fetch_all, bot.make_publisher = orig
            return code, saved

    def test_success_saves_state(self):
        p = FakePublisher()
        code, saved = self.run_main(p, {"Colombo": 160})
        self.assertEqual((code, len(p.sent)), (0, 1))
        self.assertEqual(saved["cities"]["Colombo"]["level"], "Unhealthy")

    def test_failed_post_does_not_save_state(self):
        code, saved = self.run_main(FakePublisher(fail=True), {"Colombo": 160},
                                    state_of(Colombo="Good"))
        self.assertEqual(code, 1)
        self.assertEqual(saved["cities"]["Colombo"]["level"], "Good")

    def test_quota_used_up_skips_post_but_records_state(self):
        p = FakePublisher(remaining=0)
        code, saved = self.run_main(p, {"Colombo": 160}, state_of(Colombo="Good"))
        self.assertEqual((code, p.sent), (0, []))
        self.assertEqual(saved["cities"]["Colombo"]["level"], "Unhealthy")

    def test_non_serious_change_saves_without_posting(self):
        p = FakePublisher()
        code, saved = self.run_main(p, {"Colombo": 80}, state_of(Colombo="Good"))
        self.assertEqual((code, p.sent), (0, []))
        self.assertEqual(saved["cities"]["Colombo"]["level"], "Moderate")


if __name__ == "__main__":
    unittest.main()
