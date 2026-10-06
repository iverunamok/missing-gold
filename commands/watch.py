"""watch: a channel watch list. Replaces TubeLab's "find small channels in a
niche and watch what they post".

  watch add <id | @handle | channel URL | video URL> [--note TEXT]
  watch list
  watch remove <id>
  watch scan [--days 14] [--mult 3.0] [--subs-mult 5.0] [--rss] [--out FILE]

Storage is watch.json next to ytlib.py: a list of
{"id", "title", "subs", "added", "from", "median", "scanned"}. Another
command (hunt --seed-watch) writes the first four fields only, so every
reader here tolerates missing fields.

`scan` spends zero search calls. With the key it reads each channel's
uploads playlist (1 unit per channel), then videos.list in batches of 50
(1 unit each), then channels.list in batches of 50 (1 unit each). With
`--rss` it reads the public Atom feed instead (no key, no quota) and calls
videos.list only for ids missing from cache/.
"""

import argparse
import datetime as dt
import json
import os
import re
import statistics
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

import ytlib
from ytlib import (Api, load_key, fetch_videos, fetch_channels, ledger_read,
                   resolve_channel, recent_uploads, is_short, thumb, fmt_int,
                   cache_put)

WATCH = os.path.join(ytlib.HOME, "watch.json")
FEED = "https://www.youtube.com/feeds/videos.xml?channel_id="
ATOM = "{http://www.w3.org/2005/Atom}"
YT = "{http://www.youtube.com/xml/schemas/2015}"
FIELDS = ("id", "title", "subs", "added", "from", "median", "scanned")
SETTLE_DAYS = 3        # an upload younger than this is too fresh for the median
MEDIAN_WINDOW = 10     # long-form uploads the median is taken over
UPLOADS_PER_CHANNEL = 15


# ---------------------------------------------------------------- storage

def normalize(row):
    """Fill the fields another writer may have left out. Never raises on a
    row that has at least an id."""
    out = {
        "id": str(row.get("id", "")).strip(),
        "title": row.get("title") or "",
        "subs": int(row.get("subs") or 0),
        "added": row.get("added") or dt.date.today().isoformat(),
        "from": row.get("from") or "",
        "median": row.get("median") if row.get("median") is not None else None,
        "scanned": row.get("scanned") or None,
    }
    if out["median"] is not None:
        out["median"] = int(out["median"])
    return out


def merge(rows):
    """Normalize and dedupe by id. The first row wins; later duplicates fill
    only the fields the first left empty."""
    seen = {}
    order = []
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        row = normalize(raw)
        if not row["id"]:
            continue
        if row["id"] in seen:
            kept = seen[row["id"]]
            for k in FIELDS:
                if kept.get(k) in ("", 0, None) and row.get(k) not in ("", 0, None):
                    kept[k] = row[k]
            continue
        seen[row["id"]] = row
        order.append(row["id"])
    return [seen[i] for i in order]


def load_watch(path=WATCH):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict):
        data = data.get("channels", [])
    return merge(data)


def save_watch(rows, path=WATCH):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(merge(rows), f, indent=2)
        f.write("\n")


# ---------------------------------------------------------------- RSS (no key)

def parse_feed(text):
    """Atom feed text -> [{"id", "title", "published"}], newest first as the
    feed lists them. An empty or non-feed body gives []."""
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return []
    out = []
    for entry in root.iter(ATOM + "entry"):
        vid = entry.findtext(YT + "videoId")
        if not vid:
            continue
        out.append({
            "id": vid,
            "title": entry.findtext(ATOM + "title") or "",
            "published": (entry.findtext(ATOM + "published") or "")[:10],
        })
    return out


