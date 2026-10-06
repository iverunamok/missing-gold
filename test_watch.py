"""Offline checks for commands/watch.py. No key, no network."""

import datetime as dt
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
import urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import ytlib  # noqa: E402
from commands import watch as w  # noqa: E402

TODAY = dt.date(2026, 10, 5)


def video(vid, cid, published, views, seconds=600, title=None):
    return dict(id=vid, title=title or vid.upper(), channelId=cid, channelTitle=cid + " title",
                publishedAt=published, views=views, seconds=seconds)


class FakeApi:
    """Answers videos, channels, and playlistItems from fixtures and counts
    the units each call declares. Any search call fails the test."""

    def __init__(self, videos=(), channels=(), playlists=None):
        self.videos = {v["id"]: v for v in videos}
        self.channels = {c["id"]: c for c in channels}
        self.playlists = playlists or {}
        self.calls = []
        self.units = 0
        self.searches = 0

    def get(self, endpoint, params, cost_units=0, cost_searches=0):
        self.calls.append((endpoint, dict(params)))
        self.units += cost_units
        self.searches += cost_searches
        if endpoint == "search":
            raise AssertionError("watch must never call search.list")
        if endpoint == "videos":
            items = []
            for vid in params["id"].split(","):
                v = self.videos.get(vid)
                if v:
                    items.append({
                        "id": v["id"],
                        "snippet": {"title": v["title"], "channelId": v["channelId"],
                                    "channelTitle": v["channelTitle"], "publishedAt": v["publishedAt"] + "T00:00:00Z"},
                        "statistics": {"viewCount": str(v["views"])},
                        "contentDetails": {"duration": "PT%dS" % v["seconds"]},
                    })
            return {"items": items}
        if endpoint == "channels":
            if "forHandle" in params:
                hits = [c for c in self.channels.values() if c.get("handle") == params["forHandle"]]
                return {"items": [{"id": c["id"]} for c in hits]}
            items = []
            for cid in params["id"].split(","):
                c = self.channels.get(cid)
                if c:
                    items.append({
                        "id": c["id"],
                        "snippet": {"title": c["title"]},
                        "statistics": {"subscriberCount": str(c["subs"]), "videoCount": "40"},
                        "contentDetails": {"relatedPlaylists": {"uploads": "UU" + c["id"][2:]}},
                    })
            return {"items": items}
        if endpoint == "playlistItems":
            ids = self.playlists.get(params["playlistId"], [])
            return {"items": [{"contentDetails": {"videoId": v}} for v in ids[:params.get("maxResults", 50)]]}
        raise AssertionError("unexpected endpoint " + endpoint)

    def paged(self, endpoint, params, limit, cost_units=1):
        return ytlib.Api.paged(self, endpoint, params, limit, cost_units)


