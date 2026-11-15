"""The posting ledger: what ReelForge knows about each post it is helping with (stats.db, tables post_jobs and
post_events). A job is one video going to one platform, through the browser extension or a platform's own API, from
the moment you confirm it until it is up, has failed or was called off. One press of Post makes a batch: a job for
each platform you ticked.

A job moves along ORDER and never backwards:

  queued → claimed → opening → uploading → filling → ready → posting → posted

Steps may be skipped (the extension goes from claimed straight to uploading). ready means everything is filled in and
the platform's own Post button is waiting for you; posting means the extension was allowed to press it. From
anywhere before posting a job can end as failed or cancelled; a queued one nobody picked up in 10 minutes expires
(nothing happened). The state that matters most is unknown: Post was pressed, or may have been, and no confirmation
came. Nothing ever retries such a job. Only you ("It's posted" / "It didn't post") or a stats read that finds the
post settle it. Once the extension was allowed to press, or saw you press Post in the tab yourself, it can only end
the job as posted or unknown, and a Cancel in the window is refused: a press can't be taken back, so the job ends
on your answer to "did it post?". If the browser then still reports the post as up, the job is unknown again.

A video is the SHA-256 of its bytes, never its file name, and it can have one live job per platform: while a job is
queued, under way, posted or unknown, a second one for the same video and platform is refused (the unique index
post_jobs_one_live holds that even between two windows). Posting again takes saying so (force), which sets the old
row aside as superseded. It stands again if the new try ends without a post.

Everything here is a plain function on the database dashboard.db has open, so the window, the local server for the
extension and the tests all drive the same code. Each change of state is one transaction, leaves a row in post_events
and counts as a change every open window shows (db.bump). The percent of an upload going by is lighter: it is kept
on the job's row and moves only the count a window watches for its posting view (signature).
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
ON_THE_PAGE = ("uploading", "filling")       # the video is on the platform's page, whose own Post button you can press

QUEUED_FOR = timedelta(minutes=10)   # a job nobody picked up in this long expires
READY_FOR = timedelta(minutes=45)    # filled in and then silent this long: you may have pressed Post yourself
POSTING_FOR = timedelta(minutes=15)  # Post pressed for you and then silent this long
# A browser that took a job and then says nothing more (it crashed, was closed for good, the extension was removed).
# Both are longer than the extension itself ever stays silent while it works: it waits 15 minutes for your answer
# about another account, and 30 for a platform to take a video in.
OPENING_FOR = timedelta(minutes=20)  # ... before the video was on any page: nothing was posted
FILLING_FOR = timedelta(minutes=45)  # ... with the video on the page: you may have pressed Post yourself
CLOCK_SLACK = timedelta(minutes=2)   # a platform's clock and this computer's needn't agree to the second

X_LIMIT = videos.X_LIMIT
DIGESTS = "video_digests"   # meta: {path: [size, mtime_ns, sha256]}, so a 100 MB video is read once, not at every look
CHANGES = "post_changes"    # meta: a count of every write here, for a window to see that jobs changed (signature)
# The id each platform's stats reader files a post under. Anything else the extension sends as an id (an Instagram
# shortcode, a whole link) is left out, so the reader links the post itself later instead of ReelForge listing it twice.
POST_ID = {"tiktok": r"\d{6,}", "instagram": r"\d{6,}", "x": r"\d{6,}", "youtube": r"[\w-]{11}"}


# How far the extension's own tests go. Said wherever posting with it is offered: the window and the local page.
TESTED = "Tested against copies of the upload pages, not yet on the live sites: check each post before you press Post."
# The step inside a state, as the extension names it.
STEPS = {
    "open_tab": "opening the tab", "signed_in": "checking you're signed in", "find_input": "finding the upload box",
    "attach": "attaching the video", "upload": "uploading", "fill_title": "the title", "fill_caption": "the caption",
    "audience": "the audience question", "visibility": "who can see it", "ready": "", "press": "",
    "confirm": "waiting for the platform to say it's up",
}
# Why a job failed, when the extension gave a code and no sentence of its own. {name}: the platform.
FAILURES = {
    "extension_outdated": "the extension is older than this ReelForge: update it, then try again",
    "app_unreachable": "the extension couldn't reach ReelForge",
    "not_signed_in": "sign in to {name} in that browser, then try again",
    "wrong_account": "that browser is signed in to another {name} account",
    "hook_not_found": "{name} changed its page, so the extension lost its way: post this one by hand",
    "challenge": "{name} is asking you to prove it's you: finish that in the tab",
    "platform_error": "{name} showed an error",
    "timeout": "nothing happened in time",
    "tab_closed": "the tab was closed before it posted",
    "press_rejected": "{name} showed no reaction to the press: check your profile there before you post again",
    "changed": "the video file changed after you pressed Post here",
    "unknown_outcome": "ReelForge can't tell whether it posted: check your profile there",
}


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


def whereabouts(job: dict, cfg: dict) -> Path:
    """The file a job's video is in now: where it was when the job was made, else a video on the Publish list with
    the same bytes (the pipeline moves a delivered video to completed videos/, and a ReelForge folder can be moved
    whole). When neither is there, the path it had."""
    path = Path(job["video_path"])
    if path.is_file():
        return path
    for f in videos.find(cfg):
        try:
            if f.stat().st_size == job["video_size"] and digest(f) == job["video_sha256"]:
                return f
        except OSError:
            continue
    return path


# ---- the text

def snapshot(item: PublishItem, platform: str) -> dict[str, str]:
    """Exactly what goes in a platform's boxes, from the same functions the Copy buttons use. Taken once, when you
    confirm: the extension types these strings and never builds or trims text itself."""
    if platform == "youtube":
        return {"title": item.youtube_title, "description": item.text_for("youtube")}
    if platform == "x":
        return {"text": item.text_for("x")}
    return {"caption": item.text_for(platform)}


def headline(text: dict) -> str:
    """A snapshot's main line: the title, the caption or the X post. It is what the button that opens a platform's
    upload page copies."""
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


def spotted(name: str) -> dict[str, dict]:
    """Platform -> the post a stats read has filed under this video file's episode (its first there). Typed-in
    numbers aren't posts anyone found."""
    episode = db.episode_for_video(name)
    out: dict[str, dict] = {}
    if episode:
        with db.connect() as c:
            for row in c.execute("SELECT platform, post_id, url, published_at FROM posts WHERE episode_id = ? "
                                 "AND post_id NOT LIKE 'manual-%' ORDER BY published_at", (episode["id"],)):
                out.setdefault(row["platform"], dict(row))
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


