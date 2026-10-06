"""Offline checks for commands/analyze.py. No key, no network."""

import unittest

from commands import analyze as a


class FakeApi:
    """Records every call and answers from fixtures keyed by endpoint."""

    def __init__(self, answers=None, fail=None):
        self.calls = []
        self.answers = answers or {}
        self.fail = fail or {}

    def get(self, endpoint, params, cost_units=0, cost_searches=0):
        self.calls.append((endpoint, dict(params), cost_units))
        if endpoint in self.fail:
            raise SystemExit(self.fail[endpoint])
        ans = self.answers.get(endpoint)
        if callable(ans):
            return ans(params)
        return ans or {"items": []}


def video_item(vid, title="t", tags=None, description="", views=1000, likes=50, comments=5,
               duration="PT12M", category="28", channel="UC1"):
    st = {"viewCount": str(views)}
    if likes is not None:
        st["likeCount"] = str(likes)
    if comments is not None:
        st["commentCount"] = str(comments)
    return {
        "id": vid,
        "snippet": {"title": title, "tags": tags or [], "description": description,
                    "categoryId": category, "channelId": channel, "channelTitle": "ch",
                    "publishedAt": "2026-09-01T00:00:00Z", "defaultLanguage": "en"},
        "statistics": st,
        "contentDetails": {"duration": duration},
        "topicDetails": {"topicCategories": ["https://en.wikipedia.org/wiki/Technology"]},
    }


class Ids(unittest.TestCase):
    def test_markdown_patterns(self):
        text = ("| 1 | [x](https://youtu.be/bYbzI28RNmo) | ![](https://i.ytimg.com/vi/AAAAAAAAAAA/mqdefault.jpg)\n"
                "https://www.youtube.com/watch?v=BBBBBBBBBBB&t=10s and again youtu.be/bYbzI28RNmo\n")
        self.assertEqual(a.extract_ids(text), ["bYbzI28RNmo", "AAAAAAAAAAA", "BBBBBBBBBBB"])

    def test_arguments(self):
        ids = a.extract_ids("", ["CCCCCCCCCCC", "https://youtu.be/DDDDDDDDDDD", "not-an-id", "CCCCCCCCCCC"])
        self.assertEqual(ids, ["CCCCCCCCCCC", "DDDDDDDDDDD"])

    def test_arguments_come_first(self):
        self.assertEqual(a.extract_ids("youtu.be/EEEEEEEEEEE", ["FFFFFFFFFFF"]), ["FFFFFFFFFFF", "EEEEEEEEEEE"])


class TitleShapes(unittest.TestCase):
    SAMPLES = [
        ("5 Things I Wish I Knew Before Buying a 3D Printer", {"N things", "I wish I knew"}),
        ("My First 10 Prints: what actually worked", {"first N"}),
        ("How I Built a Vacuum Former for $40", {"how I"}),
        ("Bambu A1 vs Ender 3: which one for a beginner?", {"vs", "question", "for beginners"}),
        ("Why is my first layer ALWAYS failing?", {"question", "ALL-CAPS word"}),
        ("3D Printing for Beginners (2026 Guide)", {"for beginners", "bracket or pipe suffix", "year"}),
        ("I printed a whole van kitchen in 7 days", {"in N minutes/days"}),
        ("Filament dry box build | Step by step", {"bracket or pipe suffix"}),
        ("The one setting NOBODY talks about", {"ALL-CAPS word"}),
        ("What I wish I'd known about resin printing", {"I wish I knew"}),
        ("Learn slicing in 20 minutes", {"in N minutes/days"}),
        ("A quiet afternoon with the printer", {"none"}),
    ]

    def test_twelve_titles(self):
        for title, want in self.SAMPLES:
            self.assertEqual(set(a.classify_title(title)), want, title)

    def test_caps_ignores_short_and_3d(self):
        self.assertEqual(a.classify_title("I love 3D printing"), ["none"])
        self.assertIn("ALL-CAPS word", a.classify_title("DIY van shelf"))

    def test_counts_in_report(self):
        videos = [a.fetch_full_videos(FakeApi({"videos": {"items": [
            video_item("V%010d" % i, title=t) for i, (t, _) in enumerate(self.SAMPLES)]}}),
            ["V%010d" % i for i in range(len(self.SAMPLES))])][0]
        md = a.report(videos, {})
        self.assertIn("| N things | 1 |", md)
        self.assertIn("| question | 2 |", md)
        self.assertIn("| bracket or pipe suffix | 2 |", md)
        self.assertIn("| none | 1 |", md)