def fetch_feed(channel_id, opener=None):
    """The channel's public uploads feed, 15 newest videos. A 404 (no public
    uploads, or a terminated channel) gives []."""
    opener = opener or urllib.request.urlopen
    try:
        with opener(FEED + channel_id, timeout=30) as r:
            text = r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return []
        raise
    except (urllib.error.URLError, OSError) as e:
        print("  feed unreachable: %s" % getattr(e, "reason", e), file=sys.stderr)
        return []
    return parse_feed(text)


# ---------------------------------------------------------------- the math

def days_old(published, today):
    try:
        return (today - dt.date.fromisoformat(published[:10])).days
    except (TypeError, ValueError):
        return 10 ** 6


def channel_median(videos, today, settle_days=SETTLE_DAYS, window=MEDIAN_WINDOW):
    """Median views over the channel's newest `window` long-form uploads that
    are older than `settle_days`. None when fewer than 3 qualify."""
    pool = [v for v in videos
            if not is_short(v.get("seconds", 0)) and days_old(v.get("publishedAt", ""), today) > settle_days]
    pool.sort(key=lambda v: v.get("publishedAt", ""), reverse=True)
    pool = pool[:window]
    if len(pool) < 3:
        return None
    return int(statistics.median([v["views"] for v in pool]))


def flag(videos, median, subs, today, days, mult, subs_mult):
    """Uploads in the last `days` days whose views reach `mult` x the channel
    median or `subs_mult` x subscribers."""
    out = []
    for v in videos:
        if days_old(v.get("publishedAt", ""), today) > days:
            continue
        views = v.get("views", 0)
        x_med = round(views / median, 1) if median else None
        rt = ytlib.ratio(views, subs)
        hit_median = median is not None and views >= mult * median
        hit_subs = views >= subs_mult * max(subs, 100)
        if hit_median or hit_subs:
            row = dict(v)
            row["x_median"] = x_med
            row["ratio"] = rt
            row["subs"] = subs
            out.append(row)
    return out


def compute(rows, channels, uploads, today, days=14, mult=3.0, subs_mult=5.0):
    """Pure core of `scan`. rows: watch.json rows. channels: id -> channel
    record. uploads: channel id -> [video records]. Returns (flags, rows)
    where rows carry the fresh median, subs, title, and scan date."""
    flags = []
    updated = []
    for row in rows:
        row = dict(row)
        ch = channels.get(row["id"], {})
        if ch.get("title"):
            row["title"] = ch["title"]
        if ch.get("subs") is not None:
            row["subs"] = int(ch["subs"])
        vids = uploads.get(row["id"], [])
        median = channel_median(vids, today)
        row["median"] = median
        row["scanned"] = today.isoformat()
        for f in flag(vids, median, row["subs"], today, days, mult, subs_mult):
            f["channelTitle"] = row["title"] or f.get("channelTitle", "")
            f["channelId"] = row["id"]
            flags.append(f)
        updated.append(row)
    flags.sort(key=lambda f: (f["x_median"] is None, -(f["x_median"] or 0), -f["ratio"]))
    return flags, updated


# ---------------------------------------------------------------- fetching

def fetch_videos_fresh(api, ids):
    """videos.list for every id, cache or not, so view counts are today's.
    1 unit per batch of 50."""
    out = {}
    ids = list(dict.fromkeys(ids))
    for i in range(0, len(ids), 50):
        batch = ids[i:i + 50]
        body = api.get("videos", {
            "part": "snippet,statistics,contentDetails",
            "id": ",".join(batch),
        }, cost_units=1)
        for item in body.get("items", []):
            rec = {
                "id": item["id"],
                "title": item["snippet"]["title"],
                "channelId": item["snippet"]["channelId"],
                "channelTitle": item["snippet"]["channelTitle"],
                "publishedAt": item["snippet"]["publishedAt"][:10],
                "views": int(item["statistics"].get("viewCount", 0)),
                "seconds": ytlib.parse_duration(item["contentDetails"].get("duration")),
                "fetched": dt.date.today().isoformat(),
            }
            cache_put("video", rec["id"], rec)
            out[rec["id"]] = rec
    return out


