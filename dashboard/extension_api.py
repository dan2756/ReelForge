"""The door the browser extension comes in by: a small web server on this computer only, the pairing that lets one
browser in, and the two local pages ReelForge opens in your browser (dashboard/web/). docs/EXTENSION.md says what
all of it is for; the ledger it moves along is dashboard/posting.py.

Each ReelForge window (so each channel that is open) listens on 127.0.0.1, on the first free port from 47821 to
47830, while it is open. `reelforge extension serve` does the same without a window. Never the demo.

Who gets in:

  the extension   A browser is let in once, on the pair page: the page carries a secret that works one time, for ten
                  minutes, and the extension trades it for a token. Only the SHA-256 of that token is kept here
                  (dashboard/data/extension.json, one file for every channel of this ReelForge folder). The token is
                  the only real authentication: it goes in the X-ReelForge-Token header of every /v1 call but hello.
                  Before the extension sends it anywhere, hello has to prove that this is the ReelForge the token
                  came from (proof): ten ports on this computer are easy for another program to listen on.
  the local pages /pair and /post/<batch>, opened by ReelForge with a key in their address that is good for two
                  hours and for that one page. They can read where their own batch stands and ask for the by-hand
                  flow, nothing else.
  nobody else     A request is refused unless its Host is exactly 127.0.0.1:<port> (so no other name can be pointed
                  at this server), and when it carries an Origin, unless that is the extension's or this server's
                  own. A web page can therefore neither read anything here nor send the token header. Only GET and
                  POST, JSON bodies of at most 64 KiB, and no route takes a file path: a video is asked for by its
                  job. None of the window's own calls (studio/bridge.py) can be reached from here.

A request runs on a thread of its own with a socket timeout, so a slow or stuck client holds up nothing. Whatever
changes a job goes through dashboard/posting.py; the window then hears of it (App.changed) and every other window
sees the change in the database.

  python -m studio extension            where the extension stands (main, at the end of this file)
"""

from __future__ import annotations

import hashlib
import hmac
import html
import json
import math
import os
import re
import secrets
import socket
import socketserver
import sqlite3
import sys
import threading
import time
import traceback
import unicodedata
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qs, urlsplit

from . import config, db, desktop, posting, sync, videos
from .platforms import PublishItem, build, connected

try:  # the ReelForge folder's own package: its version
    from studio import KIT_VERSION
except Exception:  # the dashboard on its own, outside a ReelForge folder
    KIT_VERSION = ""

PROTOCOL = posting.PROTOCOL
EXTENSION_ID = "pikflpmjggoledmjbgbkjigbnincplgb"  # what the key in extension/manifest.json makes Chrome call it
PORTS = tuple(range(47821, 47831))
TOKEN_HEADER = "X-ReelForge-Token"
MAX_BODY = 64 * 1024
SECRET_FOR = 10 * 60      # seconds a pair page's secret works
PAGE_FOR = 2 * 60 * 60    # seconds a local page's key works
SEEN_EVERY = 30           # a browser's "last seen" is written at most this often, however much it asks
PAIRED_MOST = 20          # browsers remembered: the one unheard of longest goes when another connects
TIMEOUT = 20.0            # seconds a client may leave a request or an answer hanging
WEB = Path(__file__).resolve().parent / "web"
# The pages' own files, by name: nothing else is ever read from disk for /assets/.
ASSETS = {"page.css": "text/css; charset=utf-8", "page.js": "text/javascript; charset=utf-8",
          "icon.svg": "image/svg+xml"}
