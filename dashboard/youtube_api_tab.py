"""Posting to YouTube through its own API, as the window shows and starts it. The Google side (the sign-in, the
upload itself, the file it keeps) is dashboard/platforms/youtube_api.py; the ledger every post goes through is
dashboard/posting.py; docs/YOUTUBE-API.md is the page for the person.

A post here is a job like the extension's, with via "api": made by posting.create_batch, so the same things stop
it (a video that isn't approved, a post of it already under way, up, or found by a stats read), with the same text
(the title is what Copy YouTube caption copies, the description what Copy YouTube hashtags copies). It then moves

  queued → uploading (with its percent) → posting (the last piece is on its way) → posted, with the video's id

or ends as failed (nothing was posted, and why) or unknown (the last piece went out and YouTube couldn't be asked
whether it arrived: check YouTube Studio; nothing tries again by itself). Who can see the video is your choice for
each post, private until you change it, and YouTube's answer is read back: a video it stored another way is still
posted, and its row says so and what to do.

The first half of this file is plain functions on the ledger (run, settle_orphans), so the tests drive them without
a window. The second is a mixin of the window's service (dashboard/service.py), listed before PostingTab so that
its mind_the_jobs runs first. One view, youtube_api (the connection: off, connecting, on or again), and the calls
behind the buttons: youtube_api_info, youtube_api_connect, youtube_api_disconnect, youtube_api_defaults and
post_with_api. Only the last one, and Retry on a failed row, start an upload, and only the window calls them.
"""

from __future__ import annotations

import json
import sqlite3
import sys
import traceback
from datetime import timedelta
from pathlib import Path
from typing import Callable

from studio.bridge import rpc

from . import analytics, db, posting, videos
from .analytics import fmt_ago
from .platforms import youtube_api
from .platforms.youtube_api import AGAIN, NOT_CONNECTED, PRIVACY, UNPROVEN, ApiError
from .view import GOOD, MUTED, TYPICAL, Text

AMBER = TYPICAL
DOCS = "docs/YOUTUBE-API.md"
STALE = timedelta(minutes=10)  # on a drive that can't lock files: an upload silent this long has nobody sending it
WHO = {"private": "Private", "unlisted": "Unlisted", "public": "Public"}
# What ReelForge leaves to YouTube Studio, said wherever a post is about to be sent.
NOT_SET = ("ReelForge doesn't set the altered-content answer, tags, a thumbnail or a schedule. Set those in YouTube "
           "Studio.")
STUDIO = "Check YouTube Studio before trying again."
PRIVATE = "It is private, as you chose. Change who can see it in YouTube Studio when you're ready."


# ---- one job's upload

def orphans() -> list[dict]:
    """The API jobs that are part-way: each has an upload running in some ReelForge, or had one in a ReelForge
    that closed. With them, a job the clock called unknown before anyone asked its saved session (it was silent
    for a quarter of an hour: posting.expire_stale)."""
    with db.connect() as c:
        jobs = [dict(r) for r in c.execute("SELECT * FROM post_jobs WHERE via = 'api' AND superseded = 0 "
                                           "AND state IN ('uploading', 'posting', 'unknown') ORDER BY rowid")]
    return [j for j in jobs if j["state"] != "unknown" or youtube_api.session(j["id"])]


def run(job_id: str, stop: Callable[[], bool] = lambda: False) -> dict | None:
    """Upload a queued job's video, one upload at a time for this channel, and move the job along as it goes.
    Returns the job as it stands afterwards. stop() saying True (ReelForge is closing) leaves it where it is, for
    the next start to settle (settle_orphans); a job that was called off meanwhile stops the upload too."""
    lock = youtube_api.upload_lock()
    if not lock.acquire(wait=posting.QUEUED_FOR.total_seconds(), stop=stop):
        return posting.get(job_id)  # another upload took that long: the clock has expired this one by now
    try:
        job = posting.get(job_id)
        if job is None or job["via"] != "api" or job["state"] != "queued":
            return job
        _upload(job, stop)
        job = posting.get(job_id)
        if job is None or job["state"] not in ("uploading", "posting"):
            youtube_api.drop_session(job_id)  # over: its session is no use any more
        return job
    finally:
        lock.release()


