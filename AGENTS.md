# Missing Gold: agent guide

This folder is a Python 3.9, standard-library CLI for the YouTube Data API.
It finds videos that drew far more views than their channel's size explains
and runs the Missing Gold method on them. Read `README.md` for the commands
and `docs/method.md` for the method. The skill in
`.claude/skills/missing-gold/` runs the whole method from one sentence.

## How to drive it

- Every command is `python3 outliers.py <command> ...` from this folder.
  `python3 outliers.py --help` lists them; each has its own `--help`.
- The key lives in `.env`. If it is missing, run `python3 outliers.py init`
  and ask the person for the key. Never print, echo, or commit the key.
- Quota is the constraint. Run `python3 outliers.py quota` before a hunt
  and say the plan: searches cost 1 each of 100 a day; `check` costs about
  4 units of 10,000 on a small channel and up to 40 on a big one. The tool
  refuses a run that would pass the search cap; do not work around it.
- Candidates come from `search` or `hunt`. Verify every row with `check`
  before you report it. Report only numbers you pulled this run, with the
  date.
- `suggest` and `thumbs` cost nothing. Use them freely.
- Keep `seen.md` current: every id you judged, one row each, so it never
  returns. Pass it with `--exclude-seen seen.md`.
- A video id that starts with a dash goes after `--`.

## What to say back

Lead with the budget if anything ran out. Then the three best rows with
title, views on subscribers, ratio, packaging grade, and the number of
independent channels that won on the topic. One channel is a lead; three
are a pattern. Never pad a table to twelve rows; say how many passed.

## Never

- Call the API from a test. Tests run on fakes with no key.
- Write a video script or spoken line for the person. Titles, thumbnail
  descriptions, chapter maps, and talking points are the hand-off.
- Push, publish, or open a pull request without being asked in the session.

## Adding a command

One file in `commands/<name>.py` with `register(subparsers)`, every API
call through `Api.get` or `Api.paged`, and `test_<name>.py` at the root.
`commands/README.md` has the contract.