PAGE_POLICY = ("default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; "
               "base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
BATCH, JOB = r"pb_[0-9a-f]{16}", r"pj_[0-9a-f]{16}"
ROUTES = {name: re.compile(pattern) for name, pattern in (
    ("post_page", rf"/post/({BATCH})"), ("page_batch", rf"/v1/page/batch/({BATCH})"),
    ("batch", rf"/v1/batch/({BATCH})"), ("job", rf"/v1/jobs/({JOB})/(claim|file|event|press)"),
    ("asset", r"/assets/([a-z.]+)"))}
# The sites a post's link may point at, per platform. A link anywhere else is dropped: the window opens these.
POST_HOSTS = {"tiktok": ("tiktok.com",), "youtube": ("youtube.com", "youtu.be"), "instagram": ("instagram.com",),
              "x": ("x.com", "twitter.com")}
# What each platform calls its own last button.
BUTTONS = {"tiktok": "Post", "youtube": "Publish (or Save)", "instagram": "Share", "x": "Post"}
BY_HAND = ("queued", "expired", "failed", "cancelled")  # the states the local page's by-hand button is for

clock = time.monotonic  # (the tests move it on)
_store_lock = threading.Lock()


def ports_here() -> tuple[int, ...]:
    """The ports to try, in order. REELFORGE_EXTENSION_PORT picks one instead (0: any free one, off: none), for
    tests and for trying things. Without it a ReelForge that leaves the computer alone (STUDIO_DRY_SYSTEM, the
    tests) doesn't listen at all: it would sit on the ports your own ReelForge and your browser use."""
    asked = os.environ.get("REELFORGE_EXTENSION_PORT", "").strip().lower()
    if asked.isdigit():
        return (int(asked),)
    if asked or os.environ.get("STUDIO_DRY_SYSTEM", "").strip().lower() in ("1", "true", "yes", "on"):
        return ()
    return PORTS


class Refusal(Exception):
    """A request that gets a 4xx or 5xx answer: {"error": code, "message": a plain sentence}."""

    def __init__(self, status: int, code: str, message: str, **more):
        super().__init__(message)
        self.status, self.code, self.message, self.more = status, code, message, more


# ---- the connections file

def store_path() -> Path:
    """Where the extension's connections are kept: one file for every profile of this ReelForge folder, since a
    browser connects to ReelForge, not to a channel."""
    return config.APP_DIR / "data" / "extension.json"


def read_store() -> dict:
    """The connections file, or {} while there is none (or it can't be read: then nothing is connected)."""
    try:
        data = json.loads(store_path().read_text("utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def paired(store: dict) -> list[dict]:
    """The browsers on file, whatever else someone's edit left in the list."""
    listed = store.get("paired")
    return [p for p in listed if isinstance(p, dict)] if isinstance(listed, list) else []


def change_store(change: Callable[[dict], object]):
    """Read the connections file, let change(store) alter it, and write it back if it did: one ReelForge at a time
    (every open window serves the extension, and they share this file), the new file put in place whole, readable
    by you alone. Returns what change returned. OSError when the file can't be written."""
    path = store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    turn = sync.ReadLock(path.with_name(path.name + ".lock"))
    with _store_lock:
        turn.acquire(wait=5)  # still taken after that: go ahead, as a ReelForge that can't lock files does
        try:
            try:
                store = json.loads(path.read_text("utf-8"))
            except (FileNotFoundError, ValueError):  # none yet, or not JSON any more: a new one takes its place
                store = None
            # (any other trouble reading it goes to the caller: a file that can't be read just now is never
            # written over, or every browser would have to connect again)
            had = json.dumps(store, sort_keys=True) if isinstance(store, dict) else None
            store = store if isinstance(store, dict) else {}
            store.setdefault("app_id", str(uuid.uuid4()))
            if not isinstance(store.get("extension_ids"), list):
                store["extension_ids"] = []
            store["paired"] = paired(store)
            out = change(store)
            if json.dumps(store, sort_keys=True) != had:
                _write_store(path, store)
            return out
        finally:
            turn.release()


def _write_store(path: Path, store: dict) -> None:
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


def app_id() -> str:
    """This ReelForge folder's own id (made the first time it is asked for): the extension keeps its token under
    it, so it never offers one ReelForge's token to another."""
    return str(change_store(lambda store: store["app_id"]))


def origins(store: dict) -> set[str]:
    """The extension origins that may talk to ReelForge: the unpacked extension's, and any id listed under
    "extension_ids" in the connections file (for a copy from a store, whose id is another)."""
    listed = store.get("extension_ids")
    ids = [i for i in listed if isinstance(i, str) and re.fullmatch("[a-p]{32}", i)] if isinstance(listed, list) else []
    return {f"chrome-extension://{i}" for i in (EXTENSION_ID, *ids)}


def whose(store: dict, token: str) -> dict | None:
    """The paired browser a token belongs to. Only the token's SHA-256 is on file, and every entry is compared in
    the same time whether or not it matches."""
    digest = hashlib.sha256(token.encode("utf-8", "replace")).hexdigest().encode()
    found = None
    for p in paired(store):
        if hmac.compare_digest(str(p.get("token_sha256", "")).encode("utf-8"), digest):
            found = p
    return found if token else None


def proof(store: dict, install: str, nonce: str, port: int | None) -> str | None:
    """ReelForge's proof, to a browser that is connected, that it is the ReelForge that browser connected to and not
    another program listening on one of ReelForge's ports. An HMAC over the nonce the browser just made up, the
    port this server listens on and this ReelForge's id, keyed with the SHA-256 of that browser's token: what the
    connections file holds of the token, and what the browser can work out from the token it kept. The extension
    asks for it with every hello and sends its token, or believes anything it is told, only where the proof holds.
    The port is in it so that the real ReelForge's answer, passed on by a program on another port, proves nothing
    there. None for a browser that isn't connected, or a nonce that isn't 32 hex digits."""
    if not re.fullmatch(r"[0-9a-f]{32}", nonce):
        return None
    for p in paired(store):
        key = str(p.get("token_sha256", ""))
        if p.get("install") == install and re.fullmatch(r"[0-9a-f]{64}", key):
            said = f"reelforge hello\n{nonce}\n{port}\n{store.get('app_id')}"
            return hmac.new(bytes.fromhex(key), said.encode("utf-8"), hashlib.sha256).hexdigest()
    return None


def unpair(install: str) -> str | None:
    """Forget a browser: its token stops working at once. Returns its name, or None if it wasn't connected."""
    def drop(store: dict) -> str | None:
        gone = [p for p in store["paired"] if p.get("install") == install]
        store["paired"] = [p for p in store["paired"] if p.get("install") != install]
        return str(gone[0].get("name") or "A browser") if gone else None

    return change_store(drop)


def folder() -> Path:
    """The extension itself: the folder you load in your browser (Load unpacked)."""
    return config.APP_DIR.parent / "extension"


def manifest_id(where: Path | None = None) -> str:
    """The id Chrome gives the extension in this folder, worked out from the key in its manifest.json the way
    Chrome does (the first half of the key's SHA-256, written in the letters a to p). "" without a key: then the
    id depends on where the folder is, and ReelForge can't know it. OSError or ValueError when there is no manifest
    to read."""
    import base64

    manifest = json.loads(((where or folder()) / "manifest.json").read_text("utf-8-sig"))
    key = manifest.get("key") if isinstance(manifest, dict) else None
    if not isinstance(key, str) or not key.strip():
        return ""
    digest = hashlib.sha256(base64.b64decode(key)).hexdigest()[:32]
    return "".join(chr(ord("a") + int(c, 16)) for c in digest)


def selectors() -> dict | None:
    """extension/selectors.json: where the extension looks for each box and button on the upload pages. It is data,
    never code, and an update of ReelForge brings a newer one to an extension that hasn't been reloaded yet."""
    try:
        data = json.loads((folder() / "selectors.json").read_text("utf-8-sig"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def update_which(theirs) -> str:
    """Which side to update when the extension speaks another version of the link than this ReelForge."""
    if isinstance(theirs, int) and not isinstance(theirs, bool) and theirs > PROTOCOL:
        return "This ReelForge is older than the browser extension. Update ReelForge on its Updates tab."
    return ("The browser extension is older than this ReelForge. Press Reload on its card on your browser's "
            "extensions page.")


# ---- what goes over the wire

def line(value, limit: int) -> str | None:
    """A string from outside as one line of at most limit characters, without control characters. None for
    anything that isn't a string (or is empty)."""
    if not isinstance(value, str):
        return None
    text = " ".join("".join(c for c in value if unicodedata.category(c) != "Cc" or c.isspace()).split())
    return text[:limit] or None


def post_url(platform: str, value) -> str | None:
    """A post's link as the extension read it off the page, if it is an https address on that platform's own site.
    Only the plainest shape is taken: a host name and a path. Python and a browser read anything else differently
    (a backslash, a name and password before the host), and it is a browser that opens this link."""
    url = line(value, 500)
    if not url or not re.fullmatch(r"https://[a-z0-9.-]+(/[^\s\\]*)?", url):
        return None
    try:
        host = urlsplit(url).netloc
    except ValueError:
        return None
    return url if any(host == h or host.endswith("." + h) for h in POST_HOSTS.get(platform, ())) else None


def listed(cfg: dict) -> list[Path]:
    """The videos on the Publish list, newest first (the ones you hid there left out)."""
    hidden = db.get_meta("hidden_videos", [])
    names = {db.video_file(h).name.lower() for h in hidden if isinstance(h, str)} if isinstance(hidden, list) else set()
    return [f for f in videos.find(cfg) if db.video_file(f.name).name.lower() not in names]


def state(app: "App") -> dict:
    """What the extension's popup shows: the channel, its platforms, and the newest videos with where each stands."""
    platforms = app.platforms()
    files = listed(app.cfg)[:20]
    jobs = posting.for_files(files)
    shown = []
    for f in files:
        try:
            size, sha = f.stat().st_size, posting.digest(f)
        except OSError:
            continue  # moved or deleted since the folder was listed
        review = videos.approval_shown(f, app.cfg)  # for the list only: Post works it out afresh (create_batch)
        ok = review["state"] in videos.POSTABLE
        mine, found = jobs.get(f, {}), posting.spotted(f.name)
        where = {}
        for p in platforms:
            if not p["connected"]:
                continue
            job, post = mine.get(p["key"]), found.get(p["key"])
            stands = posting.standing(job, post)
            own = job is not None and stands == job["state"]
            where[p["key"]] = {"state": stands, "job": job["id"] if own else None,
                               "url": (job["url"] if own else None) or (post["url"] if post else None)}
        shown.append({"id": sha, "name": f.name, "size": size, "title": videos.meta(f, app.cfg)[0]["caption"],
                      "review": review["state"], "postable": ok, "why": "" if ok else review["line"],
                      "platforms": where})
    return {"profile": app.profile(),
            "platforms": [{**p, "via": "extension", "press_unlocked": posting.press_unlocked(p["key"])}
                          for p in platforms],
            "videos": shown, "active": posting.active_batches()}


def page_job(job: dict, name: str, handles: dict[str, str]) -> dict:
    """A job as the local page shows it: the job without its text, and the words for its row. line: where it
    stands. note: what to do about it, if anything. tone: wait, busy, ready, good, bad or ask, for the mark beside
    it. by_hand: the Post by hand button applies."""
    out = posting.job_json(job, handles)
    del out["text"]
    stands, detail = job["state"], job["detail"] or ""
    note, tone = "", "busy"
    if stands == "queued":
        words, tone = "Queued: waiting for the extension", "wait"
    elif stands == "claimed":
        words = "The extension has it"
    elif stands == "opening":
        words = f"Opening {name}"
        if job["step"] == "signed_in":
            words = f"Checking you're signed in to {name}"
    elif stands == "uploading":
        words = "Uploading"
    elif stands == "filling":
        words = "Filling in the text"
    elif stands == "ready":
        words, tone = f"Ready: check it and press {BUTTONS.get(job['platform'], 'Post')} in the {name} tab", "ready"
        note = "ReelForge stops here. Nothing is posted until you press it."
    elif stands == "posting":
        words = "Pressing Post for you"
    elif stands == "posted":
        words, tone = "Posted", "good"
        note = "" if job["url"] else "Its link shows in ReelForge after the next stats read."
    elif stands == "failed":
        step = posting.STEPS.get(job["step"] or "", "")
        words, tone = "Failed" + (f" at {step}" if step else ""), "bad"
        why = posting.failure(job, name)
        if why:  # the extension's own sentence, or ours for its code: one sentence either way
            why = why[:1].upper() + why[1:] + ("" if why[-1] in ".!?" else ".") + " "
        note = why + "You can finish in the tab that is open, or press Post by hand."
    elif stands == "unknown":
        words, tone = "ReelForge can't tell whether this posted", "ask"
        note = (f"Check {name} before trying again. Then say on ReelForge's Publish tab whether it posted. "
                "Nothing tries again by itself.")
    elif stands == "expired":
        words, tone = "The extension didn't pick this up in time", "wait"
        note = "Nothing was posted. Start it again from ReelForge, or use Post by hand."
    else:  # cancelled
        words, tone = ("You're posting this one by hand" if detail == "posted by hand" else "Cancelled"), "wait"
        note = "" if detail in ("", "posted by hand") else \
            detail[:1].upper() + detail[1:] + ("" if detail[-1] in ".!?" else ".")
    if detail and stands in posting.IN_FLIGHT:  # what the extension last said ("Visibility: Private"), first
        note = detail + (("" if detail[-1] in ".!?" else ".") + " " + note if note else "")
    out.update(name=name, line=words, note=note, tone=tone, by_hand=stands in BY_HAND)
    return out


def by_hand(job: dict, name: str, copy: Callable[[str], object], record: Callable[[PublishItem], object]) -> dict:
    """Today's flow for one platform, with a job's own text: the text copied, the file shown in the file manager,
    the upload page opened. record(item) notes the opened page the way the window's own button does. Nothing is
    noted when no browser opened: a page that never opened would later claim some other post for this video.
    Returns {"opened", "upload_url", "message"}."""
    text = posting.headline(json.loads(job["text"]))
    video = Path(job["video_path"])
    what = {"youtube": "title", "x": "post"}.get(job["platform"], "caption and hashtags")
    if text:
        copy(text)
    shown = video.exists()
    if shown:
        desktop.reveal(video)
    opened = desktop.open_upload_page(job["platform"])
    if opened:
        record(PublishItem(video=video, caption=text, tags=""))
        message = f"{name}'s upload page is opening. The {what} is copied: " + (
            f"drag {video.name} in from {desktop.FILES_WINDOW}, then paste." if shown else
            f"paste it there. ReelForge can't find {video.name} any more, so you have to find the video yourself.")
    else:
        message = f"ReelForge couldn't open your browser for {name}. Open its upload page yourself: the {what} is " + (
            f"copied, and {video.name} is showing in {desktop.FILES_WINDOW}." if shown else
            f"copied. ReelForge can't find {video.name} any more, so you have to find the video yourself.")
    if job["platform"] == "youtube":
        message += " For the description, use Copy YouTube hashtags on ReelForge's Publish tab."
    if job["platform"] == "instagram":
        message += " On Instagram, click + Create, then Post or Reel."
    return {"opened": bool(opened), "upload_url": desktop.UPLOAD_PAGES[job["platform"]], "message": message}


class App:
    """ReelForge as the server sees it: the channel it serves, and what it asks ReelForge to do. This one is
    ReelForge without its window (reelforge extension serve and pair); the window's own is
    dashboard.posting_tab.WindowApp."""

    demo = False

    def __init__(self, cfg: dict, every: dict):
        self.cfg, self.every = cfg, every

    def profile(self) -> dict:
        """The channel this ReelForge shows: the extension tells two open windows apart by it."""
        p = config.profile()
        return {"key": p.key if p else ".", "name": p.name if p else ""}

    def platforms(self) -> list[dict]:
        """Every platform: its key, name, the account ReelForge has for it ("@name" or "") and whether it is
        connected in ReelForge. That says nothing about the browser the extension is in."""
        on = connected(self.every, db.stored_platforms())
        return [{"key": p.key, "name": p.name, "handle": p.handle, "connected": p.key in on}
                for p in list(self.every.values())]

    def changed(self) -> None:
        """A job or a connection changed. Nothing to redraw here: open windows see it in the database."""

    def by_hand(self, job: dict, name: str) -> dict | None:
        """The local page's Post by hand (see by_hand)."""
        def record(item: PublishItem) -> None:
            db.adopt_episode(item.title or item.video.stem, str(item.video))
            db.log_upload(job["platform"], item.video.name, "manual", message="opened the upload page")
            db.bump()

        return by_hand(job, name, desktop.copy, record)


# ---- the server

class _Listener(ThreadingHTTPServer):
    daemon_threads = True      # a request still running never keeps ReelForge from closing
    block_on_close = False
    # Off on Windows, where it would let a second program listen on the very same port.
    allow_reuse_address = os.name != "nt"
    api: "Server"

    def server_bind(self) -> None:
        if os.name == "nt":
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        socketserver.TCPServer.server_bind(self)  # (HTTPServer's own looks the computer's name up first)
        self.server_name, self.server_port = self.server_address[:2]

    def handle_error(self, request, client_address) -> None:
        if not isinstance(sys.exc_info()[1], (ConnectionError, TimeoutError)):  # a client that left is no news
            traceback.print_exc()


class Server:
    """The local server for one ReelForge (see the top of this file). start() makes it listen, stop() ends it."""

    def __init__(self, app: App, ports: tuple[int, ...] | None = None):
        self.app = app
        self.ports = ports_here() if ports is None else ports
        self.port: int | None = None
        self.timeout = TIMEOUT
        self.lock = threading.Lock()
        # A local page's key -> {"kind": "post" or "pair", "until": when the key runs out, and for a post page its
        # "batch", for a pair page its "secret", "claim_until" and "name" (the browser that used the secret)}.
        self.pages: dict[str, dict] = {}
        self.seen: dict[str, float] = {}    # install -> when its "last seen" was last written
        self.app_id = ""
        self.httpd: _Listener | None = None

    def start(self) -> bool:
        """Listen on the first of the ports that is free. False when none is, or when the connections file can't
        be written: ReelForge then works as it does without the extension."""
        if not self.ports:
            return False  # switched off here: nothing is written either
        try:
            self.app_id = app_id()
        except (OSError, ValueError):
            return False
        for port in self.ports:
            try:
                httpd = _Listener(("127.0.0.1", port), Handler)
            except OSError:
                continue  # taken: another ReelForge window, most likely
            httpd.api = self
            self.httpd, self.port = httpd, httpd.server_address[1]
            threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.2}, daemon=True,
                             name="extension-api").start()
            return True
        return False

    def stop(self) -> None:
        httpd, self.httpd, self.port = self.httpd, None, None
        if httpd is not None:
            httpd.shutdown()
            httpd.server_close()

    # -- the local pages

    def _page(self, kind: str, **what) -> str:
        key = secrets.token_urlsafe(24)
        with self.lock:
            now = clock()
            for old in [k for k, v in self.pages.items() if v["until"] <= now]:  # the ones that ran out go
                del self.pages[old]
            self.pages[key] = {"kind": kind, "until": now + PAGE_FOR, **what}
        return key

    def pair_url(self) -> str:
        """The address of a new pair page: whoever opens it in a browser with the extension connects that browser.
        Its secret works once, for ten minutes."""
        key = self._page("pair", secret=secrets.token_urlsafe(32), claim_until=clock() + SECRET_FOR, name=None)
        return f"http://127.0.0.1:{self.port}/pair?k={key}"

    def post_url(self, batch: str) -> str:
        """The address of the page that hands a batch to the extension and shows where each of its posts stands."""
        return f"http://127.0.0.1:{self.port}/post/{batch}?k={self._page('post', batch=batch)}"

    def page(self, key: str | None, kind: str, batch: str | None = None) -> dict:
        """The page a key belongs to. Refused when the key isn't one, ran out, or is another page's."""
        with self.lock:
            page = self.pages.get(key or "")
            if page is None or page["until"] <= clock() or page["kind"] != kind or page.get("batch") != batch:
                raise Refusal(403, "bad_key", "This page is too old to follow ReelForge. Start again from ReelForge.")
            return dict(page)

    def paired_on(self, url: str) -> str | None:
        """The name of the browser that connected on a pair page (by the address pair_url gave), or None so far."""
        key = parse_qs(urlsplit(url).query).get("k", [""])[0]
        with self.lock:
            page = self.pages.get(key)
            return page["name"] if page and page["kind"] == "pair" else None

    # -- pairing

    def claim(self, body: dict, origin: str | None) -> dict:
        """The extension trades a pair page's secret for a token of its own. The secret works once."""
        if origin not in origins(read_store()):
            raise Refusal(403, "wrong_origin", "Only ReelForge's own browser extension can connect.")
        if body.get("protocol") != PROTOCOL:
            raise Refusal(426, "protocol", update_which(body.get("protocol")))
        install, secret = line(body.get("install"), 80), body.get("secret")
        if not install or not isinstance(secret, str):
            raise Refusal(400, "bad_request", "The extension didn't say which browser it is.")
        token = secrets.token_urlsafe(32)
        now = db.now_iso()
        entry = {"install": install, "name": line(body.get("name"), 80) or "A browser",
                 "version": line(body.get("extension_version"), 20) or "", "protocol": PROTOCOL,
                 "token_sha256": hashlib.sha256(token.encode()).hexdigest(), "paired_at": now, "last_seen": now}

        def add(store: dict) -> None:
            rest = [p for p in store["paired"] if p.get("install") != install]
            rest.sort(key=lambda p: str(p.get("last_seen") or ""))
            store["paired"] = [*rest[-(PAIRED_MOST - 1):], entry]

        with self.lock:  # two asking with the same secret at once: one of them gets the token
            known = next((page for page in self.pages.values() if page["kind"] == "pair"
                          and hmac.compare_digest(page["secret"].encode(), secret.encode("utf-8", "replace"))), None)
            if known is None or known["name"] is not None:
                raise Refusal(403, "bad_secret", "This pairing page was used already. Start again from ReelForge.")
            if known["claim_until"] <= clock():
                raise Refusal(410, "expired", "This pairing page is more than ten minutes old. Start again from "
                                              "ReelForge.")
            try:
                change_store(add)
            except OSError as e:
                raise Refusal(500, "store", f"ReelForge couldn't save the connection ({e}).") from e
            known["name"] = entry["name"]
            self.seen[install] = clock()
        return {"token": token, "app_id": self.app_id}

    def heard(self, browser: dict, **fields) -> None:
        """A paired browser asked for something: its "last seen" (and whatever it said about itself) goes on file,
        but not at every request."""
        install = str(browser.get("install"))
        with self.lock:
            due = fields or clock() - self.seen.get(install, -SEEN_EVERY) >= SEEN_EVERY
            if due:
                self.seen[install] = clock()
        if not due:
            return

        def touch(store: dict) -> None:
            for p in store["paired"]:
                if p.get("install") == install:
                    p.update(fields, last_seen=db.now_iso())

        try:
            change_store(touch)
        except OSError:
            pass  # the answer matters more than the note of when
        self.app.changed()


class Handler(BaseHTTPRequestHandler):
    """One request. Every rule in the list at the top of this file is checked in answer(), before any route."""

    protocol_version = "HTTP/1.1"
    server: _Listener

    def setup(self) -> None:
        self.timeout = self.server.api.timeout  # every read and write on this connection gives up after it
        super().setup()

    def version_string(self) -> str:
        return "ReelForge"

    def log_message(self, format, *args) -> None:
        """Nothing is logged: an address here carries its page's key."""

    def send_error(self, code, message=None, explain=None) -> None:
        """What the standard library answers by itself (a request it can't read, a method it doesn't know)."""
        self.reply(code, {"error": "bad_request", "message": str(message or "ReelForge couldn't read that request.")})

    # -- answering

    def reply(self, status: int, data: dict, headers: dict | None = None) -> None:
        self.send(status, json.dumps(data, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8",
                  headers)

    def send(self, status: int, body: bytes, kind: str, headers: dict | None = None) -> None:
        self.head(status, kind, len(body), headers)
        self.wfile.write(body)

    def head(self, status: int, kind: str, length: int, headers: dict | None = None) -> None:
        self.answered = True
        self.close_connection = True  # one request for each connection: no thread waits for a second one
        self.send_response(status)
        every = {"Content-Type": kind, "Content-Length": str(length), "Cache-Control": "no-store",
                 "X-Content-Type-Options": "nosniff", "Cross-Origin-Resource-Policy": "same-origin",
                 "Referrer-Policy": "no-referrer", "Connection": "close", **(headers or {})}
        if getattr(self, "extension_origin", None):  # never *: only the extension's own origin, by name
            every.update({"Access-Control-Allow-Origin": self.extension_origin, "Vary": "Origin"})
        for name, value in every.items():
            self.send_header(name, value)
        self.end_headers()

    def answer(self) -> None:
        self.answered = False
        self.extension_origin = None
        length = self.headers.get("Content-Length") or ""
        self.unread = int(length) if length.isdigit() else 0  # what the request says is still to come
        try:
            self.route()
        except Refusal as r:
            self.reply(r.status, {"error": r.code, "message": r.message, **r.more})
        except posting.Refused as r:
            self.reply(404 if r.code == "not_found" else 409, {"error": r.code, "message": r.message})
        except sqlite3.OperationalError as e:  # another ReelForge is writing: the extension asks again
            self.reply(503, {"error": "busy", "message": f"ReelForge's database is busy ({e}). Try again in a moment."})
        except (ConnectionError, TimeoutError):
            raise  # the client left, or stopped listening
        except Exception:
            traceback.print_exc()
            if not self.answered:
                self.reply(500, {"error": "internal", "message": "ReelForge ran into a problem with that. Its log "
                                                                 "(dashboard/data/window-log.txt) says more."})
        finally:
            self.drain()

    def drain(self) -> None:
        """A request refused before its body was read: read a little of what is left, briefly. Closing on unread
        bytes can cost the client the answer that says why."""
        left, end = min(self.unread, 1024 * 1024), time.monotonic() + 2
        try:
            self.connection.settimeout(0.5)
            while left > 0 and time.monotonic() < end:
                chunk = self.rfile.read1(min(left, 64 * 1024))
                if not chunk:
                    break
                left -= len(chunk)
        except (OSError, ValueError):
            pass  # it stopped sending, or left

    do_GET = do_POST = do_OPTIONS = do_HEAD = do_PUT = do_DELETE = do_PATCH = answer

    # -- the rules for every request, then the routes

    def route(self) -> None:
        api = self.server.api
        own = f"127.0.0.1:{api.port}"
        if self.headers.get_all("Host") != [own]:
            raise Refusal(421, "wrong_host", f"ReelForge answers only as http://{own}.")
        given = self.headers.get_all("Origin") or []
        fetch_site = (self.headers.get("Sec-Fetch-Site") or "").lower()
        if len(given) > 1 or fetch_site in ("cross-site", "same-site"):
            raise Refusal(403, "wrong_origin", "Web pages can't talk to ReelForge.")
        origin = given[0] if given else None
        if origin is not None:
            if origin in origins(read_store()):
                self.extension_origin = origin
            elif origin != f"http://{own}":
                raise Refusal(403, "wrong_origin", "Web pages can't talk to ReelForge.")
        if self.command == "OPTIONS":
            if not self.extension_origin:
                raise Refusal(403, "wrong_origin", "Web pages can't talk to ReelForge.")
            self.head(204, "text/plain", 0, {"Access-Control-Allow-Headers": f"{TOKEN_HEADER}, Content-Type",
                                             "Access-Control-Allow-Methods": "GET, POST",
                                             "Access-Control-Max-Age": "600"})
            return
        if self.command not in ("GET", "POST"):
            raise Refusal(405, "method", "ReelForge takes GET and POST only.")
        path, _, query = self.path.partition("?")
        if not re.fullmatch(r"/[A-Za-z0-9_./-]*", path) or ".." in path or "//" in path:
            raise Refusal(404, "not_found", "ReelForge has nothing at that address.")
        asked = parse_qs(query)
        body = self.body() if self.command == "POST" else {}
        key = body.get("k") if self.command == "POST" else (asked.get("k") or [None])[0]
        key = key if isinstance(key, str) else None
        app = api.app

        if (self.command, path) == ("GET", "/v1/hello"):
            token, store = self.headers.get(TOKEN_HEADER) or "", read_store()
            said = {"app": "reelforge", "protocol": PROTOCOL, "version": KIT_VERSION, "app_id": api.app_id,
                    "profile": app.profile(), "demo": bool(app.demo),
                    "paired": bool(token and not app.demo and whose(store, token))}
            # For a connected browser that says which one it is and sends a nonce: the proof that this is its
            # ReelForge (see proof). The extension sends no token with a hello.
            proven = None if app.demo else proof(store, (asked.get("install") or [""])[0],
                                                 (asked.get("nonce") or [""])[0], api.port)
            self.reply(200, {**said, "proof": proven} if proven else said)
            return
        if app.demo:
            raise Refusal(403, "demo", "ReelForge is showing its demo, which never posts.")
        if self.command == "GET":
            self.get(path, key)
        else:
            self.post(path, key, body, origin)

    def body(self) -> dict:
        """A POST's JSON object. Refused: no length, more than 64 KiB, not JSON."""
        kind = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if kind != "application/json":
            raise Refusal(415, "not_json", "ReelForge takes JSON only.")
        length = self.headers.get("Content-Length") or ""
        if self.headers.get("Transfer-Encoding") or not length.isdigit():
            raise Refusal(411, "no_length", "ReelForge needs a Content-Length.")
        if int(length) > MAX_BODY:
            raise Refusal(413, "too_large", "That is more than ReelForge takes in one request (64 KiB).")
        raw = self.rfile.read(int(length))
        self.unread = 0

        def not_a_number(name: str):
            raise ValueError(name)

        try:
            data = json.loads(raw.decode("utf-8"), parse_constant=not_a_number) if raw else {}
        except (ValueError, RecursionError):
            data = None
        if not isinstance(data, dict):
            raise Refusal(400, "bad_json", "ReelForge couldn't read that as a JSON object.")
        return data

    def browser(self) -> dict:
        """The paired browser asking (by its token). Refused without a good token."""
        browser = whose(read_store(), self.headers.get(TOKEN_HEADER) or "")
        if browser is None:
            raise Refusal(401, "unpaired", "This browser isn't connected to ReelForge. Connect it from the Publish "
                                           "tab, or with: reelforge extension pair")
        return browser

    def names(self) -> tuple[dict[str, str], dict[str, str]]:
        """Each platform's name, and the account ReelForge has connected for it."""
        platforms = self.server.api.app.platforms()
        return {p["key"]: p["name"] for p in platforms}, {p["key"]: p["handle"] for p in platforms}

    def get(self, path: str, key: str | None) -> None:
        api = self.server.api
        if path == "/pair":
            page = api.page(key, "pair")
            self.html("pair.html", secret="" if page["name"] else page["secret"])  # a used secret isn't shown again
        elif m := ROUTES["post_page"].fullmatch(path):
            api.page(key, "post", m[1])
            self.html("post.html", batch=m[1])
        elif m := ROUTES["asset"].fullmatch(path):
            if m[1] not in ASSETS:
                raise Refusal(404, "not_found", "ReelForge has nothing at that address.")
            self.send(200, (WEB / m[1]).read_bytes(), ASSETS[m[1]])
        elif path == "/v1/page/pair":
            name = api.page(key, "pair")["name"]
            self.reply(200, {"state": "paired" if name else "waiting", "name": name or "", **self.where(),
                             "note": posting.TESTED})
        elif m := ROUTES["page_batch"].fullmatch(path):
            api.page(key, "post", m[1])
            self.reply(200, self.page_batch(m[1]))
        elif path == "/v1/state":
            api.heard(self.browser())
            self.reply(200, state(api.app))
        elif m := ROUTES["batch"].fullmatch(path):
            api.heard(self.browser())
            jobs = posting.in_batch(m[1])
            if not jobs:
                raise Refusal(404, "no_batch", "ReelForge has no such batch of posts.")
            handles = self.names()[1]
            self.reply(200, {"batch": m[1], "jobs": [posting.job_json(j, handles) for j in jobs]})
        elif path == "/v1/selectors":
            api.heard(self.browser())
            data = selectors()
            if data is None:
                raise Refusal(404, "no_selectors", "This ReelForge has no extension/selectors.json.")
            self.reply(200, data)
        elif (m := ROUTES["job"].fullmatch(path)) and m[2] == "file":
            browser = self.browser()
            api.heard(browser)
            self.file(m[1], str(browser.get("install")))
        else:
            raise Refusal(404, "not_found", "ReelForge has nothing at that address.")

    def post(self, path: str, key: str | None, body: dict, origin: str | None) -> None:
        api = self.server.api
        if path == "/v1/pair/claim":
            answer = api.claim(body, origin)
            api.app.changed()
            self.reply(200, answer)
            return
        if path == "/v1/page/manual":
            self.manual(key, body)
            return
        m = ROUTES["job"].fullmatch(path)
        if path not in ("/v1/seen", "/v1/batches", "/v1/unpair") and not (m and m[2] != "file"):
            raise Refusal(404, "not_found", "ReelForge has nothing at that address.")
        browser = self.browser()
        install = str(browser.get("install"))  # who is asking is the token's word, never the request's
        if path == "/v1/unpair":
            unpair(install)
            api.app.changed()
            self.reply(200, {"ok": True})
        elif path == "/v1/seen":
            theirs = body.get("protocol")
            said = {"protocol": theirs if isinstance(theirs, int) and not isinstance(theirs, bool) else None,
                    "accounts": accounts(body.get("accounts"))}
            for field, limit, name in (("name", 80, "name"), ("extension_version", 20, "version")):
                if value := line(body.get(field), limit):
                    said[name] = value
            api.heard(browser, **said)
            if theirs != PROTOCOL:
                raise Refusal(426, "protocol", update_which(theirs))
            version = (selectors() or {}).get("version")
            self.reply(200, {"ok": True, "selectors_version": version if isinstance(version, int) else 0})
        elif path == "/v1/batches":
            api.heard(browser)
            self.batches(body)
        else:
            api.heard(browser)
            self.job(m[1], m[2], install, body)
        if path != "/v1/seen":
            api.app.changed()

    # -- the routes with more to them

    def html(self, name: str, **values) -> None:
        text = (WEB / name).read_text("utf-8")
        for field, value in {"protocol": PROTOCOL, "port": self.server.api.port, **values}.items():
            text = text.replace("{{" + field + "}}", html.escape(str(value), quote=True))
        self.send(200, text.encode("utf-8"), "text/html; charset=utf-8",
                  {"Content-Security-Policy": PAGE_POLICY, "X-Frame-Options": "DENY"})

    def where(self) -> dict:
        """Where the extension's folder is, for a page's set-up steps."""
        return {"folder": str(folder()), "folder_there": (folder() / "manifest.json").is_file()}

    def page_batch(self, batch: str) -> dict:
        names, handles = self.names()
        jobs = posting.in_batch(batch)
        seen = []
        for p in paired(read_store()):
            try:
                seen.append((datetime.now(timezone.utc) - db.parse(p["last_seen"])).total_seconds())
            except (KeyError, TypeError, ValueError):
                seen.append(None)
        heard = [s for s in seen if s is not None]
        first = jobs[0] if jobs else None
        return {"batch": batch, "jobs": [page_job(j, names.get(j["platform"], j["platform"]), handles) for j in jobs],
                "video": {"name": first["video"], "size": first["video_size"], "sha256": first["video_sha256"]}
                if first else None,
                "extension": {"paired": bool(seen), "seen_seconds_ago": max(0, int(min(heard))) if heard else None,
                              **self.where()},
                "note": posting.TESTED}

    def manual(self, key: str | None, body: dict) -> None:
        api = self.server.api
        batch, platform = body.get("batch"), body.get("platform")
        if not isinstance(batch, str) or not re.fullmatch(BATCH, batch) or not isinstance(platform, str):
            raise Refusal(400, "bad_request", "Which post is that?")
        api.page(key, "post", batch)
        job = next((j for j in reversed(posting.in_batch(batch)) if j["platform"] == platform), None)
        if job is None:
            raise Refusal(404, "not_found", "ReelForge has no such post.")
        if job["state"] not in BY_HAND:
            raise Refusal(409, "not_manual", "That post is already under way in your browser. Finish it there, or "
                                             "cancel it on ReelForge's Publish tab first.")
        names, handles = self.names()
        name = names.get(platform, platform)
        # (the video where it is now: it may have been moved to completed videos/ since the job was made)
        done = api.app.by_hand({**job, "video_path": str(posting.whereabouts(job, api.app.cfg))}, name)
        if done is None:
            raise Refusal(503, "busy", "ReelForge didn't answer in time. Try again.")
        job = posting.cancel(job["id"], "posted by hand")
        api.app.changed()
        self.reply(200, {"ok": True, **done, "job": page_job(job, name, handles)})

    def batches(self, body: dict) -> None:
        """Post pressed in the extension's popup: jobs for a video on the Publish list, with its saved caption and
        hashtags, by the same rules as the window's own Post."""
        app = self.server.api.app
        sha, asked = body.get("video"), body.get("platforms")
        if not isinstance(sha, str) or not isinstance(asked, list):
            raise Refusal(400, "bad_request", "Which video, and where to?")
        on = {p["key"] for p in app.platforms() if p["connected"]}
        ticked = [k for k in asked if isinstance(k, str) and k in on]
        if not ticked:
            raise Refusal(400, "no_platform", "Pick a platform that is connected in ReelForge.")
        video = None
        for f in listed(app.cfg):
            try:
                if posting.digest(f) == sha:
                    video = f
                    break
            except OSError:
                continue
        if video is None:
            raise Refusal(404, "no_video", "That video isn't on ReelForge's Publish list any more.")
        meta = videos.meta(video, app.cfg)[0]
        made = posting.create_batch(video, ticked, meta["caption"], meta["tags"], app.cfg,
                                    press=body.get("press") is True)
        handles = self.names()[1]
        self.reply(201 if made["batch"] else 200, {
            "batch": made["batch"], "jobs": [posting.job_json(j, handles) for j in made["jobs"]],
            "blocked": made["blocked"]})

    def job(self, job_id: str, what: str, install: str, body: dict) -> None:
        handles = self.names()[1]
        if what == "claim":
            self.reply(200, posting.job_json(posting.claim(job_id, install), handles))
        elif what == "press":
            try:
                posting.grant_press(job_id, install)
            except posting.Refused as r:
                if r.code == "not_found":
                    raise
                self.reply(409, {"ok": False, "error": r.code, "message": r.message})
                return
            self.reply(200, {"ok": True})
        else:  # event
            to = body.get("state")
            job = posting.get(job_id)
            if job is None:
                raise posting.Refused("not_found", "ReelForge has no such post.")
            if to not in posting.EXTENSION_STATES:
                raise posting.Refused("bad_transition", f"The extension can't make a post {line(str(to), 40)}.", job)
            progress = body.get("progress")
            if isinstance(progress, bool) or not isinstance(progress, (int, float)) or not math.isfinite(progress):
                progress = None
            code = line(body.get("error_code"), 40)
            job = posting.advance(
                job_id, to, install=install, step=line(body.get("step"), 40),
                progress=None if progress is None else max(0.0, min(float(progress), 1.0)),
                detail=line(body.get("detail"), 300),
                error_code=code if code and re.fullmatch(r"[a-z_]+", code) else None,
                error_message=line(body.get("error_message"), 300), post_id=line(body.get("post_id"), 80),
                url=post_url(job["platform"], body.get("url")), strategy=line(body.get("strategy"), 200))
            self.reply(200, posting.job_json(job, handles))

    def file(self, job_id: str, install: str) -> None:
        """A job's video, whole or the part a Range header asks for. Only for the browser that claimed the job,
        while the job is under way and the file is still the video the job was made for."""
        job = posting.get(job_id)
        if job is None:
            raise posting.Refused("not_found", "ReelForge has no such post.")
        if job["claimed_by"] != install:
            raise Refusal(409, "not_yours", "This post isn't this browser's: claim it first.")
        if job["state"] not in posting.IN_FLIGHT:
            raise Refusal(409, "ended", "This post is over.")
        changed = Refusal(409, "changed", f"{job['video']} was replaced or removed after this post was started. "
                                          "Nothing was posted: start it again from ReelForge.")
        if not posting.unchanged(job):
            raise changed
        try:
            f = open(job["video_path"], "rb")
        except OSError:
            raise changed from None
        with f:
            size = os.fstat(f.fileno()).st_size
            if size != job["video_size"]:
                raise changed
            span = byte_range(self.headers.get("Range"), size)
            if span is None:
                self.reply(416, {"error": "range", "message": "That part of the video doesn't exist."},
                           {"Content-Range": f"bytes */{size}"})
                return
            start, end, partial = span
            headers = {"Accept-Ranges": "bytes", "ETag": f'"{job["video_sha256"]}"'}
            if partial:
                headers["Content-Range"] = f"bytes {start}-{end}/{size}"
            left = end - start + 1 if size else 0
            self.head(206 if partial else 200, "video/mp4", left, headers)
            f.seek(start)
            while left > 0:
                chunk = f.read(min(64 * 1024, left))
                if not chunk:
                    break  # the file got shorter under us: the answer ends early, and the browser sees that
                self.wfile.write(chunk)
                left -= len(chunk)


def byte_range(header: str | None, size: int) -> tuple[int, int, bool] | None:
    """(first byte, last byte, whether it is a part) for a Range header on a file of this size: "bytes=0-99",
    "bytes=100-" or "bytes=-100", one range only. No header, or one this doesn't understand: the whole file.
    None: a part that isn't in the file."""
    m = re.fullmatch(r"bytes=(\d{0,18})-(\d{0,18})", (header or "").strip())
    if header is None or not m or (not m[1] and not m[2]):
        return 0, size - 1, False
    if not m[1]:  # the last so many bytes
        count = int(m[2])
        return (max(size - count, 0), size - 1, True) if count and size else None
    start, end = int(m[1]), min(int(m[2]) if m[2] else size - 1, size - 1)
    return (start, end, True) if start <= end else None


def accounts(said) -> dict:
    """What the extension last saw of each platform in its browser: {"signed_in": true, false or null, "handle"}.
    Kept as told; it says nothing about the accounts connected in ReelForge."""
    out = {}
    if isinstance(said, dict):
        for key in config.PLATFORM_KEYS:
            seen = said.get(key)
            if isinstance(seen, dict):
                signed = seen.get("signed_in")
                out[key] = {"signed_in": signed if isinstance(signed, bool) else None,
                            "handle": line(seen.get("handle"), 64)}
    return out


# ---- the command line and doctor

def check() -> tuple[bool, str]:
    """For doctor and the command line: (whether all is well, a sentence). The extension is optional, so the worst
    this ever is, is a warning."""
    where = folder()
    try:
        found = manifest_id(where)
    except (OSError, ValueError):
        return False, (f"{where} has no manifest.json that can be read, so there is no extension to load. Update "
                       "ReelForge (its Updates tab) to get it. Posting by hand works without it.")
    if found != EXTENSION_ID:
        return False, (f"extension/manifest.json makes the extension's id {found or 'depend on where the folder is'}"
                       f", not {EXTENSION_ID}, the one ReelForge lets in. Update ReelForge to get the folder as it "
                       "ships.")
    path = store_path()
    if path.exists():
        try:
            store = json.loads(path.read_text("utf-8"))
            if not isinstance(store, dict):
                raise ValueError("it isn't a list of connections")
        except (OSError, ValueError) as e:
            return False, (f"{path} can't be read ({e}), so no browser counts as connected. Connect yours again: "
                           "reelforge extension pair")
    n = len(paired(read_store()))
    return True, f"in extension/, " + (f"connected in {n} browser{'s' if n != 1 else ''}" if n else
                                       "not connected yet (optional: reelforge extension pair)")


USAGE = """reelforge extension            where the browser extension stands: its folder, its id, the browsers connected
reelforge extension folder     print its folder and show it in the file manager (the one to Load unpacked)
reelforge extension pair       connect a browser: opens the pair page and waits until the extension has connected
reelforge extension serve      listen for the extension without ReelForge's window, until Ctrl-C

None of these starts a post. A post starts when you press Post in ReelForge's window or in the extension."""


def status() -> int:
    from .analytics import fmt_ago

    ok, said = check()
    where = folder()
    print("ReelForge's browser extension (optional: it fills in each platform's upload page for you)")
    print(f"  folder     {where}" + ("" if (where / "manifest.json").is_file() else "  (not there)"))
    print(f"  id         {EXTENSION_ID}")
    browsers = paired(read_store())
    for i, p in enumerate(browsers):
        try:
            seen = fmt_ago(p["last_seen"])
        except (KeyError, TypeError, ValueError):  # none, or not a time ReelForge wrote: the file can be edited
            seen = "never"
        version = f" · v{p['version']}" if p.get("version") else ""
        label = "connected " if i == 0 else " " * 10
        print(f"  {label} {p.get('name') or 'A browser'}{version} · last seen {seen}")
    if not browsers:
        print("  connected  no browser yet: reelforge extension pair connects one")
    if not ok:
        print(f"\n{said}")
    print("\ndocs/EXTENSION.md says how to set it up and what it does.")
    return 0


def _serving() -> Server | None:
    """A server for this profile without the window, listening; None (with the reason printed) when it can't."""
    if not ports_here():
        print("ReelForge doesn't listen for the extension here (STUDIO_DRY_SYSTEM or REELFORGE_EXTENSION_PORT).")
        return None
    try:
        cfg = config.load_config()
    except config.ConfigError as e:
        print(f"ReelForge can't start: {e}")
        return None
    db.init(config.DB_PATH)
    server = Server(App(cfg, build(cfg)))
    if not server.start():
        print(f"ReelForge couldn't listen for the extension: every port from {server.ports[0]} to "
              f"{server.ports[-1]} on this computer is taken, or {store_path()} can't be written.")
        return None
    return server


def main(argv: list[str]) -> int:
    """reelforge extension [folder | pair | serve] (USAGE)."""
    if not argv:
        return status()
    if argv == ["folder"]:
        where = folder()
        print(where)
        if not where.is_dir():
            print("It isn't there. Update ReelForge (its Updates tab) to get it.")
            return 1
        desktop.reveal(where)
        return 0
    if argv == ["pair"]:
        server = _serving()
        if server is None:
            return 1
        url = server.pair_url()
        try:
            if desktop.open_url(url):
                print("A ReelForge page is opening in your browser: the extension connects there.")
                print(f"If the extension is in another browser, open this address in that one:\n  {url}")
            else:
                print(f"ReelForge couldn't open your browser. Open this address in the browser that has the "
                      f"extension:\n  {url}")
            print("Waiting for the extension, for up to ten minutes (Ctrl-C stops)...", flush=True)
            end = clock() + SECRET_FOR
            while not (name := server.paired_on(url)):
                if clock() >= end:
                    print("No browser connected in ten minutes. Nothing was changed: run this again when you're "
                          "ready.")
                    return 1
                time.sleep(0.2)
            time.sleep(1.5)  # the page asks once more and says "Connected", and the extension says hello
            print(f"Connected: {name}. It stays connected until you disconnect it.")
            return 0
        except KeyboardInterrupt:
            print("\nStopped. Nothing was connected.")
            return 1
        finally:
            server.stop()
    if argv == ["serve"]:
        server = _serving()
        if server is None:
            return 1
        who = server.app.profile()["name"]
        print(f"Listening for the browser extension on 127.0.0.1:{server.port}" + (f" for {who}" if who else "")
              + ", without ReelForge's window. Ctrl-C stops it.", flush=True)
        try:
            while True:
                time.sleep(30)
                try:
                    posting.expire_stale()  # what the window's clock does: a job nobody picked up expires
                except sqlite3.Error:
                    pass  # busy: the next half minute
        except KeyboardInterrupt:
            print("\nStopped.")
            return 0
        finally:
            server.stop()
    print(USAGE)
    return 0 if argv[0] in ("-h", "--help", "help") else 2
