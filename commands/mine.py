"""mine: the owner's own channel analytics as a Partner Program tracker.

Reads the YouTube Analytics API and the Data API with OAuth for a channel
the owner signs in to. vidIQ and TubeLab see public numbers only; this sees
watch time, subscriber flow, and traffic sources for the owner's channels.

Subcommands:
  mine auth     run the consent flow once and store a token
  mine status   subscribers, watch hours, uploads, and the YPP progress block
  mine videos   per-video watch time table for the channel's uploads
  mine traffic  traffic sources for the last 28 days, plus external URLs

Files next to ytlib.py (both belong in .gitignore):
  client_secret.json   Desktop OAuth client from Google Cloud Console
  token.json           the stored token; token-NAME.json with --profile NAME

Setup is in docs/own-channel-setup.md.

Metrics confirmed against the YouTube Analytics API reference
(developers.google.com/youtube/analytics/metrics and channel_reports),
read on 2026-10-05: views, estimatedMinutesWatched, averageViewDuration,
averageViewPercentage, subscribersGained, subscribersLost. Dimensions
confirmed: video, creatorContentType (values LIVE_STREAM, SHORTS, STORY,
VIDEO_ON_DEMAND, UNSPECIFIED), insightTrafficSourceType (includes EXT_URL),
insightTrafficSourceDetail (needs an insightTrafficSourceType filter and
maxResults of 25 or less). NOT in the API: thumbnail `impressions` and
`impressionsClickThroughRate`. The metric list has adImpressions,
cardImpressions, and annotationImpressions only. Thumbnail impressions and
click-through rate live in YouTube Studio alone, so `mine videos` prints a
line saying so instead of a column.
"""

import base64
import datetime as dt
import hashlib
import http.server
import json
import os
import secrets
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import socket
import webbrowser

from ytlib import HOME, Api, ledger_add, fmt_int

ANALYTICS = "https://youtubeanalytics.googleapis.com/v2/reports"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
SCOPES = [
    "https://www.googleapis.com/auth/yt-analytics.readonly",
    "https://www.googleapis.com/auth/youtube.readonly",
]

# Partner Program thresholds and the plan's dates. Hours and subscribers are
# judged on the trailing 365 days at application time.
DEADLINE = dt.date(2026, 12, 15)       # last safe date to apply under the 4,000-hour rule
JUDGED_BEFORE = dt.date(2027, 2, 1)    # applications judged before this date
FULL_TIER = {"subs": 1000, "hours": 4000}
LOWER_TIER = {"subs": 500, "hours": 3000, "uploads_90d": 3}

EXCLUDES = ("YouTube's qualified-hours count excludes private and unlisted "
            "videos, views from the Shorts feed, and views from ad campaigns. "
            "The API has no qualified flag, so this number is an ESTIMATE.")

CONSOLE_LIBRARY = ("https://console.cloud.google.com/apis/library/"
                   "youtubeanalytics.googleapis.com")


# ---------------------------------------------------------------- files

def token_path(profile):
    name = "token-%s.json" % profile if profile else "token.json"
    return os.path.join(HOME, name)


def secret_path(explicit):
    """--client-secret, then $YOUTUBE_OAUTH_CLIENT_SECRET, then ./client_secret.json."""
    for cand in (explicit, os.environ.get("YOUTUBE_OAUTH_CLIENT_SECRET"),
                 os.path.join(HOME, "client_secret.json")):
        if cand:
            return os.path.expanduser(cand)
    return os.path.join(HOME, "client_secret.json")


def load_secret(path):
    if not os.path.exists(path):
        sys.exit(
            "No OAuth client secret at %s. Download the Desktop client JSON "
            "from Google Cloud Console (docs/own-channel-setup.md, step 3) "
            "and save it as client_secret.json next to ytlib.py, or pass "
            "--client-secret PATH." % path
        )
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    client = data.get("installed") or data.get("web")
    if not client or "client_id" not in client or "client_secret" not in client:
        sys.exit(
            "%s is not a Desktop OAuth client file. In Google Cloud Console "
            "create credentials of type OAuth client ID with application type "
            "Desktop app and download its JSON." % path
        )
    return client


