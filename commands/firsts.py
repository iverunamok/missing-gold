"""firsts: the oldest uploads of a channel, in the order they were posted.

  firsts <channel id | @handle | channel URL | video id | video URL>...
         [--count 25] [--out FILE] [--max-subs N] [--min-created YYYY]

Zero search calls. Per channel: channels.list with part=snippet,statistics
(1 unit; ytlib.fetch_channels drops publishedAt, so the call is made here),
playlistItems.list through the uploads playlist to its end (1 unit per 50
uploads, at most MAX_PAGES pages), then videos.list for the kept ids
(1 unit per 50). A video id or video URL costs one more videos.list call
unless cache/ already has it.

The uploads playlist comes back newest-first, so the command pages to the
end and keeps the last --count items, then sorts them by publish date.
"""

import datetime as dt
import re
import statistics
import sys

from ytlib import (Api, load_key, fetch_videos, resolve_channel, is_short,
                   thumb, fmt_int, ledger_read)

MAX_PAGES = 20          # playlistItems pages per channel, 50 items each
BREAKOUT_MULT = 10.0    # first upload at this multiple of the early median
EARLY_DAYS = 90         # window for the "uploads in first N days" column
VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
VIDEO_URL = re.compile(r"(?:v=|youtu\.be/|/shorts/|/live/)([A-Za-z0-9_-]{11})")


# ---------------------------------------------------------------- lookups

def video_ref(ref):
    """The video id inside a bare id or a watch URL, else None. A channel id
    (UC...) and an @handle are never video ids."""
    ref = ref.strip()
    if ref.startswith("UC") or ref.startswith("@"):
        return None
    m = VIDEO_URL.search(ref)
    if m:
        return m.group(1)
    if VIDEO_ID.match(ref):
        return ref
    return None


def resolve(api, ref):
    """Channel id for a channel id, @handle, channel URL, video id, or video
    URL. A video costs one videos.list call (cached after the first time)."""
    vid = video_ref(ref)
    if vid:
        v = fetch_videos(api, [vid]).get(vid)
        if not v:
            sys.exit("No video with id %s" % vid)
        return v["channelId"]
    return resolve_channel(api, ref)


def channel_info(api, cid):
    """channels.list with publishedAt kept. 1 unit."""
    body = api.get("channels", {
        "part": "snippet,statistics,contentDetails",
        "id": cid,
    }, cost_units=1)
    items = body.get("items", [])
    if not items:
        sys.exit("No channel with id %s" % cid)
    item = items[0]
    st = item.get("statistics", {})
    uploads = (item.get("contentDetails", {})
               .get("relatedPlaylists", {})
               .get("uploads")) or ("UU" + cid[2:])
    return {
        "id": item["id"],
        "title": item["snippet"]["title"],
        "created": item["snippet"].get("publishedAt", "")[:10],
        "subs": int(st.get("subscriberCount", 0)),
        "hidden_subs": bool(st.get("hiddenSubscriberCount", False)),
        "videoCount": int(st.get("videoCount", 0)),
        "uploads": uploads,
    }


def all_upload_ids(api, playlist, max_pages=MAX_PAGES):
    """Every video id in the uploads playlist, newest-first, 1 unit per page.
    Returns (ids, capped): capped is True when max_pages ran out with a
    nextPageToken still pending, so the tail is not the true beginning."""
    ids, token, pages = [], None, 0
    while True:
        params = {"part": "contentDetails", "playlistId": playlist, "maxResults": 50}
        if token:
            params["pageToken"] = token
        body = api.get("playlistItems", params, cost_units=1)
        pages += 1
        ids.extend(i["contentDetails"]["videoId"] for i in body.get("items", []))
        token = body.get("nextPageToken")
        if not token:
            return ids, False
        if pages >= max_pages:
            return ids, True


# ---------------------------------------------------------------- math

def parse_date(s):
    return dt.date.fromisoformat(s[:10])


def early_rows(videos, count):
    """The oldest `count` videos, chronological, with the per-row math.
    `videos` is any iterable of fetch_videos records."""
    rows = sorted(videos, key=lambda v: (v["publishedAt"], v["id"]))[:count]
    views = [v["views"] for v in rows]
    median = statistics.median(views) if views else 0
    top = max(views) if views else 0
    out, prev = [], None
    for n, v in enumerate(rows, 1):
        d = parse_date(v["publishedAt"])
        out.append({
            "n": n,
            "id": v["id"],
            "title": v["title"],
            "published": v["publishedAt"],
            "gap": (d - prev).days if prev else None,
            "views": v["views"],
            "seconds": v["seconds"],
            "mult": round(v["views"] / median, 1) if median else None,
            "top": bool(views) and v["views"] == top,
        })
        prev = d
    return out, median


def breakout(rows, median, mult=BREAKOUT_MULT):
    """The first row at `mult` times the median, or None."""
    if not median:
        return None
    for r in rows:
        if r["views"] >= mult * median:
            return r
    return None


def uploads_in_window(rows, days=EARLY_DAYS):
    if not rows:
        return 0
    first = parse_date(rows[0]["published"])
    return sum(1 for r in rows if (parse_date(r["published"]) - first).days <= days)


def summary_row(ch, rows, median, truncated):
    """One summary line per channel. `truncated` is True when the channel
    has more uploads than the rows shown, so a 90-day count that fills
    every row is a floor, not a total."""
    b = breakout(rows, median)
    n90 = uploads_in_window(rows)
    n90_text = str(n90)
    if truncated and rows and n90 == len(rows):
        n90_text = "%d+" % n90
    return {
        "channel": ch["title"],
        "id": ch["id"],
        "created": ch["created"],
        "subs": ch["subs"],
        "first_upload": rows[0]["published"] if rows else "",
        "uploads_90d": n90_text,
        "breakout_n": b["n"] if b else None,
        "breakout_title": b["title"] if b else "",
        "breakout_mult": b["mult"] if b else None,
    }


