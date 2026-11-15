"""Posting with the browser extension, as the window shows and starts it. The ledger is dashboard/posting.py; the
local server the extension talks to, its pages and pairing are dashboard/extension_api.py, which this window starts
and stops (serve_extension, stop_serving).

A mixin of the window's service (dashboard/service.py). Two views:

  posting     for the video picked on the Publish tab: where its review stands, whether the extension is there,
              whether Post can be pressed (and why not), and a row per connected platform with its state, a line
              of words, and what can be done about it (retry, cancel, mark_posted, not_posted, open_post, post_again)
  extension   the extension itself: off (never connected), waiting (connected, not heard from lately), on, or old
              (it and ReelForge are different versions of the link between them), the browsers it is connected in,
              and where its folder is

and the calls behind the buttons: post_info (what the question before a post shows), post_with_extension (your
yes), post_action (a row's button), pair_extension (connect a browser), unpair_extension (disconnect one),
extension_folder (show the folder to load in the browser) and dismiss_extension_hint. Every word of the two views
is written here, so the window only draws them.

Nothing here posts by itself. A post starts with post_with_extension, which only your press in the window calls, and
ends at the platform's own Post button unless you switched "stop before posting" off for it.
"""

from __future__ import annotations

import sqlite3
import sys
import threading
import traceback
from datetime import timedelta
from pathlib import Path

from studio.bridge import rpc

from . import analytics, db, desktop, extension_api, posting, videos
from .analytics import fmt_ago, fmt_num
from .platforms import PublishItem
from .posting import STEPS, TESTED, failure, standing
from .view import BAD, GOOD, MUTED, TYPICAL, Text

FAINT = "#5c6370"
AMBER = TYPICAL  # in progress, and anything waiting for you
ON_WITHIN = timedelta(minutes=3)   # the extension counts as there while it has said so this recently
QUIET_AFTER = timedelta(minutes=3)  # a job under way with no news for this long says so
HINT = "extension_hint_dismissed"   # meta: you closed the "set up the extension" hint on the Publish tab
# A job's state in a few words. An API upload has no extension to wait for.
WORDS = {
    "queued": "waiting for the extension to pick it up", "claimed": "the extension has it",
    "opening": "opening the upload page", "uploading": "uploading", "filling": "filling in the text",
    "ready": "ready: check it in the browser, then press Post there", "posting": "pressing Post",
    "posted": "posted", "unknown": "did it post? ReelForge lost track: check your profile there",
    "failed": "failed", "cancelled": "cancelled", "expired": "the extension didn't pick it up",
}
API_WORDS = {"queued": "about to start", "expired": "it never started"}
# The same in a word or two, for the POSTED line under the form.
BRIEF = {"queued": "queued", "claimed": "starting", "opening": "opening", "uploading": "uploading",
         "filling": "filling in", "ready": "ready for you to press Post", "posting": "posting",
         "unknown": "not sure it posted", "failed": "failed"}
BUSY = ("queued", "claimed", "opening", "uploading", "filling", "posting")  # ReelForge or the extension is at work
ACTIONS = {
    "none": (), "spotted": ("open_post", "post_again"), "posted": ("open_post", "post_again"),
    "queued": ("cancel",), "claimed": ("cancel",), "opening": ("cancel",), "uploading": ("cancel",),
    "filling": ("cancel",), "ready": ("mark_posted", "cancel"), "posting": ("mark_posted", "not_posted"),
    "unknown": ("mark_posted", "not_posted"), "failed": ("retry",), "cancelled": ("retry",), "expired": ("retry",),
}
# What each platform's text is called in its own boxes, for the question before a post.
FIELDS = {"title": "Title", "description": "Description", "caption": "Caption", "text": "Post"}


def numbers(post: dict) -> str:
    """A post's views and age. One the extension reported has no numbers until the next stats read brings them."""
    if post["views"] is None:
        return f"{fmt_ago(post['published_at'])}, its numbers come with the next refresh"
    return f"{fmt_num(post['views'])} views, {fmt_ago(post['published_at'])}"


def flag_style(color: str, state: str) -> str:
    """A platform's two letters on the video list: its colour once it's up there, amber while a post is under way
    or waiting for you, red when the last try failed, grey otherwise."""
    if state in ("posted", "spotted"):
        return f"bold {color}"
    if state in posting.LIVE:
        return f"bold {AMBER}"
    return f"bold {BAD}" if state == "failed" else "#3a4049"