def gather(api, rows, rss=False, feed=None, want=UPLOADS_PER_CHANNEL):
    """Channel records and the newest uploads for every watched channel.
    Returns (channels, uploads) in the shapes `compute` takes."""
    feed = feed or fetch_feed
    ids = [r["id"] for r in rows]
    channels = fetch_channels(api, ids)
    per_channel = {}
    for cid in ids:
        if rss:
            per_channel[cid] = [e["id"] for e in feed(cid)][:want]
        else:
            ch = channels.get(cid)
            if not ch or not ch.get("uploads"):
                per_channel[cid] = []
                continue
            per_channel[cid] = recent_uploads(api, ch["uploads"], want=want)
    all_ids = [v for vids in per_channel.values() for v in vids]
    if rss:
        videos = fetch_videos(api, all_ids)        # cache first, API for the rest
    else:
        videos = fetch_videos(api, all_ids, fresh=True)  # today's views for every id
    uploads = {cid: [videos[v] for v in vids if v in videos] for cid, vids in per_channel.items()}
    return channels, uploads


# ---------------------------------------------------------------- report

def report(flags, rows, today, days, mult, subs_mult, spent, rss):
    lines = []
    lines.append("# Watch scan, %s" % today.isoformat())
    lines.append("")
    lines.append("%d channels, uploads in the last %d days, flagged at %sx the channel median "
                 "(10 long-form uploads older than %d days) or %sx subscribers. Source: %s." % (
                     len(rows), days, mult, SETTLE_DAYS, subs_mult, "RSS feed" if rss else "uploads playlist"))
    lines.append("")
    if flags:
        lines.append("| # | Channel | Video | Published | Views | Subs | x median | Ratio | Thumb |")
        lines.append("|---|---|---|---|---|---|---|---|---|")
        for i, f in enumerate(flags, 1):
            lines.append("| %d | %s | [%s](https://youtu.be/%s) | %s | %s | %s | %s | %s | ![](%s) |" % (
                i, f["channelTitle"].replace("|", "/"), f["title"].replace("|", "/"), f["id"],
                f["publishedAt"], fmt_int(f["views"]), fmt_int(f["subs"]),
                f["x_median"] if f["x_median"] is not None else "n/a", f["ratio"], thumb(f["id"])))
    else:
        lines.append("No upload cleared the bar.")
    lines.append("")
    lines.append("| Channel | Subs | Median | Scanned |")
    lines.append("|---|---|---|---|")
    for r in rows:
        lines.append("| %s | %s | %s | %s |" % (
            r["title"] or r["id"], fmt_int(r["subs"]),
            fmt_int(r["median"]) if r["median"] is not None else "n/a", r["scanned"] or ""))
    lines.append("")
    lines.append("Budget: %d units and %d search calls this scan; %d of %d units used today." % (
        spent["units"], spent["searches"], spent["after"]["units"], ytlib.DAILY_UNITS))
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- commands

VIDEO_URL = re.compile(r"(?:youtu\.be/|[?&]v=|/shorts/|/live/)([A-Za-z0-9_-]{11})")


def resolve_any(api, ref):
    """Channel id, @handle, channel URL, or a video URL (resolved through
    videos.list, 1 unit, then the video's channelId)."""
    m = VIDEO_URL.search(ref)
    if m and "UC" not in ref:
        vid = m.group(1)
        v = fetch_videos(api, [vid]).get(vid)
        if not v:
            sys.exit("No video with id %s" % vid)
        return v["channelId"]
    return resolve_channel(api, ref)


