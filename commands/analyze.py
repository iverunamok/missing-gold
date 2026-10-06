"""analyze: what a set of winning videos have in common.

Replaces vidIQ's tag view and TubeLab's trending-formats and comments tools
for one list of video ids. Reads tags, channel keywords, title shapes,
description patterns, engagement, length, countries, and categories, and
(with --comments N) the questions viewers ask under each video.

Cost: 1 unit per 50 videos, 1 unit per 50 channels, and 1 unit per video
when --comments is above 0. Every call goes through Api.get.
"""

import datetime as dt
import re
import statistics
import sys
from collections import Counter, OrderedDict

from ytlib import Api, load_key, ledger_add, ledger_read, parse_duration, fmt_int

ID_RE = re.compile(r"(?:youtu\.be/|watch\?v=|/vi/)([A-Za-z0-9_-]{11})")
BARE_ID_RE = re.compile(r"[A-Za-z0-9_-]{11}$")

# Title shapes. A title can carry several; one that carries none is "none".
SHAPES = OrderedDict([
    ("N things", re.compile(r"^\s*\d+\s+\w")),
    ("I wish I knew", re.compile(r"\bI wish I (knew|had known|'d known)\b|\bwish I('d| had) known\b", re.I)),
    ("first N", re.compile(r"\bfirst \d+\b", re.I)),
    ("how I", re.compile(r"\bhow I\b", re.I)),
    ("vs", re.compile(r"\bvs\.?(\s|$)|\bversus\b", re.I)),
    ("question", re.compile(r"\b(why|what|how|which|where|when|who|should|can|is|are|do|does|did)\b[^?]*\?", re.I)),
    ("for beginners", re.compile(r"\bbeginners?\b", re.I)),
    ("bracket or pipe suffix", re.compile(r"([\(\[][^\)\]]+[\)\]]\s*$)|(\|[^|]+$)")),
    ("ALL-CAPS word", re.compile(r"(?<![A-Za-z0-9])[A-Z]{3,}(?![A-Za-z0-9])")),
    ("year", re.compile(r"\b(19|20)\d{2}\b")),
    ("in N minutes/days", re.compile(r"\bin \d+ (minutes?|mins?|hours?|days?|weeks?|seconds?)\b", re.I)),
])

# YouTube's fixed category ids. videoCategories.list returns the same table.
CATEGORIES = {
    "1": "Film & Animation", "2": "Autos & Vehicles", "10": "Music",
    "15": "Pets & Animals", "17": "Sports", "19": "Travel & Events",
    "20": "Gaming", "22": "People & Blogs", "23": "Comedy",
    "24": "Entertainment", "25": "News & Politics", "26": "Howto & Style",
    "27": "Education", "28": "Science & Technology", "29": "Nonprofits & Activism",
}

QUESTION_WORDS = re.compile(r"\b(how|where|what|which|link|file)\b", re.I)
CHAPTER_LINE = re.compile(r"^\s*\(?0?0:00\b", re.M)
LINK = re.compile(r"https?://|\bwww\.", re.I)
PRODUCT = re.compile(
    r"amzn\.to|amazon\.[a-z.]+/|/dp/[A-Z0-9]{10}|aliexpress\.|geni\.us|ebay\.|"
    r"\baffiliate\b|\bgear (I|we) use\b|\bproducts? (I|we) use\b|\bmy (printer|setup|kit)\b|"
    r"\bequipment\b|\bgear list\b",
    re.I,
)

LIKE_FLAG = 0.002  # under 0.2% likes per view reads as possible paid traffic
LONG_SECONDS = 600


# ---------------------------------------------------------------- pure helpers

def extract_ids(text="", args=()):
    """Video ids from markdown text (youtu.be, watch?v=, /vi/ links) and from
    bare 11-character arguments. Order kept, duplicates dropped."""
    ids = []
    for a in args:
        m = ID_RE.search(a)
        vid = m.group(1) if m else (a if BARE_ID_RE.match(a) else None)
        if vid and vid not in ids:
            ids.append(vid)
    for m in ID_RE.finditer(text or ""):
        if m.group(1) not in ids:
            ids.append(m.group(1))
    return ids


def classify_title(title):
    """Names of every shape the title matches, in SHAPES order."""
    hits = [name for name, rx in SHAPES.items() if rx.search(title or "")]
    return hits or ["none"]


def count_tags(videos):
    """Counter of lowercase tag -> number of videos carrying it."""
    c = Counter()
    for v in videos:
        for t in set(t.strip().lower() for t in v.get("tags", []) if t.strip()):
            c[t] += 1
    return c


