# The Missing Gold method

The question: where is a crowd already waiting, and which of its meals was
served badly enough that a better cook wins. A weak video on a small
channel with a big view count proves the topic carried the video. That is
the asset. You re-package the topic, never the video.

## 1. Map the crowd before you search

Write 4 to 6 search lanes. Two from the niche's own words, two from the
viewer's motivation rather than the topic (the person who wants "van
build" also watches "jobs that pay to travel"), and one title shape, such
as "I tried X for 30 days". Lanes are what make the supply endless; the
same three keywords return the same twelve videos every week.
`outliers.py suggest` turns a seed into lanes from YouTube's autocomplete
at no cost.

Keep a `seen.md` of video ids you have already judged and pass it with
`--exclude-seen`.

## 2. Hunt, then verify

`search` or `hunt` with the four gates: 100,000+ views, under 100,000
subscribers, ratio 5.0 or better, and (for the default) long-form inside
24 months. For Shorts raise the bar: 500,000+ views, ratio 10 or better.

Numbers in a search index drift. Every candidate gets `check` before it
enters your table: the live view count, the live subscriber count, the
ratio computed by you, and the channel's own median so you can see how
far outside its normal the video sits.

## 3. Grade the packaging

Open the thumbnail (`thumbs` makes a contact sheet), read the title, and
read the first 30 seconds.

| Grade | What you saw |
|---|---|
| BAD | Two or more of: unreadable or absent thumbnail text, a screenshot or stock photo as the thumbnail, a title with no promise or a keyword string, no hook in the first 30 seconds, poor audio or lighting, a logo intro. |
| MEDIOCRE | One of the above, or a competent but generic package: a face and a word, a title that names the topic and nothing else. |
| FACELESS | Stock footage, slides, or screen capture with voiceover. Grade the title and hook as above and add this label. A faceless win means a face wins harder. |
| GOOD | A clear promise in the title, a thumbnail that reads at phone size, a hook inside 10 seconds. This is a cut: the creator earned it, so the topic is not proven on its own. |

## 4. Score the crowd

Four factors, 1 to 10 each. The source is the starving-crowd test; the
bands are a reading of it.

| Factor | The question | 10 looks like |
|---|---|---|
| Pain | If this viewer does nothing for 12 months, what gets worse? | Money, health, or a job is lost. |
| Purchasing power | Can they pay for a real fix? | They already pay for a course, a service, or a tool in this lane. |
| Targetability | Can you find them on purpose? | A search term, a subreddit, a group, a job title. |
| Growth | Is the crowd getting bigger? | A release cycle, a season, a law change, a layoff wave. |

Sum the four. 32 to 40 strong. 24 to 31 worth a video. 16 to 23 hard.
Under 16 cut, whatever the ratio.

## 5. Fit, 0 to 1

| Fit | Meaning |
|---|---|
| 1.0 | Lands on a pillar of your channel and breaks none of your rules. |
| 0.7 | Adjacent. Your viewer would watch it, but it is not a pillar. |
| 0.4 | A stretch. Needs a reframe to belong on your channel. |
| 0 | A lane you have ruled out. Cut. |

## 6. Room, the sort key

```
Room = ratio × packaging × fit × recency
```

| Term | Values |
|---|---|
| ratio | views divided by subscribers, from the live pull |
| packaging | BAD 1.0, FACELESS 0.9, MEDIOCRE 0.7, GOOD 0.3 |
| fit | the fit above |
| recency | published within 12 months 1.0, 12 to 24 months 0.8, older 0.6 |

Rank by Room, never by raw views. A 32:1 ratio with bad packaging and a
two-year-old audience outside your market ranks below a 9:1 ratio from
last month in it.

## 7. One channel is a lead, three are a pattern

Every row you keep is a one-off on its own channel; `check` shows you by
how much. A brand's own remake of its 14-million-view Short drew 879. So
before a topic becomes a video, find three independent channels that each
won on it (different people, no re-upload farms, no dealers, no restoration
timelapse mills). One channel is a lead worth a search. Three are a
pattern worth a shoot. Print the count next to every row.

## 8. Caveats to print

- Published more than 18 months ago.
- The channel's audience is mostly outside your market.
- The view count is one video on a channel whose other videos sit under
  1,000. Often a share from outside YouTube.
- The crowd score is under 24.
- The topic passed only on a reframe.

## 9. Hand-off

Pick a row. Write three title variants, a thumbnail description, a list
of what the muse video did that you must not repeat, and a chapter map.
The proven title stays about 90% the same in your remake; the thumbnail is
never copied. Then write the script yourself.