def cmd_add(args):
    api = Api(load_key())
    rows = load_watch(args.path)
    cid = resolve_any(api, args.channel)
    if any(r["id"] == cid for r in rows):
        r = next(r for r in rows if r["id"] == cid)
        print("Already watching %s (%s), added %s." % (r["title"] or cid, cid, r["added"]))
        return
    ch = fetch_channels(api, [cid]).get(cid)
    if not ch:
        sys.exit("No channel with id %s" % cid)
    rows.append(normalize({
        "id": cid, "title": ch["title"], "subs": ch["subs"],
        "added": dt.date.today().isoformat(), "from": args.note,
    }))
    save_watch(rows, args.path)
    print("Added %s (%s), %s subs. %d channels watched." % (ch["title"], cid, fmt_int(ch["subs"]), len(rows)))


def cmd_list(args):
    rows = load_watch(args.path)
    if not rows:
        print("Nothing watched. Add one with: watch add @handle")
        return
    print("| # | Channel | Id | Subs | Median | Added | From | Scanned |")
    print("|---|---|---|---|---|---|---|---|")
    for i, r in enumerate(rows, 1):
        print("| %d | %s | %s | %s | %s | %s | %s | %s |" % (
            i, (r["title"] or "").replace("|", "/"), r["id"], fmt_int(r["subs"]),
            fmt_int(r["median"]) if r["median"] is not None else "n/a",
            r["added"], (r["from"] or "").replace("|", "/"), r["scanned"] or ""))


def cmd_remove(args):
    rows = load_watch(args.path)
    m = re.search(r"(UC[A-Za-z0-9_-]{22})", args.channel)
    cid = m.group(1) if m else args.channel.strip()
    kept = [r for r in rows if r["id"] != cid]
    if len(kept) == len(rows):
        sys.exit("%s is not in the watch list." % cid)
    save_watch(kept, args.path)
    print("Removed %s. %d channels watched." % (cid, len(kept)))


def cmd_scan(args):
    rows = load_watch(args.path)
    if not rows:
        sys.exit("Nothing watched. Add one with: watch add @handle")
    api = Api(load_key())
    before = ledger_read()
    today = dt.date.today()
    channels, uploads = gather(api, rows, rss=args.rss)
    flags, rows = compute(rows, channels, uploads, today, args.days, args.mult, args.subs_mult)
    save_watch(rows, args.path)
    after = ledger_read()
    spent = {
        "units": after["units"] - before["units"],
        "searches": after["searches"] - before["searches"],
        "after": after,
    }
    text = report(flags, rows, today, args.days, args.mult, args.subs_mult, spent, args.rss)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text)
        print("Wrote %d flagged uploads to %s" % (len(flags), args.out))
        print(text.strip().splitlines()[-1])
    else:
        print(text, end="")


def register(sub):
    p = sub.add_parser("watch", help="a channel watch list: add, list, remove, scan")
    p.add_argument("--path", default=WATCH, help=argparse.SUPPRESS)
    ps = p.add_subparsers(dest="watch_cmd", required=True)

    a = ps.add_parser("add", help="watch a channel (id, @handle, channel URL, or video URL)")
    a.add_argument("channel")
    a.add_argument("--note", default="", help="lane or note, stored in the from field")
    a.set_defaults(func=cmd_add)

    ls = ps.add_parser("list", help="the watch list as a markdown table")
    ls.set_defaults(func=cmd_list)

    rm = ps.add_parser("remove", help="stop watching a channel id")
    rm.add_argument("channel")
    rm.set_defaults(func=cmd_remove)

    sc = ps.add_parser("scan", help="flag recent uploads that beat each channel's median; 0 search calls")
    sc.add_argument("--days", type=int, default=14, help="how far back an upload counts as recent")
    sc.add_argument("--mult", type=float, default=3.0, help="flag at this many times the channel median")
    sc.add_argument("--subs-mult", type=float, default=5.0, help="or at this many times subscribers")
    sc.add_argument("--rss", action="store_true", help="discover uploads from the public Atom feed, no quota")
    sc.add_argument("--out", default="", help="write the markdown report here instead of stdout")
    sc.set_defaults(func=cmd_scan)
