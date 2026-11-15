"""The posting ledger: what ReelForge knows about each post it is helping with (stats.db, tables post_jobs and
post_events). A job is one video going to one platform, through the browser extension or a platform's own API, from
the moment you confirm it until it is up, has failed or was called off. One press of Post makes a batch: a job for
each platform you ticked.

A job moves along ORDER and never backwards:

  queued → claimed → opening → uploading → filling → ready → posting → posted

Steps may be skipped (the extension goes from claimed straight to uploading). ready means everything is filled in and
the platform's own Post button is waiting for you; posting means the extension was allowed to press it. From
anywhere before posted a job can end as failed or cancelled; a queued one nobody picked up in 10 minutes expires
(nothing happened). The state that matters most is unknown: Post was pressed, or was about to be, and no confirmation
came. Nothing ever retries such a job. Only you ("It's posted" / "It didn't post") or a stats read that finds the
post settle it.

A video is the SHA-256 of its bytes, never its file name, and it can have one live job per platform: while a job is
queued, under way, posted or unknown, a second one for the same video and platform is refused (the unique index
post_jobs_one_live holds that even between two windows). Posting again takes saying so (force), which sets the old
row aside as superseded.

Everything here is a plain function on the database dashboard.db has open, so the window, the local server for the
extension and the tests all drive the same code. Each change is one transaction, leaves a row in post_events and
counts as a change every open window shows (db.bump).
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import db, desktop, videos
from .platforms import PublishItem

PROTOCOL = 1  # what ReelForge and the extension speak to each other: both send it, and refuse another number

ORDER = ("queued", "claimed", "opening", "uploading", "filling", "ready", "posting", "posted")
IN_FLIGHT = ORDER[:-1]                       # under way: nothing else can start for that video and platform
FREE = ("failed", "cancelled", "expired")    # over without a post: a new job may be made
LIVE = (*ORDER, "unknown")                   # every state that stands in the way of a new job
EXTENSION_STATES = ("opening", "uploading", "filling", "ready", "posted", "failed", "cancelled", "unknown")
WAITING = ("ready", "posting", "unknown")    # a post may be up without ReelForge having heard: a stats read can tell

QUEUED_FOR = timedelta(minutes=10)   # a job nobody picked up in this long expires
READY_FOR = timedelta(minutes=45)    # filled in and then silent this long: you may have pressed Post yourself
POSTING_FOR = timedelta(minutes=15)  # Post pressed for you and then silent this long
CLOCK_SLACK = timedelta(minutes=2)   # a platform's clock and this computer's needn't agree to the second

X_LIMIT = videos.X_LIMIT
DIGESTS = "video_digests"   # meta: {path: [size, mtime_ns, sha256]}, so a 100 MB video is read once, not at every look
CHANGES = "post_changes"    # meta: a count of every write here, for a window to see that jobs changed (signature)
# The id each platform's stats reader files a post under. Anything else the extension sends as an id (an Instagram
# shortcode, a whole link) is left out, so the reader links the post itself later instead of ReelForge listing it twice.
POST_ID = {"tiktok": r"\d{6,}", "instagram": r"\d{6,}", "x": r"\d{6,}", "youtube": r"[\w-]{11}"}


class Refused(Exception):
    """A change the ledger doesn't allow. code is for the extension ("bad_transition", "already_claimed",
    "not_ready"...), the message a sentence for a person, job the row as it stands when there is one."""

    def __init__(self, code: str, message: str, job: dict | None = None):
        super().__init__(message)
        self.code, self.message, self.job = code, message, job


# ---- the video

def digest(path: Path) -> str:
    """A video's identity: the SHA-256 of its bytes, in lowercase hex. Remembered by path, size and time, so the
    file is read again only when it has changed."""
    st = path.stat()
    key = str(path)
    known = _digests()
    hit = known.get(key)
    if isinstance(hit, list) and hit[:2] == [st.st_size, st.st_mtime_ns]:
        return hit[2]
    sha = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            sha.update(chunk)
    after = path.stat()
    if (after.st_size, after.st_mtime_ns) == (st.st_size, st.st_mtime_ns):  # not written to while it was read
        try:
            known = {p: v for p, v in _digests().items() if Path(p).exists()}  # videos that are gone drop out
            known[key] = [st.st_size, st.st_mtime_ns, sha.hexdigest()]
            db.set_meta(DIGESTS, known)
        except sqlite3.Error:
            pass  # remembering it is an extra
    return sha.hexdigest()


def _digests() -> dict:
    known = db.get_meta(DIGESTS, {})
    return known if isinstance(known, dict) else {}


def unchanged(job: dict) -> bool:
    """Whether the file a job was made for is still that video (not replaced by another cut since)."""
    path = Path(job["video_path"])
    try:
        return path.stat().st_size == job["video_size"] and digest(path) == job["video_sha256"]
    except OSError:
        return False


# ---- the text

def snapshot(item: PublishItem, platform: str) -> dict[str, str]:
    """Exactly what goes in a platform's boxes, from the same functions the Copy buttons use. Taken once, when you
    confirm: the extension types these strings and never builds or trims text itself."""
    if platform == "youtube":
        return {"title": item.youtube_title, "description": item.text_for("youtube")}
    if platform == "x":
        return {"text": item.text_for("x")}
    return {"caption": item.text_for(platform)}


def _headline(text: dict) -> str:
    """A snapshot's main line: the title, the caption or the X post."""
    return text.get("title") or text.get("caption") or text.get("text") or ""


