# Own channel setup for `mine`

`python3 outliers.py mine` reads the signed-in channel's analytics through
the YouTube Analytics API and the Data API with OAuth. One-time setup takes
about ten minutes in Google Cloud Console, then one consent run per channel.

## Before you start

`client_secret.json` and `token*.json` hold secrets. Both are already in
`.gitignore`; keep them there.

The OAuth client's project sets the quota. The
Analytics API has its own quota, separate from the Data API's 10,000 units.
`quota.json` still counts 1 unit per Analytics call so the ledger shows every
request the tool made.

## 1. Enable the YouTube Analytics API

1. Open the [Google Cloud console](https://console.cloud.google.com/) and
   pick the project that holds your API key in the project selector at the top.
2. Open **APIs & Services** and then **Library**.
3. Search for **YouTube Analytics API** and click it.
4. Click **Enable**.
5. Repeat for **YouTube Data API v3** if the project does not show it as
   enabled under **APIs & Services**, **Enabled APIs & services**.

If `mine status` later ends with "The YouTube Analytics API is not enabled",
this step was skipped or ran on a different project than the OAuth client.

## 2. Set the OAuth consent screen

1. Open **APIs & Services** and then **OAuth consent screen** (in the 2025
   console it sits under **Google Auth Platform**, **Branding**).
2. Choose **External** as the user type and click **Create**.
3. Fill the app name (`outlier-finder` works), the user support email, and
   the developer contact email. Both emails are your own. Click **Save and
   continue**.
4. On **Scopes**, click **Add or remove scopes** and tick:
   - `https://www.googleapis.com/auth/youtube.readonly`
   - `https://www.googleapis.com/auth/yt-analytics.readonly`
   Click **Update**, then **Save and continue**.
5. On **Test users** (under **Audience** in the 2025 console), click **Add
   users** and enter the Google account email that owns the channels. Add any
   other Google account that owns a channel you want to track. Click **Save**.
6. Leave the app in **Testing**. An app in Testing with your own email as a
   test user needs no Google verification. Testing tokens expire after 7
   days of no use; rerun `mine auth` for that channel if a refresh fails.

## 3. Create a Desktop OAuth client and download the secret

1. Open **APIs & Services** and then **Credentials**.
2. Click **Create credentials** and choose **OAuth client ID**.
3. For **Application type** choose **Desktop app**. Name it
   `outlier-finder desktop`. Click **Create**.
4. In the dialog, click **Download JSON**.
5. Save the file as `client_secret.json` next to `outliers.py`.

To keep the file elsewhere, pass `--client-secret PATH` on every `mine` call
or set `YOUTUBE_OAUTH_CLIENT_SECRET=PATH` in the environment.

## 4. Connect each channel

Run the consent flow once per channel. The tool opens a browser tab, you
sign in, and Google asks which channel (brand account) to use. Pick the one
you want for that profile.

```
cd missing-gold
python3 outliers.py mine --profile main   auth    # your first channel
python3 outliers.py mine --profile second auth    # another channel, if you run one
```

Each run prints the channel it connected and writes `token-NAME.json` next
to `outliers.py`. Without `--profile` the file is `token.json`. The tool
catches Google's redirect on `127.0.0.1` on a port it picks, so nothing
needs to be opened in a firewall. Approve both scopes at the consent screen;
with one missing, `mine status` stops with a scope error.

If the consent screen shows "Google hasn't verified this app", click
**Continue**. That warning is the Testing status from step 2.

## 5. Read the numbers

```
python3 outliers.py mine --profile main status
python3 outliers.py mine --profile main videos --days 365 --limit 50
python3 outliers.py mine --profile main traffic
```

`status` prints subscribers, watch hours for the trailing 365 days and the
last 28 days, public uploads in the last 90 days, and the gap to each Partner
Program tier with the per-day pace needed by the deadline you pass with
`--deadline YYYY-MM-DD` (default 2026-12-15, the last safe date to apply
under the 4,000-hour rule before the 8,000-hour rule starts on
2027-02-01). The watch-hours
figure is labelled ESTIMATE: the API has no qualified-hours flag, and
YouTube's count excludes private and unlisted videos, Shorts feed views, and
ad campaign views. The tool removes Shorts watch time with the
`creatorContentType` dimension; the other exclusions stay in the number.

`videos` has no impressions or click-through columns. The Analytics API does
not expose thumbnail impressions; read those in YouTube Studio under
**Analytics**, **Content**.

## Fixing errors

| Message | Fix |
|---|---|
| No OAuth client secret at ... | Do step 3 and save the file as `client_secret.json` next to `outliers.py`. |
| No channel connected for ... | Run `mine auth` with that `--profile`. |
| The stored token ... expired and the refresh failed | Run `mine auth` again for that profile. Testing-mode tokens expire after 7 idle days. |
| The YouTube Analytics API is not enabled | Do step 1 on the same project as the OAuth client. |
| The token lacks the yt-analytics.readonly scope | Run `mine auth` again and approve both scopes. |