def parse_keywords(s):
    """brandingSettings.channel.keywords is space separated with quoted
    multi-word entries. Returns lowercase keywords."""
    out = []
    for m in re.finditer(r'"([^"]*)"|(\S+)', s or ""):
        k = (m.group(1) if m.group(1) is not None else m.group(2)).strip().lower()
        if k:
            out.append(k)
    return out


def count_keywords(channels):
    c = Counter()
    for ch in channels:
        for k in set(parse_keywords(ch.get("keywords", ""))):
            c[k] += 1
    return c


def engagement(video):
    """(like_rate, comment_rate, flag). Rates are fractions, None when the
    count is hidden or views are 0. flag is True under LIKE_FLAG."""
    views = video.get("views") or 0
    likes = video.get("likes")
    comments = video.get("comments")
    if views <= 0:
        return None, None, False
    like_rate = likes / views if likes is not None else None
    comment_rate = comments / views if comments is not None else None
    flag = like_rate is not None and like_rate < LIKE_FLAG
    return like_rate, comment_rate, flag


def description_patterns(descriptions):
    """Counts over a list of description strings."""
    descs = [d or "" for d in descriptions]
    chapters = sum(1 for d in descs if CHAPTER_LINE.search(d))
    links_top = sum(1 for d in descs if LINK.search("\n".join(d.splitlines()[:3])))
    product = sum(1 for d in descs if PRODUCT.search(d))
    lengths = [len(d) for d in descs]
    return {
        "count": len(descs),
        "chapters": chapters,
        "links_in_first_3_lines": links_top,
        "names_a_product": product,
        "median_length": int(statistics.median(lengths)) if lengths else 0,
    }


def is_viewer_question(text):
    t = text or ""
    return "?" in t or bool(QUESTION_WORDS.search(t))


def fmt_len(seconds):
    seconds = int(seconds or 0)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return "%d:%02d:%02d" % (h, m, s) if h else "%d:%02d" % (m, s)


def pct(x):
    return "n/a" if x is None else "%.2f%%" % (100 * x)


def topic_name(url):
    return url.rstrip("/").rsplit("/", 1)[-1].replace("_", " ")


# ---------------------------------------------------------------- API

def fetch_full_videos(api, ids):
    """videos.list with the analysis parts, 50 ids per call, 1 unit each.
    Returns records in input order; ids the API does not return are dropped."""
    recs = {}
    for i in range(0, len(ids), 50):
        batch = ids[i:i + 50]
        body = api.get("videos", {
            "part": "snippet,statistics,contentDetails,topicDetails",
            "id": ",".join(batch),
            "maxResults": 50,
        }, cost_units=1)
        for item in body.get("items", []):
            sn = item.get("snippet", {})
            st = item.get("statistics", {})
            recs[item["id"]] = {
                "id": item["id"],
                "title": sn.get("title", ""),
                "description": sn.get("description", ""),
                "tags": sn.get("tags", []),
                "categoryId": str(sn.get("categoryId", "")),
                "language": sn.get("defaultLanguage") or sn.get("defaultAudioLanguage") or "",
                "channelId": sn.get("channelId", ""),
                "channelTitle": sn.get("channelTitle", ""),
                "publishedAt": sn.get("publishedAt", "")[:10],
                "views": int(st["viewCount"]) if "viewCount" in st else 0,
                "likes": int(st["likeCount"]) if "likeCount" in st else None,
                "comments": int(st["commentCount"]) if "commentCount" in st else None,
                "seconds": parse_duration(item.get("contentDetails", {}).get("duration")),
                "topics": [topic_name(u) for u in item.get("topicDetails", {}).get("topicCategories", [])],
            }
    return [recs[v] for v in ids if v in recs]


def fetch_channel_meta(api, ids):
    """channels.list for keywords and country, 50 ids per call, 1 unit each."""
    out = {}
    ids = list(dict.fromkeys(i for i in ids if i))
    for i in range(0, len(ids), 50):
        batch = ids[i:i + 50]
        body = api.get("channels", {
            "part": "brandingSettings,snippet,statistics",
            "id": ",".join(batch),
            "maxResults": 50,
        }, cost_units=1)
        for item in body.get("items", []):
            sn = item.get("snippet", {})
            br = item.get("brandingSettings", {}).get("channel", {})
            st = item.get("statistics", {})
            out[item["id"]] = {
                "id": item["id"],
                "title": sn.get("title", ""),
                "country": sn.get("country", "") or br.get("country", ""),
                "keywords": br.get("keywords", ""),
                "subs": int(st.get("subscriberCount", 0)),
            }
    return out