def state_line(job: dict | None, post: dict | None, name: str) -> Text:
    """A platform's row on the posting view: its state in words, with what the extension last said."""
    state = standing(job, post)
    if state == "none":
        return Text("○ not posted", FAINT)
    if state == "spotted" or (state == "posted" and post):
        return Text("✓ posted", f"bold {GOOD}").append("  " + numbers(post), MUTED)
    words = {**WORDS, **(API_WORDS if job["via"] == "api" else {})}[state]
    when = fmt_ago(job["updated_at"])
    if state == "posted":
        return Text("✓ posted", f"bold {GOOD}").append(f"  {when}, not in your stats yet", MUTED)
    if state == "failed":
        why, step = failure(job, name), STEPS.get(job["step"] or "", "")
        out = Text("× failed", f"bold {BAD}")
        return out.append((f" at {step}" if step else "") + (f": {why}" if why else "") + f"  {when}", MUTED)
    if state in ("cancelled", "expired"):
        return Text(f"○ {words}" + (f" ({job['detail']})" if job["detail"] else "") + f"  {when}", MUTED)
    if state == "unknown":
        return Text(f"? {words}", f"bold {AMBER}")
    out = Text(f"● {words}", f"bold {AMBER}" if state == "ready" else AMBER)
    step = STEPS.get(job["step"] or "", "")
    if step and step != words:
        out.append(f": {step}")
    if job["progress"] is not None and state == "uploading":
        out.append(f" {max(0.0, min(float(job['progress']), 1.0)):.0%}")
    if job["detail"]:
        out.append(f"  {job['detail']}", MUTED)
    if state in BUSY and analytics.now() - db.parse(job["updated_at"]) >= QUIET_AFTER:
        out.append(f"  no news for {when[:-4]}", MUTED)  # "4m ago" without the "ago"
    return out


def upload_note(job: dict, name: str) -> tuple[Text, Text]:
    """(mark, note) for RECENT UPLOADS: a post started with the extension (or an API), by where its job stands."""
    state, link = job["state"], job["url"] or ""
    words = {**WORDS, **(API_WORDS if job["via"] == "api" else {})}[state]
    how = "with the extension" if job["via"] == "extension" else "with its API"
    if state == "posted":
        return Text("✓", GOOD), Text(f"posted {how} · {link or 'not in your stats yet'}")
    if state == "failed":
        why = failure(job, name)
        return Text("×", BAD), Text("failed" + (f": {why}" if why else ""))
    if state in posting.FREE:
        return Text("○", MUTED), Text(words + (f" ({job['detail']})" if job["detail"] else ""), MUTED)
    return Text("?" if state == "unknown" else "●", AMBER), Text(words, AMBER)


