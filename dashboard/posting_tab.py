"""Posting with the browser extension, as the window shows and starts it. The ledger is dashboard/posting.py; the
local server the extension talks to, its pages and pairing are dashboard/extension_api.py, which this window starts
and stops (serve_extension, stop_serving).

A mixin of the window's service (dashboard/service.py). Two views:

  posting     for the video picked on the Publish tab: where its review stands, whether the extension is there,
              whether Post can be pressed (and why not), and a row per connected platform with its state, a line
              of words, and what can be done about it (retry, cancel, mark_posted, not_posted, open_post, post_again,
              each with the words for its button)
  extension   the extension itself: off (never connected), waiting (connected, not heard from lately), on, or old
              (it and ReelForge are different versions of the link between them), the browsers it is connected in
              and what each was last seen signed in to, where its folder is, the steps to set it up, and why this
              window can't reach it when it can't

and the calls behind the buttons: post_info (what the question before a post shows), post_with_extension (your
yes), post_action (a row's button), pair_extension (connect a browser), unpair_extension (disconnect one),
extension_folder (show the folder to load in the browser) and dismiss_extension_hint. Every word of the two views
is written here, so the window only draws them.

Nothing here posts by itself. A post starts with post_with_extension, which only your press in the window calls, and
ends at the platform's own Post button. The extension presses that button only where all of this holds: you switched
"stop before posting" off for the post, Press Post for me is on for the platform in the extension's own settings, and
you said yes to it for that post in the browser, on ReelForge's own page, which opens first. Never on X.
"""

from __future__ import annotations

import json
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
from .view import BAD, GOOD, MUTED, TYPICAL, Text, parts

FAINT = "#5c6370"
AMBER = TYPICAL  # in progress, and anything waiting for you
# The extension counts as there while it has said so this recently. With nothing to post it says hello every five
# minutes (extension/worker/jobs.js), so this is a little over two of those: one that got lost doesn't turn it amber.
ON_WITHIN = timedelta(minutes=11)
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
API_WORDS = {"queued": "about to start", "expired": "it never started",
             "posting": "the last piece is on its way: waiting for YouTube to say it's up",
             "unknown": "did it post? ReelForge lost track: check YouTube Studio before trying again"}
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
# A job the extension saw you press Post for, in the tab, before it had any word from the platform: a press can't
# be cancelled (posting.advance refuses it), so its row asks what a row that is posting asks.
PRESSED = ("mark_posted", "not_posted")
PRESSABLE = ("uploading", "filling", "ready")  # the states the platform's own button can be pressed in by you
# What each platform's text is called in its own boxes, for the question before a post.
FIELDS = {"title": "Title", "description": "Description", "caption": "Caption", "text": "Post"}
# What the button for each of a row's actions says. Short: a row that is posted keeps its two beside its numbers.
# The last three are for YouTube's own API (youtube_api_tab.py): post_api and connect_api are actions on the YouTube
# row, and post_api_again is the window's own button on a row whose "api" says a post there would be a post again.
# An ending "…" means the button asks before anything starts.
LABELS = {"retry": "Try again", "cancel": "Cancel", "mark_posted": "It's posted", "not_posted": "It didn't post",
          "open_post": "Open", "post_again": "Post again…", "post_api": "Post with API…",
          "connect_api": "Connect API", "post_api_again": "Again with API…"}
# Setting the extension up, once (docs/EXTENSION.md says the same at more length).
SETUP = (
    "Open your browser's extensions page: type chrome://extensions in the address bar. In Edge it is "
    "edge://extensions, in Brave brave://extensions.",
    "Switch on Developer mode, at the top right of that page.",
    "Press Load unpacked and pick the extension folder inside your ReelForge folder. Show the extension folder "
    "shows it and copies its path.",
    "Press Connect a browser. A ReelForge page opens in your browser and the extension connects there by itself.",
)
# Under the connected browsers. The extension works in your everyday browser, and ReelForge's own log-ins (for
# reading stats) are in its own: neither says anything about the other.
SIGNED_NOTE = ("That is what the extension saw in each browser. It is apart from the accounts connected on the Accounts "
               "tab, which ReelForge reads your stats from.")
