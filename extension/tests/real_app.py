"""ReelForge's real local server (dashboard.extension_api.Server) and its real ledger, on a scratch channel, for
the extension's tests (real.mjs starts this and real.test.mjs drives it). The stats database, the connections
file and the videos are in a folder made for the run: finished videos as the pipeline leaves them, with QA passed
and an approved review, and all four platforms connected. The clipboard, the file manager and the browser are
stand-ins that only note what they were asked for. Nothing of yours is read or written, and no site is reached.

It prints one line of JSON when it listens, then answers each line of JSON on its standard input with one line:

  {"do": "<name>", ...arguments}   ->   {"ok": <what it returns>}   or   {"error": "<what went wrong>"}

  python extension/tests/real_app.py <the ReelForge folder>
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

KIT = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(KIT))
sys.dont_write_bytecode = True

from dashboard import db, desktop, extension_api, posting  # noqa: E402
from dashboard.config import PLATFORM_KEYS  # noqa: E402
from dashboard.platforms import Account, Platform  # noqa: E402
from dashboard.tests.test_posting import approve  # noqa: E402  (QA and a review, as ReelForge's own tests write them)

NAMES = {"tiktok": "TikTok", "youtube": "YouTube", "instagram": "Instagram", "x": "X"}
CAPTION, TAGS = "What if hail doubled every 2 seconds?", "#whatif #hail"

root = Path(tempfile.mkdtemp(prefix="reelforge-real-"))
db.init(root / "stats.db")
(root / "videos").mkdir()
cfg = {"videos_dir": str(root / "videos"), "episodes_dir": str(root / "episodes"),
       "completed_dir": str(root / "completed"), **{key: {} for key in PLATFORM_KEYS}}
extension_api.store_path = lambda: root / "extension.json"
opened: list[str] = []
copied: list[str] = []
desktop.copy = lambda text: copied.append(text) or True
desktop.reveal = lambda path: None
desktop.open_url = lambda url: opened.append(url) or True


class Reader(Platform):
    """A connected platform that reads nothing."""

    def __init__(self, key: str, name: str):
        super().__init__({"handle": "fixtureuser"})
        self.key, self.name = key, name

    @property
    def stats_source(self) -> str:
        return "public"

    @property
    def uses_browser(self) -> bool:
        return False

    def fetch(self):
        return Account("@fixtureuser", 1000), []


class App(extension_api.App):
    def profile(self) -> dict:
        return {"key": ".", "name": "Rendered Wrong"}

    def platforms(self) -> list[dict]:
        return [{"key": p.key, "name": p.name, "handle": p.handle, "connected": True} for p in self.every.values()]


app = App(cfg, {key: Reader(key, name) for key, name in NAMES.items()})
server: extension_api.Server | None = None
busy = [0]  # how many of the extension's reports find the database busy before one is taken
advance = posting.advance


def reporting(*args, **kw):
    if busy[0] > 0 and kw.get("install") is not None:
        busy[0] -= 1
        raise sqlite3.OperationalError("database is locked")
    return advance(*args, **kw)


posting.advance = reporting

# A newer list of page hooks than the one packed with the extension, when a test hands one over: what a
# ReelForge that was updated serves to an extension that has not been reloaded yet.
newer: dict = {}
packed = extension_api.selectors
extension_api.selectors = lambda: newer or packed()


def rows(sql: str, *args) -> list[dict]:
    with db.connect() as c:
        return [dict(r) for r in c.execute(sql, args)]


def start(port: int | None = None) -> int:
    """Listen: on this port, or on the first of ReelForge's own that is free."""
    global server
    server = extension_api.Server(app, ports=(port,) if port else extension_api.PORTS)
    if not server.start():
        raise RuntimeError("no free port between 47821 and 47830")
    return server.port


def stop() -> bool:
    global server
    if server is not None:
        server.stop()
    server = None
    return True


def video(name: str = "ep32_hail", size: int = 300 * 1024, caption: str = CAPTION,
          review: str | None = "Every sheet looked at.\nAPPROVED") -> dict:
    """A finished video from the pipeline on the Publish list: its episode folder with the hook line and the
    hashtags, QA passed on this exact file, and a review written after it (None: no review yet)."""
    episode = root / "episodes" / name
    (episode / "final").mkdir(parents=True)
    spec = {"id": name, "title": caption, "hashtags": [tag.lstrip("#") for tag in TAGS.split()]}
    (episode / "episode.json").write_text(json.dumps(spec), "utf-8")
    path = episode / "final" / f"{name}.mp4"
    path.write_bytes(os.urandom(size))
    approve(episode, path, review)
    return {"path": str(path), "name": path.name, "size": size, "id": posting.digest(path)}