def _upload(job: dict, stop: Callable[[], bool]) -> None:
    text, options = json.loads(job["text"]), json.loads(job["options"])
    privacy = options.get("privacy")
    at = ["uploading"]  # the state the job is in, for the percent to be said again in

    def move(state: str, **fields) -> dict:
        """The ledger has the last word: a job that was called off, or that the clock gave up on, stops here."""
        try:
            return posting.advance(job["id"], state, by="app", **fields)
        except posting.Refused:
            raise youtube_api.Stopped() from None

    def last() -> None:
        """Before the piece that ends the upload goes: from here on the video may exist, and the ledger has to
        know that first. If it can't be told, the piece doesn't go."""
        move("posting")
        at[0] = "posting"

    def started(uri: str) -> None:
        try:
            youtube_api.save_session(job["id"], uri, job["video_size"])
        except OSError:
            raise ApiError("store", "ReelForge couldn't note the upload in its data folder, so it didn't start.") \
                from None

    def progress(sent: int, size: int) -> None:
        try:
            move(at[0], progress=sent / size)
        except sqlite3.Error:
            pass  # the percent is an extra

    try:
        if privacy not in PRIVACY:  # never guessed: left out, YouTube would make the video public
            raise ApiError("odd", "This post doesn't say who may see the video, so it wasn't sent. Post it again.")
        move("uploading", step="upload", progress=0.0, detail=f"to YouTube, as {privacy}")
        if not posting.unchanged(job):
            raise ApiError("changed", "The video file changed after you pressed Post. Check it, then post again.")
        made = youtube_api.upload(Path(job["video_path"]), text.get("title", ""), text.get("description", ""),
                                  privacy, options.get("made_for_kids") is True, started=started,
                                  progress=progress, last=last, stop=stop)
    except youtube_api.Stopped:
        pass  # called off (the job says so), or ReelForge is closing (the next start settles it)
    except youtube_api.Unknown:
        _end(job, "unknown", detail="The last piece went out, but YouTube didn't say whether it arrived. " + STUDIO)
    except ApiError as e:
        _end(job, "failed", error_code=e.code, error_message=f"{e.message} Nothing was posted.")
    except Exception as e:  # a fault of ReelForge's own: said by its kind only, since its words may carry a token
        print(f"youtube api: the upload stopped on a {type(e).__name__}\n"
              + "".join(traceback.format_tb(e.__traceback__)), file=sys.stderr)
        _stopped_short(job, f"ReelForge ran into a problem of its own ({type(e).__name__}).")
    else:
        try:
            finish(job, made)
        except sqlite3.Error:
            pass  # the database is busy: the job waits as it is, and settle_orphans asks its session and finishes it


def _end(job: dict, state: str, **fields) -> None:
    try:
        posting.advance(job["id"], state, by="app", **fields)
    except (posting.Refused, sqlite3.Error):
        pass  # called off meanwhile, or the database is busy: then settle_orphans ends it


def _stopped_short(job: dict, why: str) -> None:
    """End a job whose upload stopped without YouTube's word: before the last piece nothing can have been posted;
    after it, nobody knows."""
    try:
        now = posting.get(job["id"])
    except sqlite3.Error:
        return  # busy: settle_orphans ends it
    if now and now["state"] == "posting":
        _end(job, "unknown", detail=f"{why} The last piece of the video may have arrived. {STUDIO}")
    else:
        _end(job, "failed", error_code="interrupted", error_message=f"{why} Nothing was posted: start it again.")