def standing(job: dict | None, post: dict | None) -> str:
    """Where a video stands on a platform, from its job (current) and the post a stats read has filed under its
    episode: "none", "spotted" (the read found it and no job says more), or the job's state. A live job has the say.
    One that ended without a post never hides a post the read found, whichever came first: the video is up there,
    and a try at posting it again that failed or was called off doesn't change that."""
    if job and job["state"] in LIVE:
        return job["state"]
    if post:
        return "spotted"
    return job["state"] if job else "none"


def failure(job: dict, name: str) -> str:
    """Why a job failed: the extension's own sentence, else ours for its code, else the code."""
    return job["error_message"] or FAILURES.get(job["error_code"] or "", "").format(name=name) or \
        job["error_code"] or ""


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
    return _create(whereabouts(job, cfg), {platform: text}, headline(text), cfg, bool(job["press"]), (),
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
    if old in FREE:
        # Over. You can still set one aside as cancelled (you posted that one by hand instead): either way there
        # was no post, so it changes nothing about what may be posted next.
        return by == "person" and new == "cancelled"
    if old == "posted":
        return False
    if old == "unknown":  # only you, or a stats read, can say what happened
        return by in ("person", "reconcile") and new in ("posted", "cancelled")
    if by in ("person", "reconcile") and new == "posted":
        # "It's posted" makes sense once the post was ready to go. A stats read that found the post is believed
        # from the moment the video was on the page: some platforms take a press of Post while it is going up.
        return old in WAITING or (by == "reconcile" and old in ON_THE_PAGE)
    if old == "posting" and by == "extension" and new in ("failed", "cancelled"):
        return False  # Post was pressed for you: what the browser calls over is "not sure" (see advance)
    if new in ("failed", "cancelled"):
        return True
    if new == "expired":
        return old == "queued"
    if new == "unknown":
        # From any state a browser has the job in: your own press of Post can come before the extension calls
        # the post ready (the video only has to be on the page), and the clock gives up on a silent browser.
        return old in IN_FLIGHT and old != "queued"
    return new in ORDER and ORDER.index(new) > ORDER.index(old)


def advance(job_id: str, state: str, *, by: str = "extension", install: str | None = None, step: str | None = None,
            progress: float | None = None, detail: str | None = None, error_code: str | None = None,
            error_message: str | None = None, post_id: str | None = None, url: str | None = None,
            strategy: str | None = None, answered: bool = False) -> dict:
    """Move a job to a state (or say again where it is, with its step and progress). Returns the job as it is now.
    strategy: how the extension found what it was looking for on the page ("caption=role"), kept in the job's log
    only. answered: this is your answer to "did it post?" (resolve), not a plain Cancel. Refused: no such job
    ("not_found"), not this browser's ("not_yours", with install: only the browser that claimed a job reports on
    it), a Cancel after Post was pressed ("pressed"), or not a move a job can make ("bad_transition")."""
    with db.connect() as c:
        c.execute("BEGIN IMMEDIATE")
        old = _locked(c, job_id)
        # (A job still waiting is nobody's yet: any connected browser may call it off, as the popup's Cancel does
        # for the posts it asked about.)
        if install is not None and old["claimed_by"] != install and (old["state"], state) != ("queued", "cancelled"):
            raise Refused("not_yours", "Another browser took this post.", old)
        under_way = old["state"] in IN_FLIGHT and state in ("failed", "cancelled", "posted")
        pressed = under_way and (old["state"] == "posting" or _pressed(c, old["id"]))
        if by == "extension" and pressed and state != "posted":
            # Post was pressed (for you, once the press was granted, or by you in the tab), so the click may have
            # landed whatever the page showed afterwards. A browser that gives up from here leaves a job nobody
            # is sure of, with its own word on why: free again only on your "It didn't post", or settled by a
            # stats read. Never free for a retry that would press twice.
            state = "unknown"
        if by == "person" and pressed and state == "cancelled" and not (answered and old["via"] == "extension"):
            # A Cancel can't take a press back. It would free the video for another try while the post that the
            # press made is on its way up, and that post's own news would then find a job that is over. So after
            # a press only your answer ends a job: "It didn't post". (An upload through an API has nobody who
            # could know yet: its last piece is on its way, and its answer is YouTube's to give.)
            raise Refused("pressed", "The last piece of the video is already on its way, so this can't be called "
                          "off any more. Its row says how it ended." if old["via"] != "extension" else
                          "Post was already pressed for this one. Check your profile there, then say whether it "
                          "posted.", old)
        if post_id and by != "reconcile" and _anothers(c, old, str(post_id)):
            post_id = url = None  # the page showed some other post: a stats read finds this job's own (reconcile)
        if by == "extension" and state == "posted" and old["state"] == "cancelled" and _pressed(c, old["id"]):
            new = _back(c, old, post_id, url)
        else:
            if by == "person" and pressed and state == "posted" and old["state"] in ON_THE_PAGE:
                pass  # you pressed Post in the tab while the video was going up: "It's posted" is yours to say
            elif not allowed(old["state"], state, by):
                raise Refused("bad_transition", f"A post that is {old['state']} can't become {state}.", old)
            new = _write(c, old, state, by, step=step, progress=progress, detail=detail, error_code=error_code,
                         error_message=error_message, post_id=post_id, url=url)
        if strategy:  # when a platform changes its page, this is where it shows first
            _event(c, old["id"], new["updated_at"], new["state"], new["step"], f"found by {strategy}")
    return _settled(old, new, by)


def pressed(job_id: str) -> bool:
    """Whether a platform's own Post button was pressed for a job, as far as ReelForge was told: it let the
    extension press (grant_press), or the extension saw you press it in the tab."""
    with db.connect() as c:
        return _pressed(c, job_id)


def _pressed(c: sqlite3.Connection, job_id: str) -> bool:
    # (The job's log keeps each state and step once: "posting" is the granted press, and the step "press" under
    # any other state is the extension's word that it saw yours.)
    return c.execute("SELECT 1 FROM post_events WHERE job = ? AND (state = 'posting' OR step = 'press') LIMIT 1",
                     (job_id,)).fetchone() is not None


def _back(c: sqlite3.Connection, old: dict, post_id: str | None, url: str | None) -> dict:
    """The browser says a post went up, for a job you ended with "It didn't post" after Post was pressed. That is
    news about a real post, and it isn't dropped: the job is "not sure" again, with the post's id and link if the
    page showed them, so nothing posts this video there once more until you have looked. Where another try stands
    for the video by now, this one is kept behind it and stands again if that try ends without a post."""
    other = c.execute(f"SELECT 1 FROM post_jobs WHERE video_sha256 = ? AND platform = ? AND superseded = 0 "
                      f"AND id != ? AND state IN ({','.join('?' * len(LIVE))})",
                      (old["video_sha256"], old["platform"], old["id"], *LIVE)).fetchone()
    c.execute("UPDATE post_jobs SET superseded = ? WHERE id = ?", (int(other is not None), old["id"]))
    old = {**old, "superseded": int(other is not None)}
    return _write(c, old, "unknown", "app", post_id=post_id, url=url,
                  detail="the browser saw it posted after you said it didn't")


def _anothers(c: sqlite3.Connection, job: dict, post_id: str) -> bool:
    """Whether a post id the extension read off the page is a post ReelForge already has as another's: one that
    went up before this job was made (the page showed an older video), or one that you, or ReelForge, filed under
    another video. Such a post stays where it is."""
    known = c.execute("SELECT episode_id, link, published_at FROM posts WHERE platform = ? AND post_id = ?",
                      (job["platform"], post_id)).fetchone()
    if known is None:
        return False
    since = (db.parse(job["created_at"]) - CLOCK_SLACK).isoformat(timespec="seconds")
    return (known["published_at"] or "") < since or \
        (known["link"] in ("hand", "publish") and known["episode_id"] != job["episode_id"])


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
    return advance(job_id, "posted" if posted else "cancelled", by="person", answered=True,
                   detail="you said it's posted" if posted else "you said it didn't post")


def expire_stale(now: datetime | None = None) -> int:
    """What the clock decides, to be run every so often. Returns how many jobs it moved.

    A job nobody picked up in 10 minutes expires. One that was ready (for 45 minutes) or being posted (for 15)
    without a word since becomes unknown. And a job a browser took and then went silent on doesn't stay under way
    for ever, blocking its video: before the video was on a page (20 minutes) it has failed, and nothing was
    posted; with the video on the page (45 minutes) it is unknown, because the platform's own Post button was
    there to press. Those two are for the extension's jobs: an upload through a platform's API is ended by the
    ReelForge that runs it."""
    now = (now or _now()).astimezone(timezone.utc)  # as the rows' times are written
    gone = {"error_code": "timeout",
            "error_message": "The browser stopped reporting before the video was on the page. Nothing was posted."}
    quiet = {"detail": "no word from the browser since the video was on the page"}
    # (state, the time that counts, how long, what it becomes, with what, the extension's jobs only)
    rules = (("queued", "created_at", QUEUED_FOR, "expired", {"detail": "not picked up in time"}, False),
             ("ready", "updated_at", READY_FOR, "unknown", {"detail": "no word since it was ready to post"}, False),
             ("posting", "updated_at", POSTING_FOR, "unknown", {"detail": "no confirmation after Post was pressed"},
              False),
             ("claimed", "updated_at", OPENING_FOR, "failed", gone, True),
             ("opening", "updated_at", OPENING_FOR, "failed", gone, True),
             ("uploading", "updated_at", FILLING_FOR, "unknown", quiet, True),
             ("filling", "updated_at", FILLING_FOR, "unknown", quiet, True))

    def stale(c: sqlite3.Connection) -> list[tuple[dict, str, dict]]:
        found = []
        for state, column, limit, to, fields, extension in rules:
            cut = (now - limit).isoformat(timespec="seconds")
            found += [(dict(row), to, fields) for row in c.execute(
                f"SELECT * FROM post_jobs WHERE state = ? AND {column} <= ?"
                + (" AND via = 'extension'" if extension else ""), (state, cut)).fetchall()]
        return found

    moved = 0
    with db.connect() as c:
        # A look first, which never waits: this runs every half minute in every window, and nearly always finds
        # nothing. Only then the write, with the look taken again inside it.
        if not stale(c):
            return 0
        c.execute("BEGIN IMMEDIATE")
        for old, to, fields in stale(c):
            _write(c, old, to, "app", **fields)
            moved += 1
    if moved:
        db.bump()
    return moved


def reconcile() -> int:
    """After a stats read: a job that may have posted without ReelForge hearing of it (ready, posting or unknown, or
    one of the extension's whose video was on the page) is posted once the read has filed a post under its episode,
    on its platform, that went up after the job was made. Returns how many it settled."""
    found = []
    with db.connect() as c:
        marks, early = ",".join("?" * len(WAITING)), ",".join("?" * len(ON_THE_PAGE))
        for j in c.execute(f"SELECT * FROM post_jobs WHERE superseded = 0 AND episode_id IS NOT NULL AND (state IN "
                           f"({marks}) OR (via = 'extension' AND state IN ({early})))",
                           (*WAITING, *ON_THE_PAGE)).fetchall():
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
        if state in FREE:
            given = {k: v for k, v in given.items() if k == "detail"}  # over: only a word on why can follow
        elif state == "posted":
            given = {k: v for k, v in given.items() if k in ("post_id", "url")}  # up: only its id and link can
        if state in (*FREE, "posted") and all(old[k] == v for k, v in given.items()):
            return new  # nothing to add
        given = {k: v for k, v in given.items() if k in ("step", "progress", "detail") or not old[k]}
    else:
        new.update(state=state, step=None, progress=None, detail=None)
        if old["state"] in ("queued", "claimed") and state in FREE:
            # Nobody picked it up, or the browser that did hadn't opened its tab yet (it says "opening" when it
            # has). So nothing was opened anywhere: its line in the upload log goes, or a post that happens to go
            # up in the next hours would be filed under this video (analytics.auto_link).
            c.execute("DELETE FROM uploads WHERE ts = ? AND platform = ? AND video = ? AND status = ?",
                      (old["created_at"], old["platform"], old["video"], old["via"]))
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
    if state in FREE and old["state"] not in FREE:
        _stands_again(c, new)
    return new


def _stands_again(c: sqlite3.Connection, job: dict) -> None:
    """A job just ended without a post. If it was a post again, the posted or unknown job it set aside stands
    again: as far as ReelForge knows the video is still up there (or still to be asked about), and a plain Post
    must go on being refused. Without this, a Post again that never got anywhere would quietly forget the post
    before it."""
    aside = c.execute(
        """SELECT id, state FROM post_jobs WHERE video_sha256 = ? AND platform = ? AND superseded = 1
           AND state IN ('posted', 'unknown') AND rowid < (SELECT rowid FROM post_jobs WHERE id = ?)
           ORDER BY rowid DESC LIMIT 1""", (job["video_sha256"], job["platform"], job["id"])).fetchone()
    live = c.execute(f"SELECT 1 FROM post_jobs WHERE video_sha256 = ? AND platform = ? AND superseded = 0 "
                     f"AND state IN ({','.join('?' * len(LIVE))})", (job["video_sha256"], job["platform"], *LIVE))
    if aside and not live.fetchone():  # (a newer try that is under way has the say until it ends)
        c.execute("UPDATE post_jobs SET superseded = 0 WHERE id = ?", (aside["id"],))
        _event(c, aside["id"], job["updated_at"], aside["state"], None,
               "stands again: the new try ended without a post")


def _settled(old: dict, new: dict, by: str) -> dict:
    """After the transaction: every window redraws for a change of state, and a post whose id the extension read
    off the page goes into the stats as this episode's, so it shows before the next read (which fills in its
    numbers and never files it anywhere else)."""
    # (Its id may have come before the post did: YouTube shows a video's link while it is still a draft.)
    if new["state"] == "posted" and new["post_id"] and by != "reconcile" and \
            (old["state"] != "posted" or not old["post_id"]):
        db.add_post(new["platform"], new["post_id"], new["episode_id"], headline(json.loads(new["text"])),
                    new["url"])
    if (new["state"], new["post_id"], new["url"]) != (old["state"], old["post_id"], old["url"]):
        db.bump()
    return new
