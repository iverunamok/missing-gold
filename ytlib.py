"""Core library for Missing Gold: API client, cache, quota ledger, lookups,
ranking, and the built-in commands. The CLI is outliers.py; the method is
docs/method.md.

Key: put YOUTUBE_API_KEY in the environment or in .env next to this file.
Set OUTLIERS_HOME to keep cache/, quota.json, seen.md, and tokens elsewhere.
"""

import datetime as dt
import json
import socket
import os
import re
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
HOME = os.environ.get("OUTLIERS_HOME") or HERE   # where state files live
API = "https://www.googleapis.com/youtube/v3/"
CACHE = os.path.join(HOME, "cache")
LEDGER = os.path.join(HOME, "quota.json")
SHORT_SECONDS = 180  # Shorts are 3 minutes or under since October 2024.
DAILY_SEARCHES = 100
DAILY_UNITS = 10000


# ---------------------------------------------------------------- key, quota

def load_key():
    key = os.environ.get("YOUTUBE_API_KEY")
    if not key:
        env = os.path.join(HERE, ".env")
        if os.path.exists(env):
            with open(env, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("export "):
                        line = line[7:].strip()
                    if line.startswith("YOUTUBE_API_KEY="):
                        key = line.split("=", 1)[1].strip().strip('"').strip("'")
    if not key:
        sys.exit(
            "No API key. Put YOUTUBE_API_KEY=... in .env next to outliers.py "
            "or export it. README.md says how to get one."
        )
    return key


def pacific_day(now=None):
    """The quota day: YouTube resets at midnight Pacific, daylight saving included."""
    now = now or dt.datetime.now(dt.timezone.utc)
    try:
        from zoneinfo import ZoneInfo
        local = now.astimezone(ZoneInfo("America/Los_Angeles"))
    except Exception:  # no tz database (Windows without tzdata): fixed offset
        local = now.astimezone(dt.timezone(dt.timedelta(hours=-8)))
    return local.strftime("%Y-%m-%d")


def ledger_read():
    try:
        with open(LEDGER, encoding="utf-8") as f:
            data = json.load(f)
        if data.get("day") == pacific_day():
            return data
    except (OSError, ValueError):   # missing, or another process mid-write
        pass
    return {"day": pacific_day(), "searches": 0, "units": 0}


def ledger_add(searches=0, units=0):
    """Read, add, write via a temp file and os.replace so a concurrent reader
    never sees a half-written ledger. Two writers can still lose an increment;
    the count is a guard, not an audit."""
    data = ledger_read()
    data["searches"] += searches
    data["units"] += units
    os.makedirs(os.path.dirname(LEDGER), exist_ok=True)
    tmp = LEDGER + ".%d.tmp" % os.getpid()
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, LEDGER)
    return data


# ---------------------------------------------------------------- HTTP

class Api:
    def __init__(self, key):
        self.key = key

    def get(self, endpoint, params, cost_units=0, cost_searches=0):
        params = dict(params, key=self.key)
        url = API + endpoint + "?" + urllib.parse.urlencode(params, doseq=True)
        try:
            with urllib.request.urlopen(url, timeout=30) as r:
                body = json.load(r)
        except urllib.error.HTTPError as e:
            text = e.read().decode("utf-8", "replace")
            if e.code == 403 and "quota" in text.lower():
                sys.exit(
                    "Quota exhausted for today (resets midnight Pacific). "
                    "Ledger: %s" % json.dumps(ledger_read())
                )
            if e.code == 400 and "API key not valid" in text:
                sys.exit("The API key was rejected. Check .env and that the "
                         "YouTube Data API v3 is enabled on the project.")
            sys.exit("HTTP %s on %s: %s" % (e.code, endpoint, text[:400]))
        except (urllib.error.URLError, socket.timeout, OSError) as e:
            sys.exit("Network error on %s: %s" % (endpoint, getattr(e, "reason", e)))
        ledger_add(searches=cost_searches, units=cost_units)
        return body

    def paged(self, endpoint, params, limit, cost_units=1):
        """Collect items across pages until `limit` items or no next page."""
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