def fetch_comments(api, video_id, n):
    """Top comments by relevance, 1 unit. Returns (comments, note). A video
    with comments off makes Api.get exit on HTTP 403 commentsDisabled; that is
    caught here, charged 1 unit (the API bills failed calls), and reported."""
    try:
        body = api.get("commentThreads", {
            "part": "snippet",
            "videoId": video_id,
            "order": "relevance",
            "maxResults": min(max(n, 1), 100),
            "textFormat": "plainText",
        }, cost_units=1)
    except SystemExit as e:
        msg = str(e)
        if "commentsDisabled" in msg or "HTTP 404" in msg or "HTTP 403" in msg:
            ledger_add(units=1)
            return [], "comments unavailable (%s)" % ("disabled" if "commentsDisabled" in msg else msg[:60])
        raise
    out = []
    for item in body.get("items", []):
        top = item.get("snippet", {}).get("topLevelComment", {}).get("snippet", {})
        text = top.get("textOriginal") or top.get("textDisplay") or ""
        out.append({"text": re.sub(r"\s+", " ", text).strip(), "likes": int(top.get("likeCount", 0))})
    return out, ""


# ---------------------------------------------------------------- report

def table(header, rows):
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    for r in rows:
        lines.append("| " + " | ".join(str(c) for c in r) + " |")
    return "\n".join(lines)


def md_cell(s):
    return (s or "").replace("|", "\\|").replace("\n", " ")


def report(videos, channels, comments=None, top=40):
    """Markdown for the whole analysis. channels: id -> record.
    comments: id -> (list, note) or None."""
    out = []
    out.append("# Analyze: %d videos, %d channels (%s)" % (
        len(videos), len(channels), dt.date.today().isoformat()))
    out.append("")

    # (a) tags
    tags = count_tags(videos)
    tagged = sum(1 for v in videos if v.get("tags"))
    out.append("## Tags")
    out.append("")
    out.append("%d of %d videos carry tags; %d distinct. Top %d:" % (tagged, len(videos), len(tags), top))
    out.append("")
    rows = sorted(tags.items(), key=lambda kv: (-kv[1], kv[0]))[:top]
    out.append(table(["Tag", "Videos"], [(md_cell(t), n) for t, n in rows]) if rows else "(none)")
    out.append("")

    # (b) channel keywords
    kws = count_keywords(channels.values())
    out.append("## Channel keywords")
    out.append("")
    out.append("%d distinct across %d channels. Top %d:" % (len(kws), len(channels), top))
    out.append("")
    rows = sorted(kws.items(), key=lambda kv: (-kv[1], kv[0]))[:top]
    out.append(table(["Keyword", "Channels"], [(md_cell(k), n) for k, n in rows]) if rows else "(none)")
    out.append("")

    # (c) title shapes
    by_shape = OrderedDict((name, []) for name in list(SHAPES) + ["none"])
    for v in videos:
        for name in classify_title(v.get("title", "")):
            by_shape[name].append(v)
    out.append("## Title shapes")
    out.append("")
    out.append("A title can carry several shapes.")
    out.append("")
    out.append(table(["Shape", "Titles"], [(name, len(vs)) for name, vs in by_shape.items()]))
    out.append("")
    for name, vs in by_shape.items():
        if not vs:
            continue
        out.append("### %s (%d)" % (name, len(vs)))
        out.append("")
        for v in vs:
            out.append("- %s (%s, %s views)" % (md_cell(v.get("title")), v.get("id"), fmt_int(v.get("views", 0))))
        out.append("")

    # (d) descriptions
    d = description_patterns([v.get("description", "") for v in videos])
    out.append("## Description patterns")
    out.append("")
    out.append(table(["Check", "Videos"], [
        ("Has chapters (a 00:00 line)", "%d of %d" % (d["chapters"], d["count"])),
        ("Link in the first 3 lines", "%d of %d" % (d["links_in_first_3_lines"], d["count"])),
        ("Names a product (store or affiliate link, gear list)", "%d of %d" % (d["names_a_product"], d["count"])),
        ("Median description length (characters)", d["median_length"]),
    ]))
    out.append("")

    # (e) engagement
    out.append("## Engagement")
    out.append("")
    like_rates, comment_rates, rows, flagged = [], [], [], 0
    for v in videos:
        lr, cr, flag = engagement(v)
        if lr is not None:
            like_rates.append(lr)
        if cr is not None:
            comment_rates.append(cr)
        flagged += 1 if flag else 0
        rows.append((v.get("id"), md_cell(v.get("title"))[:70], fmt_int(v.get("views", 0)),
                     pct(lr), pct(cr), "possible paid traffic" if flag else ""))
    out.append("Median like rate %s, median comment rate %s. Flag: likes under %.1f%% of views (%d flagged)." % (
        pct(statistics.median(like_rates)) if like_rates else "n/a",
        pct(statistics.median(comment_rates)) if comment_rates else "n/a",
        100 * LIKE_FLAG, flagged))
    out.append("")
    out.append(table(["Id", "Title", "Views", "Likes/views", "Comments/views", "Flag"], rows))
    out.append("")

    # (f) length
    secs = [v.get("seconds", 0) for v in videos if v.get("seconds")]
    under = sum(1 for s in secs if s < LONG_SECONDS)
    out.append("## Length")
    out.append("")
    out.append("Median %s. Under 10 minutes: %d. 10 minutes or more: %d." % (
        fmt_len(statistics.median(secs)) if secs else "n/a", under, len(secs) - under))
    out.append("")

    # (g) countries, categories, languages, topics
    out.append("## Countries and categories")
    out.append("")
    countries = Counter(channels[v["channelId"]].get("country") or "unset"
                        for v in videos if v.get("channelId") in channels)
    out.append(table(["Channel country", "Videos"], sorted(countries.items(), key=lambda kv: (-kv[1], kv[0]))))
    out.append("")
    cats = Counter("%s %s" % (v.get("categoryId"), CATEGORIES.get(v.get("categoryId"), "")) for v in videos)
    out.append(table(["Category", "Videos"], sorted(cats.items(), key=lambda kv: (-kv[1], kv[0]))))
    out.append("")
    langs = Counter(v.get("language") or "unset" for v in videos)
    out.append(table(["Default language", "Videos"], sorted(langs.items(), key=lambda kv: (-kv[1], kv[0]))))
    out.append("")
    topics = Counter(t for v in videos for t in set(v.get("topics", [])))
    if topics:
        out.append(table(["Topic (topicDetails)", "Videos"], sorted(topics.items(), key=lambda kv: (-kv[1], kv[0]))))
        out.append("")

    # comments
    if comments:
        out.append("## What viewers ask")
        out.append("")
        out.append("Top comments by relevance that hold a question mark or how, where, what, which, link, file.")
        out.append("")
        for v in videos:
            got, note = comments.get(v["id"], ([], ""))
            asks = [c for c in got if is_viewer_question(c["text"])]
            out.append("### %s (%s)" % (md_cell(v.get("title")), v["id"]))
            out.append("")
            if note:
                out.append("- %s" % note)
            elif not asks:
                out.append("- none of the %d top comments ask anything" % len(got))
            for c in asks:
                out.append("- (%d likes) %s" % (c["likes"], md_cell(c["text"])[:300]))
            out.append("")
    return "\n".join(out).rstrip() + "\n"


