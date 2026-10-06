"""Tests for commands/hunt.py. No key, no network, no writes outside a temp dir."""

import argparse
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ytlib  # noqa: E402
from commands import hunt  # noqa: E402


def read_json(path):
    with open(path) as f:
        return json.load(f)


def args(**kw):
    base = dict(query=["lane a"], views_min=100000, subs_max=100000, ratio_min=5.0,
                months=24, format="long", pages=3, orders="viewCount,relevance,date",
                fresh=False, min_length=0, lang="en", region="US", limit=40,
                exclude_seen="", seed_watch="", out="")
    base.update(kw)
    return argparse.Namespace(**base)


def video_item(vid, channel, views, duration, title=None):
    return {
        "id": vid,
        "snippet": {"title": title or "video " + vid, "channelId": channel,
                    "channelTitle": "channel " + channel, "publishedAt": "2026-01-01T00:00:00Z"},
        "statistics": {"viewCount": str(views)},
        "contentDetails": {"duration": duration},
    }


def channel_item(cid, subs, hidden=False):
    return {
        "id": cid,
        "snippet": {"title": "channel " + cid},
        "statistics": {"subscriberCount": str(subs), "hiddenSubscriberCount": hidden, "videoCount": "10"},
        "contentDetails": {"relatedPlaylists": {"uploads": "UU" + cid[2:]}},
    }


class FakeApi:
    """Answers search, videos, and channels from canned data and records every call.

    `pages` maps (q, order) to a list of pages; each page is a list of video ids.
    """

    def __init__(self, pages=None, videos=None, channels=None):
        self.pages = pages or {}
        self.videos = videos or {}
        self.channels = channels or {}
        self.calls = []
        self.searches = 0

    def get(self, endpoint, params, cost_units=0, cost_searches=0):
        self.calls.append((endpoint, dict(params), cost_units, cost_searches))
        self.searches += cost_searches
        if endpoint == "search":
            pages = self.pages.get((params["q"], params["order"]), [])
            index = int(params.get("pageToken", "p0")[1:])
            ids = pages[index] if index < len(pages) else []
            body = {"items": [{"id": {"kind": "youtube#video", "videoId": v}} for v in ids]}
            if index + 1 < len(pages):
                body["nextPageToken"] = "p%d" % (index + 1)
            return body
        if endpoint == "videos":
            wanted = params["id"].split(",")
            return {"items": [self.videos[v] for v in wanted if v in self.videos]}
        if endpoint == "channels":
            wanted = params["id"].split(",")
            return {"items": [self.channels[c] for c in wanted if c in self.channels]}
        raise AssertionError("unexpected endpoint " + endpoint)