class PostingTab:
    """Posting's half of the window's service."""

    jobs_seen = 0                       # the ledger as this window last drew it (posting.signature)
    extension_seen: float | None = None  # the connections file's time, as last read
    extension_server: extension_api.Server | None = None  # the local server, while this window listens
    extension_port: int | None = None    # ... and its port
    extension_ports: tuple[int, ...] | None = None  # the ports to try instead of extension_api's (the tests')
    extension_problem = ""               # why this window isn't listening for the extension, when it isn't
    picked_review: tuple[Path, dict] | None = None  # the picked video's approval, worked out once per look
    form_text = ("", "")                 # the Publish form's caption and hashtags as last typed

    # --- what the window shows ------------------------------------------------------------------

    def review_of(self, video: Path, fresh: bool = False) -> dict:
        """The picked video's approval (videos.approval). It reads files, and with review receipts it hashes the
        video, so it's kept between looks: read again (fresh) when the Publish tab is opened, every half minute
        while it is open, and when a post is asked for."""
        if self.demo:
            return {"state": "demo", "line": "This is the demo: nothing is posted from it."}
        if fresh or self.picked_review is None or self.picked_review[0] != video:
            self.picked_review = (video, videos.approval(video, self.cfg))
        return self.picked_review[1]

    def spotted_posts(self, video: Path) -> dict[str, dict]:
        """Platform -> the post a stats read has filed under this video's episode (its first there)."""
        episode = self._safe(lambda: db.episode_for_video(video.name), None)
        by: dict[str, dict] = {}
        if episode:
            for q in sorted((q for q in self.posts if q["episode_id"] == episode["id"]),
                            key=lambda q: q["published_at"]):
                by.setdefault(q["platform"], q)
        return by

    def extension_status(self) -> dict:
        """The extension as ReelForge knows it: {"state", "line", "installs"}. off: no browser is connected. on: one
        said hello in the last few minutes. waiting: connected, but quiet (the browser is closed, or asleep: it
        wakes when a post starts). old: it and this ReelForge speak different versions of the link."""
        if self.demo:
            return {"state": "off", "line": Text("○ The demo doesn't use the browser extension", MUTED), "installs": []}
        now, installs = analytics.now(), []
        for p in extension_api.paired(extension_api.read_store()):
            try:
                seen = db.parse(p["last_seen"])
            except (KeyError, TypeError, ValueError):
                seen = None
            theirs = p.get("protocol", posting.PROTOCOL)
            installs.append({"install": str(p.get("install", "")), "name": str(p.get("name") or "A browser"),
                             "version": str(p.get("version", "")),
                             "seen": fmt_ago(p.get("last_seen")) if seen else "never",
                             "on": bool(seen and now - seen <= ON_WITHIN),
                             "old": theirs != posting.PROTOCOL, "at": seen, "protocol": theirs,
                             # What that browser was last seen signed in to. It is the extension's word about its
                             # own browser, and says nothing about the accounts connected in ReelForge.
                             "accounts": p.get("accounts") if isinstance(p.get("accounts"), dict) else {}})
        installs.sort(key=lambda i: (i["at"] is not None, i["at"]), reverse=True)  # the one heard from last, first
        theirs = installs[0]["protocol"] if installs else None
        for i in installs:
            del i["at"], i["protocol"]
        if not installs:
            return {"state": "off", "line": Text("○ Browser extension not connected", MUTED), "installs": []}
        first = installs[0]
        who = first["name"] + (f" · v{first['version']}" if first["version"] else "")
        if all(i["old"] for i in installs):  # (one browser that is behind doesn't stop another that isn't)
            why = extension_api.update_which(theirs)
            line = Text("● Browser extension needs an update", f"bold {BAD}").append("  " + why, MUTED)
            return {"state": "old", "line": line, "installs": installs, "why": why}
        if first["on"]:
            line = Text("● Browser extension connected", f"bold {GOOD}")
            return {"state": "on", "line": line.append(f"  {who} · seen {first['seen']}", MUTED),
                    "installs": installs}
        line = Text("● Browser extension connected", AMBER)
        line.append(f"  {who} · last seen {first['seen']} (it wakes up when you post)", MUTED)
        return {"state": "waiting", "line": line, "installs": installs}

    def update_extension(self) -> None:
        """The extension's own view: its state, the browsers it's connected in, the port ReelForge listens on for
        it, where its folder is, and whether you closed the hint to set it up."""
        status = self.extension_status()
        folder = extension_api.folder()
        lines = []
        if status["state"] == "off" and not self.demo:
            lines.append(Text("Posting with the extension fills in each platform's upload page for you: the video, "
                              "the caption, the hashtags. You check it and press Post.", MUTED))
        if self.extension_problem:
            lines.append(Text(self.extension_problem, AMBER))
        if not self.demo:
            lines.append(Text(TESTED, MUTED))
        self.emit("extension", {
            "state": status["state"], "line": status["line"].json(), "lines": [t.json() for t in lines],
            "installs": status["installs"], "port": self.extension_port, "protocol": posting.PROTOCOL,
            "folder": str(folder), "folder_there": folder.is_dir(),
            "hint_dismissed": bool(self._safe(lambda: db.get_meta(HINT, False), False)),
        })

    def follow_extension(self) -> None:
        """Every second (keep_in_step): a browser that connected, said hello or was disconnected since the last
        look, whichever window's server it spoke to."""
        try:
            seen = extension_api.store_path().stat().st_mtime
        except OSError:
            seen = None
        if seen != self.extension_seen:
            self.extension_seen = seen
            self.update_extension()
            self.update_posting()

    def update_posting(self, fresh: bool = False) -> None:
        """The picked video's posting view. fresh: read its approval again."""
        self.jobs_seen = self._safe(posting.signature, self.jobs_seen)  # first: a change after it shows next
        video = self.selected_file
        extension = self.extension_status()
        view = {"video": None, "review": {"state": "none", "line": []},
                "extension": {"state": extension["state"], "line": extension["line"].json()},
                "can_post": False, "why": "Pick a video on the left first.", "rows": []}
        if video is None:
            self.emit("posting", view)
            return
        review = self.review_of(video, fresh)
        jobs = self._safe(lambda: posting.for_files([video]).get(video, {}), {})
        posts = self.spotted_posts(video)
        rows = []
        for p in self.platforms.values():
            job, post = jobs.get(p.key), posts.get(p.key)
            state = standing(job, post)
            url = (job["url"] if job and state == job["state"] else None) or (post["url"] if post else None)
            rows.append({"key": p.key, "name": p.name, "color": p.color, "state": state,
                         "line": state_line(job, post, p.name).json(), "busy": state in BUSY, "url": url,
                         "job": job["id"] if job and state == job["state"] else None,
                         "actions": [a for a in ACTIONS[state] if a != "open_post" or url]})
        style = {"approved": GOOD, "none": MUTED, "demo": MUTED}.get(review["state"], f"bold {AMBER}")
        mark = {"approved": "✓ ", "none": "", "demo": ""}.get(review["state"], "! ")
        why = self.cant_post(video, review, extension, rows)
        view.update(video={"path": str(video), "name": video.name},
                    review={"state": review["state"], "line": Text(mark + review["line"], style).json()},
                    can_post=not why, why=why, rows=rows)
        self.emit("posting", view)

    def cant_post(self, video: Path, review: dict, extension: dict, rows: list[dict]) -> str:
        """Why Post with the extension can't be pressed for the picked video now ("": it can)."""
        if self.demo:
            return "The demo doesn't post anything."
        if not video.exists():
            return f"{video.name} isn't there any more."
        if not rows:
            return "Connect an account on the Accounts tab first."
        if review["state"] not in videos.POSTABLE:
            return review["line"]
        if extension["state"] in ("off", "old"):
            return self.extension_needed(extension)
        if any(r["state"] in ("none", *posting.FREE) for r in rows):
            return ""
        if any(r["state"] in posting.IN_FLIGHT for r in rows):
            return "A post is already under way."
        if any(r["state"] == "unknown" for r in rows):
            return "Say first whether the last try posted (the rows marked ?)."
        return "Already posted everywhere. Post again on a platform's row posts it there once more."

    def extension_needed(self, extension: dict) -> str:
        """What to do before the extension can post ("" when it can)."""
        if extension["state"] == "off":
            return "Connect the browser extension first."
        if extension["state"] == "old":
            return extension.get("why") or "Update the browser extension first."
        return ""

    # --- keeping it current ------------------------------------------------------------------------

    def jobs_changed(self, everywhere: bool = False) -> None:
        """A job changed: in this window, in another one, or through the local server (which calls this on the
        loop for a quicker redraw than the once-a-second look). everywhere: this window made the change itself, so
        the list, POSTED and RECENT UPLOADS follow at once instead of at the reload a change of state brings."""
        if not everywhere:
            self.update_posting()
            return
        self.episodes = self._safe(db.episodes, self.episodes)
        self.update_uploads()
        self.update_posted()  # the posting view goes with it
        if self.tab == "publish":
            self.scan_files()

    def mind_the_jobs(self) -> None:
        """At start and every half minute: what the clock decides (a job nobody picked up expires, one that went
        silent after it was ready becomes unknown: posting.expire_stale), a post a stats read found while this
        window wasn't looking, the extension's "seen 2m ago", and the picked video's review as it stands now."""
        if not self.demo:
            self._safe(posting.expire_stale, 0)
            self._safe(posting.reconcile, 0)
        self.update_extension()
        self.update_posting(fresh=self.tab == "publish")

    def settle_jobs(self) -> None:
        """After a stats read, here or anywhere: a post it found settles the job that was waiting to hear."""
        if not self.demo and self._safe(posting.reconcile, 0):
            self.update_posting()

    # --- what the buttons call ---------------------------------------------------------------------

    @rpc
    def post_info(self, caption: str, tags: str) -> dict | None:
        """What the question before a post shows, for the picked video and the form as it is: per platform the
        exact text, what stops it if anything does, and whether Post can be pressed for you there."""
        if not self._picked():
            return None
        video = self.selected_file
        try:
            look = posting.preview(video, list(self.platforms), caption, tags, self.cfg, demo=self.demo)
        except sqlite3.OperationalError as e:
            self.say_busy(e)
            return None
        self.picked_review = (video, look["review"])  # read afresh just now: the view follows it
        extension = self.extension_status()
        platforms = []
        for p in self.platforms.values():
            block = look["blocked"].get(p.key)
            platforms.append({
                "key": p.key, "name": p.name, "color": p.color, "handle": p.handle,
                "text": look["texts"][p.key],
                "fields": [{"label": FIELDS[k], "text": v or "—"} for k, v in look["texts"][p.key].items()],
                "blocked": block, "review": look["review"]["state"],
                # Posting again is yours to ask for, by name: only what is already up (or may be) can be forced.
                "again": bool(block and block["reason"] in ("posted", "spotted", "unknown")),
                "press": posting.press_unlocked(p.key),
            })
        if self.demo:
            why = "The demo doesn't post anything."
        elif look["review"]["state"] not in videos.POSTABLE:
            why = look["review"]["line"]
        elif not platforms:
            why = "Connect an account on the Accounts tab first."
        elif all(pl["blocked"] and not pl["again"] for pl in platforms):
            why = platforms[0]["blocked"]["message"]
        else:
            why = self.extension_needed(extension)
        self.update_posting()
        return {"video": {"path": str(video), "name": video.name,
                          "size": f"{video.stat().st_size / 1024 / 1024:.1f} MB"},
                "review": look["review"], "extension": {"state": extension["state"], "line": extension["line"].json()},
                "platforms": platforms, "can_post": not why, "why": why, "stop_before": True, "note": TESTED}

    @rpc
    def post_with_extension(self, platforms: list[str], caption: str, tags: str, stop_before: bool = True,
                            force: list[str] | None = None) -> dict | None:
        """Your yes to the question: a job for each ticked platform (posting.create_batch), then the local page that
        hands them to the extension. stop_before: the extension stops at the platform's own Post button (off only
        where post_info said press is available). force: the platforms to post to again although the video is up
        there. Returns {"batch", "jobs": [{"id", "platform"}], "blocked": [...], "opened"}; None when there was
        nothing to start from."""
        if self.demo:
            self.notify("Demo mode: nothing is posted.", timeout=4)
            return None
        if not self._picked():
            return None
        needed = self.extension_needed(self.extension_status())
        ticked = [k for k in platforms or [] if k in self.platforms]
        if needed or not ticked:
            self.notify(needed or "Tick a platform to post to first.", severity="warning", timeout=8)
            return None
        try:
            made = posting.create_batch(self.selected_file, ticked, caption, tags, self.cfg,
                                        press=not stop_before, force=tuple(force or ()))
        except sqlite3.OperationalError as e:
            self.say_busy(e)
            return None
        return self._hand_over(made)

    def _hand_over(self, made: dict) -> dict:
        """After jobs were made (or refused): say what was refused, open the local page for the rest."""
        refused: dict[str, list[str]] = {}  # one pop-up for each reason, naming the platforms it stops
        for b in made["blocked"]:
            p = self.every.get(b["platform"])
            refused.setdefault(b["message"], []).append(p.name if p else b["platform"])
        for message, names in refused.items():
            self.notify(message, title=f"{', '.join(names)}: not started", severity="warning", timeout=12)
        opened = False
        if made["jobs"]:
            opened = bool(self.open_post_page(made["batch"]))
            if not opened:
                # A queued job nobody can pick up would stand in the way for ten minutes: call it off now.
                for j in made["jobs"]:
                    try:
                        posting.cancel(j["id"], "the posting page didn't open", by="app")
                    except (posting.Refused, sqlite3.Error):
                        pass  # the clock expires it
                why = "ReelForge couldn't open your browser" if self.extension_server is not None else \
                    "ReelForge isn't listening for the extension in this window"
                self.notify(f"{why}, so it couldn't open the posting page and nothing was started. The buttons "
                            "under UPLOAD still open each platform's page for you to post by hand.",
                            title="Nothing was posted", severity="warning", timeout=15)
        self.picked_review = None
        self.jobs_changed(everywhere=True)
        return {"batch": made["batch"] if opened else None,
                "jobs": [{"id": j["id"], "platform": j["platform"]} for j in made["jobs"]] if opened else [],
                "blocked": made["blocked"], "opened": opened}

    def say_busy(self, e: sqlite3.OperationalError) -> None:
        """The database was locked by another ReelForge just then (as db_safe says it for the rest of the window)."""
        self.notify(f"The stats database is busy ({e}). Try again in a moment.", severity="warning", timeout=8)

    def open_post_page(self, batch: str) -> bool:
        """Open the local page that hands a batch to the extension and shows where each post stands, in your
        browser. False when this window isn't listening (there is no page then) or no browser opened."""
        server = self.extension_server
        return bool(server and desktop.open_url(server.post_url(batch)))

    # --- the local server ---------------------------------------------------------------------------

    def serve_extension(self) -> None:
        """When the window starts: listen for the extension (dashboard/extension_api.py). Never in the demo. When
        it can't (every port taken, a folder that can't be written), that is a line on the extension view and
        nothing more: ReelForge works as it does without the extension."""
        if self.demo or self.extension_server is not None:
            return
        try:
            server = extension_api.Server(WindowApp(self), self.extension_ports)
            if server.start():
                self.extension_server, self.extension_port, self.extension_problem = server, server.port, ""
            elif server.ports:
                self.extension_problem = (
                    "ReelForge isn't listening for the extension in this window: every port from "
                    f"{server.ports[0]} to {server.ports[-1]} on this computer is taken, or its connections file "
                    "can't be written. Close other ReelForge windows, then open this one again.")
            else:
                self.extension_problem = "ReelForge isn't listening for the extension here (it is switched off)."
        except Exception as e:  # whatever it is, the window opens
            traceback.print_exc()
            self.extension_problem = f"ReelForge isn't listening for the extension in this window ({e})."

    def stop_serving(self) -> None:
        server, self.extension_server, self.extension_port = self.extension_server, None, None
        if server is not None:
            server.stop()

    def heard_from_extension(self) -> None:
        """On the loop, right after the local server changed a job or a connection: the views follow now instead
        of at the next second's look."""
        self.follow_extension()
        if self._safe(posting.signature, self.jobs_seen) != self.jobs_seen:
            self.jobs_changed()

    def post_by_hand(self, job: dict, name: str) -> dict:
        """The local page's Post by hand, for one job: this window's own flow for a platform's button (the text
        copied, the file shown, the upload page opened and noted), with the text the job was made with."""
        p = self.every.get(job["platform"])

        def record(item: PublishItem) -> None:
            if p is not None:
                self.record_manual([p], item)

        return extension_api.by_hand(job, name, self.copy_text, record)

    @rpc
    def pair_extension(self) -> bool:
        """Connect a browser: open the local page the extension pairs on. With no extension in that browser yet,
        the page says how to add it. True when a browser opened."""
        if self.demo:
            self.notify("The demo doesn't use the browser extension.", timeout=4)
            return False
        server = self.extension_server
        if server is None:
            self.notify(self.extension_problem or "ReelForge isn't listening for the extension in this window.",
                        severity="warning", timeout=12)
            return False
        url = server.pair_url()
        if desktop.open_url(url):
            self.notify("A ReelForge page is opening in your browser: the extension connects there. No extension "
                        "yet? The page says how to add it.", title="Connecting your browser", timeout=12)
            return True
        self.copy_text(url)
        self.notify("ReelForge couldn't open your browser. The page's address is copied: paste it into the browser "
                    "that has the extension. It works for ten minutes.", title="Connecting your browser",
                    severity="warning", timeout=20)
        return False

    @rpc
    def unpair_extension(self, install: str) -> None:
        """Disconnect a browser (by the install id the extension view lists it under): its token stops working."""
        if self.demo:
            self.notify("The demo doesn't use the browser extension.", timeout=4)
            return
        try:
            name = extension_api.unpair(str(install))
        except OSError as e:
            self.notify(f"ReelForge couldn't change its connections file ({e}).", severity="warning", timeout=10)
            return
        self.notify(f"{name} is disconnected. Connect it again any time." if name else
                    "That browser wasn't connected any more.", timeout=8)
        self.follow_extension()

    @rpc
    def extension_folder(self) -> None:
        """Show the extension's folder in the file manager, with its path on the clipboard: it is the folder to
        pick after Load unpacked on your browser's extensions page."""
        if self.demo:
            self.notify("The demo doesn't use the browser extension.", timeout=4)
            return
        folder = extension_api.folder()
        if not folder.is_dir():
            self.notify(f"This ReelForge has no extension folder yet ({folder}). Update ReelForge on the Updates "
                        "tab to get it.", severity="warning", timeout=12)
            return
        desktop.reveal(folder)
        self.copy_text(str(folder))
        self.notify(f"It is showing in {desktop.FILES_WINDOW}, and its path is copied. On your browser's "
                    "extensions page, switch on Developer mode, press Load unpacked and pick it.",
                    title="The extension's folder", timeout=15)

    @rpc
    def post_action(self, job: str, action: str) -> None:
        """A button on a platform's row. job: the row's job id, or its platform's key when it has none (a post the
        stats read found). cancel calls a job off; mark_posted and not_posted are your word on one ReelForge lost
        track of; retry starts a failed one again with the same text; open_post opens the post; post_again starts
        a new post of the picked video there, with the form's text as it is, stopping before Post."""
        row = self._safe(lambda: posting.get(job), None) if job not in self.every else None
        platform = row["platform"] if row else job
        if platform not in self.every or action not in {a for acts in ACTIONS.values() for a in acts}:
            return
        name = self.every[platform].name
        if action == "open_post":
            post = self.spotted_posts(self.selected_file).get(platform) if self.selected_file else None
            url = (row["url"] if row else None) or (post["url"] if post else None)
            if not url or not desktop.open_url(url):
                self.notify(f"ReelForge has no link to open for {name} yet." if not url else
                            f"ReelForge couldn't open your browser. The post is at {url}", severity="warning")
            return
        if self.demo:
            self.notify("Demo mode: nothing is posted.", timeout=4)
            return
        needed = self.extension_needed(self.extension_status()) if action in ("post_again", "retry") else ""
        if needed:
            self.notify(needed, severity="warning", timeout=8)
            return
        try:
            if action == "post_again":
                if self._picked():
                    self._hand_over(posting.create_batch(self.selected_file, [platform], *self.form_text, self.cfg,
                                                       force=(platform,)))
                return
            if row is None:
                return
            if action == "retry":
                self._hand_over(posting.retry(row["id"], self.cfg))
                return
            if action == "cancel":
                posting.cancel(row["id"], "you cancelled it")
            else:
                posting.resolve(row["id"], action == "mark_posted")
        except posting.Refused as e:
            self.notify(e.message, title=name, severity="warning", timeout=8)
        except sqlite3.OperationalError as e:
            self.say_busy(e)
        self.jobs_changed(everywhere=True)

    @rpc
    def dismiss_extension_hint(self) -> None:
        """Close the Publish tab's hint to set the extension up, for good (this channel's)."""
        self._safe(lambda: db.set_meta(HINT, True), None)
        self.update_extension()


