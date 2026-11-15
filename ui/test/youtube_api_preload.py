"""For the window's tests of posting to YouTube through its API (ui/test/youtube_api.test.tsx): posting_preload.py's
scratch channel, with Google played by the stand-in the Python tests use (dashboard/tests/google.py), listening on
this computer inside the window's Python side. The addresses dashboard/platforms/youtube_api.py talks to are pointed
at it, a piece of an upload is four bytes (the videos here are a few bytes long), and nothing waits between tries.

"Your browser" is played here too. When ReelForge opens Google's consent page, this follows it the way a browser does
after your answer there, and notes "google consent" in REELFORGE_OPENED's file, never the page's address. With
consent "later" the page stays open until the test answers it.

What a test asks for comes in REELFORGE_TEST_POSTING beside posting_preload's own keys, under "api". Without that
key the connection's file is left as it is: a window opened later on the same folder.

  client     true: a client file was handed over in a ReelForge that has closed since, so this window is not
             connected. "on": this window is connected, as after Connect and your yes on Google's page
  kids       your made-for-kids answer on file, true or false (none unless given)
  privacy    who can see a new post, if you changed it
  consent    "yes" (unless given), "no" (you refuse on Google's page) or "later"
  hold       how many pieces Google takes (of every upload together) before the next one waits for the test (0: the
             first one waits). None wait unless given
  google     what the stand-in is told before the window starts (its own words: start_script, script, then,
             stores_as, channel_kids, upload_status). A refusal is [status, "reason"]

The client file Google Cloud hands out is <the folder>/client_secret_test.json, and a Web application's, which
ReelForge refuses, is client_secret_web.json. The test_google call added here lets a test move the stand-in along
and read what it was sent. Nothing here reaches Google or the internet, and no browser opens."""

from __future__ import annotations

import json
import os
import runpy
import time
from pathlib import Path

import httpx

from dashboard import __main__ as entry
from dashboard import desktop
from dashboard.platforms import youtube_api
from dashboard.service import Dashboard
from dashboard.tests.google import Google
from studio.bridge import rpc

runpy.run_path(str(Path(__file__).with_name("posting_preload.py")), run_name="preload")
ROOT = Path(os.environ["REELFORGE_TEST_ROOT"]).resolve()
API = json.loads(os.environ.get("REELFORGE_TEST_POSTING") or "{}").get("api")
ASKED = API or {}
CLIENT = {"client_id": "1234567890-abcdefghijklmnop.apps.googleusercontent.com", "client_secret": "GOCSPX-not-a-real-one"}
GOOGLE = Google()
HOLD = {"after": ASKED.get("hold")}  # the pieces Google takes before one waits for the test (None: none waits)
PENDING: list[str] = []         # Google's consent page, open and not answered yet (consent "later")
SETTING_UP: list[bool] = []     # not empty while the connection the test asked for is being made

os.environ["NO_PROXY"] = os.environ["no_proxy"] = "127.0.0.1"  # the stand-in is on this computer
youtube_api.AUTH_URL, youtube_api.TOKEN_URL = f"{GOOGLE.base}/auth", f"{GOOGLE.base}/token"
youtube_api.REVOKE_URL, youtube_api.UPLOAD_URL = f"{GOOGLE.base}/revoke", f"{GOOGLE.base}/upload/youtube/v3/videos"
youtube_api.CHUNK = 4
youtube_api.sleep = lambda seconds: None


def told(value):
    """What a test tells the stand-in, as the stand-in takes it: a refusal is a (status, reason) pair."""
    if isinstance(value, list):
        return tuple(value) if len(value) == 2 and isinstance(value[0], int) else [told(v) for v in value]
    return value


def wait_for_the_test(pieces_before: int) -> None:
    """A piece of an upload has arrived: it waits here while the test holds it (half a minute at most)."""
    until = time.monotonic() + 30
    while HOLD["after"] is not None and pieces_before >= HOLD["after"] and time.monotonic() < until:
        time.sleep(0.02)


GOOGLE.consent = ASKED.get("consent", "yes")
GOOGLE.on_put = wait_for_the_test
for name, value in (ASKED.get("google") or {}).items():
    setattr(GOOGLE, name, told(value))

(ROOT / "client_secret_test.json").write_text(json.dumps({"installed": {
    **CLIENT, "project_id": "night-physics", "auth_uri": "https://elsewhere.invalid/auth",
    "token_uri": "https://elsewhere.invalid/token", "redirect_uris": ["http://localhost"]}}), encoding="utf-8")
(ROOT / "client_secret_web.json").write_text(json.dumps({"web": CLIENT}), encoding="utf-8")

other_pages = desktop.open_url  # posting_preload's: every other page is listed in REELFORGE_OPENED's file


def answer(url: str) -> None:
    """Your answer on Google's consent page: the browser goes where Google sends it, back to ReelForge."""
    httpx.get(url, follow_redirects=True, timeout=10)


def open_url(url: str) -> bool:
    if not url.startswith(GOOGLE.base):
        return other_pages(url)
    if not SETTING_UP:
        other_pages("google consent")
    if GOOGLE.consent == "later":
        PENDING.append(url)
    else:
        answer(url)
    return True


desktop.open_url = open_url
with_the_channel = entry.load_platforms


def load_platforms(*args, **kwargs):
    """posting_preload's channel, then the connection to YouTube's API the test asked for (it is kept beside the
    stats database, which is open by now)."""
    out = with_the_channel(*args, **kwargs)
    if ASKED.get("client"):
        youtube_api.change_store(lambda store: youtube_api._keep_client(store, CLIENT))
    if ASKED.get("client") == "on":
        consent, GOOGLE.consent = GOOGLE.consent, "yes"
        SETTING_UP.append(True)  # (a connection from before the test starts: no page is noted for it)
        youtube_api.connect().done.wait(10)
        SETTING_UP.clear()
        GOOGLE.consent = consent
    if "kids" in ASKED or "privacy" in ASKED:
        youtube_api.set_defaults(ASKED.get("privacy"), ASKED.get("kids"))
    return out


entry.load_platforms = load_platforms


@rpc
def test_google(self, what: str, *args):
    """The stand-in for Google, from the test. answer: your answer on the consent page that is open ("yes" or
    "no"). hold: pieces wait once Google has taken this many, counted over every upload. release: every piece goes
    through. tell: set what it does next ({"script": [...], ...}). end_grant: Google ends the sign-in it gave. sent:
    what it was sent and what it made."""
    if what == "answer":
        GOOGLE.consent = args[0] if args else "yes"
        answer(PENDING.pop())
    elif what == "hold":
        HOLD["after"] = args[0]
    elif what == "release":
        HOLD["after"] = None
    elif what == "tell":
        for name, value in args[0].items():
            setattr(GOOGLE, name, told(value))
    elif what == "end_grant":
        GOOGLE.end_grant()
        youtube_api._access.clear()
    return {"consents": len(GOOGLE.asked), "starts": [s["body"] for s in GOOGLE.starts], "pieces": len(GOOGLE.puts),
            "videos": [v["video"] for v in GOOGLE.videos], "revoked": len(GOOGLE.revoke_calls)}


Dashboard.test_google = test_google