def load_token(profile):
    path = token_path(profile)
    if not os.path.exists(path):
        which = ("--profile %s" % profile) if profile else "this profile"
        sys.exit(
            "No channel connected for %s (missing %s). Run "
            "`python3 outliers.py mine%s auth` once and pick the channel at "
            "the consent screen." % (which, path, (" --profile " + profile) if profile else "")
        )
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_token(profile, tok):
    path = token_path(profile)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(tok, f, indent=2)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


# ---------------------------------------------------------------- token logic

def token_expired(tok, now=None, margin=60):
    """True when the access token is missing or ends within `margin` seconds."""
    now = time.time() if now is None else now
    try:
        return float(tok.get("expires_at", 0)) - margin <= now
    except (TypeError, ValueError):
        return True


def token_post(fields):
    data = urllib.parse.urlencode(fields).encode()
    req = urllib.request.Request(TOKEN_URL, data=data, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        text = e.read().decode("utf-8", "replace")
        return {"error": "http_%d" % e.code, "error_description": text[:300]}
    except (urllib.error.URLError, OSError) as e:
        return {"error": "network", "error_description": str(getattr(e, "reason", e))}


def store_response(tok, resp, now=None):
    now = time.time() if now is None else now
    tok["access_token"] = resp["access_token"]
    tok["expires_at"] = now + float(resp.get("expires_in", 3600))
    if resp.get("refresh_token"):
        tok["refresh_token"] = resp["refresh_token"]
    tok["scope"] = resp.get("scope", " ".join(SCOPES))
    return tok


def fresh_token(profile, secret_file):
    """Return a token dict with a live access token, refreshing when needed."""
    tok = load_token(profile)
    if not token_expired(tok):
        return tok
    client = load_secret(secret_path(secret_file))
    resp = token_post({
        "grant_type": "refresh_token",
        "refresh_token": tok.get("refresh_token", ""),
        "client_id": client["client_id"],
        "client_secret": client["client_secret"],
    })
    if "access_token" not in resp:
        flag = (" --profile " + profile) if profile else ""
        sys.exit(
            "The stored token for %s expired and the refresh failed (%s: %s). "
            "Run `python3 outliers.py mine%s auth` again to reconnect the "
            "channel." % (token_path(profile), resp.get("error", "?"),
                          resp.get("error_description", "")[:120], flag)
        )
    tok = store_response(tok, resp)
    save_token(profile, tok)
    return tok


# ---------------------------------------------------------------- consent flow

class _Catcher(http.server.BaseHTTPRequestHandler):
    """Catches the one redirect from Google and stores its query string."""
    result = {}

    def do_GET(self):
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        _Catcher.result = {k: v[0] for k, v in q.items()}
        body = ("<html><body style='font-family:sans-serif'>"
                "<p>Signed in. You can close this tab and go back to the "
                "terminal.</p></body></html>").encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def pkce_pair():
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(48)).rstrip(b"=").decode()
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


def consent(client):
    """Installed-app flow with PKCE on a loopback redirect. Returns the token response."""
    server = http.server.HTTPServer(("127.0.0.1", 0), _Catcher)
    port = server.server_address[1]
    redirect = "http://127.0.0.1:%d/" % port
    verifier, challenge = pkce_pair()
    state = secrets.token_urlsafe(16)
    url = AUTH_URL + "?" + urllib.parse.urlencode({
        "client_id": client["client_id"],
        "redirect_uri": redirect,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "access_type": "offline",
        "prompt": "consent select_account",
        "state": state,
    })
    print("Open this URL, sign in, and pick the channel to connect:\n\n  %s\n" % url)
    print("Waiting on %s for Google to redirect back ..." % redirect)
    t = threading.Thread(target=server.handle_request)
    t.daemon = True
    t.start()
    webbrowser.open(url)
    t.join(600)
    server.server_close()
    got = _Catcher.result
    if not got:
        sys.exit("No redirect arrived within 10 minutes. Run `mine auth` again.")
    if got.get("state") != state:
        sys.exit("The redirect carried the wrong state value. Run `mine auth` again.")
    if "code" not in got:
        sys.exit("Google returned %s. Approve both scopes at the consent screen and run `mine auth` again."
                 % got.get("error", "no code"))
    resp = token_post({
        "grant_type": "authorization_code",
        "code": got["code"],
        "client_id": client["client_id"],
        "client_secret": client["client_secret"],
        "redirect_uri": redirect,
        "code_verifier": verifier,
    })
    if "access_token" not in resp:
        sys.exit("Token exchange failed: %s %s" % (resp.get("error"), resp.get("error_description", "")))
    return resp


