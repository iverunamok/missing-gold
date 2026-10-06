"""hunt: a deeper `search`.

search.list returns the biggest videos first, so one page per lane is mostly
channels over 100K subscribers that the filters then throw away. `hunt` runs
up to --pages pages per lane under up to three sort orders, dedupes the video
ids before any videos.list call, and applies the same filters as `search`.

Same flags as `search` (views-min, subs-max, ratio-min, months, format, lang,
region, limit, exclude-seen, out) so a skill or script can swap it in (docs/method.md).
Extra flags: --pages (default 3), --orders (default all three), --fresh
(30-day window, order date), --min-length (seconds), --seed-watch (append
passing channels to a JSON list for a later watch-list rescan).

Quota: lanes x orders x pages search calls, each 1 of the 100 daily. The plan
prints to stderr and the run refuses before the first call if it would push
today's count past the cap.
"""

import datetime as dt
import json
import os
import sys

import ytlib
from ytlib import (
    DAILY_SEARCHES, DAILY_UNITS, Api, fetch_channels, fetch_videos, fmt_int,
    is_short, ledger_read, load_key, rank, read_seen, thumb,
)

ORDERS = ("viewCount", "relevance", "date")
FRESH_MONTHS = 1  # ytlib.search takes months as 30-day blocks; 1 is 30 days.


# ---------------------------------------------------------------- plan

def parse_orders(text):
    """'viewCount,relevance' -> ['viewCount', 'relevance']. Rejects unknown names."""
    orders = []
    for name in (text or "").split(","):
        name = name.strip()
        if not name:
            continue
        if name not in ORDERS:
            raise ValueError("unknown order %r; choose from %s" % (name, ", ".join(ORDERS)))
        if name not in orders:
            orders.append(name)
    if not orders:
        raise ValueError("no order given; choose from %s" % ", ".join(ORDERS))
    return orders


def plan_calls(lanes, orders, pages):
    """search.list calls the run can make at most: lanes x orders x pages."""
    return len(lanes) * len(orders) * max(pages, 0)


def quota_problem(need, ledger):
    """None when the run fits under today's search cap, else the refusal text."""
    used = ledger.get("searches", 0)
    if used + need > DAILY_SEARCHES:
        return ("This run needs %d searches; %d of %d used today, so it would "
                "end at %d. Drop lanes, orders, or pages." %
                (need, used, DAILY_SEARCHES, used + need))
    return None


def plan_text(lanes, orders, pages, need, ledger):
    return ("plan: %d lane(s) x %d order(s) x %d page(s) = %d search call(s); "
            "today %d of %d searches, %d of %d units used; after: %d searches"
            % (len(lanes), len(orders), pages, need,
               ledger.get("searches", 0), DAILY_SEARCHES,
               ledger.get("units", 0), DAILY_UNITS,
               ledger.get("searches", 0) + need))


# ---------------------------------------------------------------- gather

def gather(api, lanes, orders, pages, months, lang, region, search_fn=None, log=None):
    """Run every lane under every order and return {video_id: (lane, order)}.

    The first lane and order that surfaces a video keeps it. Every search.list
    call goes through ytlib.search, which charges cost_searches=1 per page.
    """
    search_fn = search_fn or ytlib.search
    found = {}
    for lane in lanes:
        for order in orders:
            ids = search_fn(api, lane, months, pages, lang, region, order)
            fresh = 0
            for vid in ids:
                if vid not in found:
                    found[vid] = (lane, order)
                    fresh += 1
            if log:
                log("lane %r order %s: %d results, %d not seen before"
                    % (lane, order, len(ids), fresh))
    return found


# ---------------------------------------------------------------- filter

def passes_length(row, min_length, fmt):
    """True when the row clears --min-length and, for long format, is not a Short."""
    if fmt == "long" and is_short(row["seconds"]):
        return False
    return row["seconds"] >= min_length


def hunt_rank(videos, channels, args, seen):
    """ytlib.rank (the `search` filter set) plus --min-length."""
    rows = rank(videos, channels, args, seen)
    min_length = getattr(args, "min_length", 0) or 0
    return [r for r in rows if passes_length(r, min_length, args.format)]


# ---------------------------------------------------------------- output