class WindowApp(extension_api.App):
    """This window, as the local server sees it. The server's requests run on threads of their own: what they read
    here is read in one step, and anything that touches the window runs on the window's loop."""

    wait = 10.0  # seconds a request waits for the window's loop to do something for it

    def __init__(self, window) -> None:
        self.window = window

    @property
    def cfg(self) -> dict:
        return self.window.cfg

    @property
    def demo(self) -> bool:
        return self.window.demo

    def profile(self) -> dict:
        p = self.window.profile
        return {"key": p.key if p else ".", "name": p.name if p else ""}

    def platforms(self) -> list[dict]:
        on = set(self.window.platforms)
        return [{"key": p.key, "name": p.name, "handle": p.handle, "connected": p.key in on}
                for p in list(self.window.every.values())]

    def changed(self) -> None:
        self.window.loop.call_soon(self.window.heard_from_extension)

    def by_hand(self, job: dict, name: str) -> dict | None:
        """The by-hand flow, run on the window's loop while this thread waits for it. None: the window didn't get to
        it in time (then it never does it, so a page that is told "try again" isn't answered twice), or it failed
        there, where the window says so."""
        done, late = threading.Event(), threading.Event()
        answer: list[dict] = []

        def run() -> None:
            try:
                if not late.is_set():
                    answer.append(self.window.post_by_hand(job, name))
            finally:
                done.set()

        self.window.loop.call_soon(run)
        if not done.wait(self.wait):
            late.set()
            print("extension: the window didn't get to a by-hand post in time", file=sys.stderr)
        return answer[0] if answer else None