# ---------------------------------------------------------------- clients

def not_enabled_message(text):
    if "accessNotConfigured" in text or "has not been used in project" in text:
        return ("The YouTube Analytics API is not enabled on the OAuth client's "
                "project. Open %s and click Enable, then rerun." % CONSOLE_LIBRARY)
    return None


class OAuthApi(Api):
    """ytlib.Api with a Bearer token instead of an API key. Same ledger."""

    def __init__(self, access_token):
        Api.__init__(self, key="")
        self.token = access_token

    def get(self, endpoint, params, cost_units=0, cost_searches=0):
        url = "https://www.googleapis.com/youtube/v3/" + endpoint + "?" + \
            urllib.parse.urlencode(params, doseq=True)
        req = urllib.request.Request(url, headers={"Authorization": "Bearer " + self.token})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                body = json.load(r)
        except urllib.error.HTTPError as e:
            text = e.read().decode("utf-8", "replace")
            if e.code == 401:
                sys.exit("The access token was rejected by the Data API. Run `mine auth` again.")
            if e.code == 403 and "quota" in text.lower():
                sys.exit("Data API quota exhausted for today (resets midnight Pacific).")
            sys.exit("HTTP %s on %s: %s" % (e.code, endpoint, text[:400]))
        except (urllib.error.URLError, socket.timeout, OSError) as e:
            sys.exit("Network error: %s" % getattr(e, "reason", e))
        ledger_add(searches=cost_searches, units=cost_units)
        return body


class Analytics:
    """YouTube Analytics API v2 reports.query. 1 ledger unit per call."""

    def __init__(self, access_token):
        self.token = access_token

    def query(self, **params):
        params.setdefault("ids", "channel==MINE")
        url = ANALYTICS + "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={"Authorization": "Bearer " + self.token})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                body = json.load(r)
        except urllib.error.HTTPError as e:
            text = e.read().decode("utf-8", "replace")
            if e.code == 401:
                sys.exit("The access token was rejected by the Analytics API. Run `mine auth` again.")
            if e.code == 403:
                msg = not_enabled_message(text)
                if msg:
                    sys.exit(msg)
                if "insufficient" in text.lower():
                    sys.exit("The token lacks the yt-analytics.readonly scope. "
                             "Run `mine auth` again and approve both scopes.")
            sys.exit("Analytics HTTP %s: %s" % (e.code, text[:400]))
        except (urllib.error.URLError, socket.timeout, OSError) as e:
            sys.exit("Network error: %s" % getattr(e, "reason", e))
        ledger_add(units=1)
        return body


def parse_report(body):
    """columnHeaders + rows -> list of dicts keyed by column name."""
    names = [h["name"] for h in body.get("columnHeaders", [])]
    return [dict(zip(names, row)) for row in body.get("rows", [])]


def totals(rows, *keys):
    """Sum the named numeric columns across rows."""
    out = {}
    for k in keys:
        out[k] = sum(float(r.get(k, 0) or 0) for r in rows)
    return out


# ---------------------------------------------------------------- arithmetic

