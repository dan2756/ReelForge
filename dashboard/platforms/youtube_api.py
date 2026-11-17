"""YouTube through Google's own API (YouTube Data API v3, videos.insert): a second way to post there, beside the
browser extension. docs/YOUTUBE-API.md is the page for the person. This file is the Google side of it; the window's
side, and what moves a post's job along the ledger, is dashboard/youtube_api_tab.py.

Nothing here is ReelForge's own at Google. You make a Google Cloud project and a Desktop OAuth client yourself and
point ReelForge at the client file Google gave you. Then:

  connect     Google's consent page opens in your browser. It sends you back to a listener on this computer only
              (127.0.0.1, a free port) that answers that one request and closes. The answer counts only if it
              carries the state this try was started with, and the code in it is traded for the sign-in with a PKCE
              proof (S256), so a code somebody else caught is no use to them. One permission is asked for: uploading
              videos (SCOPE), nothing wider.
  upload      The resumable protocol: start a session, send the file in pieces, and after anything unclear ask the
              session where it stands and carry on from there. One session makes one video, so the same video is
              never sent twice: when the answer to the last piece is lost and the session can't say either, that is
              Unknown, never a new try.
  disconnect  The file is forgotten here first, then Google is told to end the grant.

The sign-in Google gives (a refresh token) is never written down. It stays in the memory of the ReelForge that got
it (_held) and goes when that one closes, so every ReelForge that is opened connects again before it posts. That is
on purpose: with no sign-in in any file, nothing in this folder can upload by itself. A script, or an AI working
here, would first have to get your yes on Google's page.

What is kept: one file for each channel, data/youtube_api.json next to its stats (store_path), written whole and
readable by you alone: the client's id and secret, your choices (who sees a post, the made-for-kids answer), the
channel the last upload went to, and the session of an upload under way. None of it, and nothing of the sign-in, is
ever printed, logged, put in a message or sent anywhere but the addresses below. A sign-in Google has ended (it does
after seven days while your consent screen is in Testing) is "connect again", never something tried over and over.

This was written from Google's published description of the API and tested against a stand-in for Google
(dashboard/tests/test_youtube_api.py). It has never uploaded to Google itself: UNPROVEN says so wherever it shows.

  python -m studio youtube-api          where the connection stands (main, at the end of this file)
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import socketserver
import threading
import time
import unicodedata
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx

from .. import config, db, desktop, sync

# Where anything secret may go: these four addresses and the session address the last one hands out (_ours). They
# are constants on purpose. A client file's own "token_uri" is never used, and no setting or environment variable
# changes them, so nothing can point a token somewhere else. (The tests patch them to a stand-in on this computer.)
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"
SCOPE = "https://www.googleapis.com/auth/youtube.upload"  # upload videos: enough for videos.insert and its answer

PRIVACY = ("private", "unlisted", "public")  # who can see a post; "private" until you choose otherwise
CATEGORY = "22"            # People & Blogs. Said outright, like everything else a post sends
CHUNK = 4 * 1024 * 1024    # a piece of the file: a multiple of 256 KiB, as the protocol asks of all but the last
TRIES = 6                  # how often something unclear is tried again, waiting 1, 2, 4... seconds in between
CONSENT_FOR = 10 * 60      # seconds the listener waits for you on Google's page
TIMEOUT = httpx.Timeout(30, read=120, write=120)
UNPROVEN = ("This way of posting is tested against a stand-in for Google, never against Google itself. Post as "
            "private first and check the video in YouTube Studio.")
NOT_CONNECTED = "YouTube's API isn't connected yet. docs/YOUTUBE-API.md says how to set it up."
AGAIN = {
    "session": ("ReelForge keeps YouTube's sign-in only while it is open, in its memory and never in a file. Connect "
                "before you post through the API: it is one press, then Google's page."),
    "grant": ("YouTube's sign-in has run out or was taken back at Google. Connect again. While your Google Cloud "
              "consent screen is in Testing, Google ends it after 7 days."),
    "client": ("Google no longer knows the OAuth client ReelForge was connected with. Connect again with the client "
               "file of one that exists in your Google Cloud project."),
}
# What Google answers when it refuses an upload, by its own word for it: (a word of ours, a sentence for you).
# The words are the ones Google's reference lists for videos.insert and for its APIs in general, as they were known
# when this was written: the reference couldn't be opened from where this was built, and none of them has been seen
# in an answer from Google. A refusal with a word that isn't here is shown with Google's word (_refusal).
REFUSALS = {
    "quotaExceeded": ("quota", "Your Google Cloud project has used up today's YouTube API allowance. It starts again "
                               "at midnight Pacific time."),
    "dailyLimitExceeded": ("quota", "Your Google Cloud project has used up today's YouTube API allowance. It starts "
                                    "again at midnight Pacific time."),
    "uploadLimitExceeded": ("limit", "YouTube says this channel has uploaded as many videos as it may for now. Try "
                                     "again tomorrow."),
    "accessNotConfigured": ("api_off", "The YouTube Data API v3 isn't switched on in your Google Cloud project. "
                                       "Enable it there, wait a minute, then try again."),
    "SERVICE_DISABLED": ("api_off", "The YouTube Data API v3 isn't switched on in your Google Cloud project. Enable "
                                    "it there, wait a minute, then try again."),
    "youtubeSignupRequired": ("no_channel", "The Google account you connected has no YouTube channel. Connect again "
                                            "with the account your channel belongs to."),
    "insufficientPermissions": ("not_connected", "Google didn't give ReelForge permission to upload. Connect again "
                                                 "and allow it on Google's page."),
    "forbidden": ("forbidden", "YouTube doesn't allow this account to upload this video."),
    "forbiddenPrivacySetting": ("privacy", "YouTube doesn't allow that choice of who can see the video for this "
                                           "channel. Pick another one."),
    "forbiddenLicenseSetting": ("refused", "YouTube didn't take the video's licence setting."),
    "invalidTitle": ("title", "YouTube didn't take the title. It can't be empty, longer than 100 characters, or "
                              "contain < or >."),
    "invalidDescription": ("description", "YouTube didn't take the description. It can't contain < or >, or be "
                                          "longer than 5,000 characters."),
    "invalidCategoryId": ("refused", "YouTube didn't take the video's category."),
    "invalidVideoMetadata": ("refused", "YouTube didn't take the video's details as ReelForge sent them."),
    "invalidFilename": ("refused", "YouTube didn't take the video file's name."),
    "invalidTags": ("refused", "YouTube didn't take the video's tags."),
    "mediaBodyRequired": ("refused", "YouTube says the request had no video in it."),
}
BUSY = ("rateLimitExceeded", "userRateLimitExceeded", "backendError")  # said with a 403: wait, then go on
KINDS = {".mp4": "video/mp4", ".mov": "video/quicktime", ".m4v": "video/x-m4v"}
PAGE = ("<!doctype html><html lang=\"en\"><meta charset=\"utf-8\"><title>ReelForge</title>"
        "<body style=\"font: 17px/1.5 system-ui, sans-serif; max-width: 34em; margin: 4em auto; padding: 0 1em\">"
        "<h1 style=\"font-size: 1.3em\">{title}</h1><p>{body}</p></body></html>")

clock = time.monotonic  # (the tests move it on)
sleep = time.sleep      # (and don't wait)
_store_lock = threading.Lock()
_token_lock = threading.Lock()
_access: dict[str, tuple[str, float]] = {}  # refresh token -> (the access token it last gave, when that runs out)
# A channel's file (store_path, as text) -> the sign-in this ReelForge got for it: {"client_id", "client_secret",
# "refresh_token", "since", "expires"}. Only ever here, in memory: see the top of this file.
_held: dict[str, dict] = {}
_ended: dict[str, str] = {}  # the same -> why Google ended the sign-in this ReelForge had ("grant" or "client")


class ApiError(Exception):
    """Why something here didn't happen, in a sentence for the person. Nothing was posted. code is a word for it
    ("not_connected": connect first, or again). definite: Google itself said no, in words this file knows."""

    def __init__(self, code: str, message: str, definite: bool = False):
        super().__init__(message)
        self.code, self.message, self.definite = code, message, definite


class Unknown(Exception):
    """The last piece of a video went out, and nobody can say whether YouTube has all of it: the video may be up.
    Whoever catches this never tries the upload again by itself."""


class Stopped(Exception):
    """Whoever asked for the upload called it off between two pieces."""


# ---- the file

def store_path() -> Path:
    """Where this channel's connection is kept: in its own data folder, next to its stats, because each channel
    signs in to its own YouTube account. It stays through an update and a move, and goes into the backup on a
    factory reset, like everything else in that folder."""
    return (db._path or config.db_path()).with_name("youtube_api.json")


def read_store() -> dict:
    """The file, or {} while there is none (or it can't be read: then nothing is connected)."""
    try:
        return _read()
    except OSError:
        return {}


def _read() -> dict:
    """The file, or {} while there is none or what is there isn't one. OSError when it is there and can't be read,
    which says nothing about the connection either way."""
    for attempt in range(4):
        try:
            data = json.loads(store_path().read_text("utf-8"))
            return data if isinstance(data, dict) else {}
        except (FileNotFoundError, ValueError):
            return {}
        except PermissionError:  # Windows, while another ReelForge puts a new file in place: a moment later it is free
            if attempt == 3:
                raise
            time.sleep(0.02)
    return {}


def change_store(change: Callable[[dict], object]):
    """Read the file, let change(store) alter it, and write it back if it did: one ReelForge at a time, the new file
    put in place whole and readable by you alone. A store left empty takes the file away. Returns what change
    returned. OSError when it can't be written."""
    path = store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    turn = sync.ReadLock(path.with_name(path.name + ".lock"))
    with _store_lock:
        turn.acquire(wait=5)  # still taken after that: go ahead, as a ReelForge that can't lock files does
        try:
            try:
                store = json.loads(path.read_text("utf-8"))
            except (FileNotFoundError, ValueError):
                store = None
            had = json.dumps(store, sort_keys=True) if isinstance(store, dict) else None
            store = store if isinstance(store, dict) else {}
            out = change(store)
            if not store:
                path.unlink(missing_ok=True)
            elif json.dumps(store, sort_keys=True) != had:
                _write(path, store)
            return out
        finally:
            turn.release()


def _write(path: Path, store: dict) -> None:
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        with os.fdopen(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w", encoding="utf-8") as f:
            json.dump(store, f, indent=2)
        if os.name != "nt":
            os.chmod(tmp, 0o600)  # also when a file by that name was left behind with other rights
        for attempt in range(5):
            try:
                os.replace(tmp, path)
                return
            except PermissionError:  # Windows, while another window is reading it: a moment later it is free
                if attempt == 4:
                    raise
                time.sleep(0.05)
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass  # it was moved into place


def upload_lock() -> sync.ReadLock:
    """Held by whichever ReelForge is uploading for this channel, one video at a time. The system lets go of it
    when that ReelForge closes, so a lock nobody holds means no upload is under way anywhere."""
    path = store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    return sync.ReadLock(path.with_name("youtube_api.upload.lock"))


def _usable(value) -> bool:
    """Whether a value is a secret this file can send: visible ASCII and nothing else. Anything odd would end up
    quoted in an error of the HTTP library's, so it is turned away before it gets there."""
    return isinstance(value, str) and re.fullmatch(r"[\x21-\x7e]{1,4096}", value) is not None


def _word(value, limit: int = 40) -> str:
    """One of Google's own words for something ("quotaExceeded"), cut down to letters: fit to show."""
    return re.sub(r"[^A-Za-z0-9_.-]", "", value if isinstance(value, str) else "")[:limit]


def _line(value, limit: int) -> str:
    """Text from Google (a channel's name) as one line of at most limit characters, without control characters."""
    if not isinstance(value, str):
        return ""
    return " ".join("".join(c for c in value if unicodedata.category(c) != "Cc" or c.isspace()).split())[:limit]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def status() -> dict:
    """Where this channel's connection stands in this ReelForge, with nothing secret in it. state: "on", "again"
    (a client is on file and this ReelForge has no sign-in: why says "session" when it hasn't connected since it
    opened, "grant" or "client" when Google ended the sign-in it had) or "off". client: a client is on file, so
    connecting needs no file. since and expires: when you connected, and when Google said the sign-in runs out, if
    it said. channel: where the last upload went ({"id", "title"}), the only way Google tells with this permission.
    privacy and made_for_kids: your choices for the next post. made_for_kids is None until you have answered:
    nothing here answers that for you."""
    store = read_store()
    client = _usable(store.get("client_id")) and _usable(store.get("client_secret"))
    on = _signed_in(store) if client else None
    channel = store.get("channel") if isinstance(store.get("channel"), dict) else None
    return {
        "state": "on" if on else "again" if client else "off",
        "client": bool(client),
        "since": on["since"] if on else None,
        "expires": on["expires"] if on else None,
        "why": None if on or not client else _ended.get(str(store_path()), "session"),
        "channel": {"id": _word(channel.get("id")), "title": _line(channel.get("title"), 100)} if channel else None,
        "privacy": store.get("privacy") if store.get("privacy") in PRIVACY else "private",
        "made_for_kids": store.get("made_for_kids") if isinstance(store.get("made_for_kids"), bool) else None,
    }


def _signed_in(store: dict | None = None) -> dict | None:
    """The sign-in this ReelForge holds for the channel (_held), while the client it was made with is still the one
    on file: a disconnect in any ReelForge takes the client off the file, and that ends the sign-in in all of them.
    store: the file as just read (None: it is read here)."""
    held = _held.get(str(store_path()))
    if held is None:
        return None
    if store is None:
        try:
            store = _read()
        except OSError:
            return held  # the file can't be read just now, which is not a disconnect
    if all(store.get(key) == held[key] for key in ("client_id", "client_secret")):
        return held
    _held.pop(str(store_path()), None)  # for good: the same client given again doesn't bring it back
    _access.pop(held["refresh_token"], None)
    return None


def _keep_client(store: dict, client: dict) -> None:
    """Put a client on the file (inside change_store)."""
    if store.get("client_id") != client["client_id"]:
        store.pop("channel", None)  # another project: where its uploads go isn't known yet
    # No sign-in is ever on file. These are what an earlier build of this file kept there.
    for gone in ("refresh_token", "connected_at", "expires_at", "lapsed"):
        store.pop(gone, None)
    store.update(client)


def set_defaults(privacy: str | None = None, made_for_kids: bool | None = None) -> None:
    """Your choices for the posts to come: who can see one, and the answer to "is it made for kids"."""
    def change(store: dict) -> None:
        if privacy in PRIVACY:
            store["privacy"] = privacy
        if isinstance(made_for_kids, bool):
            store["made_for_kids"] = made_for_kids

    change_store(change)


def save_session(job: str, uri: str, size: int) -> None:
    """Note an upload's session before any of the video goes: after a quit, it is the only one who can say whether
    the upload finished."""
    change_store(lambda store: store.setdefault("sessions", {}).update({job: {"uri": uri, "size": size, "at": _now()}}))


def session(job: str) -> dict | None:
    saved = read_store().get("sessions")
    found = saved.get(job) if isinstance(saved, dict) else None
    ok = isinstance(found, dict) and isinstance(found.get("uri"), str) and isinstance(found.get("size"), int)
    return found if ok else None


def drop_session(job: str) -> None:
    def drop(store: dict) -> None:
        if isinstance(store.get("sessions"), dict):
            store["sessions"].pop(job, None)
            if not store["sessions"]:
                del store["sessions"]

    try:
        change_store(drop)
    except OSError:
        pass  # it stays on file: a session that is over is no use to anyone


def note_channel(made: dict) -> None:
    """Remember the channel an upload went to (see status)."""
    if made.get("channel_id"):
        try:
            change_store(lambda store: store.update(channel={"id": made["channel_id"],
                                                             "title": made.get("channel_title") or ""}))
        except OSError:
            pass  # an extra


# ---- asking Google

def _client() -> httpx.Client:
    # Never a redirect: a token goes where this file says and nowhere an answer points.
    return httpx.Client(timeout=TIMEOUT, follow_redirects=False)


def _json(r: httpx.Response) -> dict:
    try:
        data = r.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _pause(attempt: int) -> float:
    return float(min(2 ** attempt, 32))


def _token_call(form: dict) -> tuple[int, dict]:
    """One question to Google's token address, asked again while Google is busy or can't be reached. Returns
    (the status, its JSON answer)."""
    for attempt in range(TRIES):
        try:
            with _client() as http:
                r = http.post(TOKEN_URL, data=form)
        except httpx.HTTPError:
            r = None  # (nothing of the error is kept: its words could carry what was sent)
        if r is not None and r.status_code != 429 and r.status_code < 500:
            return r.status_code, _json(r)
        if attempt < TRIES - 1:
            sleep(_pause(attempt))
    raise ApiError("offline", "Google's sign-in service couldn't be reached. Is this computer online? Try again in "
                              "a while.")


def access_token(fresh: bool = False) -> str:
    """A token Google takes for the next call, from the sign-in this ReelForge holds: the one it last gave while
    that is good, else a new one (fresh: a new one whatever). A sign-in Google has ended is let go of, so from then
    on this says "connect again" without asking Google each time."""
    held = _signed_in()
    if held is None:
        why = status()["why"]
        raise ApiError("not_connected", AGAIN[why] if why else NOT_CONNECTED)
    refresh = held["refresh_token"]
    with _token_lock:
        hit = _access.get(refresh)
        if hit and not fresh and hit[1] - 60 > time.time():
            return hit[0]
        code, data = _token_call({"grant_type": "refresh_token", "refresh_token": refresh,
                                  "client_id": held["client_id"], "client_secret": held["client_secret"]})
        token = data.get("access_token")
        if code == 200 and _usable(token):
            _access.clear()
            _access[refresh] = (token, time.time() + _seconds(data.get("expires_in"), 3600))
            return token
        error = data.get("error")
        if error in ("invalid_grant", "invalid_client", "unauthorized_client", "deleted_client"):
            why = "grant" if error == "invalid_grant" else "client"
            _lapse(held, why)
            raise ApiError("not_connected", AGAIN[why])
        raise ApiError("refused", f"Google's sign-in service answered with an error ({_word(error) or code}). Try "
                                  "again in a while.")


def _seconds(value, default: int) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else default


def _lapse(held: dict, why: str) -> None:
    """Google has ended this sign-in: let go of it and note why, so the state is "connect again"."""
    path = str(store_path())
    _access.pop(held["refresh_token"], None)
    if _held.get(path) is held:  # (not one you connected again with in the meantime)
        del _held[path]
        _ended[path] = why


# ---- connecting

def read_client(path: Path) -> dict:
    """The OAuth client in the file Google Cloud gave you (client_secret_<...>.json), as {"client_id",
    "client_secret"}. Only those two are read: the addresses in the file are never used."""
    path = Path(path).expanduser()
    name = path.name
    try:
        if path.stat().st_size > 64 * 1024:
            raise ValueError("too big to be one")
        data = json.loads(path.read_text("utf-8-sig"))
    except FileNotFoundError:
        raise ApiError("client_file", f"There is no file at {path}.") from None
    except (OSError, ValueError, RecursionError):  # (the last: brackets nested deeper than any client file has)
        raise ApiError("client_file", f"{name} isn't a client file from Google Cloud. Download the JSON of your "
                                      "Desktop app client there and pick that file.") from None
    if isinstance(data, dict) and "installed" not in data and "web" in data:
        raise ApiError("client_file", f"{name} is the file of a Web application client. ReelForge needs a Desktop "
                                      "app client: make one in Google Cloud and download its JSON.")
    client = data.get("installed") if isinstance(data, dict) else None
    client = client if isinstance(client, dict) else {}
    if not (_usable(client.get("client_id")) and _usable(client.get("client_secret"))):
        raise ApiError("client_file", f"{name} isn't a client file from Google Cloud. Download the JSON of your "
                                      "Desktop app client there and pick that file.")
    return {"client_id": client["client_id"], "client_secret": client["client_secret"]}


class _Listener(HTTPServer):
    consent: "Consent"
    allow_reuse_address = False  # the port is this try's alone (on Windows, reuse would let another program share it)

    def server_bind(self) -> None:
        socketserver.TCPServer.server_bind(self)  # (HTTPServer's own looks the computer's name up first)
        self.server_name, self.server_port = self.server_address[:2]

    def handle_error(self, request, client_address) -> None:
        """Nothing is printed: a request here carries Google's code."""


class _Return(BaseHTTPRequestHandler):
    """Google sending you back from its consent page: the one request the listener is there for."""

    timeout = 5  # a connection that says nothing is dropped, so the one that matters gets its turn
    server: _Listener

    def version_string(self) -> str:
        return "ReelForge"

    def log_message(self, format, *args) -> None:
        """Nothing is logged: the address carries Google's code."""

    def do_GET(self) -> None:
        consent = self.server.consent
        parts = urlsplit(self.path)
        # Only its own address, and only the page Google sends you back to: a favicon asked for, or another
        # name pointed at this port, is not the answer and doesn't end the wait.
        if self.headers.get("Host") != f"127.0.0.1:{self.server.server_port}" or parts.path != "/":
            self.send_error(404)
            return
        outcome = consent.answer(parse_qs(parts.query))
        title = "ReelForge is connected to YouTube" if outcome["ok"] else "ReelForge wasn't connected"
        text = "You can close this tab and go back to ReelForge." if outcome["ok"] else outcome["message"]
        body = PAGE.format(title=title, body=text.replace("&", "&amp;").replace("<", "&lt;")).encode("utf-8")
        self.send_response(200 if outcome["ok"] else 400)
        for header, value in (("Content-Type", "text/html; charset=utf-8"), ("Content-Length", str(len(body))),
                              ("Cache-Control", "no-store"), ("Referrer-Policy", "no-referrer"),
                              ("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'"),
                              ("Connection", "close")):
            self.send_header(header, value)
        self.end_headers()
        self.wfile.write(body)


class Consent:
    """One try at connecting: Google's consent page is open in your browser, and a listener on this computer waits
    for Google to send you back. done is set when it is over, and outcome then says how: {"ok", "code", "message"}
    (code: "connected", "state", "refused", "timeout", "cancelled" or what the trade for the sign-in ran into)."""

    def __init__(self, client: dict, on_done: Callable[[dict], object] | None = None):
        self.client, self.on_done = client, on_done
        self.state = secrets.token_urlsafe(32)
        self.verifier = secrets.token_urlsafe(64)  # PKCE: only its SHA-256 goes to the consent page
        self.outcome: dict | None = None
        self.done = threading.Event()
        self.lock = threading.Lock()
        self.httpd = _Listener(("127.0.0.1", 0), _Return)  # any free port: Google takes any for a Desktop client
        self.httpd.consent, self.httpd.timeout = self, 0.25
        self.redirect = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        self.until = clock() + CONSENT_FOR

    def url(self) -> str:
        """Google's consent page for this try. It carries the client's id, so it is opened and never shown."""
        challenge = base64.urlsafe_b64encode(hashlib.sha256(self.verifier.encode("ascii")).digest()).rstrip(b"=")
        return AUTH_URL + "?" + urlencode({
            "client_id": self.client["client_id"], "redirect_uri": self.redirect, "response_type": "code",
            "scope": SCOPE, "state": self.state, "code_challenge": challenge.decode("ascii"),
            "code_challenge_method": "S256", "access_type": "offline", "prompt": "consent"})

    def listen(self) -> None:
        """Wait for the one answer, then close the port. Runs on a thread of its own."""
        try:
            while self.outcome is None and clock() < self.until:
                self.httpd.handle_request()
        finally:
            self.httpd.server_close()
            self._end(False, "timeout", "Nobody answered on Google's page in ten minutes, so nothing was "
                                        "connected. Connect again when you're ready.")
            self.done.set()
            if self.on_done:
                self.on_done(self.outcome)

    def cancel(self) -> None:
        """Stop waiting. Nothing is connected."""
        self._end(False, "cancelled", "Stopped. Nothing was connected.")

    def _end(self, ok: bool, code: str, message: str) -> dict:
        with self.lock:
            if self.outcome is None:  # the first word on how it ended stands
                self.outcome = {"ok": ok, "code": code, "message": message}
            return self.outcome

    def answer(self, query: dict[str, list[str]]) -> dict:
        """What Google's page sent you back with: checked, then traded for the sign-in. Returns the outcome."""
        def first(key: str) -> str:
            return (query.get(key) or [""])[0]

        if not hmac.compare_digest(first("state").encode("utf-8", "replace"), self.state.encode()):
            return self._end(False, "state", "That answer didn't come from the page ReelForge opened, so nothing "
                                             "was connected. Connect again.")
        if first("error") or not first("code"):
            if first("error") == "access_denied":
                return self._end(False, "refused", "You said no on Google's page, so nothing was connected.")
            return self._end(False, "refused", "Google's page ended with an error"
                             + (f" ({_word(first('error'))})" if _word(first("error")) else "")
                             + ", so nothing was connected.")
        try:
            code, data = _token_call({"grant_type": "authorization_code", "code": first("code"),
                                      "client_id": self.client["client_id"],
                                      "client_secret": self.client["client_secret"],
                                      "redirect_uri": self.redirect, "code_verifier": self.verifier})
        except ApiError as e:
            return self._end(False, e.code, e.message + " Nothing was connected.")
        refresh, token = data.get("refresh_token"), data.get("access_token")
        if code != 200 or not _usable(token):
            said = _word(data.get("error")) or str(code)
            which = (" Check that the client file is the one of a Desktop app client in your Google Cloud project."
                     if data.get("error") in ("invalid_client", "unauthorized_client", "deleted_client") else "")
            return self._end(False, "refused", f"Google didn't give ReelForge the sign-in ({said}), so nothing was "
                                               f"connected.{which}")
        granted = data.get("scope")
        if isinstance(granted, str) and SCOPE not in granted.split():
            return self._end(False, "scope", "Google's page didn't give ReelForge permission to upload videos, so "
                                             "nothing was connected. Connect again and allow it there.")
        if not _usable(refresh):
            return self._end(False, "refused", "Google gave no lasting sign-in, so nothing was connected. Connect "
                                               "again.")
        lasts = data.get("refresh_token_expires_in")  # sent when the sign-in is one that runs out (Testing: 7 days)
        with self.lock:
            if self.outcome is not None:
                return self.outcome  # called off while Google was answering: nothing is kept

            try:
                change_store(lambda store: _keep_client(store, self.client))
            except OSError:
                self.outcome = {"ok": False, "code": "store", "message": (
                    f"ReelForge couldn't write {store_path().name} in its data folder, so nothing was connected.")}
                return self.outcome
            ends = datetime.now(timezone.utc) + timedelta(seconds=_seconds(lasts, 0))
            path = str(store_path())
            with _token_lock:
                # The sign-in: held here and nowhere else, until this ReelForge closes.
                _held[path] = {**self.client, "refresh_token": refresh, "since": _now(),
                               "expires": ends.isoformat(timespec="seconds") if _seconds(lasts, 0) else None}
                _ended.pop(path, None)
                _access.clear()
                _access[refresh] = (token, time.time() + _seconds(data.get("expires_in"), 3600))
            self.outcome = {"ok": True, "code": "connected", "message": "ReelForge is connected to YouTube's API."}
            return self.outcome


def connect(client_file: Path | str | None = None, on_done: Callable[[dict], object] | None = None) -> Consent:
    """Start connecting: read the client file (None: the client already on file, to connect again), open Google's
    consent page in your browser and listen for the answer. Returns at once; on_done(outcome) is called from the
    listener's thread when it is over. ApiError when it can't start: nothing is listening then."""
    if client_file:
        client = read_client(Path(client_file))
    else:
        store = read_store()
        if not (_usable(store.get("client_id")) and _usable(store.get("client_secret"))):
            raise ApiError("client_file", "Pick the client file you downloaded from Google Cloud "
                                          "(client_secret_....json). docs/YOUTUBE-API.md says how to get one.")
        client = {"client_id": store["client_id"], "client_secret": store["client_secret"]}
    try:
        consent = Consent(client, on_done)
    except OSError:
        raise ApiError("listen", "ReelForge couldn't listen on this computer for Google's answer, so nothing was "
                                 "started.") from None
    threading.Thread(target=consent.listen, daemon=True, name="youtube-api-consent").start()
    if not desktop.open_url(consent.url()):
        consent.on_done = None  # whoever asked hears it from the error
        consent.cancel()
        raise ApiError("no_browser", "ReelForge couldn't open your browser for Google's consent page, so nothing "
                                     "was connected.")
    return consent


def disconnect() -> dict:
    """Forget this channel's connection, then tell Google to end the grant. {"was": there was something to
    forget, "revoked": True, False (Google couldn't be told) or None (this ReelForge held no sign-in to end)}. The
    file goes first: with the client off it, no ReelForge can post any more, whatever Google answers."""
    path = str(store_path())
    held = _held.get(path)
    if held is None and not read_store() and not store_path().exists():
        return {"was": False, "revoked": None}
    change_store(lambda s: s.clear())
    with _token_lock:
        _held.pop(path, None)
        _ended.pop(path, None)
        _access.clear()
    if held is None:
        return {"was": True, "revoked": None}
    try:
        with _client() as http:
            r = http.post(REVOKE_URL, data={"token": held["refresh_token"]})
        # (a token Google doesn't know any more is as ended as one it just ended)
        revoked = r.status_code == 200 or (r.status_code == 400 and _json(r).get("error") == "invalid_token")
    except httpx.HTTPError:
        revoked = False
    return {"was": True, "revoked": revoked}


# ---- uploading

def upload(video: Path, title: str, description: str, privacy: str, made_for_kids: bool | None, *,
           started: Callable[[str], object], progress: Callable[[int, int], object] = lambda sent, size: None,
           sha256: str | None, last: Callable[[], object] = lambda: None,
           stop: Callable[[], bool] = lambda: False) -> dict:
    """Send a video to the connected channel and return what YouTube says it made of it (_made).

    Who can see it always goes with it, and is asked back: left out, YouTube would make the video public. The
    made-for-kids answer is a declaration of yours, so it goes only once you have given one (None: nothing is
    declared, and the audience setting of your channel at YouTube applies). started(address) is called with the
    session's address before any of the file goes, progress(sent, size) after each piece, and last() before the
    piece that ends the upload: from then on the video may exist. stop() saying True ends it between two pieces
    (Stopped).

    sha256: the video you said yes to, which every caller has to name (None only where nobody said yes to any).
    Each piece is read from the file as it is when it goes, so a new cut written over the file meanwhile (with its
    size and time kept, or longer) would go up spliced into the old one. So the bytes are added up as they go, a
    piece sent again must be the same bytes as the first time, and the piece that ends the upload goes only when
    everything sent is that video: ApiError "changed" otherwise, before anything could be posted. (The file is read
    once, as it goes up, not once more before.)

    ApiError: it didn't happen, and nothing was posted. Unknown: the last piece went out and neither its answer nor
    the session says whether YouTube has the whole file. Nothing here ever starts a second session for a video.
    """
    if privacy not in PRIVACY:
        raise ValueError("who can see the video has to be said")
    size = video.stat().st_size
    if not size:
        raise ApiError("empty", f"{video.name} is empty.")
    kind = KINDS.get(video.suffix.lower(), "application/octet-stream")
    body = {"snippet": {"title": title, "description": description, "categoryId": CATEGORY},
            "status": {"privacyStatus": privacy}}
    if isinstance(made_for_kids, bool):
        body["status"]["selfDeclaredMadeForKids"] = made_for_kids
    uri = _start(body, size, kind)
    started(uri)
    sent = stuck = 0  # stuck: how many pieces in a row left YouTube with no more of the file than before
    out = False       # the piece that ends the upload has left this computer at least once
    check = _Sent(video.name) if sha256 else None
    with video.open("rb", buffering=0) as f, _client() as http:  # (unbuffered: each piece is read as the file is now)
        while True:
            if stop():
                raise Stopped()
            end = min(sent + CHUNK, size)
            again = out  # the piece that ends the upload left before: this would be a second sending of it
            try:
                if stuck > TRIES:
                    raise ApiError("offline", "The connection to YouTube kept breaking, so the upload stopped.")
                piece = _piece(f, sent, end - sent)
                if len(piece) != end - sent:  # the file ends before where it ended when the upload started
                    raise ApiError("changed", f"{video.name} changed while it was being uploaded.")
                if check:
                    check.add(sent, piece)
                    if end == size and check.digest() != sha256:
                        raise ApiError("changed", f"{video.name} changed while it was being uploaded.")
                if end == size and not out:
                    last()
                out = out or end == size
                what, value = _stands(_put(http, uri, {"Content-Type": kind,
                                                       "Content-Range": f"bytes {sent}-{end - 1}/{size}"}, piece),
                                      size, ends=end == size)
                if what not in ("made", "have"):
                    # No answer, or one that says nothing about the piece: the session knows what it has.
                    if what != "auth":
                        sleep(_pause(stuck))
                    what, value = _where(http, uri, size, fresh=what == "auth")
            except ApiError as e:
                # Once the last piece has gone out, only YouTube's own "no" to its first sending means nothing was
                # posted. A "no" to a second sending may be about a video the first one made after all.
                if out and (again or not e.definite):
                    raise Unknown() from None
                raise
            if what == "made":
                return value
            stuck = 0 if value > sent else stuck + 1
            sent = value
            progress(sent, size)


class _Sent:
    """What has gone of a video, added up in order: its SHA-256 so far, and the last piece read, which a piece sent
    again (YouTube had less of it than was sent) is held against."""

    def __init__(self, name: str):
        self.name, self.sha, self.upto, self.last = name, hashlib.sha256(), 0, (0, b"")

    def add(self, start: int, piece: bytes) -> None:
        """A piece about to go. ApiError "changed": it isn't the bytes that went there before."""
        at, before = self.last
        if start > self.upto or start < at:
            # YouTube has always said it has at most what was sent, and never less than it had: a piece outside that
            # can't be checked against what went, so it doesn't go.
            raise ApiError("odd", "YouTube asked for a part of the video ReelForge can't check, so the upload stopped.")
        again = piece[:self.upto - start]
        if again != before[start - at:start - at + len(again)]:
            raise ApiError("changed", f"{self.name} changed while it was being uploaded.")
        self.sha.update(piece[len(again):])
        self.upto = max(self.upto, start + len(piece))
        self.last = (start, piece)

    def digest(self) -> str:
        return self.sha.hexdigest()


def _piece(f, start: int, length: int) -> bytes:
    """A piece of the file as it is now: read until it is whole or the file ends. One read may give less than was
    asked for (on a network drive, say) without the file having changed."""
    f.seek(start)
    parts, left = [], length
    while left:
        part = f.read(left)
        if not part:
            break
        parts.append(part)
        left -= len(part)
    return b"".join(parts)


def ask(uri: str, size: int) -> dict | None:
    """Where a saved session stands, for an upload nobody is sending any more: what YouTube made of it (_made), or
    None when it never got the whole file. ApiError when that can't be learned."""
    if not _ours(uri):
        raise ApiError("session", "The upload's saved address isn't one of Google's.")
    with _client() as http:
        what, value = _where(http, uri, size)
    return value if what == "made" else None


def _start(body: dict, size: int, kind: str) -> str:
    """Open a session for one video and return its address. Nothing of the video has gone yet, so this may be
    tried again freely."""
    fresh = renewed = False
    for attempt in range(TRIES):
        token = access_token(fresh)
        fresh = False
        try:
            with _client() as http:
                r = http.post(UPLOAD_URL, params={"uploadType": "resumable", "part": "snippet,status"},
                              content=json.dumps(body).encode("ascii"),
                              headers={"Authorization": f"Bearer {token}", "X-Upload-Content-Length": str(size),
                                       "X-Upload-Content-Type": kind,
                                       "Content-Type": "application/json; charset=UTF-8"})
        except httpx.HTTPError:
            r = None
        if r is not None and r.status_code == 200:
            uri = r.headers.get("location", "")
            if not _ours(uri):
                raise ApiError("odd", "Google answered with an upload address ReelForge doesn't know, so nothing "
                                      "was sent.")
            return uri
        if r is not None and r.status_code == 401 and not renewed:
            fresh = renewed = True  # a token Google stopped taking early: one new one, at once
            continue
        if r is not None and r.status_code not in (408, 429) and r.status_code < 500:
            refusal = _refusal(r)
            if r.status_code == 401 and refusal.code == "refused":  # (not one of the reasons it has a sentence for)
                raise ApiError("not_connected", "YouTube didn't take ReelForge's sign-in. Connect again.")
            if refusal.code != "busy":
                raise refusal
        if attempt < TRIES - 1:
            sleep(_pause(attempt))
    raise ApiError("offline", "YouTube couldn't be reached, so the upload didn't start. Is this computer online?")


def _ours(uri) -> bool:
    """Whether a session's address is one a token may go to: Google's API hosts over https, or wherever UPLOAD_URL
    itself points."""
    if not _usable(uri):
        return False
    try:
        parts, home = urlsplit(uri), urlsplit(UPLOAD_URL)
        host = (parts.hostname or "").lower()
    except ValueError:
        return False
    return (parts.scheme, parts.netloc) == (home.scheme, home.netloc) or \
        (parts.scheme == "https" and host.endswith(".googleapis.com"))


def _put(http: httpx.Client, uri: str, headers: dict, content: bytes, fresh: bool = False) -> httpx.Response | None:
    """A piece of the file, or (with no content) the question where the session stands. None: no answer came, and
    whether the piece arrived is for the session to say."""
    token = access_token(fresh)
    try:
        return http.put(uri, content=content, headers={"Authorization": f"Bearer {token}", **headers})
    except httpx.HTTPError:
        return None


def _stands(r: httpx.Response | None, size: int, ends: bool = True) -> tuple[str, object]:
    """What an answer from a session means: ("made", the video) when the upload is finished, ("have", how many
    bytes YouTube has) when it wants more, ("auth", None) when it wants a newer token, ("unclear", None) when it
    says nothing either way (no answer, or YouTube is busy). ApiError for a refusal. ends: the answer is to the
    piece that ends the upload, or to the question where it stands: only those can say it is finished."""
    if r is None or r.status_code in (408, 429) or r.status_code >= 500:
        return "unclear", None
    if r.status_code in (200, 201):
        # Finished is a video with its id, in answer to something that can finish an upload. Any other "OK" (a
        # page some proxy put in the way, say) is no word of YouTube's about the upload: the session is asked.
        made = _made(_json(r))
        return ("made", made) if ends and made["id"] else ("unclear", None)
    if r.status_code == 308:
        got = re.fullmatch(r"bytes=0-(\d+)", r.headers.get("range", "").strip())
        have = int(got[1]) + 1 if got else 0  # (no Range: it has nothing yet)
        # (all of the file, and still "send more": it hasn't said what it made of it, so it is asked again)
        return ("have", have) if have < size else ("unclear", None)
    if r.status_code == 401:
        return "auth", None
    if r.status_code in (404, 410):
        raise ApiError("session", "YouTube has let go of this upload, so it stopped.")
    refusal = _refusal(r)
    if refusal.code == "busy":
        return "unclear", None
    raise refusal


def _where(http: httpx.Client, uri: str, size: int, fresh: bool = False) -> tuple[str, object]:
    """Ask a session where it stands until it says: ("made", the video) or ("have", bytes). ApiError when it
    can't be learned."""
    renewed = fresh
    for attempt in range(TRIES):
        try:
            what, value = _stands(_put(http, uri, {"Content-Range": f"bytes */{size}"}, b"", fresh), size)
        except ApiError as e:
            e.definite = False  # a "no" to the question says nothing about what the session has
            raise
        fresh = False
        if what in ("made", "have"):
            return what, value
        if what == "auth":
            if renewed:  # even a brand-new token isn't taken
                raise ApiError("not_connected", "YouTube stopped taking ReelForge's sign-in. Connect again.")
            fresh = renewed = True
            continue
        if attempt < TRIES - 1:
            sleep(_pause(attempt))
    raise ApiError("offline", "YouTube couldn't be reached to ask about the upload.")


def _refusal(r: httpx.Response) -> ApiError:
    """Google's "no" as a sentence. Its own free text is never passed on, only its word for the reason."""
    error = _json(r).get("error")
    error = error if isinstance(error, dict) else {}
    reasons = [e.get("reason") for key in ("errors", "details") for e in
               (error.get(key) if isinstance(error.get(key), list) else []) if isinstance(e, dict)]
    reasons = [x for x in reasons if isinstance(x, str) and _word(x)]
    if any(x in BUSY for x in reasons):
        return ApiError("busy", "YouTube is limiting how fast it takes requests.")
    known = next((x for x in reasons if x in REFUSALS), None)
    if known:
        return ApiError(*REFUSALS[known], definite=True)
    # A word this file doesn't know is shown, and proves nothing about what YouTube did with the upload.
    said = _word(reasons[0]) if reasons else f"error {r.status_code}"
    return ApiError("refused", f"YouTube refused the upload ({said}).")


def _made(data: dict) -> dict:
    """What YouTube says it made of an upload, cut down to what ReelForge uses: {"id" (None if it isn't a video
    id), "privacy" (as YouTube stored it: None if it didn't say), "made_for_kids" (your declaration as it stored
    it: None if it has none), "for_kids" (whether YouTube treats the video as made for kids, whoever said so: None
    if it didn't say), "upload_status", "reason" (why it was rejected or failed, if it was), "channel_id",
    "channel_title"}."""
    status = data.get("status") if isinstance(data.get("status"), dict) else {}
    snippet = data.get("snippet") if isinstance(data.get("snippet"), dict) else {}
    video, kids = data.get("id"), status.get("selfDeclaredMadeForKids")
    return {
        "id": video if isinstance(video, str) and re.fullmatch(r"[\w-]{11}", video) else None,
        "privacy": status.get("privacyStatus") if status.get("privacyStatus") in PRIVACY else None,
        "made_for_kids": kids if isinstance(kids, bool) else None,
        "for_kids": status.get("madeForKids") if isinstance(status.get("madeForKids"), bool) else None,
        "upload_status": _word(status.get("uploadStatus")),
        "reason": _word(status.get("rejectionReason") or status.get("failureReason")),
        "channel_id": _word(snippet.get("channelId")),
        "channel_title": _line(snippet.get("channelTitle"), 100),
    }


# ---- the command

USAGE = """reelforge youtube-api               where the connection to YouTube's API stands (it is optional)
reelforge youtube-api client FILE   take the client file from your own Google Cloud project, so that ReelForge's
                                    window can connect with it
reelforge youtube-api disconnect    forget the client: no ReelForge can connect or post until it is given again

docs/YOUTUBE-API.md says how to set it up, and what is and isn't known about it.

None of these posts anything, and none of them signs in. You connect in ReelForge's window, on Google's page, and a
post starts when you press it there."""
NO_GRANT = ("If you ever allowed ReelForge on Google's page, Google still lists it: take it off at "
            "https://myaccount.google.com/connections")


def main(argv: list[str]) -> int:
    """reelforge youtube-api [client FILE | disconnect] (USAGE). A command never holds a sign-in (it would end with
    the command), so nothing here can connect or post."""
    if not argv:
        said = status()
        print("YouTube's API (optional: a second way to post to YouTube, with your own Google Cloud project)")
        print("  client     " + ("on file" if said["client"] else
                                 "none yet: reelforge youtube-api client FILE (the client file from Google Cloud)"))
        if said["client"]:
            print("  sign-in    never kept in a file: you connect in ReelForge's window, each time you open it")
            channel = said["channel"]
            print("  channel    " + (f"{channel['title'] or channel['id']} (where the last upload went)" if channel
                                     else "Google says which one with the first upload"))
            kids = {True: "yes", False: "no"}.get(said["made_for_kids"],
                                                  "not answered, so your channel's own setting at YouTube applies")
            print(f"  posts      start as {said['privacy']}, made for kids: {kids}")
        print(f"  kept in    {store_path()}" + ("" if store_path().exists() else "  (nothing yet)"))
        print(f"\n{UNPROVEN}\ndocs/YOUTUBE-API.md says how to set it up, and what is and isn't known about it.")
        return 0
    if argv[0] == "client" and len(argv) == 2:
        try:
            client = read_client(Path(argv[1]))
            change_store(lambda store: _keep_client(store, client))
        except ApiError as e:
            print(e.message)
            return 1
        except OSError as e:
            print(f"ReelForge couldn't write {store_path()} ({e}). Nothing was kept.")
            return 1
        print("ReelForge has the client, and you can delete the file you downloaded. Nothing is connected yet: you "
              "connect in ReelForge's window, on Google's page, each time you open it.")
        return 0
    if argv == ["disconnect"]:
        try:
            done = disconnect()
        except OSError as e:
            print(f"ReelForge couldn't change {store_path()} ({e}). Nothing was disconnected.")
            return 1
        print(f"Disconnected. ReelForge has forgotten the client, so no ReelForge can post through the API. {NO_GRANT}"
              if done["was"] else "YouTube's API wasn't set up.")
        return 0
    print(USAGE)
    return 0 if argv[0] in ("-h", "--help", "help") else 2
