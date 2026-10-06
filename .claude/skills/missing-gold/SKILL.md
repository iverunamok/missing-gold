---
name: missing-gold
description: Run the Missing Gold method end to end with the outliers.py CLI in this repo. Give it a niche statement ("I help X do Y") and it maps search lanes, hunts the YouTube Data API for videos that out-ran their channel, verifies every number live, grades packaging, scores the crowd, checks whether three channels won on each topic, and writes a ranked shortlist to shortlists/<date>-<slug>.md. Use when someone asks for video ideas that proved demand, outliers, an underserved audience, or "what should I make next".
---

# Missing Gold

The question: where is a crowd already waiting, and which of its meals was
served badly enough that a better cook wins. Read `docs/method.md` before
grading anything. Every command is `python3 outliers.py ...` from the repo
root.

## Inputs

| Input | Default when missing |
|---|---|
| Niche statement, "I help X do Y" | Ask for it. It is the only input the hunt needs; print it unchanged at the top of the report. |
| Format | long. Say `shorts` to switch (then views 500K+, ratio 10+). |
| Window | 24 months. |
| Budget | 12 searches and 400 units. Say why if you need more. |

## Step 0: budget

Run `quota`. If it reports no key or `.env` is missing, do not ask for
the key in chat. Tell the person to type `! python3 outliers.py init` in
this session and paste the key at the prompt, then wait for them to say it
is done and run `quota` again. If `python3` is not found, say which
installer to use (python.org, tick "Add to PATH" on Windows) and stop.
State the plan in one line: lanes × pages searches, plus about 4 units per
verified row.

## Step 1: map the crowd

Write 4 to 6 lanes before any search: two from the niche's own words, two
from the viewer's motivation rather than the topic, one title shape. Run
`suggest "<seed>"` for each seed to pull YouTube's own phrasing (free).

## Step 2: hunt

```
python3 outliers.py hunt "<lane 1>" "<lane 2>" ... --pages 2 --orders viewCount \
  --exclude-seen seen.md --out shortlists/<date>-<slug>-raw.md
```

Shorts: add `--format short --views-min 500000 --ratio-min 10`.

## Step 3: verify every candidate

For each row you intend to keep, run `check <id>`. Record views, subs,
the ratio you compute, the publish date, and the ratio to the channel's
median. A row that fails any gate after the live pull goes to the cuts
with the reason.

## Step 4: grade packaging

`thumbs --file <raw.md> --cols 5` makes the contact sheet. If it exits
asking for Pillow, run `python3 -m pip install --user pillow` and retry. Open it. Grade
each row BAD, MEDIOCRE, FACELESS, or GOOD per `docs/method.md`. GOOD is a
cut.

## Step 5: score and rank

Crowd score (four factors, 1 to 10), fit (0 to 1), then
Room = ratio × packaging × fit × recency. Sort by Room.

## Step 6: lead or pattern

For each of the top rows, count independent channels that won on the same
topic inside the window (from the hunt output; run one more lane if the
count is unclear). Print the count. One is a lead; three are a pattern.

## Step 7: deliver

Write `shortlists/<YYYY-MM-DD>-<slug>.md` from
`assets/report-template.md`. Append every id you judged to `seen.md`.
Then say, in chat: the path, the three headline rows (title, views on
subs, ratio, grade, channels on the topic, one caveat), and the quota
used with any lane that returned nothing.

## Never

- Report a number you did not pull this run.
- Pad the table. Fewer than twelve passed? Say so.
- Copy a muse title or thumbnail; the angle column is a talking point.
- Write a script or a spoken line.
- Treat one video as a pattern.