class Tags(unittest.TestCase):
    def test_count_once_per_video_case_folded(self):
        videos = [
            {"tags": ["3D Printing", "3d printing", "Bambu"]},
            {"tags": ["3d printing", "ender 3"]},
            {"tags": []},
        ]
        c = a.count_tags(videos)
        self.assertEqual(c["3d printing"], 2)
        self.assertEqual(c["bambu"], 1)
        self.assertEqual(c["ender 3"], 1)
        self.assertEqual(len(c), 3)

    def test_channel_keywords(self):
        self.assertEqual(a.parse_keywords('"3d printing" van DIY "small space"'),
                         ["3d printing", "van", "diy", "small space"])
        c = a.count_keywords([{"keywords": '"3d printing" van'}, {"keywords": "van"}, {"keywords": ""}])
        self.assertEqual(c["van"], 2)
        self.assertEqual(c["3d printing"], 1)


class Engagement(unittest.TestCase):
    def test_rates_and_flag(self):
        lr, cr, flag = a.engagement({"views": 100000, "likes": 150, "comments": 20})
        self.assertAlmostEqual(lr, 0.0015)
        self.assertAlmostEqual(cr, 0.0002)
        self.assertTrue(flag)
        lr, cr, flag = a.engagement({"views": 100000, "likes": 4000, "comments": 300})
        self.assertAlmostEqual(lr, 0.04)
        self.assertFalse(flag)

    def test_hidden_or_zero(self):
        self.assertEqual(a.engagement({"views": 0, "likes": 10, "comments": 1}), (None, None, False))
        lr, cr, flag = a.engagement({"views": 500, "likes": None, "comments": 3})
        self.assertIsNone(lr)
        self.assertFalse(flag)

    def test_flag_in_report(self):
        videos = [
            {"id": "A", "title": "low", "views": 100000, "likes": 100, "comments": 10, "seconds": 700, "tags": [], "channelId": "UC1", "categoryId": "28"},
            {"id": "B", "title": "fine", "views": 100000, "likes": 5000, "comments": 300, "seconds": 300, "tags": [], "channelId": "UC1", "categoryId": "26"},
        ]
        md = a.report(videos, {"UC1": {"country": "US", "keywords": ""}})
        self.assertIn("| A | low | 100,000 | 0.10% | 0.01% | possible paid traffic |", md)
        self.assertIn("| B | fine | 100,000 | 5.00% | 0.30% |  |", md)
        self.assertIn("(1 flagged)", md)
        self.assertIn("Under 10 minutes: 1. 10 minutes or more: 1.", md)
        self.assertIn("| 28 Science & Technology | 1 |", md)
        self.assertIn("| US | 2 |", md)


class Descriptions(unittest.TestCase):
    def test_patterns(self):
        descs = [
            "Build log.\nParts: https://amzn.to/abc\n\n00:00 intro\n02:10 the print",
            "No links up top.\nline two\nline three\nhttps://example.com later\n0:00 start",
            "Gear I use: Bambu A1 https://geni.us/x\nmore",
            "",
        ]
        d = a.description_patterns(descs)
        self.assertEqual(d["count"], 4)
        self.assertEqual(d["chapters"], 2)
        self.assertEqual(d["links_in_first_3_lines"], 2)
        self.assertEqual(d["names_a_product"], 2)
        lengths = sorted(len(x) for x in descs)
        self.assertEqual(d["median_length"], int((lengths[1] + lengths[2]) / 2))

    def test_chapter_needs_line_start(self):
        self.assertEqual(a.description_patterns(["see 00:00 inline"])["chapters"], 0)
        self.assertEqual(a.description_patterns(["(00:00) intro"])["chapters"], 1)


