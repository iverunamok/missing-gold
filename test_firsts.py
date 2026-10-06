"""Offline checks for commands/firsts.py. No key, no network."""

import io
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import ytlib  # noqa: E402
from commands import firsts as fs  # noqa: E402


def video(vid, cid, published, views, seconds=600, title=None):
    return dict(id=vid, title=title or vid.upper(), channelId=cid, channelTitle=cid + " title",
                publishedAt=published, views=views, seconds=seconds)


class FakeApi:
    """Answers videos, channels, and playlistItems from fixtures, pages the
    playlist 50 at a time with a token, and counts units. Any search call
    fails the test."""

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
            raise AssertionError("firsts must never call search.list")
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
                        "snippet": {"title": c["title"], "publishedAt": c["created"] + "T00:00:00Z"},
                        "statistics": {"subscriberCount": str(c["subs"]), "videoCount": str(c["videoCount"])},
                        "contentDetails": {"relatedPlaylists": {"uploads": "UU" + c["id"][2:]}},
                    })
            return {"items": items}
        if endpoint == "playlistItems":
            ids = self.playlists.get(params["playlistId"], [])
            start = int(params.get("pageToken") or 0)
            size = params.get("maxResults", 50)
            page = ids[start:start + size]
            body = {"items": [{"contentDetails": {"videoId": v}} for v in page]}
            if start + size < len(ids):
                body["nextPageToken"] = str(start + size)
            return body
        raise AssertionError("unexpected endpoint " + endpoint)

    def paged(self, endpoint, params, limit, cost_units=1):
        return ytlib.Api.paged(self, endpoint, params, limit, cost_units)