# What happens after your yes to the question before a post: as it is asked, and with "stop before posting" off.
# That switch alone presses nothing (docs/EXTENSION.md, "Two ways to post"): the extension also wants Press Post for
# me switched on in its own settings, and your click for this post on ReelForge's own page (dashboard/web/post.html),
# which opens in your browser before any upload page does. Said as that page: "the page that opens" read as the
# platform's upload page. HOW_PRESS is about the press alone, and no longer than three rows in a window 80 columns
# wide: at six, the question had no row left for its platforms in a window 24 rows tall, and drew them over each other.
HOW = ("ReelForge opens each upload page in your browser, attaches the video and fills in the text. You check it "
       "and press Post.")
HOW_PRESS = ("The extension presses Post only where Press Post for me is on in its settings, and after you click "
             "Yes, press Post for me on ReelForge's own page, which opens first. Never on X. Otherwise you press "
             "Post.")


def numbers(post: dict, hidden: bool = False) -> str:
    """A post's views and age. One the extension reported has no numbers until the next stats read brings them.
    hidden: it went up through YouTube's API as private or unlisted, and a stats read only sees what everyone can."""
    if post["views"] is None:
        return f"{fmt_ago(post['published_at'])}, its numbers come " + (
            "once everyone can see it" if hidden else "with the next refresh")
    return f"{fmt_num(post['views'])} views, {fmt_ago(post['published_at'])}"


def signed_in(accounts: dict, names: dict[str, str]) -> str:
    """What a browser was last seen signed in to, in a line: the extension's word about its own browser. It says
    nothing about the accounts connected in ReelForge, which read stats through ReelForge's own browser."""
    said = []
    for key, name in names.items():
        seen = accounts.get(key)
        if not isinstance(seen, dict) or seen.get("signed_in") is None:
            continue  # the extension hasn't looked there yet
        handle = seen.get("handle")
        said.append(f"{name} {handle if isinstance(handle, str) and handle else 'yes'}" if seen["signed_in"] else
                    f"{name} no")
    return "signed in there: " + " · ".join(said) if said else \
        "It hasn't said yet which accounts that browser is signed in to: it looks when you post."


def flag_style(color: str, state: str) -> str:
    """A platform's two letters on the video list: its colour once it's up there, amber while a post is under way
    or waiting for you, red when the last try failed, grey otherwise."""
    if state in ("posted", "spotted"):
        return f"bold {color}"
    if state in posting.LIVE:
        return f"bold {AMBER}"
    return f"bold {BAD}" if state == "failed" else "#3a4049"