def markdown(rows, args, lanes, orders, ledger):
    """The `search` table with one extra column, Found by: the order that found the row."""
    lines = [
        "# Outliers (hunt), pulled %s via YouTube Data API v3" % dt.date.today().isoformat(),
        "",
        "Filters: views >= %s, subs < %s, ratio >= %s, format %s, window %d months, min length %ds."
        % (fmt_int(args.views_min), fmt_int(args.subs_max), args.ratio_min,
           args.format, args.months, getattr(args, "min_length", 0) or 0),
        "Lanes: " + "; ".join(lanes) + ".",
        "Orders: %s; pages per lane: %d." % (", ".join(orders), args.pages),
        "Quota today: %d of %d searches, %d of %d units."
        % (ledger["searches"], DAILY_SEARCHES, ledger["units"], DAILY_UNITS),
        "",
        "| # | Video | Channel | Published | Views | Subs | Ratio | Length | Lane | Found by | Thumb |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for i, r in enumerate(rows, 1):
        mins = "%d:%02d" % divmod(r["seconds"], 60)
        lines.append("| %d | [%s](https://youtu.be/%s) | %s | %s | %s | %s | %s | %s | %s | %s | ![](%s) |" % (
            i, r["title"].replace("|", "/"), r["id"], r["channelTitle"].replace("|", "/"),
            r["publishedAt"], fmt_int(r["views"]), fmt_int(r["subs"]), r["ratio"],
            mins, r["lane"], r.get("order", ""), thumb(r["id"])))
    if not rows:
        lines.append("| | no video passed every filter | | | | | | | | | |")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- seed watch

def seed_watch_merge(existing, rows, today):
    """Merge passing rows into a watch list, one entry per channel id.

    Returns (merged list, count added). Entries already present keep their
    original title, subs, added date, and lane.
    """
    merged = []
    by_id = {}
    for entry in existing or []:
        if isinstance(entry, dict) and entry.get("id") and entry["id"] not in by_id:
            by_id[entry["id"]] = entry
            merged.append(entry)
    added = 0
    for r in rows:
        cid = r["channelId"]
        if cid in by_id:
            continue
        entry = {
            "id": cid,
            "title": r["channelTitle"],
            "subs": int(r["subs"]),
            "added": today,
            "from": r.get("lane", ""),
        }
        by_id[cid] = entry
        merged.append(entry)
        added += 1
    return merged, added


def seed_watch_write(path, rows, today=None):
    """Append passing rows' channels to the JSON list at path. Creates it if missing."""
    today = today or dt.date.today().isoformat()
    existing = []
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            text = f.read().strip()
        if text:
            existing = json.loads(text)
            if not isinstance(existing, list):
                raise ValueError("%s is not a JSON list" % path)
    merged, added = seed_watch_merge(existing, rows, today)
    folder = os.path.dirname(os.path.abspath(path))
    os.makedirs(folder, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(merged, f, indent=2)
        f.write("\n")
    return merged, added


# ---------------------------------------------------------------- command

def run(api, args, log=None):
    """Plan, refuse if over quota, gather, filter, write. Returns the rows."""
    log = log or (lambda msg: print(msg, file=sys.stderr))
    lanes = list(args.query)
    if args.fresh:
        args.months = FRESH_MONTHS
        orders = ["date"]
    else:
        orders = parse_orders(args.orders)
    seen = read_seen(args.exclude_seen)
    ledger = ledger_read()
    need = plan_calls(lanes, orders, args.pages)
    log(plan_text(lanes, orders, args.pages, need, ledger))
    problem = quota_problem(need, ledger)
    if problem:
        sys.exit(problem)

    found = gather(api, lanes, orders, args.pages, args.months, args.lang, args.region, log=log)
    log("%d distinct video ids across %d lane(s) and %d order(s)" % (len(found), len(lanes), len(orders)))
    videos = fetch_videos(api, list(found))
    for vid, v in videos.items():
        lane, order = found.get(vid, ("", ""))
        v["lane"] = lane
        v["order"] = order
    channels = fetch_channels(api, sorted({v["channelId"] for v in videos.values()}))
    rows = hunt_rank(videos, channels, args, seen)
    log("%d rows passed every filter" % len(rows))
    rows = rows[:args.limit]

    if args.seed_watch:
        _, added = seed_watch_write(args.seed_watch, rows)
        log("seed watch %s: %d channel(s) added" % (args.seed_watch, added))

    text = markdown(rows, args, lanes, orders, ledger_read())
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text)
        log("wrote %s (%d rows)" % (args.out, len(rows)))
    else:
        sys.stdout.write(text)
    return rows


def cmd_hunt(args):
    try:
        parse_orders(args.orders)
    except ValueError as e:
        sys.exit(str(e))
    run(Api(load_key()), args)


def register(sub):
    p = sub.add_parser("hunt", help="search, deeper: several pages and sort orders per lane, deduped")
    p.add_argument("query", nargs="+", help="one search lane per argument")
    p.add_argument("--views-min", type=int, default=100000)
    p.add_argument("--subs-max", type=int, default=100000)
    p.add_argument("--ratio-min", type=float, default=5.0)
    p.add_argument("--months", type=int, default=24, help="publish window")
    p.add_argument("--format", choices=["long", "short", "any"], default="long")
    p.add_argument("--pages", type=int, default=3, help="search pages per lane per order, 1 search call each")
    p.add_argument("--orders", default=",".join(ORDERS),
                   help="comma-separated sort orders to run per lane (default all three)")
    p.add_argument("--fresh", action="store_true", help="30-day window, order date only")
    p.add_argument("--min-length", type=int, default=0, help="drop videos shorter than this many seconds")
    p.add_argument("--lang", default="en")
    p.add_argument("--region", default="US", help="regionCode; empty string for none")
    p.add_argument("--limit", type=int, default=40, help="rows to print")
    p.add_argument("--exclude-seen", default="", help="path to a seen.md whose second column is a video id")
    p.add_argument("--seed-watch", default="",
                   help="JSON list to append passing channels to (id, title, subs, added, from)")
    p.add_argument("--out", default="", help="write markdown here instead of stdout")
    p.set_defaults(func=cmd_hunt)
