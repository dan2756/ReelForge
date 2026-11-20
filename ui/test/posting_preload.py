"""For the window's posting and accounts tests (ui/test/posting.test.tsx, accounts.test.tsx): settings_preload.py's
channel in a scratch folder (REELFORGE_TEST_ROOT), with platforms that are connected and read nothing, and three
finished videos, newest first: ep03_hail.mp4 (from the pipeline, QA passed and reviewed), ep02_duck.mp4 (from the
pipeline, QA passed, no review yet) and clip.mp4 (not from the pipeline). Run inside the window's Python side before
it starts (REELFORGE_BRIDGE_PRELOAD). What a test asks for comes in REELFORGE_TEST_POSTING, as JSON:

  platforms    the connected platforms (all four unless given)
  videos       false: a channel with no finished video yet
  extension    a browser the extension is connected in: "on" (heard from just now), "waiting" (half an hour ago) or
               "old" (an extension of another version); none unless given
  jobs         {platform: [the states the extension reported for ep03_hail.mp4's post there]}; "expired" and
               "cancelled" are the ledger's own
  spotted      {platform: views}: a post of ep03_hail.mp4 a stats read found there
  logged_in    the platforms ReelForge's own browser is logged in to (they read their stats through it)
  needs_login  the platforms still waiting for their one log-in there (nothing reads them yet: Type in stats)
  was          {platform: "@old"}: stats that platform was read under before its handle changed
  fail         an RPC that raises (or a list of them), to see what the window makes of a call that failed

Nothing here touches this computer or the network. No browser opens: each page that would is listed in
REELFORGE_OPENED's file. Nothing is copied: REELFORGE_COPIED's file gets it. No file manager opens. ReelForge's own
browser is a stand-in that only remembers whether it is logged in. The extension's part is played by the test,
through the test_extension call added here. A local server only listens when the test sets
REELFORGE_EXTENSION_PORT=0 (any free port): the window the tests draw has it off otherwise (ui/scripts/headless.tsx),
whatever the variable is where they run."""

from __future__ import annotations

import json
import os
import runpy
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dashboard import __main__ as entry
from dashboard import config, db, desktop, posting
from dashboard.platforms import Account, Platform, Post, browser
from dashboard.service import Dashboard
from studio.bridge import rpc

runpy.run_path(str(Path(__file__).with_name("settings_preload.py")), run_name="preload")
ROOT = Path(os.environ["REELFORGE_TEST_ROOT"]).resolve()
ASKED = json.loads(os.environ.get("REELFORGE_TEST_POSTING") or "{}")
NAMES = {"tiktok": "TikTok", "youtube": "YouTube", "instagram": "Instagram", "x": "X"}
COLORS = {"tiktok": "#25f4ee", "youtube": "#ff4040", "instagram": "#b46cff", "x": "#e7e9ea"}
CHROME = "install-chrome"
CAPTION, TAGS = "What if hail doubled every 2 seconds?", "#fyp #whatif #weather"
LOGGED_IN = set(ASKED.get("logged_in") or ())
NEEDS_LOGIN = set(ASKED.get("needs_login") or ())


def _note(env: str, text: str) -> None:
    if os.environ.get(env):
        with open(os.environ[env], "a", encoding="utf-8", newline="\n") as f:
            f.write(text + "\n")


desktop.open_url = lambda url: _note("REELFORGE_OPENED", url) or True  # as a browser that opened would answer
desktop.copy = lambda text: _note("REELFORGE_COPIED", text + "\n-- copied --") or True
desktop.reveal = lambda path: _note("REELFORGE_OPENED", f"reveal {Path(path).name}")
browser.logged_in = lambda key: key in LOGGED_IN
browser.login = lambda key, timeout=600: LOGGED_IN.add(key)
browser.logout = lambda key: LOGGED_IN.discard(key)


class Reader(Platform):
    """A connected platform whose stats read finds the posts it's given, and nothing on the network."""

    def __init__(self, key: str, posts: list[Post] = (), connected: bool = True):
        super().__init__({"handle": "me"} if connected else {})
        self.key, self.name, self.color, self.posts = key, NAMES[key], COLORS[key], list(posts)
        self.browser_stats = key in LOGGED_IN or key in NEEDS_LOGIN
        self.public_stats = not self.browser_stats
        self.setup_note = f"A stand-in for {self.name}: nothing is read."

    @property
    def uses_browser(self) -> bool:
        return False

    def fetch(self):
        return Account("@me", 1_000), self.posts


