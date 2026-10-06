"""init: one-time setup. Writes .env from a pasted key, creates seen.md and
shortlists/, and prints the first commands to run. Costs nothing."""

import os
import sys

from ytlib import HERE

SEEN_HEADER = "| date | id | note |\n|---|---|---|\n"


def cmd_init(args):
    env = os.path.join(HERE, ".env")
    if os.path.exists(env) and not args.force:
        print(".env exists; pass --force to replace it.")
    else:
        key = args.key
        if not key and sys.stdin.isatty():
            key = input("Paste your YouTube Data API key (README.md says how to get one): ").strip()
        if not key:
            sys.exit("No key given. Run `init --key YOUR_KEY` or paste it at the prompt.")
        with open(env, "w", encoding="utf-8") as f:
            f.write("YOUTUBE_API_KEY=%s\n" % key)
        try:
            os.chmod(env, 0o600)
        except OSError:
            pass
        print("Wrote .env (ignored by git).")
    seen = os.path.join(HERE, "seen.md")
    if not os.path.exists(seen):
        with open(seen, "w", encoding="utf-8") as f:
            f.write(SEEN_HEADER)
        print("Created seen.md: every id you judge goes here so it never comes back.")
    out = os.path.join(HERE, "shortlists")
    if not os.path.isdir(out):
        os.makedirs(out)
        print("Created shortlists/ for hunt and search output.")
    print("\nNext:\n"
          "  python3 outliers.py suggest \"your niche\"            # lanes, free\n"
          "  python3 outliers.py hunt \"lane one\" \"lane two\" --pages 2 --out shortlists/first.md\n"
          "  python3 outliers.py check VIDEO_ID                  # verify one row\n"
          "  python3 outliers.py quota                           # what today cost\n"
          "In Claude Code: /missing-gold \"I help X do Y\" runs the whole method.")
    return 0


def register(sub):
    p = sub.add_parser("init", help="write .env from a key, create seen.md and shortlists/ (no key needed to start)")
    p.add_argument("--key", default="", help="the API key; prompted for if omitted")
    p.add_argument("--force", action="store_true", help="replace an existing .env")
    p.set_defaults(func=cmd_init)