def batch(path: str, platforms: list[str], press: bool = False, force: list[str] | None = None) -> dict:
    """Post pressed in ReelForge's window: the jobs, and the address of the page that hands them to the extension."""
    made = posting.create_batch(Path(path), platforms, CAPTION, TAGS, cfg, press=press, force=tuple(force or ()))
    return {"batch": made["batch"], "post": server.post_url(made["batch"]) if made["batch"] else None,
            "jobs": {j["platform"]: j["id"] for j in made["jobs"]}, "blocked": made["blocked"]}


def unlock(platform: str) -> bool:
    """As if one post made with the extension's help had gone up there: what lets the press be switched on."""
    earlier = root / "videos" / f"earlier_{platform}.mp4"
    earlier.write_bytes(os.urandom(2048))
    (job,) = posting.create_batch(earlier, [platform], "An earlier one", "#a", cfg)["jobs"]
    posting.claim(job["id"], "an-earlier-browser")
    advance(job["id"], "ready", install="an-earlier-browser")
    advance(job["id"], "posted", install="an-earlier-browser")
    earlier.unlink()
    return posting.press_unlocked(platform)


def age(job: str, minutes: float, column: str = "updated_at") -> bool:
    """As if the job had last been heard of (or been made: created_at) this long ago."""
    then = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat(timespec="seconds")
    with db.connect() as c:
        if column == "created_at":  # its line in the upload log was written in the same second
            c.execute("UPDATE uploads SET ts = ? WHERE ts = (SELECT created_at FROM post_jobs WHERE id = ?) "
                      "AND platform = (SELECT platform FROM post_jobs WHERE id = ?)", (then, job, job))
        c.execute(f"UPDATE post_jobs SET {'created_at' if column == 'created_at' else 'updated_at'} = ? WHERE id = ?",
                  (then, job))
    return True


COMMANDS = {
    "start": start, "stop": stop, "video": video, "batch": batch, "unlock": unlock, "age": age,
    "pair_url": lambda: server.pair_url(),
    "job": lambda id: posting.get(id),
    "jobs": lambda: rows("SELECT * FROM post_jobs ORDER BY rowid"),
    "events": lambda id: [e["state"] for e in rows("SELECT state FROM post_events WHERE job = ? ORDER BY id", id)],
    "log": lambda id: rows("SELECT state, step, detail FROM post_events WHERE job = ? ORDER BY id", id),
    "uploads": lambda: rows("SELECT platform, video, status FROM uploads ORDER BY id"),
    "posts": lambda: rows("SELECT platform, post_id, episode_id, link, url FROM posts"),
    "paired": lambda: [p.get("name") for p in extension_api.paired(extension_api.read_store())],
    # The connected browsers as the connections file has them, without what is left of their tokens.
    "browsers": lambda: [{k: v for k, v in p.items() if k != "token_sha256"}
                         for p in extension_api.paired(extension_api.read_store())],
    "forget": lambda install: extension_api.unpair(install),  # Disconnect in ReelForge's window
    "selectors": lambda data: (newer.clear(), newer.update(data)) and True,
    "protocol": lambda number: setattr(extension_api, "PROTOCOL", number) or True,
    "demo": lambda on: setattr(app, "demo", on) or True,
    "desk": lambda: {"opened": opened, "copied": copied},  # what the stand-in browser and clipboard were given
    "busy": lambda count: busy.__setitem__(0, count) or True,
    "expire": lambda: posting.expire_stale(),
    "replace": lambda path: Path(path).write_bytes(os.urandom(300 * 1024 + 7)) > 0,
    "resolve": lambda id, posted: posting.resolve(id, posted)["state"],
    "press_unlocked": lambda platform: posting.press_unlocked(platform),
}

print(json.dumps({"port": start(), "root": str(root)}), flush=True)
for line in sys.stdin:
    try:
        command = json.loads(line)
        print(json.dumps({"ok": COMMANDS[command.pop("do")](**command)}, default=str), flush=True)
    except Exception as e:  # whatever it is, the test is told
        print(json.dumps({"error": f"{type(e).__name__}: {e}"}), flush=True)
stop()
shutil.rmtree(root, ignore_errors=True)