# ---- reading

def get(job_id: str) -> dict | None:
    with db.connect() as c:
        row = c.execute("SELECT * FROM post_jobs WHERE id = ?", (job_id,)).fetchone()
    return dict(row) if row else None


def in_batch(batch: str) -> list[dict]:
    with db.connect() as c:
        return [dict(r) for r in c.execute("SELECT * FROM post_jobs WHERE batch = ? ORDER BY rowid", (batch,))]


def recent(limit: int = 200) -> list[dict]:
    """The newest jobs, oldest of them first."""
    with db.connect() as c:
        rows = c.execute("SELECT * FROM post_jobs ORDER BY rowid DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in reversed(rows)]


def active_batches() -> list[str]:
    """The batches with a job still under way, oldest first."""
    marks = ",".join("?" * len(IN_FLIGHT))
    with db.connect() as c:
        return [r[0] for r in c.execute(f"SELECT batch FROM post_jobs WHERE state IN ({marks}) GROUP BY batch "
                                        "ORDER BY MIN(rowid)", IN_FLIGHT)]


def current(jobs: list[dict]) -> dict[str, dict]:
    """Per platform, the job that says where a video stands: its live one (there is at most one), else the newest
    that ended without a post. jobs: one video's, oldest first. Superseded ones are history."""
    out: dict[str, dict] = {}
    for j in jobs:
        if not j["superseded"] and (j["state"] in LIVE or out.get(j["platform"], j)["state"] not in LIVE):
            out[j["platform"]] = j
    return out


def for_files(paths: list[Path]) -> dict[Path, dict[str, dict]]:
    """Where each of these video files stands on each platform (see current). A job is looked up by the file's name
    first, so only a file that has jobs is ever hashed, and then it's a file ReelForge has hashed before."""
    with db.connect() as c:
        rows = [dict(r) for r in c.execute("SELECT * FROM post_jobs WHERE superseded = 0 ORDER BY rowid")]
    named: dict[str, list[dict]] = {}
    for j in rows:
        named.setdefault(j["video"].lower(), []).append(j)
    out = {}
    for path in paths:
        jobs = named.get(path.name.lower())
        if jobs:
            try:
                sha = digest(path)
            except OSError:
                continue
            out[path] = current([j for j in jobs if j["video_sha256"] == sha])
    return out


def signature(conn: sqlite3.Connection | None = None) -> int:
    """A number that changes with every write to the ledger, a step's progress included (db.changes only counts the
    changes of state). A window compares it with the one it last drew."""
    count = db.get_meta(CHANGES, 0, conn)
    return count if isinstance(count, int) else 0


def press_unlocked(platform: str) -> bool:
    """Whether "press Post for me" may be offered for a platform: never on X, and elsewhere only once a post made
    with the extension's help is confirmed up there, so the first one is always pressed by you."""
    if platform == "x":
        return False
    with db.connect() as c:
        return _assisted_before(c, platform, "")


def _assisted_before(c: sqlite3.Connection, platform: str, job_id: str) -> bool:
    return c.execute("SELECT 1 FROM post_jobs WHERE platform = ? AND via = 'extension' AND state = 'posted' "
                     "AND id != ? LIMIT 1", (platform, job_id)).fetchone() is not None


def job_json(job: dict, handles: dict[str, str] | None = None) -> dict:
    """A job as the extension and the local pages get it. handles: the account ReelForge has connected for each
    platform ("@name"), so the extension can warn when the browser is signed in as someone else."""
    return {
        "id": job["id"], "batch": job["batch"], "platform": job["platform"], "via": job["via"],
        "state": job["state"], "step": job["step"], "progress": job["progress"], "detail": job["detail"],
        "error_code": job["error_code"], "error_message": job["error_message"],
        "post_id": job["post_id"], "url": job["url"], "press": bool(job["press"]), "review": job["review"],
        "video": {"name": job["video"], "size": job["video_size"], "sha256": job["video_sha256"]},
        "text": json.loads(job["text"]), "upload_url": desktop.UPLOAD_PAGES.get(job["platform"], ""),
        "handle": (handles or {}).get(job["platform"], ""), "updated_at": job["updated_at"],
    }


# ---- making jobs

def create_batch(video: Path, platforms: list[str], caption: str, tags: str, cfg: dict, *, press: bool = False,
                 force: tuple[str, ...] | list[str] = (), via: dict[str, str] | None = None,
                 options: dict[str, dict] | None = None, demo: bool = False) -> dict:
    """One press of Post: a job for each of these platforms that can take this video now, with the caption and
    hashtags as the Publish form has them.

    Returns {"batch": its id (None when nothing could start), "jobs": [the new rows],
    "blocked": [{"platform", "reason", "message", and "job", "state" and "url" when there are any}]}. A platform is
    blocked when the video isn't approved (videos.approval), when it has a job that is under way, posted or unknown,
    or when a stats read has already found this video's post there. force names the platforms to post to again all
    the same: it sets a posted or unknown job aside, never one that is under way (cancel that first). press: you
    switched "stop before posting" off. via and options are per platform ("api" for a platform's own API, and what
    it needs).
    """
    item = PublishItem(video=video, caption=(caption or "").strip(), tags=(tags or "").strip())
    return _create(video, {p: snapshot(item, p) for p in platforms}, item.title, cfg, press, force, via, options, demo)


def preview(video: Path, platforms: list[str], caption: str, tags: str, cfg: dict, *,
            force: tuple[str, ...] | list[str] = (), demo: bool = False) -> dict:
    """What create_batch would do with this right now, without doing it, for the question before a post:
    {"review": the video's approval, "texts": {platform: its text}, "blocked": {platform: why it can't go}}."""
    item = PublishItem(video=video, caption=(caption or "").strip(), tags=(tags or "").strip())
    texts = {p: snapshot(item, p) for p in platforms}
    review, blocked = _before(video, texts, item.title, cfg, demo)
    left = [p for p in platforms if p not in {b["platform"] for b in blocked}]
    if left:
        sha, episode = digest(video), db.episode_for_video(video.name)
        with db.connect() as c:
            for p in left:
                block, _ = _standing(c, sha, p, episode["id"] if episode else None, force)
                if block:
                    blocked.append(block)
    return {"review": review, "texts": texts, "blocked": {b["platform"]: b for b in blocked}}


def retry(job_id: str, cfg: dict, demo: bool = False) -> dict:
    """The same post once more, for a job that failed, expired or was cancelled: a new job with the text that one
    had. Returns what create_batch returns."""
    job = get(job_id)
    if job is None or job["state"] not in FREE:
        raise Refused("bad_transition", "Only a post that failed, expired or was cancelled can be tried again.", job)
    text, platform = json.loads(job["text"]), job["platform"]
    return _create(Path(job["video_path"]), {platform: text}, _headline(text), cfg, bool(job["press"]), (),
                   {platform: job["via"]}, {platform: json.loads(job["options"])}, demo)


def _before(video: Path, texts: dict[str, dict], title: str, cfg: dict, demo: bool) -> tuple[dict, list[dict]]:
    """What stops a post before the ledger is looked at: (the video's approval, the platforms that can't go and
    why). A video that is gone, isn't approved or has no caption stops every platform."""
    def every(reason: str, message: str) -> list[dict]:
        return [{"platform": p, "reason": reason, "message": message} for p in texts]

    if not video.is_file():
        return {"state": "none", "line": ""}, every("missing", f"{video.name} isn't there any more.")
    review = {"state": "demo", "line": "This is the demo: nothing is posted from it."} if demo else \
        videos.approval(video, cfg)
    if review["state"] not in videos.POSTABLE:
        return review, every("review", review["line"])
    if not title:
        return review, every("empty", "Write a caption first.")
    blocked = []
    for p in texts:
        if p not in desktop.UPLOAD_PAGES:
            blocked.append({"platform": p, "reason": "unknown_platform", "message": f"ReelForge doesn't post to {p}."})
        elif p == "x" and len(texts[p]["text"]) > X_LIMIT:
            blocked.append({"platform": p, "reason": "too_long", "message": f"An X post takes {X_LIMIT} characters "
                            f"and this one has {len(texts[p]['text'])}. Shorten the caption."})
    return review, blocked


def _standing(c: sqlite3.Connection, sha: str, platform: str, episode: int | None,
              force) -> tuple[dict | None, dict | None]:
    """For one platform, as the ledger and the stats stand: (why a new job can't be made for this video, the posted
    or unknown job a forced one sets aside)."""
    live = c.execute(f"SELECT * FROM post_jobs WHERE video_sha256 = ? AND platform = ? AND superseded = 0 "
                     f"AND state IN ({','.join('?' * len(LIVE))})", (sha, platform, *LIVE)).fetchone()
    if live and (live["state"] in IN_FLIGHT or platform not in force):
        return _in_the_way(dict(live)), None
    if live:  # posted, or unknown: you said to post again
        return None, dict(live)
    spotted = c.execute("SELECT url FROM posts WHERE episode_id = ? AND platform = ? AND post_id NOT LIKE 'manual-%' "
                        "ORDER BY published_at LIMIT 1", (episode, platform)).fetchone()
    if spotted and platform not in force:
        return {"platform": platform, "reason": "spotted", "url": spotted["url"],
                "message": "Already posted: ReelForge found this video's post there."}, None
    return None, None


def _create(video: Path, texts: dict[str, dict], title: str, cfg: dict, press: bool, force, via, options,
            demo: bool) -> dict:
    review, blocked = _before(video, texts, title, cfg, demo)
    left = [p for p in texts if p not in {b["platform"] for b in blocked}]
    if not left:
        return {"batch": None, "jobs": [], "blocked": blocked}
    sha, size = digest(video), video.stat().st_size
    episode = db.adopt_episode(title, str(video))
    batch, now, made = "pb_" + secrets.token_hex(8), db.now_iso(), []
    with db.connect() as c:
        c.execute("BEGIN IMMEDIATE")  # the look and the insert are one step, whoever else is making jobs
        for p in left:
            block, aside = _standing(c, sha, p, episode, force)
            if block:
                blocked.append(block)
                continue
            if aside:
                c.execute("UPDATE post_jobs SET superseded = 1, updated_at = ? WHERE id = ?", (now, aside["id"]))
                _event(c, aside["id"], now, aside["state"], None, "set aside: posted again")
            job = {"id": "pj_" + secrets.token_hex(8), "batch": batch, "created_at": now, "updated_at": now,
                   "platform": p, "via": (via or {}).get(p, "extension"), "video": video.name,
                   "video_path": str(video), "video_sha256": sha, "video_size": size, "episode_id": episode,
                   "text": json.dumps(texts[p], ensure_ascii=False), "press": int(bool(press) and p != "x"),
                   "options": json.dumps((options or {}).get(p) or {}), "review": review["state"],
                   "state": "queued"}
            try:
                c.execute(f"INSERT INTO post_jobs ({', '.join(job)}) VALUES ({','.join('?' * len(job))})",
                          list(job.values()))
            except sqlite3.IntegrityError:  # the index's word is final
                blocked.append({"platform": p, "reason": "in_progress",
                                "message": "A post of this video is already under way there."})
                continue
            _event(c, job["id"], now, "queued", None, None)
            # The upload log too, at the same second: a post that goes up in the next hours is this video's
            # (analytics.auto_link), and RECENT UPLOADS lists it.
            c.execute("INSERT INTO uploads (ts, platform, video, status, message) VALUES (?,?,?,?,?)",
                      (now, p, video.name, job["via"], "posting with the extension" if job["via"] == "extension"
                       else "posting with its API"))
            made.append(job["id"])
    if not made:
        return {"batch": None, "jobs": [], "blocked": blocked}
    db.bump()
    return {"batch": batch, "jobs": in_batch(batch), "blocked": blocked}


def _in_the_way(job: dict) -> dict:
    """Why a video's live job blocks a new one, for the person."""
    if job["state"] == "posted":
        reason, message = "posted", "Already posted with ReelForge's help."
    elif job["state"] == "unknown":
        reason, message = "unknown", ("ReelForge doesn't know whether the last try posted. Check your profile there, "
                                      "then say so on the Publish tab.")
    else:
        reason, message = "in_progress", "A post of this video is already under way there. Cancel it first."
    return {"platform": job["platform"], "reason": reason, "message": message, "job": job["id"], "state": job["state"],
            **({"url": job["url"]} if job["url"] else {})}


# ---- moving a job along

def allowed(old: str, new: str, by: str) -> bool:
    """Whether a job may go from old to new. by: "extension" (what the browser reports), "person" (you, in the
    window or on the local page), "reconcile" (a stats read found the post) or "app" (ReelForge itself: an API
    upload, a page that wouldn't open, the clock)."""
    if by == "extension" and new not in EXTENSION_STATES:
        return False  # claimed and posting are granted (claim, grant_press), never reported
    if by == "person" and new not in ("cancelled", "posted"):
        return False
    if by == "reconcile" and new != "posted":
        return False
    if new == old:
        return True  # said again: a sign of life, with the step it is on now
    if old == "posted" or old in FREE:
        return False  # over
    if old == "unknown":  # only you, or a stats read, can say what happened
        return by in ("person", "reconcile") and new in ("posted", "cancelled")
    if by in ("person", "reconcile") and new == "posted":
        return old in WAITING  # "It's posted" makes sense once the post was ready to go
    if new in ("failed", "cancelled"):
        return True
    if new == "expired":
        return old == "queued"
    if new == "unknown":
        return old in ("ready", "posting")
    return new in ORDER and ORDER.index(new) > ORDER.index(old)


def advance(job_id: str, state: str, *, by: str = "extension", install: str | None = None, step: str | None = None,
            progress: float | None = None, detail: str | None = None, error_code: str | None = None,
            error_message: str | None = None, post_id: str | None = None, url: str | None = None) -> dict:
    """Move a job to a state (or say again where it is, with its step and progress). Returns the job as it is now.
    Refused: no such job ("not_found"), another browser has it ("not_yours", with install), or not a move a job can
    make ("bad_transition")."""
    with db.connect() as c:
        c.execute("BEGIN IMMEDIATE")
        old = _locked(c, job_id)
        if install is not None and old["claimed_by"] != install:
            raise Refused("not_yours", "Another browser took this post.", old)
        if not allowed(old["state"], state, by):
            raise Refused("bad_transition", f"A post that is {old['state']} can't become {state}.", old)
        new = _write(c, old, state, by, step=step, progress=progress, detail=detail, error_code=error_code,
                     error_message=error_message, post_id=post_id, url=url)
    return _settled(old, new, by)


def claim(job_id: str, install: str) -> dict:
    """An extension takes a queued job: the first to ask gets it, and from then on only that browser reports on it.
    The same browser asking again before it has started gets the same answer (its first one may not have arrived)."""
    with db.connect() as c:
        c.execute("BEGIN IMMEDIATE")
        old = _locked(c, job_id)
        if old["state"] == "claimed" and old["claimed_by"] == install:
            return old
        if old["via"] != "extension":
            raise Refused("not_claimable", "This post doesn't go through the extension.", old)
        if old["state"] not in IN_FLIGHT:
            raise Refused("not_claimable", "This post is over.", old)
        if old["state"] != "queued":
            raise Refused("already_claimed", "This post has been taken already.", old)
        if db.parse(old["created_at"]) + QUEUED_FOR <= _now():
            new = _write(c, old, "expired", "app", detail="not picked up in time")
        else:
            new = _write(c, old, "claimed", "app", claimed_by=install)
    _settled(old, new, "app")
    if new["state"] == "expired":
        raise Refused("not_claimable", "This post waited too long to be picked up. Start it again.", new)
    return new


def grant_press(job_id: str, install: str | None = None) -> dict:
    """Let the extension press the platform's Post button, once: the job goes from ready to posting and whoever
    asks again is refused. Refused "press_not_allowed" unless you switched "stop before posting" off for this job,
    the platform isn't X, and an earlier post made with the extension is confirmed up there; "not_ready" before
    everything is filled in; "already_pressed" after."""
    with db.connect() as c:
        c.execute("BEGIN IMMEDIATE")
        old = _locked(c, job_id)
        if install is not None and old["claimed_by"] != install:
            raise Refused("not_yours", "Another browser took this post.", old)
        if not old["press"] or old["platform"] == "x" or not _assisted_before(c, old["platform"], old["id"]):
            raise Refused("press_not_allowed", "Press Post yourself: ReelForge isn't allowed to for this post.", old)
        if old["state"] != "ready":
            pressed = c.execute("SELECT 1 FROM post_events WHERE job = ? AND state = 'posting'", (job_id,)).fetchone()
            raise Refused("already_pressed" if pressed else "not_ready",
                          "Post was already pressed for this one." if pressed else "This post isn't ready yet.", old)
        new = _write(c, old, "posting", "app", step="press")
    return _settled(old, new, "app")


def cancel(job_id: str, detail: str = "", by: str = "person") -> dict:
    """Call a job off. Nothing ReelForge does afterwards touches the platform for it."""
    return advance(job_id, "cancelled", by=by, detail=detail or None)


def resolve(job_id: str, posted: bool) -> dict:
    """Your word on a job ReelForge lost track of: "It's posted", or "It didn't post" (which frees the video for
    another try)."""
    return advance(job_id, "posted" if posted else "cancelled", by="person",
                   detail="you said it's posted" if posted else "you said it didn't post")


def expire_stale(now: datetime | None = None) -> int:
    """What the clock decides, to be run every so often: a job nobody picked up in 10 minutes expires, and one that
    was ready (for 45 minutes) or being posted (for 15) without a word since becomes unknown. Returns how many."""
    now = now or _now()
    rules = (("queued", "created_at", QUEUED_FOR, "expired", "not picked up in time"),
             ("ready", "updated_at", READY_FOR, "unknown", "no word since it was ready to post"),
             ("posting", "updated_at", POSTING_FOR, "unknown", "no confirmation after Post was pressed"))
    moved = 0
    with db.connect() as c:
        c.execute("BEGIN IMMEDIATE")
        for state, column, limit, to, detail in rules:
            cut = (now - limit).isoformat(timespec="seconds")
            for row in c.execute(f"SELECT * FROM post_jobs WHERE state = ? AND {column} <= ?", (state, cut)).fetchall():
                _write(c, dict(row), to, "app", detail=detail)
                moved += 1
    if moved:
        db.bump()
    return moved


def reconcile() -> int:
    """After a stats read: a job that may have posted without ReelForge hearing of it (ready, posting or unknown) is
    posted once the read has filed a post under its episode, on its platform, that went up after the job was made.
    Returns how many it settled."""
    found = []
    with db.connect() as c:
        marks = ",".join("?" * len(WAITING))
        for j in c.execute(f"SELECT * FROM post_jobs WHERE superseded = 0 AND episode_id IS NOT NULL "
                           f"AND state IN ({marks})", WAITING).fetchall():
            since = (db.parse(j["created_at"]) - CLOCK_SLACK).isoformat(timespec="seconds")
            # Not a post another job already stands for (the one you posted again over), nor typed-in numbers.
            post = c.execute(
                """SELECT post_id, url FROM posts WHERE platform = ? AND episode_id = ? AND published_at >= ?
                   AND post_id NOT LIKE 'manual-%' AND post_id NOT IN (
                     SELECT post_id FROM post_jobs WHERE platform = ? AND post_id IS NOT NULL AND id != ?)
                   ORDER BY published_at LIMIT 1""",
                (j["platform"], j["episode_id"], since, j["platform"], j["id"])).fetchone()
            if post:
                found.append((j["id"], post["post_id"], post["url"]))
    settled = 0
    for job_id, post_id, url in found:
        try:
            advance(job_id, "posted", by="reconcile", post_id=post_id, url=url, detail="found by a stats read")
            settled += 1
        except Refused:
            pass  # it moved on in the meantime
    return settled


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _locked(c: sqlite3.Connection, job_id: str) -> dict:
    row = c.execute("SELECT * FROM post_jobs WHERE id = ?", (job_id,)).fetchone()
    if row is None:
        raise Refused("not_found", "ReelForge has no such post.")
    return dict(row)


def _event(c: sqlite3.Connection, job_id: str, ts: str, state: str, step: str | None, detail: str | None) -> None:
    c.execute("INSERT INTO post_events (job, ts, state, step, detail) VALUES (?,?,?,?,?)",
              (job_id, ts, state, step, detail))
    c.execute(f"""INSERT INTO meta VALUES ('{CHANGES}', '1')
                  ON CONFLICT (key) DO UPDATE SET value = CAST(value AS INTEGER) + 1""")


def _write(c: sqlite3.Connection, old: dict, state: str, by: str, **fields) -> dict:
    """The one place a job's row changes (inside the caller's transaction). A new state starts with a clean step,
    progress and detail; the same state said again keeps what isn't given anew."""
    new = dict(old)
    given = {k: v for k, v in fields.items() if v is not None}
    if state == old["state"]:
        if state in FREE or (state == "posted" and not {"post_id", "url"} & given.keys()):
            return new  # over already: nothing to add
        given = {k: v for k, v in given.items() if k in ("step", "progress", "detail") or not old[k]}
    else:
        new.update(state=state, step=None, progress=None, detail=None)
    if "post_id" in given and by != "reconcile" and not re.fullmatch(POST_ID.get(old["platform"], ".+"),
                                                                    str(given["post_id"])):
        del given["post_id"]
    new.update(given, updated_at=db.now_iso())
    columns = ("state", "step", "progress", "detail", "error_code", "error_message", "post_id", "url", "claimed_by",
               "updated_at")
    c.execute(f"UPDATE post_jobs SET {', '.join(f'{k} = ?' for k in columns)} WHERE id = ?",
              [new[k] for k in columns] + [old["id"]])
    # The log keeps each state and step once; the percent of an upload going by is only on the row.
    if (new["state"], new["step"], new["detail"]) != (old["state"], old["step"], old["detail"]):
        _event(c, old["id"], new["updated_at"], new["state"], new["step"], new["detail"])
    else:
        c.execute(f"UPDATE meta SET value = CAST(value AS INTEGER) + 1 WHERE key = '{CHANGES}'")
    return new


def _settled(old: dict, new: dict, by: str) -> dict:
    """After the transaction: every window redraws for a change of state, and a post whose id the extension read
    off the page goes into the stats as this episode's, so it shows before the next read (which fills in its
    numbers and never files it anywhere else)."""
    if new["state"] == "posted" and new["post_id"] and not old["post_id"] and by != "reconcile":
        db.add_post(new["platform"], new["post_id"], new["episode_id"], _headline(json.loads(new["text"])),
                    new["url"])
    if (new["state"], new["post_id"], new["url"]) != (old["state"], old["post_id"], old["url"]):
        db.bump()
    return new