class Sandbox(unittest.TestCase):
    """Point the cache, ledger, and watch file at a temp folder."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._cache, self._ledger = ytlib.CACHE, ytlib.LEDGER
        ytlib.CACHE = os.path.join(self.tmp, "cache")
        ytlib.LEDGER = os.path.join(self.tmp, "quota.json")
        self.path = os.path.join(self.tmp, "watch.json")

    def tearDown(self):
        ytlib.CACHE, ytlib.LEDGER = self._cache, self._ledger
        shutil.rmtree(self.tmp)


class Storage(Sandbox):
    def test_merge_fills_missing_fields_and_dedupes(self):
        rows = w.merge([
            {"id": "UCa", "title": "A", "subs": 10, "added": "2026-10-01"},   # hunt --seed-watch shape
            {"id": "UCb"},                                                     # bare id
            {"id": "UCa", "from": "lane one", "median": 500, "scanned": "2026-10-02"},  # duplicate fills gaps
            {"id": ""},                                                        # dropped
            "junk",                                                            # dropped
        ])
        self.assertEqual([r["id"] for r in rows], ["UCa", "UCb"])
        a, b = rows
        self.assertEqual(a["from"], "lane one")
        self.assertEqual(a["median"], 500)
        self.assertEqual(a["scanned"], "2026-10-02")
        self.assertEqual(a["title"], "A")
        for k in w.FIELDS:
            self.assertIn(k, b)
        self.assertEqual(b["subs"], 0)
        self.assertIsNone(b["median"])
        self.assertIsNone(b["scanned"])
        self.assertEqual(b["added"], dt.date.today().isoformat())

    def test_load_and_save_round_trip(self):
        with open(self.path, "w") as f:
            json.dump([{"id": "UCa", "title": "A", "subs": 1, "added": "2026-10-01"}], f)
        rows = w.load_watch(self.path)
        rows[0]["median"] = 42
        w.save_watch(rows, self.path)
        again = w.load_watch(self.path)
        self.assertEqual(again[0]["median"], 42)
        self.assertEqual(w.load_watch(os.path.join(self.tmp, "missing.json")), [])


class Math(unittest.TestCase):
    # Ten settled long-form uploads at 1,000 views, one Short at 1,000,000
    # (excluded), one 2-day-old upload at 50,000 (too fresh for the median).
    settled = [video("v%02d" % i, "UCa", "2026-09-%02d" % (1 + i), 1000) for i in range(10)]
    short = video("short", "UCa", "2026-09-25", 1000000, seconds=45)
    fresh = video("fresh", "UCa", "2026-10-03", 50000)
    older = video("older", "UCa", "2026-08-01", 90000)  # 11th settled; falls outside the window of 10

    def test_median_ignores_shorts_fresh_and_beyond_window(self):
        vids = self.settled + [self.short, self.fresh, self.older]
        self.assertEqual(w.channel_median(vids, TODAY), 1000)
        self.assertIsNone(w.channel_median(self.settled[:2], TODAY))

    def test_flag_by_median_and_by_subs(self):
        vids = self.settled + [self.short, self.fresh]
        hits = w.flag(vids, 1000, subs=20000, today=TODAY, days=14, mult=3.0, subs_mult=5.0)
        ids = {h["id"]: h for h in hits}
        self.assertIn("fresh", ids)                   # 50x median
        self.assertEqual(ids["fresh"]["x_median"], 50.0)
        self.assertEqual(ids["fresh"]["ratio"], 2.5)
        self.assertIn("short", ids)                   # 50x subs
        self.assertNotIn("v09", ids)                  # 1x median and 25 days old

    def test_flag_window_and_null_median(self):
        hits = w.flag([self.fresh], None, subs=20000, today=TODAY, days=14, mult=3.0, subs_mult=5.0)
        self.assertEqual(hits, [])                    # no median, below 5x subs
        hits = w.flag([self.fresh], None, subs=5000, today=TODAY, days=14, mult=3.0, subs_mult=5.0)
        self.assertEqual(hits[0]["x_median"], None)   # 10x subs still flags
        hits = w.flag([self.fresh], 1000, subs=5000, today=TODAY, days=1, mult=3.0, subs_mult=5.0)
        self.assertEqual(hits, [])                    # outside --days

    def test_compute_sorts_and_updates_rows(self):
        rows = [{"id": "UCa", "title": "", "subs": 0, "added": "2026-10-01", "from": "", "median": None, "scanned": None},
                {"id": "UCb", "title": "B", "subs": 100, "added": "2026-10-01", "from": "", "median": None, "scanned": None}]
        channels = {"UCa": {"id": "UCa", "title": "A", "subs": 20000}}
        b_settled = [video("b%02d" % i, "UCb", "2026-09-%02d" % (1 + i), 100) for i in range(5)]
        b_hit = video("bhit", "UCb", "2026-10-01", 2000)
        uploads = {"UCa": self.settled + [self.fresh], "UCb": b_settled + [b_hit]}
        flags, rows = w.compute(rows, channels, uploads, TODAY)
        self.assertEqual([f["id"] for f in flags], ["fresh", "bhit"])  # 50x before 20x
        self.assertEqual(flags[0]["channelTitle"], "A")
        self.assertEqual(rows[0]["title"], "A")
        self.assertEqual(rows[0]["subs"], 20000)
        self.assertEqual(rows[0]["median"], 1000)
        self.assertEqual(rows[1]["median"], 100)
        self.assertEqual(rows[1]["subs"], 100)        # no channel record: keeps the stored subs
        self.assertEqual(rows[0]["scanned"], "2026-10-05")


ATOM = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns:yt="http://www.youtube.com/xml/schemas/2015" xmlns="http://www.w3.org/2005/Atom">
 <title>Example</title>
 <entry>
  <id>yt:video:abcdefghijk</id>
  <yt:videoId>abcdefghijk</yt:videoId>
  <yt:channelId>UCa</yt:channelId>
  <title>First | video</title>
  <published>2026-10-01T12:00:00+00:00</published>
 </entry>
 <entry>
  <id>yt:video:lmnopqrstuv</id>
  <yt:videoId>lmnopqrstuv</yt:videoId>
  <title>Second</title>
  <published>2026-09-20T12:00:00+00:00</published>
 </entry>
</feed>
"""