def progress(hours_365, subs, today, uploads_90d=None, deadline=DEADLINE):
    """Gap to each YPP tier and the per-day pace needed from `today`."""
    days_left = (deadline - today).days
    out = {
        "days_left": days_left,
        "deadline_passed": days_left <= 0,
        "hours_to_3000": max(0.0, LOWER_TIER["hours"] - hours_365),
        "hours_to_4000": max(0.0, FULL_TIER["hours"] - hours_365),
        "subs_to_500": max(0, LOWER_TIER["subs"] - int(subs)),
        "subs_to_1000": max(0, FULL_TIER["subs"] - int(subs)),
    }
    if uploads_90d is not None:
        out["uploads_to_3"] = max(0, LOWER_TIER["uploads_90d"] - int(uploads_90d))
    for key in ("hours_to_3000", "hours_to_4000", "subs_to_500", "subs_to_1000"):
        rate = key.replace("_to_", "_per_day_to_")
        out[rate] = None if days_left <= 0 else out[key] / days_left
    return out


def hours(minutes):
    return float(minutes) / 60.0


def date_window(days, today=None):
    today = today or dt.date.today()
    return (today - dt.timedelta(days=days)).isoformat(), today.isoformat()


def fmt_hours(h):
    return "{:,.1f}".format(h)


def fmt_rate(x, unit):
    if x is None:
        return "deadline passed"
    return "%.2f %s/day" % (x, unit)


# ---------------------------------------------------------------- Data API bits

def my_channel(api):
    body = api.get("channels", {"part": "snippet,statistics,contentDetails", "mine": "true"}, cost_units=1)
    items = body.get("items", [])
    if not items:
        sys.exit("The signed-in account has no YouTube channel. Pick a channel (a brand "
                 "account) at the consent screen and run `mine auth` again.")
    it = items[0]
    st = it.get("statistics", {})
    return {
        "id": it["id"],
        "title": it["snippet"]["title"],
        "handle": it["snippet"].get("customUrl", ""),
        "subs": int(st.get("subscriberCount", 0)),
        "videoCount": int(st.get("videoCount", 0)),
        "uploads": it["contentDetails"]["relatedPlaylists"]["uploads"],
    }


def public_uploads_since(api, uploads_playlist, cutoff_iso):
    """Public uploads with publishedAt >= cutoff. 1 unit per 50. Shorts count too."""
    count, token = 0, None
    while True:
        p = {"part": "snippet,status", "playlistId": uploads_playlist, "maxResults": 50}
        if token:
            p["pageToken"] = token
        body = api.get("playlistItems", p, cost_units=1)
        stop = False
        for it in body.get("items", []):
            when = it["snippet"].get("publishedAt", "")[:10]
            if when < cutoff_iso:
                stop = True
                break
            if it.get("status", {}).get("privacyStatus") == "public":
                count += 1
        token = body.get("nextPageToken")
        if stop or not token:
            return count


def video_titles(api, ids):
    out = {}
    for i in range(0, len(ids), 50):
        batch = ids[i:i + 50]
        body = api.get("videos", {"part": "snippet", "id": ",".join(batch)}, cost_units=1)
        for it in body.get("items", []):
            out[it["id"]] = (it["snippet"]["title"], it["snippet"].get("publishedAt", "")[:10])
    return out


# ---------------------------------------------------------------- commands

def connect(args):
    tok = fresh_token(args.profile, args.client_secret)
    return tok, OAuthApi(tok["access_token"]), Analytics(tok["access_token"])


def cmd_auth(args):
    client = load_secret(secret_path(args.client_secret))
    resp = consent(client)
    tok = store_response({}, resp)
    api = OAuthApi(tok["access_token"])
    ch = my_channel(api)
    tok["channel"] = {"id": ch["id"], "title": ch["title"], "handle": ch["handle"]}
    save_token(args.profile, tok)
    print("Connected %s (%s, %s) and saved the token to %s" %
          (ch["title"], ch["handle"] or ch["id"], fmt_int(ch["subs"]) + " subscribers",
           token_path(args.profile)))
    if not tok.get("refresh_token"):
        print("Warning: Google sent no refresh token. Remove this app under "
              "myaccount.google.com/permissions and run `mine auth` again.")