# ---------------------------------------------------------------- cache

def cache_get(kind, key):
    path = os.path.join(CACHE, kind, key + ".json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return None


def cache_put(kind, key, value):
    os.makedirs(os.path.join(CACHE, kind), exist_ok=True)
    with open(os.path.join(CACHE, kind, key + ".json"), "w") as f:
        json.dump(value, f)


# ---------------------------------------------------------------- helpers

def parse_duration(iso):
    """PT1H2M3S -> seconds. Returns 0 for anything unparseable."""
    m = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?$", iso or "")
    if not m:
        return 0
    h, mi, s = (int(x) if x else 0 for x in m.groups())
    return h * 3600 + mi * 60 + s


def is_short(seconds):
    return 0 < seconds <= SHORT_SECONDS


def ratio(views, subs):
    """Views per subscriber, one decimal. Channels under 100 subs count as 100."""
    return round(views / max(subs, 100), 1)


def fmt_int(n):
    return "{:,}".format(int(n))


def thumb(video_id):
    return "https://i.ytimg.com/vi/%s/mqdefault.jpg" % video_id


def read_seen(path):
    """Video ids from a markdown table whose second column is the id."""
    ids = set()
    if not path or not os.path.exists(path):
        return ids
    for line in open(path, encoding="utf-8"):
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) >= 2 and re.fullmatch(r"[A-Za-z0-9_-]{11}", cells[1]):
            ids.add(cells[1])
    return ids


# ---------------------------------------------------------------- lookups

def fetch_videos(api, ids, fresh=False, max_age_days=1):
    """videos.list in batches of 50. 1 unit per batch. A cached record is
    reused for `max_age_days`; pass fresh=True to re-read every id."""
    out = {}
    missing = []
    today = dt.date.today()
    for vid in ids:
        c = None if fresh else cache_get("video", vid)
        if c:
            try:
                age = (today - dt.date.fromisoformat(c.get("fetched", "1970-01-01"))).days
            except ValueError:
                age = 10 ** 6
            if age > max_age_days:
                c = None
        if c:
            out[vid] = c
        else:
            missing.append(vid)
    for i in range(0, len(missing), 50):
        batch = missing[i:i + 50]
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
                "seconds": parse_duration(item["contentDetails"].get("duration")),
                "fetched": dt.date.today().isoformat(),
            }
            cache_put("video", rec["id"], rec)
            out[rec["id"]] = rec
    return out


def fetch_channels(api, ids):
    """channels.list in batches of 50. 1 unit per batch."""
    out = {}
    missing = []
    for cid in ids:
        c = cache_get("channel", cid)
        if c and c.get("fetched") == dt.date.today().isoformat():
            out[cid] = c
        else:
            missing.append(cid)
    for i in range(0, len(missing), 50):
        batch = missing[i:i + 50]
        body = api.get("channels", {
            "part": "snippet,statistics,contentDetails",
            "id": ",".join(batch),
        }, cost_units=1)
        for item in body.get("items", []):
            st = item["statistics"]
            rec = {
                "id": item["id"],
                "title": item["snippet"]["title"],
                "subs": int(st.get("subscriberCount", 0)),
                "hidden_subs": st.get("hiddenSubscriberCount", False),
                "videoCount": int(st.get("videoCount", 0)),
                "uploads": item["contentDetails"]["relatedPlaylists"]["uploads"],
                "fetched": dt.date.today().isoformat(),
            }
            cache_put("channel", rec["id"], rec)
            out[rec["id"]] = rec
    return out


# ---------------------------------------------------------------- search

def search(api, query, months, pages, lang, region, order):
    since = (dt.datetime.utcnow() - dt.timedelta(days=30 * months))
    params = {
        "part": "snippet",
        "q": query,
        "type": "video",
        "order": order,
        "publishedAfter": since.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "relevanceLanguage": lang,
        "maxResults": 50,
    }
    if region:
        params["regionCode"] = region
    ids, token = [], None
    for _ in range(pages):
        p = dict(params)
        if token:
            p["pageToken"] = token
        body = api.get("search", p, cost_searches=1)
        ids.extend(i["id"]["videoId"] for i in body.get("items", [])
                   if i.get("id", {}).get("videoId"))
        token = body.get("nextPageToken")
        if not token:
            break
    return ids


