"""Offline checks for commands/mine.py. No key, no token, no network."""

import datetime as dt
import unittest

from commands import mine


class Progress(unittest.TestCase):
    def test_gaps_and_rates(self):
        today = dt.date(2026, 10, 5)              # 71 days before 2026-12-15
        p = mine.progress(1200.0, 300, today, uploads_90d=1)
        self.assertEqual(p["days_left"], 71)
        self.assertFalse(p["deadline_passed"])
        self.assertEqual(p["hours_to_3000"], 1800.0)
        self.assertEqual(p["hours_to_4000"], 2800.0)
        self.assertEqual(p["subs_to_500"], 200)
        self.assertEqual(p["subs_to_1000"], 700)
        self.assertEqual(p["uploads_to_3"], 2)
        self.assertAlmostEqual(p["hours_per_day_to_3000"], 1800.0 / 71)
        self.assertAlmostEqual(p["hours_per_day_to_4000"], 2800.0 / 71)
        self.assertAlmostEqual(p["subs_per_day_to_500"], 200 / 71)
        self.assertAlmostEqual(p["subs_per_day_to_1000"], 700 / 71)

    def test_thresholds_met_clamp_to_zero(self):
        p = mine.progress(4500.0, 1200, dt.date(2026, 11, 1), uploads_90d=5)
        self.assertEqual(p["hours_to_3000"], 0.0)
        self.assertEqual(p["hours_to_4000"], 0.0)
        self.assertEqual(p["subs_to_500"], 0)
        self.assertEqual(p["subs_to_1000"], 0)
        self.assertEqual(p["uploads_to_3"], 0)
        self.assertEqual(p["hours_per_day_to_4000"], 0.0)

    def test_deadline_passed(self):
        p = mine.progress(100.0, 10, dt.date(2026, 12, 16))
        self.assertEqual(p["days_left"], -1)
        self.assertTrue(p["deadline_passed"])
        self.assertEqual(p["hours_to_4000"], 3900.0)
        self.assertIsNone(p["hours_per_day_to_3000"])
        self.assertIsNone(p["subs_per_day_to_1000"])
        self.assertNotIn("uploads_to_3", p)
        self.assertEqual(mine.fmt_rate(None, "hours"), "deadline passed")

    def test_deadline_day_counts_as_passed(self):
        p = mine.progress(0.0, 0, dt.date(2026, 12, 15))
        self.assertEqual(p["days_left"], 0)
        self.assertTrue(p["deadline_passed"])
        self.assertIsNone(p["hours_per_day_to_4000"])

    def test_hours_and_window(self):
        self.assertEqual(mine.hours(90), 1.5)
        self.assertEqual(mine.date_window(365, dt.date(2026, 10, 5)), ("2025-10-05", "2026-10-05"))
        self.assertEqual(mine.date_window(28, dt.date(2026, 10, 5)), ("2026-09-07", "2026-10-05"))


class Token(unittest.TestCase):
    def test_expiry(self):
        now = 1_000_000.0
        self.assertFalse(mine.token_expired({"expires_at": now + 3600}, now=now))
        self.assertTrue(mine.token_expired({"expires_at": now + 30}, now=now))   # inside the margin
        self.assertTrue(mine.token_expired({"expires_at": now - 1}, now=now))
        self.assertTrue(mine.token_expired({}, now=now))
        self.assertTrue(mine.token_expired({"expires_at": "junk"}, now=now))

    def test_store_response_keeps_refresh_token(self):
        tok = {"refresh_token": "r1"}
        tok = mine.store_response(tok, {"access_token": "a1", "expires_in": 3599}, now=100.0)
        self.assertEqual(tok["access_token"], "a1")
        self.assertEqual(tok["refresh_token"], "r1")
        self.assertEqual(tok["expires_at"], 3699.0)
        tok = mine.store_response(tok, {"access_token": "a2", "refresh_token": "r2"}, now=200.0)
        self.assertEqual(tok["refresh_token"], "r2")
        self.assertEqual(tok["expires_at"], 3800.0)

    def test_token_path_profile(self):
        self.assertTrue(mine.token_path("").endswith("/token.json"))
        self.assertTrue(mine.token_path("van").endswith("/token-van.json"))

    def test_pkce(self):
        verifier, challenge = mine.pkce_pair()
        self.assertGreaterEqual(len(verifier), 43)
        self.assertNotIn("=", verifier)
        self.assertNotIn("=", challenge)
        self.assertEqual(len(challenge), 43)


REPORT = {
    "kind": "youtubeAnalytics#resultTable",
    "columnHeaders": [
        {"name": "video", "columnType": "DIMENSION", "dataType": "STRING"},
        {"name": "views", "columnType": "METRIC", "dataType": "INTEGER"},
        {"name": "estimatedMinutesWatched", "columnType": "METRIC", "dataType": "INTEGER"},
        {"name": "averageViewPercentage", "columnType": "METRIC", "dataType": "FLOAT"},
    ],
    "rows": [
        ["abc123", 1500, 6000, 41.2],
        ["def456", 300, 900, 55.0],
    ],
}


class Parsing(unittest.TestCase):
    def test_rows_to_dicts(self):
        rows = mine.parse_report(REPORT)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["video"], "abc123")
        self.assertEqual(rows[0]["estimatedMinutesWatched"], 6000)
        self.assertEqual(rows[1]["averageViewPercentage"], 55.0)

    def test_empty_report(self):
        self.assertEqual(mine.parse_report({"columnHeaders": [{"name": "views"}]}), [])
        self.assertEqual(mine.parse_report({}), [])

    def test_totals(self):
        t = mine.totals(mine.parse_report(REPORT), "views", "estimatedMinutesWatched")
        self.assertEqual(t["views"], 1800.0)
        self.assertEqual(t["estimatedMinutesWatched"], 6900.0)
        self.assertEqual(mine.hours(t["estimatedMinutesWatched"]), 115.0)

    def test_not_enabled_message(self):
        text = '{"error": {"code": 403, "errors": [{"reason": "accessNotConfigured"}]}}'
        self.assertIn("Enable", mine.not_enabled_message(text))
        self.assertIsNone(mine.not_enabled_message('{"error": {"code": 403, "errors": [{"reason": "forbidden"}]}}'))


if __name__ == "__main__":
    unittest.main()