def window_report(an, days):
    start, end = date_window(days)
    metrics = "estimatedMinutesWatched,views,subscribersGained,subscribersLost"
    total = totals(parse_report(an.query(startDate=start, endDate=end, metrics=metrics)),
                   "estimatedMinutesWatched", "views", "subscribersGained", "subscribersLost")
    split = parse_report(an.query(startDate=start, endDate=end, metrics="estimatedMinutesWatched,views",
                                  dimensions="creatorContentType"))
    by_type = {r.get("creatorContentType", "?"): float(r.get("estimatedMinutesWatched", 0) or 0)
               for r in split}
    shorts_min = by_type.get("SHORTS", 0.0)
    return {
        "start": start, "end": end,
        "minutes": total["estimatedMinutesWatched"],
        "minutes_no_shorts": total["estimatedMinutesWatched"] - shorts_min,
        "shorts_minutes": shorts_min,
        "views": total["views"],
        "gained": total["subscribersGained"],
        "lost": total["subscribersLost"],
    }


def print_window(label, w):
    print("%s (%s to %s)" % (label, w["start"], w["end"]))
    print("  watch hours, all content:    %s" % fmt_hours(hours(w["minutes"])))
    print("  watch hours, Shorts removed: %s  ESTIMATE" % fmt_hours(hours(w["minutes_no_shorts"])))
    print("  views:                       %s" % fmt_int(w["views"]))
    print("  subscribers gained / lost:   %s / %s (net %s)" %
          (fmt_int(w["gained"]), fmt_int(w["lost"]), fmt_int(w["gained"] - w["lost"])))


def cmd_status(args):
    tok, api, an = connect(args)
    today = dt.date.today()
    ch = my_channel(api)
    y = window_report(an, 365)
    m = window_report(an, 28)
    cutoff = (today - dt.timedelta(days=90)).isoformat()
    uploads = public_uploads_since(api, ch["uploads"], cutoff)
    est_hours = hours(y["minutes_no_shorts"])
    deadline = dt.date.fromisoformat(args.deadline) if getattr(args, "deadline", "") else DEADLINE
    p = progress(est_hours, ch["subs"], today, uploads, deadline=deadline)

    print("%s (%s)" % (ch["title"], ch["handle"] or ch["id"]))
    print("  subscribers: %s   public uploads, last 90 days: %d (Shorts included)" %
          (fmt_int(ch["subs"]), uploads))
    print()
    print_window("Trailing 365 days", y)
    print("  " + EXCLUDES)
    print("  Analytics data lags about 2 to 3 days behind today.")
    print()
    print_window("Last 28 days", m)
    print()
    print("Progress to the Partner Program, judged before %s, apply by %s" %
          (JUDGED_BEFORE.isoformat(), DEADLINE.isoformat()))
    print("  days to %s: %d%s" % (DEADLINE.isoformat(), p["days_left"],
                                  "  (deadline passed)" if p["deadline_passed"] else ""))
    print("  hours to 3,000 (lower tier): %s   need %s" %
          (fmt_hours(p["hours_to_3000"]), fmt_rate(p["hours_per_day_to_3000"], "hours")))
    print("  hours to 4,000 (full tier):  %s   need %s" %
          (fmt_hours(p["hours_to_4000"]), fmt_rate(p["hours_per_day_to_4000"], "hours")))
    print("  subscribers to 500:          %s   need %s" %
          (fmt_int(p["subs_to_500"]), fmt_rate(p["subs_per_day_to_500"], "subs")))
    print("  subscribers to 1,000:        %s   need %s" %
          (fmt_int(p["subs_to_1000"]), fmt_rate(p["subs_per_day_to_1000"], "subs")))
    print("  uploads to 3 in 90 days:     %d" % p["uploads_to_3"])
    print("  pace, last 28 days: %s hours/day and %.2f net subs/day" %
          (fmt_hours(hours(m["minutes_no_shorts"]) / 28.0), (m["gained"] - m["lost"]) / 28.0))


