#!/usr/bin/env python3
"""Find YouTube outliers: videos with far more views than their channel earns.

Replaces TubeLab's paid search for the Missing Gold method. Uses the YouTube
Data API v3 directly, standard library only.

Built-in commands:
  init     write .env from a pasted key and create seen.md (no key needed to start)
  search   one or more queries -> ranked markdown table of outliers
  check    one video id -> its views against the channel's recent median
  channel  one channel's recent uploads against its median
  suggest  keyword lanes from YouTube autocomplete (no key)
  thumbs   contact sheet of thumbnails for packaging grades (no key)
  quota    what this project has spent today

Plug-in commands live in commands/<name>.py; each exposes register(subparsers).
Run `python3 outliers.py --help` for the full list.

Key: put YOUTUBE_API_KEY in the environment or in .env next to this file.
"""

import argparse
import importlib
import os
import pkgutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ytlib import *  # noqa: F401,F403  (core: Api, cache, ledger, lookups, built-ins)


def load_plugins(sub):
    """Import every commands/<name>.py and call its register(sub)."""
    folder = os.path.join(os.path.dirname(os.path.abspath(__file__)), "commands")
    if not os.path.isdir(folder):
        return
    for info in pkgutil.iter_modules([folder]):
        if info.name.startswith("_"):
            continue
        mod = importlib.import_module("commands." + info.name)
        if hasattr(mod, "register"):
            mod.register(sub)



VERSION = "1.0.0"


def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    p = argparse.ArgumentParser(prog="outliers.py", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--version", action="version", version="Missing Gold " + VERSION)
    sub = p.add_subparsers(dest="cmd")

    s = sub.add_parser("search", help="hunt one or more lanes")
    s.add_argument("query", nargs="+", help="one search lane per argument")
    s.add_argument("--views-min", type=int, default=100000)
    s.add_argument("--subs-max", type=int, default=100000)
    s.add_argument("--ratio-min", type=float, default=5.0)
    s.add_argument("--months", type=int, default=24, help="publish window")
    s.add_argument("--format", choices=["long", "short", "any"], default="long")
    s.add_argument("--pages", type=int, default=1, help="search pages per lane, 50 results each, 1 search call each")
    s.add_argument("--lang", default="en")
    s.add_argument("--region", default="US", help="regionCode; empty string for none")
    s.add_argument("--order", default="viewCount", choices=["viewCount", "relevance", "date"])
    s.add_argument("--limit", type=int, default=40, help="rows to print")
    s.add_argument("--exclude-seen", default="", help="path to a seen.md whose second column is a video id")
    s.add_argument("--out", default="", help="write markdown here instead of stdout")
    s.set_defaults(func=cmd_search)

    c = sub.add_parser("check", help="one video against its channel's recent median")
    c.add_argument("video_id", help="video id; put -- first if the id starts with a dash")
    c.add_argument("--window", type=int, default=10, help="recent same-format uploads to take the median of")
    c.add_argument("--fresh", action="store_true", help="re-read the video even if cached today")
    c.set_defaults(func=cmd_check)

    g = sub.add_parser("suggest", help="keyword lanes from YouTube autocomplete, no key")
    g.add_argument("seed", nargs="+")
    g.add_argument("--deep", action="store_true", help="also probe seed + a..z (27 extra calls per seed)")
    g.add_argument("--lang", default="en")
    g.add_argument("--region", default="us")
    g.set_defaults(func=cmd_suggest)

    t = sub.add_parser("thumbs", help="contact sheet of thumbnails for grading packaging, no key")
    t.add_argument("ids", nargs="*", help="video ids")
    t.add_argument("--file", default="", help="markdown file to pull ids from (a shortlist or search output)")
    t.add_argument("--cols", type=int, default=4)
    t.add_argument("--out", default="")
    t.set_defaults(func=cmd_thumbs)

    ch = sub.add_parser("channel", help="one channel's recent uploads against its median")
    ch.add_argument("channel", help="channel id, @handle, or URL")
    ch.add_argument("--count", type=int, default=30)
    ch.add_argument("--format", choices=["long", "short", "any"], default="long")
    ch.set_defaults(func=cmd_channel)

    q = sub.add_parser("quota", help="today's spend")
    q.set_defaults(func=cmd_quota)

    load_plugins(sub)

    args = p.parse_args(argv)
    if not args.cmd:
        p.print_help()
        return 2
    return args.func(args) or 0



if __name__ == "__main__":
    sys.exit(main())