# ---------------------------------------------------------------- command

def cmd_analyze(args):
    text = ""
    ids = []
    for src in args.source:
        if ID_RE.search(src) or BARE_ID_RE.match(src):
            ids.append(src)
        else:
            try:
                text += open(src, encoding="utf-8").read() + "\n"
            except OSError as e:
                sys.exit("cannot read %s: %s" % (src, e))
    ids = extract_ids(text, ids)
    if not ids:
        sys.exit("No video ids found.")
    before = ledger_read()["units"]
    api = Api(load_key())
    videos = fetch_full_videos(api, ids)
    missing = sorted(set(ids) - set(v["id"] for v in videos))
    channels = fetch_channel_meta(api, [v["channelId"] for v in videos])
    comments = None
    if args.comments > 0:
        comments = {}
        for v in videos:
            comments[v["id"]] = fetch_comments(api, v["id"], args.comments)
    md = report(videos, channels, comments, top=args.top)
    spent = ledger_read()["units"] - before
    md += "\nUnits spent: %d. Ids not returned by the API: %s\n" % (spent, ", ".join(missing) or "none")
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(md)
        print("wrote %s (%d videos, %d units)" % (args.out, len(videos), spent))
    else:
        sys.stdout.write(md)


def register(sub):
    p = sub.add_parser("analyze", help="tags, title shapes, descriptions, engagement, and viewer questions for a set of videos")
    p.add_argument("source", nargs="+", help="markdown files with video links, video ids, or both")
    p.add_argument("--comments", type=int, default=0, help="top comments to read per video (1 unit per video)")
    p.add_argument("--top", type=int, default=40, help="rows in the tag and keyword tables")
    p.add_argument("--out", default="", help="write the markdown here instead of stdout")
    p.set_defaults(func=cmd_analyze)
