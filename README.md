# Missing Gold

Find the YouTube videos that drew far more views than their channel's size
explains, on channels smaller than yours could be in a year. Each one is a
topic a crowd wanted and a creator served badly enough that a better video
wins. That is the gold the paid tools (vidIQ, TubeLab) sell. This finds it
on YouTube's free Data API, from the standard library, in one folder.

Python 3.9 or later. Nothing to install for the core: the thumbnail
contact sheet needs Pillow (`requirements-optional.txt`); everything else
runs bare. The search design is copied from
[pandich93/youtube-niche-finder](https://github.com/pandich93/youtube-niche-finder)
(MIT): search once, then read cheaply.

## Start here, with Claude Code

Three steps. The key is the only part you do by hand.

1. Get a free YouTube Data API key (five minutes, no billing). Steps are
   under "Get a key" below.
2. In a terminal:

   ```
   git clone https://github.com/iverunamok/missing-gold && cd missing-gold && claude
   ```

   Inside Claude Code, type `! python3 outliers.py init` and paste the key
   at the prompt. It lands in `.env`, which git ignores. The key never goes
   into the chat.
3. Type `/missing-gold "I help X do Y"` with your own niche. Claude Code
   runs the whole method and writes the ranked shortlist to `shortlists/`.

Python 3.9 or later is the only requirement. macOS has it. On Windows,
install it from python.org and tick "Add to PATH".

## The method, in one table

| Gate | Threshold | Why |
|---|---|---|
| Views on the video | 100,000 or more | The crowd showed up |
| Subscribers on the channel | under 100,000 | The channel did not bring them |
| Ratio, views divided by subscribers | 5.0 or more | The topic carried the video |
| Packaging | weak | A bad title and thumbnail that still won proves the idea, not the creator |

Then the rule that keeps you honest: **one channel is a lead, three
channels are a pattern.** Every outlier is a one-off on its own channel
(that is what an outlier is). Before you build a video on a topic, find
three independent channels that each won on it. `docs/method.md` has the
grading rubric and the sort key.

## Quick start

```
git clone https://github.com/iverunamok/missing-gold && cd missing-gold
python3 outliers.py init          # pastes the key into .env, creates seen.md
python3 outliers.py suggest "your niche"
python3 outliers.py hunt "lane one" "lane two" --pages 2 --out shortlists/first.md
```

With Claude Code: open the folder and type
`/missing-gold "I help X do Y"`. The skill in `.claude/skills/missing-gold/`
maps lanes, hunts, verifies every number live, grades packaging, counts
how many channels won on each topic, and writes the ranked shortlist.
`AGENTS.md` tells any agent how to drive the tool without spending the
quota by accident.

## Get a key (about five minutes, free)

1. Open the [Google Cloud console](https://console.cloud.google.com/) and
   sign in.
2. Create a project. Any name.
3. In **APIs & Services**, open **Library**, search for **YouTube Data API
   v3**, and click **Enable**.
4. In **Credentials**, click **Create credentials**, then **API key**. Copy
   it.
5. Click the key to edit it. Under **API restrictions**, choose **Restrict
   key** and tick only **YouTube Data API v3**. Save.
6. Copy `.env.example` to `.env` next to `outliers.py` and paste the key.
   `.env` is in `.gitignore`. Never paste the key anywhere else.

No billing account is needed. A key with no billing gets the default quota
below, which is enough for several runs a day.

## Quota, measured against the 2026-06-01 rules

| Call | Cost | Daily cap |
|---|---|---|
| `search.list` | 1 search call | 100 |
| `videos.list`, `channels.list`, `playlistItems.list` | 1 unit per call of up to 50 ids | 10,000 units |

A run of six lanes at one page each costs 6 searches and about 15 units.
`quota.json` keeps the day's count and the tool refuses a run that would
overshoot the search cap. The cap resets at midnight Pacific. `check`
costs 4 units (the video, the channel, one page of its uploads, and those
videos), fewer when cached. Two processes on one key share one ledger, so
run hunts one at a time if the count matters.

## Use

```
# Six lanes, long-form, 24 months, 100K+ views, under 100K subs, ratio 5+.
python3 outliers.py search \
  "3d printed van build" "van life 3d printing" "small space engineering" \
  "diy camper storage hack" "I designed and printed" "van build mistakes" \
  --exclude-seen seen.md --out shortlist.md

# One video against its channel's recent median (the paid tools' average-views ratio).
python3 outliers.py check bYbzI28RNmo

# A channel's last 30 long-form uploads against its median.
python3 outliers.py channel @somechannel

# Keyword lanes from YouTube's own autocomplete. No key, no quota.
python3 outliers.py suggest "3d printed van" "first 3d printer"
python3 outliers.py suggest "3d printed van" --deep     # also probes seed + a..z

# Contact sheet of thumbnails for grading packaging. No key. Needs Pillow.
python3 outliers.py thumbs --file shortlist.md --cols 5

# Today's spend.
python3 outliers.py quota
```

Flags on `search`: `--views-min`, `--subs-max`, `--ratio-min`, `--months`,
`--format long|short|any`, `--pages` (50 results and 1 search call per page),
`--lang`, `--region` (default `US`, pass `""` for none), `--order`, `--limit`,
`--exclude-seen`, `--out`.

An id that starts with a dash goes after `--`: `check -- -abc123defgh`.

## What it computes

- **Ratio**: views divided by subscribers, one decimal. A channel under 100
  subscribers counts as 100 so a brand-new channel cannot top the table.
- **Short vs long**: from the video's duration. 180 seconds or under is a
  Short. The API has no Shorts flag.
- **Check**: the video's views against the median of the channel's 10 most
  recent uploads of the same format, excluding itself. Needs 3 or more.
- **Thumbnails**: `https://i.ytimg.com/vi/<id>/mqdefault.jpg`, no API call.

Each video and channel lookup is cached in `cache/` as JSON. Channel numbers
are refreshed once a day; video numbers are kept until you delete the file.

## Plug-in commands

| Command | What it does | Key |
|---|---|---|
| `hunt <lanes...> [--pages 3] [--orders viewCount,relevance,date] [--fresh] [--seed-watch watch.json]` | Deep search: several pages and sort orders per lane, deduped, with the quota plan printed first and a refusal past 100 searches. Relevance order rarely finds what view-count order missed, so `--orders viewCount --pages 3` is the cheap setting. | yes |
| `watch add/list/remove/scan [--rss] [--days 14] [--mult 3]` | A watch list of small channels in your niche (`watch.example.json` shows the shape). `scan --rss` costs nothing; the playlist path costs about 1 unit per channel. Flags any new upload at 3x the channel's median or 5x its subscribers. | scan with `--rss` no; otherwise yes |
| `analyze <shortlist.md or ids> [--comments 20]` | Tags, channel keywords, title shapes, chapters and links, like and comment rates, lengths, countries, and the questions viewers ask in the comments. About 1 unit per 50 videos plus 1 per video for comments. | yes |
| `trending [--category 28] [--region US] [--list-categories]` | YouTube's most-popular chart by category with views per day. 1 unit per page. | yes |
| `firsts <channel or video>... [--count 25]` | A channel's OLDEST uploads in order, with gaps, views, and the first upload to beat 10x the early median: what a channel that grew posted first. 1 unit per 50 uploads, capped at 20 pages, plus 2. | yes |
| `mine [--profile NAME] auth/status/videos/traffic` | Your own channel through OAuth: watch hours in the trailing 365 days, subscribers, per-video view duration, traffic sources, and progress toward the Partner Program thresholds. Setup: `docs/own-channel-setup.md`. Thumbnail impressions and click-through are not in the Analytics API; Studio only. | OAuth, separate quota |

Add your own: one file in `commands/` with a `register(subparsers)`
function. `commands/README.md` has the contract.

## What each command replaces

| Command | vidIQ or TubeLab equivalent | Needs the key |
|---|---|---|
| `search`, `hunt` | outlier search | yes, 1 search call per lane page |
| `check` | the average-views ratio for one video | yes, about 4 units |
| `channel` | channel videos | yes, about 4 units |
| `suggest` | the keyword-ideas half of keyword research (no search volume) | no |
| `thumbs` | opening each thumbnail by hand | no |
| `quota` | the credits balance | no |

## What it does not do

- Transcripts. TubeLab's transcript lookup is free without a plan.
- Trending title formats. A title-shape pass over your own hit list
  replaces it (`analyze` prints the shapes).
- Grading or scoring. `docs/method.md` does that by hand, on purpose.

## Exit codes and state files

0 on success, 1 with a one-line message on any handled error (a bad key,
quota, a missing video, no network), 2 on a usage error. State lives next
to `outliers.py`: `cache/`, `quota.json`, `seen.md`, `shortlists/`,
`watch.json`, and the OAuth tokens. Set `OUTLIERS_HOME=/some/dir` to keep
them elsewhere. `check --fresh` re-reads a video cached today; cached
video numbers otherwise refresh after one day.

## Check without a key

```
python3 -m unittest
```

Every test runs on fakes; nothing touches the network.

## Contributing

Standard library only, Python 3.9 syntax, every API call through
`Api.get` or `Api.paged` so the quota ledger stays right, and a
`test_<command>.py` that runs with no key. Open an issue before a large
change. MIT, see `LICENSE`.