def rank(videos, channels, args, seen):
    rows = []
    for v in videos.values():
        ch = channels.get(v["channelId"])
        if not ch:
            continue
        if v["id"] in seen:
            continue
        if args.format == "long" and is_short(v["seconds"]):
            continue
        if args.format == "short" and not is_short(v["seconds"]):
            continue
        if v["views"] < args.views_min:
            continue
        if ch["subs"] >= args.subs_max or ch["hidden_subs"]:
            continue
        r = ratio(v["views"], ch["subs"])
        if r < args.ratio_min:
            continue
        rows.append(dict(v, subs=ch["subs"], ratio=r, lane=v.get("lane", "")))
    rows.sort(key=lambda x: -x["ratio"])
    return rows


def markdown(rows, args, lanes, ledger):
    lines = [
        "# Outliers, pulled %s via YouTube Data API v3" % dt.date.today().isoformat(),
        "",
        "Filters: views >= %s, subs < %s, ratio >= %s, format %s, window %d months."
        % (fmt_int(args.views_min), fmt_int(args.subs_max), args.ratio_min,
           args.format, args.months),
        "Lanes: " + "; ".join(lanes) + ".",
        "Quota today: %d of %d searches, %d of %d units."
        % (ledger["searches"], DAILY_SEARCHES, ledger["units"], DAILY_UNITS),
        "",
        "| # | Video | Channel | Published | Views | Subs | Ratio | Length | Lane | Thumb |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for i, r in enumerate(rows, 1):
        mins = "%d:%02d" % divmod(r["seconds"], 60)
        lines.append("| %d | [%s](https://youtu.be/%s) | %s | %s | %s | %s | %s | %s | %s | ![](%s) |" % (
            i, r["title"].replace("|", "/"), r["id"], r["channelTitle"].replace("|", "/"),
            r["publishedAt"], fmt_int(r["views"]), fmt_int(r["subs"]), r["ratio"],
            mins, r["lane"], thumb(r["id"])))
    if not rows:
        lines.append("| | no video passed every filter | | | | | | | | |")
    return "\n".join(lines) + "\n"


def cmd_search(args):
    api = Api(load_key())
    seen = read_seen(args.exclude_seen)
    ledger = ledger_read()
    need = len(args.query) * args.pages
    if ledger["searches"] + need > DAILY_SEARCHES:
        sys.exit("This run needs %d searches; %d of %d used today." %
                 (need, ledger["searches"], DAILY_SEARCHES))
    all_ids, lane_of = [], {}
    for q in args.query:
        ids = search(api, q, args.months, args.pages, args.lang, args.region, args.order)
        print("lane %r: %d results" % (q, len(ids)), file=sys.stderr)
        for vid in ids:
            lane_of.setdefault(vid, q)
        all_ids.extend(ids)
    all_ids = list(dict.fromkeys(all_ids))
    videos = fetch_videos(api, all_ids)
    for vid, v in videos.items():
        v["lane"] = lane_of.get(vid, "")
    channels = fetch_channels(api, sorted({v["channelId"] for v in videos.values()}))
    rows = rank(videos, channels, args, seen)[:args.limit]
    text = markdown(rows, args, args.query, ledger_read())
    if args.out:
        open(args.out, "w", encoding="utf-8").write(text)
        print("wrote %s (%d rows)" % (args.out, len(rows)), file=sys.stderr)
    else:
        sys.stdout.write(text)


# ---------------------------------------------------------------- check

def recent_uploads(api, uploads_playlist, want=12):
    """Newest uploads from the channel's uploads playlist, 1 unit per 50."""
    items = api.paged("playlistItems", {
        "part": "contentDetails",
        "playlistId": uploads_playlist,
    }, limit=want)
    return [i["contentDetails"]["videoId"] for i in items]


def cmd_check(args):
    api = Api(load_key())
    v = fetch_videos(api, [args.video_id]).get(args.video_id)
    if not v:
        sys.exit("No video with id %s" % args.video_id)
    ch = fetch_channels(api, [v["channelId"]]).get(v["channelId"])
    if not ch:
        sys.exit("The channel behind %s no longer exists (%s)" % (args.video_id, v["channelTitle"]))
    ids = recent_uploads(api, ch["uploads"], want=args.window + 2)
    others = fetch_videos(api, [i for i in ids if i != args.video_id])
    same_format = [o for o in others.values()
                   if is_short(o["seconds"]) == is_short(v["seconds"])][:args.window]
    views = [o["views"] for o in same_format]
    print("Video:    %s" % v["title"])
    print("Channel:  %s (%s subs, %s uploads)" % (ch["title"], fmt_int(ch["subs"]), ch["videoCount"]))
    print("Views:    %s, published %s, %d:%02d" % (fmt_int(v["views"]), v["publishedAt"], *divmod(v["seconds"], 60)))
    print("Ratio to subs: %s" % ratio(v["views"], ch["subs"]))
    if len(views) >= 3:
        med = statistics.median(views)
        print("Median of %d recent same-format uploads: %s" % (len(views), fmt_int(med)))
        print("Ratio to channel median: %s" % round(v["views"] / max(med, 1), 1))
    else:
        print("Fewer than 3 other same-format uploads; no median.")
    print("Thumb:    %s" % thumb(v["id"]))


def cmd_quota(args):
    data = ledger_read()
    print("%s (Pacific): %d of %d searches, %d of %d units" %
          (data["day"], data["searches"], DAILY_SEARCHES, data["units"], DAILY_UNITS))


# ---------------------------------------------------------------- suggest (no key)

SUGGEST = "https://suggestqueries.google.com/complete/search"


def suggestions(seed, lang="en", region="us"):
    """YouTube's own autocomplete for a seed phrase. Free, unofficial, no key."""
    q = urllib.parse.urlencode({"client": "firefox", "ds": "yt", "hl": lang,
                                "gl": region, "q": seed})
    req = urllib.request.Request(SUGGEST + "?" + q, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as r:
        body = json.loads(r.read().decode("utf-8", "replace"))
    return body[1] if len(body) > 1 else []


def cmd_suggest(args):
    """Expand each seed into lanes: the seed, then seed + each letter a..z and
    the question starters, deduplicated, in the order YouTube returns them."""
    seen, rows = set(), []
    for seed in args.seed:
        probes = [seed] + ["%s %s" % (seed, c) for c in "abcdefghijklmnopqrstuvwxyz"]
        probes += ["how to %s" % seed, "best %s" % seed, "%s for beginners" % seed,
                   "%s vs" % seed, "why %s" % seed]
        if not args.deep:
            probes = probes[:1] + probes[27:]
        for probe in probes:
            try:
                for sug in suggestions(probe, args.lang, args.region):
                    if sug not in seen:
                        seen.add(sug)
                        rows.append((seed, probe, sug))
            except (urllib.error.URLError, ValueError) as e:
                print("probe %r failed: %s" % (probe, e), file=sys.stderr)
            time.sleep(0.2)
    print("| Seed | Probe | Suggestion |")
    print("|---|---|---|")
    for seed, probe, sug in rows:
        print("| %s | %s | %s |" % (seed, probe, sug))
    print("\n%d suggestions, pulled %s from YouTube autocomplete (no search volume; order is YouTube's)."
          % (len(rows), dt.date.today().isoformat()), file=sys.stderr)


# ---------------------------------------------------------------- thumbs (no key)

def cmd_thumbs(args):
    """Download mqdefault thumbnails for every video id found in a markdown
    file (or given directly) and tile them into one contact sheet."""
    try:
        from PIL import Image, ImageDraw  # optional dependency, only here
    except ImportError:
        sys.exit("thumbs needs Pillow. Run: python3 -m pip install --user pillow")
    ids = list(dict.fromkeys(args.ids))
    if args.file:
        text = open(args.file, encoding="utf-8").read()
        for m in re.finditer(r"(?:youtu\.be/|watch\?v=|/vi/)([A-Za-z0-9_-]{11})", text):
            if m.group(1) not in ids:
                ids.append(m.group(1))
    if not ids:
        sys.exit("No video ids found.")
    folder = os.path.join(CACHE, "thumbs")
    os.makedirs(folder, exist_ok=True)
    tiles = []
    for vid in ids:
        path = os.path.join(folder, vid + ".jpg")
        if not os.path.exists(path):
            try:
                req = urllib.request.Request(thumb(vid), headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=20) as r, open(path, "wb") as f:
                    f.write(r.read())
            except urllib.error.URLError as e:
                print("thumb %s failed: %s" % (vid, e), file=sys.stderr)
                continue
        tiles.append((vid, path))
    w, h, cols, pad, label = 320, 180, args.cols, 8, 18
    rows = (len(tiles) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * (w + pad) + pad, rows * (h + label + pad) + pad), "white")
    draw = ImageDraw.Draw(sheet)
    for i, (vid, path) in enumerate(tiles):
        x = pad + (i % cols) * (w + pad)
        y = pad + (i // cols) * (h + label + pad)
        try:
            im = Image.open(path).convert("RGB").resize((w, h))
            sheet.paste(im, (x, y))
        except OSError:
            draw.rectangle([x, y, x + w, y + h], outline="red")
        draw.text((x, y + h + 2), "%d %s" % (i + 1, vid), fill="black")
    out = args.out or os.path.join(folder, "sheet-%s.png" % dt.date.today().isoformat())
    sheet.save(out)
    print("wrote %s (%d thumbnails, %d per row)" % (out, len(tiles), cols))


# ---------------------------------------------------------------- channel

def resolve_channel(api, ref):
    """Accept a channel id, an @handle, or a channel URL. 1 unit."""
    ref = ref.strip()
    m = re.search(r"(UC[A-Za-z0-9_-]{22})", ref)
    if m:
        return m.group(1)
    m = re.search(r"@([A-Za-z0-9._-]+)", ref)
    if m:
        handle = "@" + m.group(1)
    else:
        handle = "@" + ref.rstrip("/").split("/")[-1]
    body = api.get("channels", {"part": "id", "forHandle": handle}, cost_units=1)
    items = body.get("items", [])
    if not items:
        sys.exit("No channel for %s" % ref)
    return items[0]["id"]


def cmd_channel(args):
    """Recent uploads of one channel with the median and each video's ratio
    to it: TubeLab's get_channel_videos, with the outlier math done here."""
    api = Api(load_key())
    cid = resolve_channel(api, args.channel)
    ch = fetch_channels(api, [cid]).get(cid)
    if not ch:
        sys.exit("No channel with id %s" % cid)
    ids = recent_uploads(api, ch["uploads"], want=args.count)
    vids = fetch_videos(api, ids)
    rows = [vids[i] for i in ids if i in vids]
    if args.format != "any":
        want_short = args.format == "short"
        rows = [v for v in rows if is_short(v["seconds"]) == want_short]
    med = statistics.median([v["views"] for v in rows]) if len(rows) >= 3 else 0
    print("# %s, %s subs, %s uploads, pulled %s" % (ch["title"], fmt_int(ch["subs"]), ch["videoCount"], dt.date.today().isoformat()))
    print("Median views of the %d shown: %s\n" % (len(rows), fmt_int(med) if med else "n/a"))
    print("| # | Video | Published | Views | Length | x median | Thumb |")
    print("|---|---|---|---|---|---|---|")
    for i, v in enumerate(rows, 1):
        mult = round(v["views"] / med, 1) if med else ""
        print("| %d | [%s](https://youtu.be/%s) | %s | %s | %d:%02d | %s | ![](%s) |" % (
            i, v["title"].replace("|", "/"), v["id"], v["publishedAt"], fmt_int(v["views"]),
            *divmod(v["seconds"], 60), mult, thumb(v["id"])))


