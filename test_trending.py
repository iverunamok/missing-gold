"""Offline checks for commands/trending.py. No key, no network."""

import datetime as dt
import unittest

from commands import trending as t


class FakeApi:
    def __init__(self, pages):
        self.pages = list(pages)
        self.calls = []

    def get(self, endpoint, params, cost_units=0, cost_searches=0):
        self.calls.append((endpoint, dict(params), cost_units))
        return self.pages.pop(0)

    def paged(self, endpoint, params, limit, cost_units=1):
        items, token = [], None
        while len(items) < limit:
            p = dict(params, maxResults=min(50, limit - len(items)))
            if token:
                p["pageToken"] = token
            body = self.get(endpoint, p, cost_units=cost_units)
            items.extend(body.get("items", []))
            token = body.get("nextPageToken")
            if not token:
                break
        return items


def item(vid, title, channel, published, views, duration):
    return {"id": vid, "snippet": {"title": title, "channelTitle": channel, "publishedAt": published, "categoryId": "28"},
            "statistics": {"viewCount": str(views)}, "contentDetails": {"duration": duration}}


NOW = dt.datetime(2026, 10, 5, 12, 0, 0)


class ViewsPerDay(unittest.TestCase):
    def test_ten_days(self):
        self.assertEqual(t.views_per_day(1000000, "2026-09-25T12:00:00Z", NOW), 100000)

    def test_half_day_floors_to_one(self):
        self.assertEqual(t.views_per_day(50000, "2026-10-05T00:00:00Z", NOW), 50000)

    def test_fraction_rounds(self):
        # 2.5 days old
        self.assertEqual(t.views_per_day(1000, "2026-10-03T00:00:00Z", NOW), 400)

    def test_bad_date(self):
        self.assertEqual(t.views_per_day(1000, "", NOW), 0)
        self.assertEqual(t.views_per_day(1000, "yesterday", NOW), 0)


class Table(unittest.TestCase):
    def test_rows_and_table(self):
        rows = t.rows_from_items([
            item("AAAAAAAAAAA", "Pipe | in title", "Chan", "2026-09-25T12:00:00Z", 1000000, "PT1H2M3S"),
            item("BBBBBBBBBBB", "Short one", "Other", "2026-10-04T12:00:00Z", 2500, "PT9M5S"),
        ], NOW)
        self.assertEqual(rows[0]["per_day"], 100000)
        self.assertEqual(rows[1]["per_day"], 2500)
        md = t.table(rows)
        lines = md.splitlines()
        self.assertEqual(lines[0], "| # | Title | Channel | Published | Views | Views/day | Length | Thumb |")
        self.assertEqual(lines[1], "|---|---|---|---|---|---|---|---|")
        self.assertEqual(lines[2], "| 1 | [Pipe \\| in title](https://www.youtube.com/watch?v=AAAAAAAAAAA) | Chan | 2026-09-25 | 1,000,000 | 100,000 | 1:02:03 | [thumb](https://i.ytimg.com/vi/AAAAAAAAAAA/mqdefault.jpg) |")
        self.assertEqual(lines[3], "| 2 | [Short one](https://www.youtube.com/watch?v=BBBBBBBBBBB) | Other | 2026-10-04 | 2,500 | 2,500 | 9:05 | [thumb](https://i.ytimg.com/vi/BBBBBBBBBBB/mqdefault.jpg) |")

    def test_fmt_len(self):
        self.assertEqual(t.fmt_len(0), "0:00")
        self.assertEqual(t.fmt_len(599), "9:59")
        self.assertEqual(t.fmt_len(3600), "1:00:00")

    def test_categories_table_sorted_by_id(self):
        md = t.categories_table([
            {"id": "28", "snippet": {"title": "Science & Technology", "assignable": True}},
            {"id": "2", "snippet": {"title": "Autos & Vehicles", "assignable": True}},
            {"id": "18", "snippet": {"title": "Short Movies", "assignable": False}},
        ])
        self.assertEqual(md.splitlines()[2], "| 2 | Autos & Vehicles | yes |")
        self.assertEqual(md.splitlines()[3], "| 18 | Short Movies | no |")
        self.assertEqual(md.splitlines()[4], "| 28 | Science & Technology | yes |")


class ApiCalls(unittest.TestCase):
    def test_chart_params_and_paging(self):
        api = FakeApi([
            {"items": [item("V%010d" % i, "t", "c", "2026-10-01T00:00:00Z", 10, "PT1M") for i in range(50)], "nextPageToken": "p2"},
            {"items": [item("W%010d" % i, "t", "c", "2026-10-01T00:00:00Z", 10, "PT1M") for i in range(10)]},
        ])
        items = t.fetch_trending(api, "US", "28", 60)
        self.assertEqual(len(items), 60)
        self.assertEqual(len(api.calls), 2)
        p = api.calls[0][1]
        self.assertEqual(p["chart"], "mostPopular")
        self.assertEqual(p["regionCode"], "US")
        self.assertEqual(p["videoCategoryId"], "28")
        self.assertEqual(p["maxResults"], 50)
        self.assertEqual(api.calls[0][2], 1)
        self.assertEqual(api.calls[1][1]["pageToken"], "p2")
        self.assertEqual(api.calls[1][1]["maxResults"], 10)

    def test_all_drops_category(self):
        api = FakeApi([{"items": []}])
        t.fetch_trending(api, "US", "all", 20)
        self.assertNotIn("videoCategoryId", api.calls[0][1])

    def test_categories_cost_one_unit(self):
        api = FakeApi([{"items": [{"id": "28", "snippet": {"title": "x", "assignable": True}}]}])
        self.assertEqual(len(t.fetch_categories(api, "US")), 1)
        self.assertEqual(api.calls[0][0], "videoCategories")
        self.assertEqual(api.calls[0][2], 1)


if __name__ == "__main__":
    unittest.main()