# ---------------------------------------------------------------- output

def esc(s):
    return s.replace("|", "\\|").replace("\n", " ")


def length(seconds):
    h, rest = divmod(seconds, 3600)
    m, s = divmod(rest, 60)
    if h:
        return "%d:%02d:%02d" % (h, m, s)
    return "%d:%02d" % (m, s)


def subs_text(ch):
    return "hidden" if ch["hidden_subs"] else fmt_int(ch["subs"])


def channel_markdown(ch, rows, median, capped):
    lines = []
    lines.append("## %s" % ch["title"])
    lines.append("")
    lines.append("Created %s, %s subs, %s uploads, channel id `%s`." %
                 (ch["created"] or "unknown", subs_text(ch), fmt_int(ch["videoCount"]), ch["id"]))
    if capped:
        lines.append("")
        lines.append("WARNING: the uploads playlist ran past %d pages (%d items); "
                     "these are the oldest of the items read, not of the channel." %
                     (MAX_PAGES, MAX_PAGES * 50))
    lines.append("")
    lines.append("Median views of the first %d uploads: %s. Top-viewed is marked TOP." %
                 (len(rows), fmt_int(median) if median else "n/a"))
    lines.append("")
    lines.append("| # | Title | Published | Gap days | Views | Length | x median | Thumb |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for r in rows:
        mark = " TOP" if r["top"] else ""
        short = " (short)" if is_short(r["seconds"]) else ""
        lines.append("| %d | %s%s | %s | %s | %s | %s%s | %s | [thumb](%s) |" % (
            r["n"], esc(r["title"]), mark, r["published"],
            "-" if r["gap"] is None else r["gap"],
            fmt_int(r["views"]), length(r["seconds"]), short,
            "n/a" if r["mult"] is None else r["mult"],
            thumb(r["id"])))
    lines.append("")
    return "\n".join(lines)


def summary_markdown(summaries, mult=BREAKOUT_MULT):
    lines = []
    lines.append("## Summary")
    lines.append("")
    lines.append("| Channel | Created | Subs | First upload | Uploads in first %d days | First upload at %gx early median | That video |" %
                 (EARLY_DAYS, mult))
    lines.append("|---|---|---|---|---|---|---|")
    for s in summaries:
        n = "none" if s["breakout_n"] is None else "#%d (%sx)" % (s["breakout_n"], s["breakout_mult"])
        lines.append("| %s | %s | %s | %s | %s | %s | %s |" % (
            esc(s["channel"]), s["created"] or "unknown", fmt_int(s["subs"]),
            s["first_upload"], s["uploads_90d"], n, esc(s["breakout_title"])))
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------- command

def log(msg):
    sys.stderr.write(msg + "\n")


def cmd_firsts(args):
    api = Api(load_key())
    before = ledger_read()["units"]
    parts, summaries, skipped = [], [], []
    for ref in args.channel:
        cid = resolve(api, ref)
        ch = channel_info(api, cid)
        reason = skip_reason(ch, args.max_subs, args.min_created)
        if reason:
            skipped.append("%s (%s): %s" % (ch["title"], cid, reason))
            log("skip %s: %s" % (ch["title"], reason))
            continue
        ids, capped = all_upload_ids(api, ch["uploads"])
        oldest = ids[-args.count:]
        vids = fetch_videos(api, oldest)
        rows, median = early_rows([vids[i] for i in oldest if i in vids], args.count)
        truncated = len(ids) > len(rows)
        parts.append(channel_markdown(ch, rows, median, capped))
        summaries.append(summary_row(ch, rows, median, truncated))
        log("%s: %d uploads read, %d shown" % (ch["title"], len(ids), len(rows)))
    spent = ledger_read()["units"] - before
    head = ["# First uploads, pulled %s" % dt.date.today().isoformat(), ""]
    if skipped:
        head.append("Skipped:")
        head.extend("- %s" % s for s in skipped)
        head.append("")
    text = "\n".join(head) + "\n".join(parts) + "\n" + summary_markdown(summaries)
    text += "\nUnits spent: %d.\n" % spent
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text)
        log("wrote %s (%d channels, %d skipped, %d units)" % (args.out, len(parts), len(skipped), spent))
    else:
        sys.stdout.write(text)


def skip_reason(ch, max_subs, min_created):
    if max_subs and not ch["hidden_subs"] and ch["subs"] > max_subs:
        return "%s subs is over %s" % (fmt_int(ch["subs"]), fmt_int(max_subs))
    if min_created and ch["created"] and ch["created"][:4] < str(min_created):
        return "created %s, before %s" % (ch["created"], min_created)
    return ""


def register(sub):
    p = sub.add_parser("firsts", help="the oldest uploads of a channel, in posting order")
    p.add_argument("channel", nargs="+",
                   help="channel id, @handle, channel URL, video id, or video URL")
    p.add_argument("--count", type=int, default=25, help="oldest uploads to show per channel")
    p.add_argument("--max-subs", type=int, default=0, help="skip channels over this many subs (0 = no limit)")
    p.add_argument("--min-created", default="", help="skip channels created before this year, for example 2019")
    p.add_argument("--out", default="", help="write markdown here instead of stdout")
    p.set_defaults(func=cmd_firsts)