def read_back(privacy: str, made_for_kids: bool, made: dict) -> str:
    """What YouTube's own answer about a video says that isn't what was sent: who can see it, and the
    made-for-kids answer, with what to do about each. "" when it stored both as they were sent."""
    said = []
    if made["privacy"] is None:
        said.append("YouTube didn't say who can see it: check that in YouTube Studio.")
    elif made["privacy"] != privacy:
        said.append(f"YouTube stored it as {made['privacy']}, not {privacy} as you chose. Change who can see it in "
                    "YouTube Studio. YouTube has been known to keep uploads private when they come from a Google "
                    f"Cloud project it hasn't verified: {DOCS} says more.")
    if made["made_for_kids"] is not None and made["made_for_kids"] != made_for_kids:
        said.append(f"YouTube has it as {'made' if made['made_for_kids'] else 'not made'} for kids, which isn't "
                    "what ReelForge sent. Check its audience in YouTube Studio.")
    return " ".join(said)


def to_look_at(job: dict) -> tuple[str, bool]:
    """What a posted job has to add, and whether it is something to act on: what the read-back found (finish notes
    it with the step "confirm"), else that the video is private because you chose so."""
    if job["step"] == "confirm" and job["detail"]:
        return job["detail"], True
    return (PRIVATE if json.loads(job["options"]).get("privacy") == "private" else ""), False


def finish(job: dict, made: dict) -> dict | None:
    """An upload YouTube has taken whole: the job is posted, with the video's id and its link, and with what the
    read-back found if it found anything (then its step is "confirm": there is something for you to look at). One
    YouTube turned down after taking it is failed, with its reason."""
    options = json.loads(job["options"])
    youtube_api.note_channel(made)
    if made["upload_status"] in ("failed", "rejected", "deleted"):
        _end(job, "failed", error_code="platform_error", error_message=(
            f"YouTube took the upload and {made['upload_status']} it"
            + (f" ({made['reason']})" if made["reason"] else "") + ". Look at it in YouTube Studio."))
        return posting.get(job["id"])
    url = f"https://youtube.com/shorts/{made['id']}" if made["id"] else None
    note = read_back(options.get("privacy"), options.get("made_for_kids") is True, made)
    if not made["id"]:
        note = f"YouTube took the video, but ReelForge couldn't read its id: find it in YouTube Studio. {note}".strip()
    try:
        return posting.advance(job["id"], "posted", by="app", post_id=made["id"], url=url, detail=note or None,
                               step="confirm" if note else None)
    except posting.Refused:
        # The job ended another way meanwhile (called off, or the clock gave up on it), but the video is up. It
        # goes into the stats as this episode's: that is what stops a second post of it, and a job that was
        # waiting to hear is settled by it.
        if made["id"]:
            db.add_post("youtube", made["id"], job["episode_id"], posting.headline(json.loads(job["text"])), url)
            db.bump()
            posting.reconcile()
        return posting.get(job["id"])


def settle_orphans() -> int:
    """After a ReelForge closed in the middle of an upload: end the jobs nobody is uploading any more. Returns how
    many. A job still uploading never sent its last piece (the ledger hears of that piece before it goes), so
    nothing was posted and it failed. One whose last piece was on its way asks the saved session, once: finished
    is posted, anything else is unknown. While any ReelForge holds the upload lock, its jobs are left alone."""
    lock = youtube_api.upload_lock()
    if not lock.acquire():
        return 0
    try:
        settled = 0
        for job in orphans():
            quiet = analytics.now() - db.parse(job["updated_at"])
            if not lock.locked and quiet < STALE:
                continue  # this drive can't lock files: only one that has been silent for long is nobody's
            if job["state"] == "uploading":
                _end(job, "failed", error_code="interrupted", error_message=(
                    "ReelForge was closed during the upload. Nothing was posted: start it again."))
            else:
                saved, made = youtube_api.session(job["id"]), None
                try:
                    made = youtube_api.ask(saved["uri"], saved["size"]) if saved else None
                except ApiError:
                    pass  # it can't be learned
                if made:
                    finish(job, made)
                elif job["state"] == "posting":
                    _end(job, "unknown", detail=("ReelForge was closed while the last piece was going up, and "
                                                 f"YouTube can't say whether it arrived. {STUDIO}"))
            youtube_api.drop_session(job["id"])
            settled += 1
        return settled
    finally:
        lock.release()


# ---- the window's half