def state_line(job: dict | None, post: dict | None, name: str, opened: str | None = None,
               pressed: bool = False) -> Text:
    """A platform's row on the posting view: its state in words, with what the extension last said. opened: when
    its upload page was last opened by hand for this video, which is all ReelForge knows of a post made that way
    until a stats read finds it. pressed: the extension saw you press the platform's own Post button, and is
    waiting for the platform's word."""
    state = standing(job, post)
    if pressed and job and state == job["state"] and state in PRESSABLE:
        return Text(f"● you pressed Post there: waiting for {name} to say it's up", f"bold {AMBER}")
    if state == "none":
        out = Text("○ not posted", FAINT)
        return out.append(f"  upload page opened {fmt_ago(opened)}, not spotted yet", MUTED) if opened else out
    # (A post that went up through YouTube's API as private or unlisted: a stats read only sees what everyone can.)
    hidden = bool(job and state == "posted" and job["via"] == "api"
                  and json.loads(job["options"] or "{}").get("privacy") != "public")
    if state == "spotted" or (state == "posted" and post):
        return Text("✓ posted", f"bold {GOOD}").append("  " + numbers(post, hidden), MUTED)
    words = {**WORDS, **(API_WORDS if job["via"] == "api" else {})}[state]
    when = fmt_ago(job["updated_at"])
    if state == "posted":
        cut = " as an earlier cut" if job.get("earlier_cut") else ""  # (the file was rendered again since)
        stats = "its numbers come once everyone can see it" if hidden else "not in your stats yet"
        return Text("✓ posted", f"bold {GOOD}").append(f"  {when}{cut}, {stats}", MUTED)
    if state == "failed":
        why, step = failure(job, name), STEPS.get(job["step"] or "", "")
        out = Text("× failed", f"bold {BAD}")
        return out.append((f" at {step}" if step else "") + (f": {why}" if why else "") + f"  {when}", MUTED)
    if state in ("cancelled", "expired"):
        if state == "expired" and job["detail"] == "not picked up in time":
            # (in the row's own words: in brackets it said the same thing twice, and the wrap ended a row at "(")
            return Text(f"○ {words} in time  {when}", MUTED)
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
        while it is open, and when a post is asked for. Even then the video is only read in full again when one of
        the episode's files has changed (videos.approval_shown); what a post goes by is worked out anew (post_info
        and posting.create_batch)."""
        if self.demo:
            return {"state": "demo", "line": "This is the demo: nothing is posted from it."}
        if fresh or self.picked_review is None or self.picked_review[0] != video:
            self.picked_review = (video, videos.approval_shown(video, self.cfg))
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
                seen = seen if seen.tzinfo else None  # written in by hand without its zone: as good as never
            except (KeyError, TypeError, ValueError):
                seen = None
            theirs = p.get("protocol", posting.PROTOCOL)
            installs.append({"install": str(p.get("install", "")), "name": str(p.get("name") or "A browser"),
                             "version": str(p.get("version", "")),
                             "seen": fmt_ago(p.get("last_seen")) if seen else "never",
                             "on": bool(seen and now - seen <= ON_WITHIN),
                             "old": theirs != posting.PROTOCOL, "at": seen, "protocol": theirs,
                             # When it connected. The same browser connecting again keeps its place in the list
                             # and gets a new time: how the window's dialog knows to stop waiting for it.
                             "paired": str(p.get("paired_at") or ""),
                             # What that browser was last seen signed in to. It is the extension's word about its
                             # own browser, and says nothing about the accounts connected in ReelForge.
                             "accounts": p.get("accounts") if isinstance(p.get("accounts"), dict) else {}})
            installs[-1]["signed"] = signed_in(installs[-1]["accounts"], {k: q.name for k, q in self.every.items()})
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
        # The Publish tab's one line about it while it isn't set up, under the line that says so: what it does, and
        # that nobody needs it. The middle part is the one a narrow window keeps, so it says by itself how far that
        # has been tested (TESTED, in full, is in the dialog Set it up opens), in few enough words to show beside
        # the line's two buttons in a window 120 columns wide.
        hint = parts([(Text("Optional. ", MUTED), 1), (Text("Fills in upload pages (tested on copies).", MUTED), 0),
                      (Text(" You check each one and press Post.", MUTED), 2)])
        self.emit("extension", {
            "state": status["state"], "line": status["line"].json(), "lines": [t.json() for t in lines],
            "installs": status["installs"], "port": self.extension_port, "protocol": posting.PROTOCOL,
            "folder": str(folder), "folder_there": folder.is_dir(),
            "hint_dismissed": bool(self._safe(lambda: db.get_meta(HINT, False), False)),
            "hint": hint, "steps": list(SETUP), "signed_note": SIGNED_NOTE,
            # Why this window can't hand a post to the extension, whatever is connected ("": it can).
            "problem": "" if self.demo else self.extension_problem,
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
                "can_post": False, "why": "Pick a video on the left first.", "setup": False, "rows": [],
                "labels": LABELS}
        if video is None:
            self.emit("posting", view)
            return
        review = self.review_of(video, fresh)
        jobs = self._safe(lambda: posting.for_files([video]).get(video, {}), {})
        posts = self.spotted_posts(video)
        opened: dict[str, str] = {}  # platform -> when its upload page was last opened by hand for this video
        for u in self._safe(db.uploads, []):  # (newest first)
            if u["status"] == "manual" and (u["video"] or "").lower() == video.name.lower():
                opened.setdefault(u["platform"], u["ts"])
        rows = []
        for p in self.platforms.values():
            job, post = jobs.get(p.key), posts.get(p.key)
            state = standing(job, post)
            url = (job["url"] if job and state == job["state"] else None) or (post["url"] if post else None)
            pressed = bool(job and state == job["state"] and state in PRESSABLE and job["via"] == "extension"
                           and self._safe(lambda: posting.pressed(job["id"]), False))
            actions = [a for a in (PRESSED if pressed else ACTIONS[state]) if a != "open_post" or url]
            if job and state == job["state"] and state in ("failed", "cancelled") and \
                    self._safe(lambda: posting.reached_the_page(job["id"]), False):
                # It stopped with the video on the platform's page, where you can finish by hand: yours to say.
                actions.append("mark_posted")
            # The video may be up there by your own hand (posting.by_hand): a new post is a post again, and is
            # asked for like one, never started by one press of Try again.
            hand = state in ("none", *posting.FREE) and self._by_hand(video, p.key)
            if hand:
                actions = ["post_again" if a == "retry" else a for a in actions]
            rows.append({"key": p.key, "name": p.name, "color": p.color, "state": state,
                         "line": state_line(job, post, p.name, opened.get(p.key), pressed).json(),
                         "busy": state in BUSY, "url": url,
                         "job": job["id"] if job and state == job["state"] else None,
                         # How that job goes: "extension" or "api". The window asks it before Try again.
                         "via": job["via"] if job and state == job["state"] else None,
                         "by_hand": hand, "actions": actions})
        self.api_rows(rows, jobs, posts)  # what posting through YouTube's own API adds (youtube_api_tab.py)
        style = {"approved": GOOD, "none": MUTED, "demo": MUTED}.get(review["state"], f"bold {AMBER}")
        mark = {"approved": "✓ ", "none": "", "demo": ""}.get(review["state"], "! ")
        why = self.cant_post(video, review, extension, rows)
        view.update(video={"path": str(video), "name": video.name},
                    review={"state": review["state"], "line": Text(mark + review["line"], style).json()},
                    can_post=not why, why=why, rows=rows,
                    # Only the extension is in the way: Post explains how to set it up instead of being greyed out.
                    setup=bool(why) and why == self.extension_needed(extension))
        self.emit("posting", view)

    def _by_hand(self, video: Path, platform: str) -> bool:
        def look() -> bool:
            with db.connect() as c:
                return posting.by_hand(c, posting.digest(video), platform, video.name)

        try:
            return bool(self._safe(look, False))
        except OSError:
            return False  # the video is gone

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

    def cant_hand_over(self) -> str:
        """Why no post can be handed to the extension from this window now ("": one can): no browser is connected,
        the extension needs an update, or this window isn't listening for it, so there is no page to open. Asked
        before any job is made: one made first would only be called off again, and a Post again would have set the
        post that is up aside for it."""
        return self.extension_needed(self.extension_status()) or self.extension_problem

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
        window wasn't looking, the extension's "seen 2m ago", and the picked video's review as it stands now.
        Whatever goes wrong in here is for the log and the next half minute: the extension is an extra, and neither
        the window's start nor its clock stops for it."""
        try:
            if not self.demo:
                self._safe(posting.expire_stale, 0)
                self._safe(posting.reconcile, 0)
            self.update_extension()
            self.update_posting(fresh=self.tab == "publish")
        except Exception:
            traceback.print_exc()

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
                "again": bool(block and block["reason"] in posting.AGAIN),
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
                          "size": f"{video.stat().st_size / 1024 / 1024:.1f} MB",
                          # The file as the question saw it. It goes back with the yes (post_with_extension).
                          "sha256": posting.digest(video)},
                "review": look["review"], "extension": {"state": extension["state"], "line": extension["line"].json()},
                "platforms": platforms, "can_post": not why, "why": why, "stop_before": True, "note": TESTED,
                "how": HOW, "how_press": HOW_PRESS}

    @rpc
    def post_with_extension(self, platforms: list[str], caption: str, tags: str, stop_before: bool = True,
                            force: list[str] | None = None, video: str | None = None,
                            sha256: str | None = None) -> dict | None:
        """Your yes to the question: a job for each ticked platform (posting.create_batch), then the local page that
        hands them to the extension. stop_before: the extension stops at the platform's own Post button (off only
        where post_info said press is available, and the extension then still asks you in the browser before it
        presses). force: the platforms to post to again although the video is up there. video and sha256: the file
        the question was about and its content then (post_info's video.path and video.sha256), which the window
        sends back.
        Returns {"batch", "jobs": [{"id", "platform"}], "blocked": [...], "opened"}; None when there was
        nothing to start from."""
        if self.demo:
            self.notify("Demo mode: nothing is posted.", timeout=4)
            return None
        if not self._picked():
            return None
        if video is not None and Path(video) != self.selected_file:
            # The picked video changed under the question (it was hidden or removed in another window, or its file
            # went): a yes to that one must never post this one, with that one's caption.
            self.notify(f"The question was about {Path(video).name}, and {self.selected_file.name} is the picked "
                        "video now. Press Post… again to post this one.", title="Nothing was started",
                        severity="warning", timeout=12)
            return None
        if sha256 is not None:
            # ... nor another file under the same name (a new render, a copy written over it). You do see the video
            # in the tab before you press Post there, but not when Press Post for me is on: a yes is to the file
            # the question was about, as it is for a post through YouTube's API.
            try:
                same = sha256 == posting.digest(self.selected_file)
            except OSError:
                same = False
            if not same:
                self.notify(f"{self.selected_file.name} changed after the question was shown: it is another file "
                            "now, so nothing was started. Check the video, then press Post… again.",
                            title="Nothing was started", severity="warning", timeout=12)
                return None
        needed = self.cant_hand_over()
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
                            "under BY HAND still open each platform's page for you to post by hand.",
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
        the page says how to add it. True when a browser opened (the window then says it is waiting for it)."""
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
            # No pop-up: the extension's dialog, which is what asks, says in a line of its own that it is waiting for
            # the page that opened. A pop-up saying the same sat on the dialog's buttons for twelve seconds, Post…
            # among them once the browser had connected.
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
    def extension_folder(self, quiet: bool = False) -> str | None:
        """Show the extension's folder in the file manager, with its path on the clipboard: it is the folder to
        pick after Load unpacked on your browser's extensions page. quiet: asked for in the extension's dialog,
        which says itself what happened: the answer is the sentence, and no pop-up says it over the dialog's
        buttons (Connect a browser, the next step, among them)."""
        if self.demo:
            self.notify("The demo doesn't use the browser extension.", timeout=4)
            return None
        folder = extension_api.folder()
        if not folder.is_dir():
            said = (f"This ReelForge has no extension folder yet ({folder}). Update ReelForge on the Updates tab to "
                    "get it.")
            if not quiet:
                self.notify(said, severity="warning", timeout=12)
            return said
        desktop.reveal(folder)
        self.copy_text(str(folder))
        said = (f"It is showing in {desktop.FILES_WINDOW}, and its path is copied. On your browser's extensions "
                "page, switch on Developer mode, press Load unpacked and pick it.")
        if not quiet:
            self.notify(said, title="The extension's folder", timeout=15)
        return said

    @rpc
    def post_action(self, job: str, action: str) -> None:
        """A button on a platform's row. job: the row's job id, or its platform's key when it has none (a post the
        stats read found). cancel calls a job off; mark_posted and not_posted are your word on one ReelForge lost
        track of; retry starts a failed one again with the same text, through the extension, which stops before
        Post (a try through YouTube's API has no retry: api_rows); open_post opens the post; post_again starts a
        new post of the picked video there, with the form's text as it is, stopping before Post."""
        row = self._safe(lambda: posting.get(job), None) if job not in self.every else None
        platform = row["platform"] if row else job
        if self.api_action(row, platform, action):  # a button that goes through YouTube's API (youtube_api_tab.py)
            return
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
        needed = self.cant_hand_over() if action in ("post_again", "retry") else ""
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