def _video(path: Path, hours_ago: float, data: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    when = time.time() - hours_ago * 3600
    os.utime(path, (when, when))
    return path


def _episode(name: str, title: str, hours_ago: float, review: str | None) -> Path:
    """A pipeline episode whose QA passed on its video, reviewed with these words (None: not reviewed yet)."""
    ep = ROOT / "episodes" / name
    video = _video(ep / "final" / f"{name}.mp4", hours_ago, name.encode())
    (ep / "episode.json").write_text(json.dumps({"id": name, "title": title, "hashtags": ["fyp", "whatif", "weather"]}),
                                     encoding="utf-8")
    st = video.stat()
    (ep / "build" / "qa").mkdir(parents=True, exist_ok=True)
    (ep / "build" / "qa" / "qa.json").write_text(json.dumps({
        "episode": name, "pass": True, "video": video.name, "fingerprint": f"{st.st_size}:{int(st.st_mtime)}",
        "checks": []}), encoding="utf-8")
    if review is not None:
        path = ep / "build" / "qa" / "review.md"
        path.write_text(review, encoding="utf-8")
        os.utime(path, (st.st_mtime + 60, st.st_mtime + 60))
    return video


def _videos(cfg: dict) -> Path:
    for old in (ROOT / "episodes").glob("ep01_duck"):  # settings_preload's video: this channel has its own three
        for f in sorted(old.rglob("*"), reverse=True):
            f.unlink() if f.is_file() else f.rmdir()
        old.rmdir()
    if ASKED.get("videos") is False:
        return ROOT / "episodes" / "ep03_hail" / "final" / "ep03_hail.mp4"
    hail = _episode("ep03_hail", CAPTION, 1, "Looked at every sheet.\nAPPROVED")
    _episode("ep02_duck", "What if a duck doubled every 2 seconds?", 26, None)
    _video(config.videos_dir(cfg) / "clip.mp4", 50, b"a clip")
    return hail


def _extension(how: str | None) -> None:
    store = ROOT / "dashboard" / "data" / "extension.json"
    if not how:
        store.unlink(missing_ok=True)
        return
    seen = (datetime.now(timezone.utc) - timedelta(minutes=30 if how == "waiting" else 0)).isoformat(timespec="seconds")
    store.parent.mkdir(parents=True, exist_ok=True)
    store.write_text(json.dumps({"app_id": "0000", "extension_ids": [], "paired": [
        {"install": CHROME, "name": "Chrome on this PC", "version": "0.1.0", "token_sha256": "ab" * 32,
         "paired_at": seen, "last_seen": seen, **({"protocol": 0} if how == "old" else {}),
         "accounts": {"tiktok": {"signed_in": True, "handle": "@me"}, "youtube": {"signed_in": True, "handle": None},
                      "instagram": {"signed_in": False, "handle": None}}}]}), encoding="utf-8")


def _work(job_id: str, states: list[str]) -> None:
    """The extension's part for one job: take it and report these states."""
    last = {"failed": {"step": "find_input", "error_code": "hook_not_found"},
            "uploading": {"step": "upload", "progress": 0.43},
            "posted": {"post_id": "7300000000000000001", "url": "https://www.tiktok.com/@me/video/7300000000000000001"}}
    for state in states:
        if state == "queued":
            continue
        if state == "cancelled":
            posting.cancel(job_id, "you cancelled it")
        elif state == "expired":
            long_ago = (datetime.now(timezone.utc) - timedelta(minutes=11)).isoformat(timespec="seconds")
            with db.connect() as c:
                c.execute("UPDATE post_jobs SET created_at = ? WHERE id = ?", (long_ago, job_id))
            posting.expire_stale()
        else:
            if posting.get(job_id)["state"] == "queued":
                posting.claim(job_id, CHROME)
            posting.advance(job_id, state, install=CHROME, **last.get(state, {}))


def load_platforms(demo: bool = False, only_demos: bool = False, unconnected=()) -> tuple[dict, dict]:
    """This channel's settings and its stand-in platforms, with the stats database open in the scratch folder and
    whatever the test asked for already in it."""
    cfg = config.load_config()
    cfg.update(refresh_minutes=0, default_tags=TAGS)
    db.init(config.DB_PATH)
    hail = _videos(cfg)
    _extension(ASKED.get("extension"))
    found = {}
    for key, views in (ASKED.get("spotted") or {}).items():
        episode = db.adopt_episode(CAPTION, str(hail))
        day_ago = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(timespec="seconds")
        post = Post("3500000000000000001", CAPTION, f"https://example.invalid/{key}/1", day_ago, views=views)
        db.add_post(key, post.post_id, episode, CAPTION, post.url)
        db.record_snapshot(key, Account("@me", 1_000), [post])
        found[key] = [post]
    for key, handle in (ASKED.get("was") or {}).items():
        long_ago = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat(timespec="seconds")
        db.record_snapshot(key, Account(handle, 400), [Post("900001", "Someone else's video", None, long_ago, views=90)],
                           ts=long_ago)
        # (a read under the new handle with a video the old one didn't have: another account, as far as ReelForge can tell)
        db.record_snapshot(key, Account("@me", 1_000), [Post("900002", "My own video", None, db.now_iso(), views=10)])
    jobs = ASKED.get("jobs") or {}
    if jobs:
        made = posting.create_batch(hail, list(jobs), CAPTION, TAGS, cfg)
        for job in made["jobs"]:
            _work(job["id"], jobs[job["platform"]])
    on = ASKED.get("platforms", NAMES)
    return cfg, {key: Reader(key, found.get(key, ()), key in on) for key in NAMES}


entry.load_platforms = load_platforms


@rpc
def test_extension(self, platform: str, states: list[str]) -> None:
    """The extension, played by the test: it takes the picked video's post on a platform and reports these states,
    the way the local server would write them down."""
    job = posting.for_files([self.selected_file])[self.selected_file][platform]
    _work(job["id"], states)


Dashboard.test_extension = test_extension

def broken(self, *args, **kwargs):
    raise RuntimeError("boom")


for name in [ASKED["fail"]] if isinstance(ASKED.get("fail"), str) else ASKED.get("fail") or ():
    setattr(Dashboard, name, rpc(broken))
