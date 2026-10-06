"""trending: YouTube's most-popular chart for one region and category.

Replaces vidIQ's trending view. videos.list with chart=mostPopular costs
1 unit per page of 50. --list-categories reads videoCategories.list once
(1 unit) so category ids are checked against the API rather than assumed.
"""

import datetime as dt
import sys

from ytlib import Api, load_key, ledger_read, parse_duration, fmt_int, thumb

PARTS = "snippet,statistics,contentDetails"


# ---------------------------------------------------------------- pure helpers

def parse_published(iso):
    """'2026-10-01T12:34:56Z' -> naive UTC datetime. None when unparseable."""
    try:
        return dt.datetime.strptime((iso or "")[:19], "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return None


def views_per_day(views, published_iso, now=None):
    """Views divided by days since publish. Age is floored at one day so a
    video that is hours old does not get a 20x number."""
    now = now or dt.datetime.utcnow()
    pub = parse_published(published_iso)
    if pub is None:
        return 0
    age_days = max((now - pub).total_seconds() / 86400.0, 1.0)
    return int(round((views or 0) / age_days))


def fmt_len(seconds):
    seconds = int(seconds or 0)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return "%d:%02d:%02d" % (h, m, s) if h else "%d:%02d" % (m, s)


def md_cell(s):
    return (s or "").replace("|", "\\|").replace("\n", " ")


def rows_from_items(items, now=None):
    """API items -> list of row dicts."""
    rows = []
    for item in items:
        sn = item.get("snippet", {})
        st = item.get("statistics", {})
        views = int(st.get("viewCount", 0))
        rows.append({
            "id": item["id"],
            "title": sn.get("title", ""),
            "channel": sn.get("channelTitle", ""),
            "published": sn.get("publishedAt", "")[:10],
            "views": views,
            "per_day": views_per_day(views, sn.get("publishedAt", ""), now),
            "seconds": parse_duration(item.get("contentDetails", {}).get("duration")),
            "category": str(sn.get("categoryId", "")),
        })
    return rows


def table(rows):
    header = ["#", "Title", "Channel", "Published", "Views", "Views/day", "Length", "Thumb"]
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    for i, r in enumerate(rows, 1):
        lines.append("| %d | [%s](https://www.youtube.com/watch?v=%s) | %s | %s | %s | %s | %s | [thumb](%s) |" % (
            i, md_cell(r["title"]), r["id"], md_cell(r["channel"]), r["published"],
            fmt_int(r["views"]), fmt_int(r["per_day"]), fmt_len(r["seconds"]), thumb(r["id"])))
    return "\n".join(lines)


def categories_table(items):
    lines = ["| Id | Category | Assignable |", "|---|---|---|"]
    for item in sorted(items, key=lambda it: int(it["id"])):
        sn = item.get("snippet", {})
        lines.append("| %s | %s | %s |" % (item["id"], md_cell(sn.get("title", "")),
                                           "yes" if sn.get("assignable") else "no"))
    return "\n".join(lines)


# ---------------------------------------------------------------- API

def fetch_trending(api, region, category, limit):
    params = {"part": PARTS, "chart": "mostPopular", "regionCode": region}
    if category and category != "all":
        params["videoCategoryId"] = category
    return api.paged("videos", params, limit, cost_units=1)


def fetch_categories(api, region):
    body = api.get("videoCategories", {"part": "snippet", "regionCode": region}, cost_units=1)
    return body.get("items", [])


# ---------------------------------------------------------------- command

def cmd_trending(args):
    before = ledger_read()["units"]
    api = Api(load_key())
    if args.list_categories:
        items = fetch_categories(api, args.region)
        print("# Video categories, region %s (%s)\n" % (args.region, dt.date.today().isoformat()))
        print(categories_table(items))
        print("\nUnits spent: %d" % (ledger_read()["units"] - before))
        return
    items = fetch_trending(api, args.region, args.category, args.max)
    rows = rows_from_items(items)
    label = "all categories" if not args.category or args.category == "all" else "category %s" % args.category
    md = "# Trending, region %s, %s (%s)\n\n%s\n\nUnits spent: %d\n" % (
        args.region, label, dt.date.today().isoformat(), table(rows), ledger_read()["units"] - before)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(md)
        print("wrote %s (%d videos)" % (args.out, len(rows)))
    else:
        sys.stdout.write(md)


def register(sub):
    p = sub.add_parser("trending", help="YouTube's most-popular chart for a region and category (vidIQ trending)")
    p.add_argument("--category", default="28", help="videoCategoryId, or 'all' for the whole chart (default 28, Science & Technology)")
    p.add_argument("--region", default="US", help="regionCode")
    p.add_argument("--max", type=int, default=50, help="videos to print, 50 per unit")
    p.add_argument("--list-categories", action="store_true", help="print videoCategories.list for the region (1 unit) and stop")
    p.add_argument("--out", default="", help="write the markdown here instead of stdout")
    p.set_defaults(func=cmd_trending)