class Isolated(unittest.TestCase):
    """Redirects the cache and the ledger into a temp folder for the test."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._cache, self._ledger = ytlib.CACHE, ytlib.LEDGER
        ytlib.CACHE = os.path.join(self.tmp, "cache")
        ytlib.LEDGER = os.path.join(self.tmp, "quota.json")

    def tearDown(self):
        ytlib.CACHE, ytlib.LEDGER = self._cache, self._ledger
        shutil.rmtree(self.tmp)

    def set_ledger(self, searches, units=0):
        with open(ytlib.LEDGER, "w") as f:
            json.dump({"day": ytlib.pacific_day(), "searches": searches, "units": units}, f)


class Plan(unittest.TestCase):
    def test_arithmetic(self):
        self.assertEqual(hunt.plan_calls(["a", "b"], ["viewCount", "date"], 3), 12)
        self.assertEqual(hunt.plan_calls(["a"], ["viewCount"], 1), 1)
        self.assertEqual(hunt.plan_calls([], ["viewCount"], 3), 0)
        self.assertEqual(hunt.plan_calls(["a"], ["viewCount"], 0), 0)

    def test_parse_orders(self):
        self.assertEqual(hunt.parse_orders("viewCount,relevance,date"), ["viewCount", "relevance", "date"])
        self.assertEqual(hunt.parse_orders(" date , date "), ["date"])
        with self.assertRaises(ValueError):
            hunt.parse_orders("viewCount,rating")
        with self.assertRaises(ValueError):
            hunt.parse_orders("")

    def test_quota_problem(self):
        self.assertIsNone(hunt.quota_problem(6, {"searches": 94}))
        self.assertIsNone(hunt.quota_problem(0, {"searches": 100}))
        self.assertIn("would end at 101", hunt.quota_problem(7, {"searches": 94}))

    def test_plan_text(self):
        text = hunt.plan_text(["a", "b"], ["viewCount", "date"], 3, 12, {"searches": 6, "units": 15})
        self.assertIn("2 lane(s) x 2 order(s) x 3 page(s) = 12 search call(s)", text)
        self.assertIn("after: 18 searches", text)


class Gather(unittest.TestCase):
    def test_dedupes_across_lanes_pages_and_orders(self):
        api = FakeApi(pages={
            ("a", "viewCount"): [["v1", "v2"], ["v2", "v3"]],   # v2 repeats across pages
            ("a", "date"): [["v3", "v4"]],                       # v3 repeats across orders
            ("b", "viewCount"): [["v1", "v5"]],                  # v1 repeats across lanes
            ("b", "date"): [],
        })
        found = hunt.gather(api, ["a", "b"], ["viewCount", "date"], 2, 24, "en", "US")
        self.assertEqual(list(found), ["v1", "v2", "v3", "v4", "v5"])
        self.assertEqual(found["v1"], ("a", "viewCount"))
        self.assertEqual(found["v3"], ("a", "viewCount"))
        self.assertEqual(found["v4"], ("a", "date"))
        self.assertEqual(found["v5"], ("b", "viewCount"))
        # Every search page charged one search call: a/viewCount 2, a/date 1, b/viewCount 1, b/date 1.
        self.assertEqual(api.searches, 5)
        self.assertTrue(all(c[3] == 1 for c in api.calls if c[0] == "search"))

    def test_stops_at_pages(self):
        api = FakeApi(pages={("a", "date"): [["v1"], ["v2"], ["v3"], ["v4"]]})
        found = hunt.gather(api, ["a"], ["date"], 2, 24, "en", "US")
        self.assertEqual(list(found), ["v1", "v2"])
        self.assertEqual(api.searches, 2)


class Filters(unittest.TestCase):
    def test_min_length_and_shorts(self):
        self.assertFalse(hunt.passes_length({"seconds": 180}, 0, "long"))   # a Short, exactly as ytlib.is_short
        self.assertTrue(hunt.passes_length({"seconds": 181}, 0, "long"))
        self.assertTrue(hunt.passes_length({"seconds": 180}, 0, "any"))
        self.assertTrue(hunt.passes_length({"seconds": 60}, 0, "short"))
        self.assertFalse(hunt.passes_length({"seconds": 400}, 600, "long"))
        self.assertTrue(hunt.passes_length({"seconds": 600}, 600, "long"))
        # An unparseable duration is 0 seconds, which ytlib.is_short does not count as a Short.
        self.assertEqual(hunt.passes_length({"seconds": 0}, 0, "long"), not ytlib.is_short(0))


class SeedWatch(unittest.TestCase):
    def rows(self):
        return [
            {"channelId": "UCaaa", "channelTitle": "A", "subs": 1200, "lane": "lane a"},
            {"channelId": "UCbbb", "channelTitle": "B", "subs": 50000, "lane": "lane b"},
            {"channelId": "UCaaa", "channelTitle": "A again", "subs": 1300, "lane": "lane c"},
        ]

    def test_merge_dedupes_by_channel_and_keeps_existing(self):
        existing = [{"id": "UCbbb", "title": "B old", "subs": 100, "added": "2026-01-01", "from": "x"}]
        merged, added = hunt.seed_watch_merge(existing, self.rows(), "2026-10-05")
        self.assertEqual(added, 1)
        self.assertEqual([e["id"] for e in merged], ["UCbbb", "UCaaa"])
        self.assertEqual(merged[0], existing[0])
        self.assertEqual(merged[1], {"id": "UCaaa", "title": "A", "subs": 1200,
                                     "added": "2026-10-05", "from": "lane a"})

    def test_write_creates_then_appends(self):
        tmp = tempfile.mkdtemp()
        try:
            path = os.path.join(tmp, "nested", "watch.json")
            merged, added = hunt.seed_watch_write(path, self.rows()[:1], today="2026-10-05")
            self.assertEqual(added, 1)
            self.assertEqual(read_json(path), merged)
            merged, added = hunt.seed_watch_write(path, self.rows(), today="2026-10-06")
            self.assertEqual(added, 1)
            data = read_json(path)
            self.assertEqual([e["id"] for e in data], ["UCaaa", "UCbbb"])
            self.assertEqual(data[0]["added"], "2026-10-05")
            self.assertEqual(sorted(data[1]), ["added", "from", "id", "subs", "title"])
        finally:
            shutil.rmtree(tmp)


class Run(Isolated):
    def fake(self):
        return FakeApi(
            pages={
                ("lane a", "viewCount"): [["big", "hit1"], ["hit2"]],
                ("lane a", "date"): [["hit1", "short1", "small"]],
            },
            videos={
                "big": video_item("big", "UCbig", 5000000, "PT20M"),      # channel too big
                "hit1": video_item("hit1", "UCone", 300000, "PT12M"),    # passes
                "hit2": video_item("hit2", "UCtwo", 150000, "PT4M"),     # passes, found on page 2
                "short1": video_item("short1", "UCtwo", 900000, "PT1M"),  # a Short
                "small": video_item("small", "UCone", 20000, "PT10M"),   # too few views
            },
            channels={
                "UCbig": channel_item("UCbig", 2000000),
                "UCone": channel_item("UCone", 10000),
                "UCtwo": channel_item("UCtwo", 20000),
            },
        )

    def test_refuses_when_over_quota(self):
        self.set_ledger(95)
        api = self.fake()
        with self.assertRaises(SystemExit) as cm:
            hunt.run(api, args(pages=3, orders="viewCount,relevance,date"), log=lambda m: None)
        self.assertIn("needs 9 searches", str(cm.exception))
        self.assertEqual(api.calls, [])

    def test_runs_and_writes_table_with_found_by(self):
        self.set_ledger(10)
        api = self.fake()
        out = os.path.join(self.tmp, "out.md")
        watch = os.path.join(self.tmp, "watch.json")
        logs = []
        rows = hunt.run(api, args(pages=2, orders="viewCount,date", out=out, seed_watch=watch),
                        log=logs.append)
        self.assertEqual([r["id"] for r in rows], ["hit1", "hit2"])
        self.assertEqual(rows[0]["order"], "viewCount")
        self.assertEqual(api.searches, 3)
        self.assertEqual(sum(1 for c in api.calls if c[0] == "videos"), 1)
        self.assertEqual(sum(1 for c in api.calls if c[0] == "channels"), 1)
        with open(out) as f:
            text = f.read()
        self.assertIn("| Lane | Found by | Thumb |", text)
        self.assertIn("| lane a | viewCount |", text)
        self.assertIn("plan: 1 lane(s) x 2 order(s) x 2 page(s) = 4 search call(s)", logs[0])
        self.assertEqual([e["id"] for e in read_json(watch)], ["UCone", "UCtwo"])

    def test_fresh_sets_window_and_order(self):
        self.set_ledger(0)
        api = self.fake()
        a = args(fresh=True, pages=1, out=os.path.join(self.tmp, "o.md"))
        hunt.run(api, a, log=lambda m: None)
        searches = [c for c in api.calls if c[0] == "search"]
        self.assertEqual(len(searches), 1)
        self.assertEqual(searches[0][1]["order"], "date")
        self.assertEqual(a.months, 1)

    def test_min_length_drops_rows(self):
        self.set_ledger(0)
        api = self.fake()
        rows = hunt.run(api, args(pages=2, orders="viewCount", min_length=600,
                                  out=os.path.join(self.tmp, "o.md")), log=lambda m: None)
        self.assertEqual([r["id"] for r in rows], ["hit1"])


if __name__ == "__main__":
    unittest.main()
