"""Offline checks for outliers.py. No key, no network."""

import argparse
import os
import tempfile
import unittest

import outliers as o
import ytlib


class Helpers(unittest.TestCase):
    def test_duration(self):
        self.assertEqual(o.parse_duration("PT1H2M3S"), 3723)
        self.assertEqual(o.parse_duration("PT45S"), 45)
        self.assertEqual(o.parse_duration("PT12M"), 720)
        self.assertEqual(o.parse_duration("junk"), 0)

    def test_short(self):
        self.assertTrue(o.is_short(59))
        self.assertTrue(o.is_short(180))
        self.assertFalse(o.is_short(181))
        self.assertFalse(o.is_short(0))

    def test_ratio(self):
        self.assertEqual(o.ratio(141844, 1090), 130.1)
        self.assertEqual(o.ratio(500, 3), 5.0)  # floor at 100 subs

    def test_seen(self):
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as f:
            f.write("| Date | Video | File |\n|---|---|---|\n")
            f.write("| 2026-10-04 | bYbzI28RNmo | x.md |\n| 2026-10-04 | not-an-id | x.md |\n")
        try:
            self.assertEqual(o.read_seen(f.name), {"bYbzI28RNmo"})
        finally:
            os.unlink(f.name)


def args(**kw):
    base = dict(views_min=100000, subs_max=100000, ratio_min=5.0, format="long", months=24)
    base.update(kw)
    return argparse.Namespace(**base)


class Rank(unittest.TestCase):
    videos = {
        "a": dict(id="a", title="A", channelId="c1", channelTitle="C1", publishedAt="2026-01-01", views=141844, seconds=600),
        "b": dict(id="b", title="B", channelId="c2", channelTitle="C2", publishedAt="2026-01-01", views=500000, seconds=50),
        "c": dict(id="c", title="C", channelId="c3", channelTitle="C3", publishedAt="2026-01-01", views=120000, seconds=900),
        "d": dict(id="d", title="D", channelId="c1", channelTitle="C1", publishedAt="2026-01-01", views=90000, seconds=900),
    }
    channels = {
        "c1": dict(id="c1", subs=1090, hidden_subs=False),
        "c2": dict(id="c2", subs=2000, hidden_subs=False),
        "c3": dict(id="c3", subs=50000, hidden_subs=False),
    }

    def test_filters_and_sort(self):
        rows = o.rank(self.videos, self.channels, args(), set())
        # b is a Short, c fails ratio (2.4), d fails views.
        self.assertEqual([r["id"] for r in rows], ["a"])
        self.assertEqual(rows[0]["ratio"], 130.1)

    def test_short_mode_and_seen(self):
        rows = o.rank(self.videos, self.channels, args(format="short"), set())
        self.assertEqual([r["id"] for r in rows], ["b"])
        rows = o.rank(self.videos, self.channels, args(format="short"), {"b"})
        self.assertEqual(rows, [])

    def test_markdown(self):
        rows = o.rank(self.videos, self.channels, args(), set())
        text = o.markdown(rows, args(), ["lane one"], {"searches": 1, "units": 2})
        self.assertIn("| 1 | [A](https://youtu.be/a) | C1 |", text)
        self.assertIn("i.ytimg.com/vi/a/mqdefault.jpg", text)


if __name__ == "__main__":
    unittest.main()


import datetime as _dt
import json as _json
import os as _os
import tempfile as _tempfile
import urllib.error as _ue
from unittest import mock as _mock


class LedgerAndClockTests(unittest.TestCase):
    def test_pacific_day_handles_daylight_saving(self):
        # 2026-11-01 08:30 UTC is 01:30 PDT; 2026-11-02 08:30 UTC is 00:30 PST.
        utc = _dt.timezone.utc
        self.assertEqual(ytlib.pacific_day(_dt.datetime(2026, 11, 1, 8, 30, tzinfo=utc)), "2026-11-01")
        self.assertEqual(ytlib.pacific_day(_dt.datetime(2026, 11, 2, 8, 30, tzinfo=utc)), "2026-11-02")
        # 06:59 UTC on 2026-10-06 is still 2026-10-05 in Pacific daylight time.
        self.assertEqual(ytlib.pacific_day(_dt.datetime(2026, 10, 6, 6, 59, tzinfo=utc)), "2026-10-05")

    def test_ledger_read_survives_truncated_file(self):
        with _tempfile.TemporaryDirectory() as d, _mock.patch.object(ytlib, "LEDGER", _os.path.join(d, "quota.json")):
            open(ytlib.LEDGER, "w").write("{\"day\": ")
            data = ytlib.ledger_read()
            self.assertEqual((data["searches"], data["units"]), (0, 0))
            ytlib.ledger_add(searches=1, units=3)
            self.assertEqual(_json.load(open(ytlib.LEDGER))["units"], 3)
            self.assertEqual(ytlib.ledger_add(units=2)["units"], 5)
            self.assertFalse([f for f in _os.listdir(d) if f.endswith(".tmp")])

    def test_api_get_exits_cleanly_on_network_error(self):
        api = ytlib.Api("k")
        with _mock.patch("urllib.request.urlopen", side_effect=_ue.URLError("dns down")):
            with self.assertRaises(SystemExit) as cm:
                api.get("videos", {"id": "x"})
            self.assertIn("Network error", str(cm.exception))

    def test_resolve_channel_reads_handle_from_url(self):
        class FakeApi:
            def __init__(self):
                self.calls = []
            def get(self, endpoint, params, **kw):
                self.calls.append(params["forHandle"])
                return {"items": [{"id": "UCabcdefghijklmnopqrstuv"}]}
        api = FakeApi()
        ytlib.resolve_channel(api, "https://www.youtube.com/@somebody/videos")
        ytlib.resolve_channel(api, "somebody")
        self.assertEqual(api.calls, ["@somebody", "@somebody"])
        self.assertEqual(ytlib.resolve_channel(api, "UCabcdefghijklmnopqrstuv"), "UCabcdefghijklmnopqrstuv")

    def test_fetch_videos_refreshes_stale_cache(self):
        class FakeApi:
            def __init__(self):
                self.hits = 0
            def get(self, endpoint, params, **kw):
                self.hits += 1
                return {"items": [{"id": "v1", "snippet": {"title": "t", "channelId": "c", "channelTitle": "ct",
                                                            "publishedAt": "2026-01-01T00:00:00Z"},
                                   "statistics": {"viewCount": "5"}, "contentDetails": {"duration": "PT4M"}}]}
        with _tempfile.TemporaryDirectory() as d, _mock.patch.object(ytlib, "CACHE", d):
            api = FakeApi()
            ytlib.fetch_videos(api, ["v1"])
            ytlib.fetch_videos(api, ["v1"])
            self.assertEqual(api.hits, 1)
            stale = ytlib.cache_get("video", "v1"); stale["fetched"] = "2020-01-01"; ytlib.cache_put("video", "v1", stale)
            ytlib.fetch_videos(api, ["v1"])
            self.assertEqual(api.hits, 2)
            ytlib.fetch_videos(api, ["v1"], fresh=True)
            self.assertEqual(api.hits, 3)