class Sandbox(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._cache, self._ledger = ytlib.CACHE, ytlib.LEDGER
        ytlib.CACHE = os.path.join(self.tmp, "cache")
        ytlib.LEDGER = os.path.join(self.tmp, "quota.json")

    def tearDown(self):
        ytlib.CACHE, ytlib.LEDGER = self._cache, self._ledger
        shutil.rmtree(self.tmp)


def day(n):
    """2021-01-01 plus n days, as the API's date prefix."""
    import datetime as dt
    return (dt.date(2021, 1, 1) + dt.timedelta(days=n)).isoformat()


def make_channel(n_videos, views=None, gap=7):
    """A channel whose uploads playlist lists n_videos newest-first. Video
    vid00000001 is the oldest; views default to 100 each."""
    cid = "UCchan0000000000000000aa"
    vids = []
    for i in range(1, n_videos + 1):
        vids.append(video("vid%08d" % i, cid, day((i - 1) * gap),
                          (views or {}).get(i, 100), seconds=900))
    playlist = {"UU" + cid[2:]: [v["id"] for v in reversed(vids)]}
    chan = dict(id=cid, title="Chan", subs=5000, videoCount=n_videos, created="2020-12-01", handle="@chan")
    return cid, vids, chan, playlist


class Paging(Sandbox):
    def test_pages_to_the_end_one_unit_per_page(self):
        cid, vids, chan, playlist = make_channel(120)
        api = FakeApi(vids, [chan], playlist)
        ids, capped = fs.all_upload_ids(api, "UU" + cid[2:])
        self.assertEqual(len(ids), 120)
        self.assertEqual(ids[0], "vid00000120")
        self.assertEqual(ids[-1], "vid00000001")
        self.assertFalse(capped)
        pages = [c for c in api.calls if c[0] == "playlistItems"]
        self.assertEqual(len(pages), 3)
        self.assertEqual(api.units, 3)
        self.assertEqual(api.searches, 0)
        self.assertNotIn("pageToken", pages[0][1])
        self.assertEqual(pages[1][1]["pageToken"], "50")
        self.assertEqual(pages[2][1]["pageToken"], "100")

    def test_cap_flags_a_truncated_read(self):
        cid, vids, chan, playlist = make_channel(160)
        api = FakeApi(vids, [chan], playlist)
        ids, capped = fs.all_upload_ids(api, "UU" + cid[2:], max_pages=2)
        self.assertEqual(len(ids), 100)
        self.assertTrue(capped)
        self.assertEqual(api.units, 2)


class Order(Sandbox):
    def test_oldest_count_in_chronological_order(self):
        cid, vids, chan, playlist = make_channel(120)
        api = FakeApi(vids, [chan], playlist)
        ids, _ = fs.all_upload_ids(api, "UU" + cid[2:])
        oldest = ids[-25:]
        fetch = fs.fetch_videos(api, oldest)
        rows, median = fs.early_rows(fetch.values(), 25)
        self.assertEqual([r["n"] for r in rows], list(range(1, 26)))
        self.assertEqual([r["id"] for r in rows], ["vid%08d" % i for i in range(1, 26)])
        self.assertEqual(rows[0]["published"], "2021-01-01")
        self.assertEqual(rows[0]["gap"], None)
        self.assertEqual([r["gap"] for r in rows[1:]], [7] * 24)
        self.assertEqual(api.units, 3 + 1)  # three playlist pages, one videos.list

    def test_sorts_even_when_the_playlist_order_is_mixed(self):
        cid = "UCchan0000000000000000aa"
        vids = [video("late", cid, "2021-03-01", 5), video("early", cid, "2021-01-01", 5),
                video("mid", cid, "2021-02-01", 5)]
        rows, _ = fs.early_rows(vids, 25)
        self.assertEqual([r["id"] for r in rows], ["early", "mid", "late"])
        self.assertEqual([r["gap"] for r in rows], [None, 31, 28])


class Median(Sandbox):
    def test_multiple_of_median_and_top_mark(self):
        cid = "UCchan0000000000000000aa"
        vids = [video("a", cid, day(0), 100), video("b", cid, day(1), 200),
                video("c", cid, day(2), 300), video("d", cid, day(3), 5000)]
        rows, median = fs.early_rows(vids, 25)
        self.assertEqual(median, 250)
        self.assertEqual([r["mult"] for r in rows], [0.4, 0.8, 1.2, 20.0])
        self.assertEqual([r["top"] for r in rows], [False, False, False, True])

    def test_zero_median_gives_no_multiple(self):
        cid = "UCchan0000000000000000aa"
        vids = [video("a", cid, day(0), 0), video("b", cid, day(1), 0)]
        rows, median = fs.early_rows(vids, 25)
        self.assertEqual(median, 0)
        self.assertEqual([r["mult"] for r in rows], [None, None])
        self.assertIsNone(fs.breakout(rows, median))


class Summary(Sandbox):
    def test_summary_row_finds_first_10x_and_counts_90_days(self):
        # 30 uploads a week apart; #12 is the first at 10x the median of 100,
        # #20 is bigger but later.
        cid, vids, chan, playlist = make_channel(30, views={12: 1000, 20: 9000}, gap=7)
        rows, median = fs.early_rows(vids, 25)
        self.assertEqual(median, 100)
        ch = dict(chan, created="2020-12-01", hidden_subs=False, uploads="UU" + cid[2:])
        s = fs.summary_row(ch, rows, median, truncated=True)
        self.assertEqual(s["channel"], "Chan")
        self.assertEqual(s["created"], "2020-12-01")
        self.assertEqual(s["subs"], 5000)
        self.assertEqual(s["first_upload"], "2021-01-01")
        self.assertEqual(s["uploads_90d"], "13")   # days 0, 7, ... 84 are within 90
        self.assertEqual(s["breakout_n"], 12)
        self.assertEqual(s["breakout_title"], "VID00000012")
        self.assertEqual(s["breakout_mult"], 10.0)

    def test_summary_row_marks_a_floor_when_every_row_is_inside_90_days(self):
        cid, vids, chan, playlist = make_channel(40, gap=1)
        rows, median = fs.early_rows(vids, 25)
        ch = dict(chan, hidden_subs=False, uploads="UU" + cid[2:])
        s = fs.summary_row(ch, rows, median, truncated=True)
        self.assertEqual(s["uploads_90d"], "25+")
        self.assertIsNone(s["breakout_n"])
        self.assertIn("| none |", fs.summary_markdown([s]))


class Resolve(Sandbox):
    def test_video_refs_and_channel_refs(self):
        self.assertEqual(fs.video_ref("k99WrrqoDUU"), "k99WrrqoDUU")
        self.assertEqual(fs.video_ref("https://www.youtube.com/watch?v=k99WrrqoDUU&t=5"), "k99WrrqoDUU")
        self.assertEqual(fs.video_ref("https://youtu.be/k99WrrqoDUU"), "k99WrrqoDUU")
        self.assertIsNone(fs.video_ref("@BeelineFox"))
        self.assertIsNone(fs.video_ref("UCchan0000000000000000aa"))
        self.assertIsNone(fs.video_ref("https://www.youtube.com/@BeelineFox"))

    def test_resolve_by_video_id_costs_one_videos_call(self):
        cid, vids, chan, playlist = make_channel(3)
        api = FakeApi(vids, [chan], playlist)
        self.assertEqual(fs.resolve(api, "vid00000002"), cid)
        self.assertEqual(api.units, 1)
        self.assertEqual(fs.resolve(api, "@chan"), cid)
        self.assertEqual(api.units, 2)

    def test_skip_rules(self):
        ch = dict(title="Big", subs=250000, hidden_subs=False, created="2021-05-05")
        self.assertIn("over", fs.skip_reason(ch, 100000, ""))
        self.assertEqual(fs.skip_reason(ch, 0, ""), "")
        old = dict(title="Old", subs=10, hidden_subs=False, created="2015-05-05")
        self.assertIn("before 2019", fs.skip_reason(old, 100000, "2019"))
        self.assertEqual(fs.skip_reason(dict(old, created="2019-01-01"), 100000, "2019"), "")


class EndToEnd(Sandbox):
    def test_command_writes_tables_and_spends_no_searches(self):
        cid, vids, chan, playlist = make_channel(60, views={5: 4000}, gap=10)
        api = FakeApi(vids, [chan], playlist)
        fs.Api = lambda key: api
        fs.load_key = lambda: "k"
        out = os.path.join(self.tmp, "firsts.md")
        args = type("A", (), dict(channel=["@chan"], count=25, max_subs=100000,
                                  min_created="2019", out=out))()
        err = io.StringIO()
        with redirect_stdout(io.StringIO()), redirect_stderr(err):
            fs.cmd_firsts(args)
        text = open(out).read()
        self.assertIn("## Chan", text)
        self.assertIn("Created 2020-12-01, 5,000 subs, 60 uploads", text)
        self.assertIn("| 1 | VID00000001 | 2021-01-01 | - | 100 | 15:00 | 1.0 |", text)
        self.assertIn("| 5 | VID00000005 TOP | 2021-02-10 | 10 | 4,000 | 15:00 | 40.0 |", text)
        self.assertIn("## Summary", text)
        self.assertIn("| Chan | 2020-12-01 | 5,000 | 2021-01-01 | 10 | #5 (40.0x) | VID00000005 |", text)
        self.assertEqual(api.searches, 0)
        # handle 1 + channels 1 + playlist 2 pages + videos 1
        self.assertEqual(api.units, 5)
        self.assertIn("Units spent: 0.", text)  # the fake api never touches the ledger


if __name__ == "__main__":
    unittest.main()