class YouTubeApiTab:
    """The YouTube API's half of the window's service."""

    api_consent: youtube_api.Consent | None = None  # the try at connecting that is waiting for you, or the last one
    api_seen: float | None | bool = False           # the connection file's time as last shown (False: not yet)

    def mind_the_jobs(self) -> None:
        """At start and every half minute, before PostingTab's own: a connection another window made or ended, and
        an upload a ReelForge closed in the middle of (settle_orphans, off the loop's thread: it may ask YouTube)."""
        self.follow_youtube_api()
        if not self.demo and self._safe(orphans, []) and not self.loop.live(("youtube-api", "youtube-api-settle")):
            self.loop.run_worker(self._api_settle, group="youtube-api-settle")
        super().mind_the_jobs()

    def _api_settle(self) -> None:
        if settle_orphans():
            self.loop.call_soon(self.jobs_changed, True)

    def follow_youtube_api(self, changed: bool = False) -> None:
        """Show the connection as it is now, if its file changed since the last look (changed: it did, here)."""
        try:
            seen = None if self.demo else youtube_api.store_path().stat().st_mtime
        except OSError:
            seen = None
        if changed or seen != self.api_seen:
            first, self.api_seen = self.api_seen is False, seen
            self.emit("youtube_api", self.youtube_api_view())
            if not first:
                self.update_posting()  # the YouTube row's button comes or goes with the connection

    # --- what the window shows ------------------------------------------------------------------

    def youtube_api_view(self) -> dict:
        """The connection, with nothing secret in it: state ("off", "connecting", "on" or "again": Google ended the
        sign-in), a line and the lines under it, the channel the last upload went to, and your choices for the next
        post."""
        if self.demo:
            said = {"state": "off", "client": False, "channel": None, "privacy": "private", "made_for_kids": False}
            line, lines = Text("○ The demo doesn't use YouTube's API", MUTED), []
        else:
            said = youtube_api.status()
            line, lines = self._api_line(said)
            lines.append(Text(UNPROVEN, MUTED))
        waiting = self.api_consent is not None and not self.api_consent.done.is_set()
        if waiting:
            line = Text("● Waiting for your answer on Google's page", f"bold {AMBER}")
            line.append("  it opened in your browser", MUTED)
        return {"state": "connecting" if waiting else said["state"], "line": line.json(),
                "lines": [t.json() for t in lines], "client": said["client"], "channel": said["channel"],
                "privacy": said["privacy"], "made_for_kids": said["made_for_kids"],
                "privacies": [{"key": key, "label": WHO[key]} for key in PRIVACY], "docs": DOCS,
                "note": UNPROVEN}

    def _api_line(self, said: dict) -> tuple[Text, list[Text]]:
        if said["state"] == "on":
            channel = said["channel"]
            bits = [f"last upload went to {channel['title'] or channel['id']}" if channel else
                    "Google names the channel with the first upload",
                    f"connected {fmt_ago(said['since'])}" if said["since"] else "",
                    f"posts start as {said['privacy']}"]
            lines = []
            if said["expires"]:
                try:
                    left = db.parse(said["expires"]) - analytics.now()
                    lines.append(Text(f"Google ends this sign-in in {max(left.days, 0)} days: it does after 7 days "
                                      "while your consent screen is in Testing. Connect again then.", MUTED))
                except ValueError:
                    pass
            return Text("● YouTube API connected", f"bold {GOOD}").append("  " + " · ".join(b for b in bits if b),
                                                                         MUTED), lines
        if said["state"] == "again":
            return Text("● YouTube API: connect again", f"bold {AMBER}"), [Text(AGAIN[said["why"]], MUTED)]
        return Text("○ YouTube API not connected", MUTED), [
            Text("Optional. It posts to YouTube through Google's own API, with a Google Cloud project you make "
                 f"yourself. {DOCS} has the steps.", MUTED)]

    def api_rows(self, rows: list[dict], jobs: dict[str, dict], posts: dict[str, dict]) -> None:
        """The posting view's rows, as this way of posting changes them (PostingTab.update_posting calls it). While
        the API is connected and a post could start, the YouTube row has "api": {"force": whether it would be a
        post again}, and post_api among its actions unless it would be one: posting again takes the question
        (youtube_api_info) and your yes (post_with_api), never one press on a row. A posted row says what the
        read-back found, or that the video is private, until a stats read has its numbers (only a video everyone
        can see has any). A row whose last piece is on its way has no buttons: only YouTube's word ends it."""
        connected = not self.demo and youtube_api.status()["state"] == "on"
        for row in rows:
            job, post = jobs.get(row["key"]), posts.get(row["key"])
            if job is not None and job["via"] == "api" and row["job"] == job["id"]:
                if row["state"] == "posting":
                    row["actions"] = []
                elif row["state"] == "posted" and not (post and post["views"] is not None):
                    said, act_on = to_look_at(job)
                    if said:
                        row["line"].append(["  " + said, AMBER if act_on else MUTED])
            again = row["state"] in ("posted", "spotted")
            if row["key"] == "youtube" and connected and (again or row["state"] in ("none", *posting.FREE)):
                row["api"] = {"force": again}
                if not again:
                    row["actions"].append("post_api")

    def api_action(self, row: dict | None, platform: str, action: str) -> bool:
        """A row's button that goes through the API, not the extension (PostingTab.post_action asks this first).
        True when it was one: Retry on a job that went through the API, or post_api, which posts the picked video
        with the form's text as last typed and your choices, and never "again"."""
        retry = action == "retry" and row is not None and row["via"] == "api"
        if not retry and not (action == "post_api" and platform == "youtube"):
            return False
        if self.demo:
            self.notify("Demo mode: nothing is posted.", timeout=4)
        elif retry:
            self.retry_with_api(row)
        else:
            self.post_with_api(*self.form_text)
        return True

    # --- what the buttons call ---------------------------------------------------------------------

    @rpc
    def youtube_api_info(self, caption: str = "", tags: str = "") -> dict | None:
        """The connection (youtube_api_view), and for the picked video what a post through the API would send:
        "send" ({"title", "description", "privacy", "made_for_kids", "category"}), the same as "fields" to show,
        "left" (what ReelForge doesn't set), whether it can start ("can_post", "why"), and whether it would be a
        post again ("force": the video is up there, or may be). With no caption and hashtags given, the form's as
        last typed."""
        if not caption and not tags:
            caption, tags = self.form_text
        out = {**self.youtube_api_view(), "video": None, "review": None, "send": None, "fields": [],
               "left": NOT_SET, "can_post": False, "force": False, "blocked": None}
        video = self.selected_file
        if self.demo:
            return {**out, "why": "The demo doesn't post anything."}
        if video is None or not video.exists():
            return {**out, "why": "Pick a video on the left first."}
        if "youtube" not in self.platforms:
            return {**out, "why": "Connect YouTube on the Accounts tab first."}
        try:
            look = posting.preview(video, ["youtube"], caption, tags, self.cfg)
        except sqlite3.OperationalError as e:
            self.say_busy(e)
            return None
        text, block = look["texts"]["youtube"], look["blocked"].get("youtube")
        again = bool(block and block["reason"] in ("posted", "spotted", "unknown"))
        send = {"title": text["title"], "description": text["description"], "privacy": out["privacy"],
                "made_for_kids": out["made_for_kids"], "category": youtube_api.CATEGORY}
        fields = [{"label": "Title", "text": send["title"] or "—"},
                  {"label": "Description", "text": send["description"] or "—"},
                  {"label": "Who can see it", "text": WHO[send["privacy"]]},
                  {"label": "Made for kids", "text": "Yes" if send["made_for_kids"] else "No"},
                  {"label": "Category", "text": "People & Blogs"}]
        said = youtube_api.status()  # (the view may say "connecting" over a connection that is good)
        if said["state"] != "on":
            why = AGAIN[said["why"]] if said["state"] == "again" else "Connect YouTube's API first."
        elif look["review"]["state"] not in videos.POSTABLE:
            why = look["review"]["line"]
        elif block and not again:
            why = block["message"]
        else:
            why = ""
        return {**out, "video": {"path": str(video), "name": video.name,
                                 "size": f"{video.stat().st_size / 1024 / 1024:.1f} MB"},
                "review": look["review"], "send": send, "fields": fields, "can_post": not why, "why": why,
                "force": again and not why, "blocked": block}

    @rpc
    def youtube_api_connect(self, path: str = "") -> bool:
        """Connect: read the client file at path (the JSON you downloaded from Google Cloud; "" to connect again
        with the client already on file) and open Google's consent page in your browser. True when the page is
        opening; the view and a pop-up follow when you have answered there."""
        if self.demo:
            self.notify("The demo doesn't use YouTube's API.", timeout=4)
            return False
        if self.api_consent is not None:
            self.api_consent.cancel()  # an earlier try still waiting: this one takes its place
        file = (path or "").strip().strip("\"'")
        try:
            self.api_consent = youtube_api.connect(
                file or None, on_done=lambda outcome: self.loop.call_soon(self._api_connected, outcome))
        except ApiError as e:
            self.notify(e.message, title="YouTube's API", severity="warning", timeout=15)
            self.follow_youtube_api(changed=True)
            return False
        self.notify("Google's consent page is opening in your browser. Pick the Google account your channel belongs "
                    "to and allow ReelForge to upload videos. Google will say it hasn't verified the app: the app "
                    "is your own Google Cloud project.", title="Connecting YouTube's API", timeout=20)
        self.follow_youtube_api(changed=True)
        return True

    def _api_connected(self, outcome: dict) -> None:
        current = self.api_consent
        if current is None or current.outcome is not outcome:
            return  # an earlier try, which a newer one replaced
        if outcome["ok"]:
            self.notify(f"{outcome['message']} Posts start as private until you choose otherwise. {UNPROVEN}",
                        title="YouTube's API", timeout=20)
        elif outcome["code"] != "cancelled":
            self.notify(outcome["message"], title="YouTube's API", severity="warning", timeout=15)
        self.follow_youtube_api(changed=True)

    @rpc
    def youtube_api_disconnect(self) -> None:
        """Disconnect: ReelForge forgets the connection, then tells Google to end it."""
        if self.demo:
            self.notify("The demo doesn't use YouTube's API.", timeout=4)
            return
        if self.loop.live(("youtube-api",)):
            self.notify("An upload to YouTube is under way. Let it finish, or cancel it, then disconnect.",
                        title="YouTube's API", severity="warning", timeout=10)
            return
        if self.api_consent is not None:
            self.api_consent.cancel()
        self.loop.run_worker(self._api_disconnect, group="youtube-api-account")

    def _api_disconnect(self) -> None:
        try:
            done = youtube_api.disconnect()
        except OSError as e:
            done = {"error": f"ReelForge couldn't change its connection file ({e}). Nothing was disconnected."}
        self.loop.call_soon(self._api_disconnected, done)

    def _api_disconnected(self, done: dict) -> None:
        if "error" in done:
            self.notify(done["error"], title="YouTube's API", severity="warning", timeout=12)
        elif not done["was"]:
            self.notify("YouTube's API wasn't connected.", timeout=6)
        elif done["revoked"] is False:
            self.notify("ReelForge has forgotten the connection, but Google couldn't be told (is this computer "
                        "online?). Take ReelForge's access away yourself at https://myaccount.google.com/connections",
                        title="YouTube's API", severity="warning", timeout=25)
        else:
            self.notify("Disconnected. ReelForge has forgotten the connection"
                        + (", and Google has ended it." if done["revoked"] else "."), title="YouTube's API", timeout=10)
        self.follow_youtube_api(changed=True)

    @rpc
    def youtube_api_defaults(self, privacy: str | None = None, made_for_kids: bool | None = None) -> dict | None:
        """Your choices for the posts to come: who can see one ("private", "unlisted" or "public") and whether your
        videos are made for kids. Returns the view."""
        if self.demo:
            self.notify("The demo doesn't use YouTube's API.", timeout=4)
            return None
        if privacy is not None and privacy not in PRIVACY or \
                made_for_kids is not None and not isinstance(made_for_kids, bool):
            self.notify("Choose private, unlisted or public.", severity="warning", timeout=6)
            return None
        try:
            youtube_api.set_defaults(privacy, made_for_kids)
        except OSError as e:
            self.notify(f"ReelForge couldn't save that ({e}).", severity="warning", timeout=10)
            return None
        self.follow_youtube_api(changed=True)
        return self.youtube_api_view()

    @rpc
    def post_with_api(self, caption: str, tags: str, privacy: str = "", force: bool = False) -> dict | None:
        """Your yes: post the picked video to YouTube through its API, with the form's caption and hashtags.
        privacy: who can see it ("": your choice for posts to come, private unless you changed it). force: post
        it again although it is up there, or may be. Returns {"batch", "jobs": [{"id", "platform"}], "blocked",
        "started"}; None when there was nothing to start from. The upload runs on a thread of its own."""
        if self.demo:
            self.notify("Demo mode: nothing is posted.", timeout=4)
            return None
        if not self._picked():
            return None
        said = youtube_api.status()
        privacy = privacy or said["privacy"]
        if "youtube" not in self.platforms:
            why = "Connect YouTube on the Accounts tab first."
        elif said["state"] != "on":
            why = AGAIN[said["why"]] if said["state"] == "again" else NOT_CONNECTED
        elif privacy not in PRIVACY:
            why = "Choose who can see the video: private, unlisted or public."
        else:
            why = ""
        if why:
            self.notify(why, title="YouTube's API", severity="warning", timeout=10)
            return None
        try:
            made = posting.create_batch(
                self.selected_file, ["youtube"], caption, tags, self.cfg, force=("youtube",) if force else (),
                via={"youtube": "api"},
                options={"youtube": {"privacy": privacy, "made_for_kids": said["made_for_kids"]}})
        except sqlite3.OperationalError as e:
            self.say_busy(e)
            return None
        return self._api_started(made)

    def retry_with_api(self, job: dict) -> None:
        """Retry on a row whose post went through the API: the same text and choices once more, in a new job and a
        new upload. Only a job that ended without a post has the button."""
        said = youtube_api.status()
        if said["state"] != "on":
            self.notify(AGAIN[said["why"]] if said["state"] == "again" else NOT_CONNECTED, title="YouTube's API",
                        severity="warning", timeout=10)
            return
        try:
            self._api_started(posting.retry(job["id"], self.cfg))
        except posting.Refused as e:
            self.notify(e.message, title="YouTube", severity="warning", timeout=8)
        except sqlite3.OperationalError as e:
            self.say_busy(e)

    def _api_started(self, made: dict) -> dict:
        for b in made["blocked"]:
            self.notify(b["message"], title="YouTube: not started", severity="warning", timeout=12)
        for job in made["jobs"]:
            self.loop.run_worker(self._api_upload, job["id"], group="youtube-api")
        self.picked_review = None
        self.jobs_changed(everywhere=True)
        return {"batch": made["batch"], "jobs": [{"id": j["id"], "platform": j["platform"]} for j in made["jobs"]],
                "blocked": made["blocked"], "started": bool(made["jobs"])}

    def _api_upload(self, job_id: str) -> None:
        worker = self.loop.current_worker()
        job = run(job_id, stop=lambda: worker.is_cancelled)
        if not worker.is_cancelled:
            self.loop.call_soon(self._api_done, job)

    def _api_done(self, job: dict | None) -> None:
        """On the loop, when this window's upload is over: say how it ended."""
        if job and job["state"] == "posted":
            said, act_on = to_look_at(job)
            self.notify((said or "It is up.") + (f" {job['url']}" if job["url"] else ""), title="Posted to YouTube",
                        severity="warning" if act_on else "information", timeout=20)
        elif job and job["state"] == "failed":
            self.notify(job["error_message"] or "The upload failed.", title="YouTube: not posted", severity="warning",
                        timeout=20)
        elif job and job["state"] == "unknown":
            self.notify(job["detail"] or STUDIO, title="YouTube: not sure it posted", severity="warning", timeout=25)
        self.follow_youtube_api(changed=True)  # the channel the upload went to, or a sign-in Google has ended
        self.jobs_changed(everywhere=True)