class Rss(Sandbox):
    def test_parse_feed(self):
        entries = w.parse_feed(ATOM)
        self.assertEqual([e["id"] for e in entries], ["abcdefghijk", "lmnopqrstuv"])
        self.assertEqual(entries[0]["title"], "First | video")
        self.assertEqual(entries[0]["published"], "2026-10-01")
        self.assertEqual(w.parse_feed("not xml"), [])
        self.assertEqual(w.parse_feed("<feed/>"), [])

    def test_fetch_feed_404_gives_empty(self):
        def opener(url, timeout=30):
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, io.BytesIO(b""))
        self.assertEqual(w.fetch_feed("UCnone", opener=opener), [])

    def test_fetch_feed_other_error_raises(self):
        def opener(url, timeout=30):
            raise urllib.error.HTTPError(url, 500, "Boom", {}, io.BytesIO(b""))
        with self.assertRaises(urllib.error.HTTPError):
            w.fetch_feed("UCnone", opener=opener)

    def test_rss_scan_only_fetches_uncached_ids(self):
        api = FakeApi(
            videos=[video("abcdefghijk", "UCa", "2026-10-01", 5000), video("lmnopqrstuv", "UCa", "2026-09-20", 100)],
            channels=[dict(id="UCa", title="A", subs=500)],
        )
        ytlib.cache_put("video", "lmnopqrstuv", dict(video("lmnopqrstuv", "UCa", "2026-09-20", 100),
                                                     fetched=ytlib.dt.date.today().isoformat()))
        rows = w.merge([{"id": "UCa"}])

        def feed(cid):
            self.assertEqual(cid, "UCa")
            return w.parse_feed(ATOM)

        channels, uploads = w.gather(api, rows, rss=True, feed=feed)
        self.assertEqual(sorted(v["id"] for v in uploads["UCa"]), ["abcdefghijk", "lmnopqrstuv"])
        video_calls = [p for e, p in api.calls if e == "videos"]
        self.assertEqual(len(video_calls), 1)
        self.assertEqual(video_calls[0]["id"], "abcdefghijk")   # the cached id was not refetched
        self.assertEqual(api.searches, 0)
        self.assertEqual(api.units, 2)                           # 1 channels + 1 videos


class Scan(Sandbox):
    def test_playlist_scan_costs_no_search_and_flags(self):
        settled = [video("v%02d" % i, "UCa", "2026-09-%02d" % (1 + i), 1000) for i in range(10)]
        hit = video("hit", "UCa", "2026-10-02", 9000)
        api = FakeApi(
            videos=settled + [hit],
            channels=[dict(id="UCa", title="A", subs=800, handle="@a")],
            playlists={"UUa": ["hit"] + [v["id"] for v in reversed(settled)]},
        )
        w.save_watch([{"id": "UCa", "title": "", "subs": 0, "added": "2026-10-01"}], self.path)
        rows = w.load_watch(self.path)
        channels, uploads = w.gather(api, rows, rss=False)
        flags, rows = w.compute(rows, channels, uploads, TODAY)
        self.assertEqual([f["id"] for f in flags], ["hit"])
        self.assertEqual(flags[0]["x_median"], 9.0)
        self.assertEqual(flags[0]["ratio"], 11.2)
        self.assertEqual(api.searches, 0)
        self.assertEqual(api.units, 3)   # 1 channels.list, 1 playlistItems.list, 1 videos.list
        self.assertEqual([e for e, _ in api.calls], ["channels", "playlistItems", "videos"])
        w.save_watch(rows, self.path)
        with open(self.path) as f:
            saved = json.load(f)
        self.assertEqual(saved[0]["median"], 1000)
        self.assertEqual(saved[0]["subs"], 800)
        self.assertEqual(saved[0]["scanned"], "2026-10-05")
        text = w.report(flags, rows, TODAY, 14, 3.0, 5.0,
                        {"units": 3, "searches": 0, "after": {"units": 3}}, rss=False)
        self.assertIn("| 1 | A | [HIT](https://youtu.be/hit) | 2026-10-02 | 9,000 | 800 | 9.0 | 11.2 |", text)
        self.assertIn("Budget: 3 units and 0 search calls", text)

    def test_resolve_any_handles_video_url_and_handle(self):
        api = FakeApi(
            videos=[video("Csa6coOyxF4", "UCvideo", "2026-01-01", 1)],
            channels=[dict(id="UChandle", title="H", subs=1, handle="@h")],
        )
        self.assertEqual(w.resolve_any(api, "https://youtu.be/Csa6coOyxF4"), "UCvideo")
        self.assertEqual(w.resolve_any(api, "@h"), "UChandle")
        self.assertEqual(w.resolve_any(api, "UCnkGejiqVNbW4vaIMq0DoXQ"), "UCnkGejiqVNbW4vaIMq0DoXQ")
        self.assertEqual(api.searches, 0)


if __name__ == "__main__":
    unittest.main()