class ApiCalls(unittest.TestCase):
    def test_videos_batched_by_50_one_unit_each(self):
        ids = ["V%010d" % i for i in range(60)]
        api = FakeApi({"videos": lambda p: {"items": [video_item(v) for v in p["id"].split(",")]}})
        recs = a.fetch_full_videos(api, ids)
        self.assertEqual(len(recs), 60)
        self.assertEqual([c[0] for c in api.calls], ["videos", "videos"])
        self.assertEqual([c[2] for c in api.calls], [1, 1])
        self.assertEqual(api.calls[0][1]["part"], "snippet,statistics,contentDetails,topicDetails")
        self.assertEqual(len(api.calls[0][1]["id"].split(",")), 50)
        self.assertEqual(recs[0]["likes"], 50)
        self.assertEqual(recs[0]["seconds"], 720)
        self.assertEqual(recs[0]["topics"], ["Technology"])

    def test_channels_parts(self):
        api = FakeApi({"channels": {"items": [{
            "id": "UC1", "snippet": {"title": "c", "country": "CA"},
            "brandingSettings": {"channel": {"keywords": '"3d printing" van'}},
            "statistics": {"subscriberCount": "1630"}}]}})
        ch = a.fetch_channel_meta(api, ["UC1", "UC1"])
        self.assertEqual(api.calls[0][1]["part"], "brandingSettings,snippet,statistics")
        self.assertEqual(api.calls[0][2], 1)
        self.assertEqual(ch["UC1"]["country"], "CA")
        self.assertEqual(ch["UC1"]["keywords"], '"3d printing" van')

    def test_comments_and_questions(self):
        api = FakeApi({"commentThreads": {"items": [
            {"snippet": {"topLevelComment": {"snippet": {"textOriginal": "Where did you get the  file?", "likeCount": "12"}}}},
            {"snippet": {"topLevelComment": {"snippet": {"textOriginal": "Great video", "likeCount": "3"}}}},
        ]}})
        got, note = a.fetch_comments(api, "V1", 20)
        self.assertEqual(note, "")
        self.assertEqual(api.calls[0][1]["order"], "relevance")
        self.assertEqual(api.calls[0][1]["maxResults"], 20)
        self.assertEqual(api.calls[0][2], 1)
        self.assertEqual(got[0]["text"], "Where did you get the file?")
        self.assertTrue(a.is_viewer_question(got[0]["text"]))
        self.assertFalse(a.is_viewer_question(got[1]["text"]))
        self.assertEqual(a.fetch_comments(api, "V1", 500)[0] and api.calls[-1][1]["maxResults"], 100)

    def test_comments_disabled_does_not_stop(self):
        charged = []
        orig = a.ledger_add
        a.ledger_add = lambda **kw: charged.append(kw)
        try:
            api = FakeApi(fail={"commentThreads": 'HTTP 403 on commentThreads: {"error": {"errors": [{"reason": "commentsDisabled"}]}}'})
            got, note = a.fetch_comments(api, "V1", 20)
        finally:
            a.ledger_add = orig
        self.assertEqual(got, [])
        self.assertIn("disabled", note)
        self.assertEqual(charged, [{"units": 1}])

    def test_other_exit_propagates(self):
        api = FakeApi(fail={"commentThreads": "Quota exhausted for today"})
        with self.assertRaises(SystemExit):
            a.fetch_comments(api, "V1", 20)

    def test_report_comments_section(self):
        videos = [{"id": "V1", "title": "t", "views": 10, "likes": 1, "comments": 1, "seconds": 60, "tags": [], "channelId": "", "categoryId": "28"}]
        md = a.report(videos, {}, {"V1": ([{"text": "which nozzle?", "likes": 2}, {"text": "nice", "likes": 9}], "")})
        self.assertIn("## What viewers ask", md)
        self.assertIn("- (2 likes) which nozzle?", md)
        self.assertNotIn("nice", md)


if __name__ == "__main__":
    unittest.main()