def cmd_videos(args):
    tok, api, an = connect(args)
    start, end = date_window(args.days)
    rows = parse_report(an.query(
        startDate=start, endDate=end,
        metrics="views,estimatedMinutesWatched,averageViewDuration,averageViewPercentage",
        dimensions="video", sort="-estimatedMinutesWatched", maxResults=min(args.limit, 200)))
    if not rows:
        print("No video rows between %s and %s." % (start, end))
        return
    titles = video_titles(api, [r["video"] for r in rows])
    print("Uploads by watch time, %s to %s. Thumbnail impressions and click-through "
          "rate are in YouTube Studio only; the Analytics API has no such metric." % (start, end))
    print()
    print("| # | Title | Published | Views | Watch hours | Avg view | Avg % | Video |")
    print("|---|---|---|---|---|---|---|---|")
    for i, r in enumerate(rows, 1):
        vid = r["video"]
        title, pub = titles.get(vid, ("(unavailable)", ""))
        avd = int(float(r.get("averageViewDuration", 0) or 0))
        print("| %d | %s | %s | %s | %s | %d:%02d | %.1f%% | %s |" % (
            i, title.replace("|", "/")[:70], pub, fmt_int(r.get("views", 0)),
            fmt_hours(hours(r.get("estimatedMinutesWatched", 0))), avd // 60, avd % 60,
            float(r.get("averageViewPercentage", 0) or 0), vid))


def cmd_traffic(args):
    tok, api, an = connect(args)
    start, end = date_window(28)
    src = parse_report(an.query(
        startDate=start, endDate=end, metrics="views,estimatedMinutesWatched",
        dimensions="insightTrafficSourceType", sort="-views"))
    print("Traffic sources, %s to %s" % (start, end))
    print()
    print("| Source | Views | Watch hours |")
    print("|---|---|---|")
    for r in src:
        print("| %s | %s | %s |" % (r["insightTrafficSourceType"], fmt_int(r.get("views", 0)),
                                    fmt_hours(hours(r.get("estimatedMinutesWatched", 0)))))
    if not src:
        print("| (no rows) | 0 | 0 |")
    print()
    ext = parse_report(an.query(
        startDate=start, endDate=end, metrics="views,estimatedMinutesWatched",
        dimensions="insightTrafficSourceDetail", filters="insightTrafficSourceType==EXT_URL",
        sort="-views", maxResults=25))
    print("External URLs (EXT_URL), top 25. Facebook and Instagram shares show up here.")
    print()
    print("| Referrer | Views | Watch hours |")
    print("|---|---|---|")
    for r in ext:
        print("| %s | %s | %s |" % (r["insightTrafficSourceDetail"], fmt_int(r.get("views", 0)),
                                    fmt_hours(hours(r.get("estimatedMinutesWatched", 0)))))
    if not ext:
        print("| (no external referrers in this window) | 0 | 0 |")


# ---------------------------------------------------------------- register

def register(sub):
    p = sub.add_parser("mine", help="the owner's own channel analytics (OAuth) as a YPP tracker")
    p.add_argument("--profile", default="", help="token file name: token-NAME.json (one per channel)")
    p.add_argument("--client-secret", default="", help="path to the Desktop OAuth client JSON")
    ms = p.add_subparsers(dest="mine_cmd", required=True)

    a = ms.add_parser("auth", help="consent flow once; prints the connected channel")
    a.set_defaults(func=cmd_auth)

    s = ms.add_parser("status", help="subscribers, watch hours, uploads, YPP progress")
    s.add_argument("--deadline", default="", help="YYYY-MM-DD the pace is measured against (default %s)" % DEADLINE.isoformat())
    s.set_defaults(func=cmd_status)

    v = ms.add_parser("videos", help="per-video watch time table")
    v.add_argument("--days", type=int, default=365, help="window ending today")
    v.add_argument("--limit", type=int, default=50, help="rows, up to 200")
    v.set_defaults(func=cmd_videos)

    t = ms.add_parser("traffic", help="traffic sources and external URLs, last 28 days")
    t.set_defaults(func=cmd_traffic)
