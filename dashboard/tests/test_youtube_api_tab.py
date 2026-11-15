"""Posting to YouTube through its own API as the window shows and starts it (dashboard.youtube_api_tab): the
youtube_api view and the calls behind its buttons, a window that has just opened and has to connect before it can
post, a post from your press to "posted" with what YouTube's answer says, every way one ends (failed, unknown, called off), the same rules as the extension's posts about what may not
be posted twice, an upload a ReelForge closed in the middle of, and that nothing secret is ever sent to the window.
On a scratch channel; "Google" is the stand-in in dashboard/tests/google.py and "the browser" follows its consent
page (test_youtube_api.google_for)."""

from __future__ import annotations

import contextlib
import io
import json
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from unittest import mock

from dashboard import db, posting, youtube_api_tab
from dashboard.platforms import youtube_api
from dashboard.platforms.youtube_api import AGAIN, UNPROVEN
from dashboard.tests.google import CHANNEL
from dashboard.tests.test_posting import approve
from dashboard.tests.test_posting_tab import PostingTest
from dashboard.tests.test_publish import CAPTION, TAGS
from dashboard.tests.test_youtube_api import BYTES, CLIENT_ID, CLIENT_SECRET, SIZE, google_for
from dashboard.tests.window import Window, plain

TITLE = f"{CAPTION} #shorts"
LINK = "https://youtube.com/shorts/vid00000001"


class Died(BaseException):
    """ReelForge closing in the middle of something: nothing after it runs, as when a process ends."""


class ApiPosting(PostingTest):
    """Your own window on a channel with one video and four platforms connected, Google's stand-in listening, and
    (unless a test says otherwise) YouTube's API connected for the channel."""

    def setUp(self) -> None:
        super().setUp()
        self.video.write_bytes(BYTES)  # four pieces of a thousand bytes
        google_for(self, self.root)
        self.api_store = self.root / "youtube_api.json"  # the connection's file, beside the scratch stats

    def connect_api(self) -> None:
        consent = youtube_api.connect(self.client_file)
        self.assertTrue(consent.done.wait(10))
        self.assertTrue(consent.outcome["ok"])

    def window(self, api: bool = True, **options) -> Window:
        if api and not self.api_store.exists():
            self.connect_api()
        w = super().window(**options)
        self.addCleanup(self.finish, w)  # (before the window closes: no upload outlives its test)
        w.forget()
        w.svc.follow_youtube_api(changed=True)
        return w

    def finish(self, w: Window) -> None:
        if self.google.hold is not None:
            self.google.hold.set()
        for worker in w.loop.live(("youtube-api", "youtube-api-settle", "youtube-api-account")):
            worker.thread.join(15)

    def reopen(self) -> None:
        """As a ReelForge that has just opened: the sign-in the last one had in mind went with it."""
        for kept in (youtube_api._access, youtube_api._held, youtube_api._ended):
            kept.clear()

    def api_post(self, w: Window, privacy: str = "", force: bool = False) -> dict | None:
        return w.call("post_with_api", CAPTION, TAGS, privacy, force)

    def posted(self, w: Window, privacy: str = "") -> dict:
        """Post the picked video through the API and wait until its job is over. Returns the job."""
        made = self.api_post(w, privacy)
        self.assertTrue(made["started"], made)
        return self.over(w, made["jobs"][0]["id"])

    def over(self, w: Window, job_id: str) -> dict:
        self.assertTrue(w.until(lambda: posting.get(job_id)["state"] not in posting.IN_FLIGHT, 15))
        self.assertTrue(w.until(lambda: not w.loop.live(("youtube-api",)), 15))
        w.pump(0.1)
        return posting.get(job_id)

    def job(self, **kw) -> dict:
        """A queued API job for the video, made the way post_with_api makes one."""
        made = posting.create_batch(self.video, ["youtube"], CAPTION, TAGS, self.cfg, via={"youtube": "api"},
                                    options={"youtube": {"privacy": "private", "made_for_kids": False}}, **kw)
        return made["jobs"][0]

    def events(self, job: dict) -> list[tuple]:
        with db.connect() as c:
            return [(r[0], r[1]) for r in c.execute("SELECT state, step FROM post_events WHERE job = ? ORDER BY id",
                                                    (job["id"],))]

    def age(self, job: dict, minutes: float, column: str = "updated_at") -> None:
        then = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat(timespec="seconds")
        with db.connect() as c:
            c.execute(f"UPDATE post_jobs SET {column} = ? WHERE id = ?", (then, job["id"]))


class View(ApiPosting):
    def test_not_connected_it_says_what_this_is_and_offers_nothing_on_the_row(self):
        w = self.window(api=False)
        view = w.view("youtube_api")
        self.assertEqual((view["state"], view["client"], view["channel"], view["privacy"], view["made_for_kids"]),
                         ("off", False, None, "private", False))
        self.assertEqual(plain(view["line"]), "○ YouTube API not connected")
        said = [plain(line) for line in view["lines"]]
        self.assertIn("docs/YOUTUBE-API.md has the steps.", said[0])
        self.assertEqual((said[-1], view["note"], view["docs"]), (UNPROVEN, UNPROVEN, "docs/YOUTUBE-API.md"))
        row = self.rows(w)["youtube"]
        self.assertEqual((row["actions"], "api" in row), ([], False))
        info = w.call("youtube_api_info", CAPTION, TAGS)
        self.assertEqual((info["can_post"], info["why"]), (False, "Connect YouTube's API first."))
        self.assertIsNone(self.api_post(w))
        self.assertEqual(w.toasts()[-1], youtube_api.NOT_CONNECTED)
        self.assertEqual((posting.recent(), self.google.starts), ([], []))

    def test_connected_the_youtube_row_offers_it_and_the_question_shows_what_would_be_sent(self):
        w = self.window()
        view = w.view("youtube_api")
        self.assertEqual((view["state"], view["client"], view["privacy"]), ("on", True, "private"))
        line = plain(view["line"])
        self.assertTrue(line.startswith("● YouTube API connected"), line)
        self.assertIn("Google names the channel with the first upload", line)
        self.assertIn("posts start as private", line)
        self.assertEqual([p["key"] for p in view["privacies"]], ["private", "unlisted", "public"])
        rows = self.rows(w)
        self.assertEqual((rows["youtube"]["actions"], rows["youtube"]["api"]), (["post_api"], {"force": False}))
        self.assertEqual([r["actions"] for key, r in rows.items() if key != "youtube"], [[], [], []])
        info = w.call("youtube_api_info", CAPTION, TAGS)
        self.assertEqual(info["send"], {"title": TITLE, "description": TAGS, "privacy": "private",
                                        "made_for_kids": False, "category": "22"})
        self.assertEqual([(f["label"], f["text"]) for f in info["fields"]],
                         [("Title", TITLE), ("Description", TAGS), ("Who can see it", "Private"),
                          ("Made for kids", "No"), ("Category", "People & Blogs")])
        self.assertEqual((info["can_post"], info["why"], info["force"], info["blocked"]), (True, "", False, None))
        self.assertEqual((info["video"]["name"], info["review"]["state"], info["note"]), ("ep01.mp4", "none", UNPROVEN))
        self.assertIn("altered-content", info["left"])
        # The title and the description are what the Copy buttons copy for YouTube.
        w.call("copy_field", "copy-youtube-caption", CAPTION, TAGS)
        w.call("copy_field", "copy-youtube", CAPTION, TAGS)
        self.assertEqual(self.copied, [info["send"]["title"], info["send"]["description"]])
        # With nothing given, it is the form as last typed.
        w.call("form_changed", "Another caption", "#one")
        self.assertEqual(w.call("youtube_api_info")["send"]["title"], "Another caption #shorts")
        self.assertEqual(self.google.starts, [])  # looking sends nothing

    def test_a_window_that_has_just_opened_is_not_connected_and_its_row_offers_to_connect(self):
        self.connect_api()  # in a ReelForge that has closed since
        self.reopen()
        w = self.window()
        view = w.view("youtube_api")
        self.assertEqual((view["state"], view["client"]), ("again", True))
        self.assertEqual(plain(view["line"]), "○ YouTube API: not connected in this window")
        self.assertEqual(plain(view["lines"][0]), AGAIN["session"])
        self.assertIn("never in a file", AGAIN["session"])
        row = self.rows(w)["youtube"]
        self.assertEqual((row["actions"], "api" in row), (["connect_api"], False))
        info = w.call("youtube_api_info", CAPTION, TAGS)
        self.assertEqual((info["can_post"], info["why"]), (False, AGAIN["session"]))
        # Nothing posts from here, whichever way it is asked: there is no sign-in to post with.
        self.assertIsNone(self.api_post(w, "public"))
        self.assertEqual(w.toasts()[-1], AGAIN["session"])
        w.call("post_action", "youtube", "post_api")
        self.assertEqual((posting.recent(), self.google.starts, self.google.token_calls[1:]), ([], [], []))
        # The row's button opens Google's page, and your yes there connects this window.
        opened = len(self.opened)
        w.call("post_action", "youtube", "connect_api")
        self.assertEqual(len(self.opened), opened + 1)
        self.assertTrue(w.until(lambda: w.view("youtube_api")["state"] == "on"))
        self.assertEqual(self.rows(w)["youtube"]["actions"], ["post_api"])
        self.assertEqual(self.posted(w)["state"], "posted")
        self.assertEqual(json.loads(self.api_store.read_text("utf-8")),
                         {"client_id": CLIENT_ID, "client_secret": CLIENT_SECRET,
                          "channel": {"id": CHANNEL["id"], "title": CHANNEL["title"]}})  # still no sign-in on file

    def test_the_demo_has_none_of_it(self):
        self.connect_api()
        w = PostingTest.window(self)
        w.svc.demo = True
        w.svc.follow_youtube_api(changed=True)
        view = w.view("youtube_api")
        self.assertEqual((view["state"], plain(view["line"])), ("off", "○ The demo doesn't use YouTube's API"))
        self.assertEqual(w.call("youtube_api_info", CAPTION, TAGS)["why"], "The demo doesn't post anything.")
        self.assertIsNone(self.api_post(w))
        self.assertFalse(w.call("youtube_api_connect", str(self.client_file)))
        w.call("youtube_api_disconnect")
        self.assertIsNone(w.call("youtube_api_defaults", "public"))
        self.assertEqual((posting.recent(), self.google.starts, self.google.revoke_calls), ([], [], []))
        self.assertTrue(self.api_store.exists())

    def test_what_stops_a_post_is_said_before_it_is_tried(self):
        w = self.window()
        w.svc.selected_file = None
        self.assertEqual(w.call("youtube_api_info", CAPTION, TAGS)["why"], "Pick a video on the left first.")
        self.assertIsNone(self.api_post(w))
        w.svc.selected_file = self.video
        del w.svc.platforms["youtube"]  # no YouTube handle on the Accounts tab
        self.assertEqual(w.call("youtube_api_info", CAPTION, TAGS)["why"], "Connect YouTube on the Accounts tab first.")
        self.assertIsNone(self.api_post(w))
        self.assertEqual(w.toasts()[-1], "Connect YouTube on the Accounts tab first.")
        self.assertEqual((posting.recent(), self.google.starts), ([], []))

    def test_a_pipeline_video_needs_its_review_here_too(self):
        ep = self.root / "episodes" / "ep02_duck"
        (ep / "final").mkdir(parents=True)
        (ep / "episode.json").write_text(json.dumps({"id": "ep02_duck", "title": CAPTION}), encoding="utf-8")
        video = ep / "final" / "ep02_duck.mp4"
        video.write_bytes(BYTES)
        approve(ep, video, review=None)
        w = self.window()
        w.call("pick_file", str(video))
        info = w.call("youtube_api_info", CAPTION, TAGS)
        self.assertEqual((info["can_post"], info["review"]["state"]), (False, "unreviewed"))
        self.assertIn("no review yet", info["why"])
        made = self.api_post(w)
        self.assertEqual((made["started"], [b["reason"] for b in made["blocked"]]), (False, ["review"]))
        self.assertEqual(self.google.starts, [])
        approve(ep, video)
        self.assertEqual(self.posted(w)["review"], "approved")


class Buttons(ApiPosting):
    def test_connect_opens_googles_page_and_the_view_follows_your_answer(self):
        w = self.window(api=False)
        self.assertTrue(w.call("youtube_api_connect", f'"{self.client_file}"'))  # (a path pasted with its quotes)
        self.assertTrue(w.until(lambda: w.view("youtube_api")["state"] == "on"))
        titles = [(t["title"], t["severity"]) for t in w.sent("toast")]
        self.assertEqual(titles, [("Connecting YouTube's API", "information"), ("YouTube's API", "information")])
        self.assertIn("Google will say it hasn't verified the app", w.toasts()[0])
        self.assertIn("ReelForge is connected to YouTube's API. Posts start as private", w.toasts()[1])
        self.assertIn("never against Google itself", w.toasts()[1])
        self.assertEqual(self.rows(w)["youtube"]["actions"], ["post_api"])  # the row follows

    def test_connecting_again_says_what_posts_really_start_as(self):
        w = self.window()
        w.call("youtube_api_defaults", "public")  # your choice, made in an earlier session
        self.reopen()
        w.svc.follow_youtube_api(changed=True)
        w.forget()
        self.assertTrue(w.call("youtube_api_connect", ""))
        self.assertTrue(w.until(lambda: w.view("youtube_api")["state"] == "on"))
        toast = w.sent("toast")[-1]
        self.assertIn("ReelForge is connected to YouTube's API. Posts start as public, as you chose earlier.",
                      toast["body"])
        self.assertNotIn("private", toast["body"].replace("Post as private first", ""))
        self.assertEqual(toast["severity"], "warning")  # not the quiet default: worth a look
        # The pop-up, the question and what one press on the row sends all say the same.
        self.assertEqual(w.call("youtube_api_info", CAPTION, TAGS)["send"]["privacy"], "public")
        w.call("post_action", "youtube", "post_api")
        (job,) = posting.recent()
        self.over(w, job["id"])
        self.assertEqual(self.google.starts[0]["body"]["status"]["privacyStatus"], "public")

    def test_a_connect_that_cannot_start_or_is_refused_says_why(self):
        w = self.window(api=False)
        self.assertFalse(w.call("youtube_api_connect", ""))
        self.assertIn("Pick the client file", w.toasts()[-1])
        self.assertFalse(w.call("youtube_api_connect", str(self.root / "nowhere.json")))
        self.assertIn("There is no file at", w.toasts()[-1])
        self.google.consent = "no"
        self.assertTrue(w.call("youtube_api_connect", str(self.client_file)))
        self.assertTrue(w.until(lambda: "You said no on Google's page, so nothing was connected." in w.toasts()))
        self.assertEqual((w.view("youtube_api")["state"], w.sent("toast")[-1]["severity"]), ("off", "warning"))
        self.assertFalse(self.api_store.exists())

    def test_while_google_waits_for_you_the_view_says_so_and_a_new_try_replaces_the_old(self):
        w = self.window(api=False)
        with mock.patch.object(youtube_api.desktop, "open_url", lambda url: True):  # a page you haven't answered yet
            self.assertTrue(w.call("youtube_api_connect", str(self.client_file)))
            first = w.svc.api_consent
            view = w.view("youtube_api")
            self.assertEqual((view["state"], plain(view["line"])),
                             ("connecting", "● Waiting for your answer on Google's page  it opened in your browser"))
            self.assertTrue(w.call("youtube_api_connect", str(self.client_file)))
        self.assertTrue(first.done.wait(5))
        self.assertEqual(first.outcome["code"], "cancelled")
        w.pump(0.2)
        self.assertEqual(w.view("youtube_api")["state"], "connecting")  # the old one ending changes nothing
        self.assertEqual([t for t in w.toasts() if "Stopped" in t], [])
        youtube_api.desktop.open_url(w.svc.api_consent.url())  # now you answer the page of the newer try
        self.assertTrue(w.until(lambda: w.view("youtube_api")["state"] == "on"))

    def test_disconnect_forgets_it_and_tells_google(self):
        w = self.window()
        w.call("youtube_api_disconnect")
        self.assertTrue(w.until(lambda: w.view("youtube_api")["state"] == "off"))
        self.assertEqual(w.toasts()[-1],
                         "Disconnected. ReelForge has forgotten the connection, and Google has ended it.")
        self.assertEqual((self.google.grants, self.api_store.exists()), ({}, False))
        self.assertEqual(self.rows(w)["youtube"]["actions"], [])
        w.call("youtube_api_disconnect")
        self.assertTrue(w.until(lambda: w.toasts()[-1] == "YouTube's API wasn't connected."))

    def test_disconnect_when_google_cannot_be_told_says_what_to_do_yourself(self):
        w = self.window()
        with mock.patch.object(youtube_api, "REVOKE_URL", "http://127.0.0.1:9/revoke"):
            w.call("youtube_api_disconnect")
            self.assertTrue(w.until(lambda: w.view("youtube_api")["state"] == "off"))
        self.assertIn("https://myaccount.google.com/connections", w.toasts()[-1])
        self.assertFalse(self.api_store.exists())

    def test_who_can_see_a_post_is_private_until_you_change_it(self):
        w = self.window()
        self.assertEqual(w.call("youtube_api_defaults", "unlisted")["privacy"], "unlisted")
        self.assertIn("posts start as unlisted", plain(w.view("youtube_api")["line"]))
        self.assertEqual(w.call("youtube_api_info", CAPTION, TAGS)["send"]["privacy"], "unlisted")
        self.assertTrue(w.call("youtube_api_defaults", None, True)["made_for_kids"])
        fields = w.call("youtube_api_info", CAPTION, TAGS)["fields"]
        self.assertEqual([f["text"] for f in fields[2:4]], ["Unlisted", "Yes"])
        self.assertIsNone(w.call("youtube_api_defaults", "everyone"))
        self.assertEqual(w.toasts()[-1], "Choose private, unlisted or public.")
        job = self.posted(w)  # with nothing said for this post: your choices
        self.assertEqual(json.loads(job["options"]), {"privacy": "unlisted", "made_for_kids": True})
        self.assertEqual(self.google.starts[0]["body"]["status"],
                         {"privacyStatus": "unlisted", "selfDeclaredMadeForKids": True})
        self.assertIsNone(self.api_post(w, "everyone", True))
        self.assertEqual(w.toasts()[-1], "Choose who can see the video: private, unlisted or public.")

    def test_another_windows_connection_is_not_this_windows(self):
        w = self.window(api=False)
        self.connect_api()  # as another window of this channel would...
        self.reopen()       # ...with its sign-in in its own mind
        w.svc.demo = False
        w.svc.mind_the_jobs()  # the next look: the client is on file now, so this window can connect too
        self.assertEqual(w.view("youtube_api")["state"], "again")
        self.assertEqual(self.rows(w)["youtube"]["actions"], ["connect_api"])

    def test_disconnect_in_a_window_that_is_not_connected_forgets_the_client(self):
        self.connect_api()
        self.reopen()
        w = self.window()
        w.call("youtube_api_disconnect")
        self.assertTrue(w.until(lambda: w.view("youtube_api")["state"] == "off"))
        self.assertIn("Disconnected. ReelForge has forgotten the client.", w.toasts()[-1])
        self.assertIn("https://myaccount.google.com/connections", w.toasts()[-1])  # it had no sign-in to end there
        self.assertEqual((self.google.revoke_calls, self.api_store.exists()), ([], False))
        self.assertEqual(self.rows(w)["youtube"]["actions"], [])


class Posting(ApiPosting):
    def test_a_post_goes_from_your_press_to_posted_with_its_id_and_link(self):
        w = self.window()
        made = self.api_post(w)
        self.assertEqual((made["started"], made["blocked"], [j["platform"] for j in made["jobs"]]),
                         (True, [], ["youtube"]))
        self.assertRegex(made["batch"], r"^pb_[0-9a-f]{16}$")
        job = self.over(w, made["jobs"][0]["id"])
        self.assertEqual((job["state"], job["via"], job["post_id"], job["url"], job["error_code"], job["detail"]),
                         ("posted", "api", "vid00000001", LINK, None, None))
        self.assertEqual(json.loads(job["options"]), {"privacy": "private", "made_for_kids": False})
        self.assertEqual(json.loads(job["text"]), {"title": TITLE, "description": TAGS})
        self.assertEqual(self.events(job), [("queued", None), ("uploading", "upload"), ("posting", None),
                                            ("posted", None)])
        # Google got exactly that text, private as chosen, in one session and one video.
        (start,) = self.google.starts
        self.assertEqual((start["body"]["snippet"]["title"], start["body"]["snippet"]["description"],
                          start["body"]["status"]), (TITLE, TAGS, {"privacyStatus": "private",
                                                                   "selfDeclaredMadeForKids": False}))
        self.assertEqual((len(self.google.videos), bytes(self.google.videos[0]["data"])), (1, BYTES))
        # The post is this episode's in the stats, and the upload log has it.
        (episode,) = db.episodes()
        (post,) = db.all_posts()
        self.assertEqual((post["platform"], post["post_id"], post["episode_id"], post["url"]),
                         ("youtube", "vid00000001", episode["id"], LINK))
        self.assertEqual([(u["platform"], u["status"], u["message"]) for u in db.uploads()],
                         [("youtube", "api", "posting with its API")])
        # The row says it is up and what to do next, and offers to post it again; the pop-up says the same.
        row = self.rows(w)["youtube"]
        self.assertEqual((row["state"], row["url"], row["actions"], row["api"]),
                         ("posted", LINK, ["open_post", "post_again"], {"force": True}))
        said = plain(row["line"])
        self.assertTrue(said.startswith("✓ posted"), said)
        self.assertIn("It is private, as you chose. Change who can see it in YouTube Studio when you're ready.", said)
        toast = w.sent("toast")[-1]
        self.assertEqual((toast["title"], toast["severity"]), ("Posted to YouTube", "information"))
        self.assertIn("It is private, as you chose.", toast["body"])
        self.assertIn(LINK, toast["body"])
        self.assertIn(f"posted with its API · {LINK}", plain(w.view("uploads")["rows"][0]["cells"][4]))
        # The channel it went to is known from now on, and the upload's session is off the file.
        self.assertEqual(w.view("youtube_api")["channel"], CHANNEL)
        self.assertIn("last upload went to Night Physics", plain(w.view("youtube_api")["line"]))
        self.assertNotIn("sessions", json.loads(self.api_store.read_text("utf-8")))

    def test_the_rows_own_button_posts_with_the_form_as_it_is_and_never_again(self):
        w = self.window()
        w.call("form_changed", "Typed since", "#one #two")
        w.call("post_action", "youtube", "post_api")
        (job,) = posting.recent()
        job = self.over(w, job["id"])
        self.assertEqual((job["state"], job["via"], json.loads(job["text"]), json.loads(job["options"])["privacy"]),
                         ("posted", "api", {"title": "Typed since #shorts", "description": "#one #two"}, "private"))
        w.call("post_action", job["id"], "post_api")  # (a row that is up has no such button, and it would not post)
        self.assertIn("Already posted", w.toasts()[-1])
        self.assertEqual((len(posting.recent()), len(self.google.starts)), (1, 1))
        w.call("post_action", "tiktok", "post_api")  # nor is it a button of any other platform's
        self.assertEqual(len(posting.recent()), 1)
        w.svc.demo = True
        w.call("post_action", "youtube", "post_api")
        self.assertEqual((w.toasts()[-1], len(posting.recent())), ("Demo mode: nothing is posted.", 1))

    def test_the_window_never_waits_for_the_upload(self):
        w = self.window()
        self.google.hold = threading.Event()  # YouTube takes its time over the first piece
        made = self.api_post(w)
        job_id = made["jobs"][0]["id"]
        self.assertTrue(made["started"])
        self.assertTrue(w.until(lambda: self.rows(w)["youtube"]["state"] == "uploading"))
        row = self.rows(w)["youtube"]
        self.assertEqual((row["busy"], row["actions"], row["job"]), (True, ["cancel"], job_id))
        self.assertEqual(plain(row["line"]), "● uploading 0%  to YouTube, as private")
        w.call("form_changed", "Typing goes on", TAGS)  # the window answers as ever
        self.assertIn("Typing goes on", plain(w.view("preview")["rows"][0]["text"]))
        w.call("youtube_api_disconnect")  # not in the middle of an upload
        self.assertIn("An upload to YouTube is under way", w.toasts()[-1])
        self.assertTrue(self.api_store.exists())
        self.assertEqual(self.api_post(w)["blocked"][0]["reason"], "in_progress")  # nor the same video twice
        self.google.hold.set()
        self.assertEqual(self.over(w, job_id)["state"], "posted")
        self.assertEqual((len(self.google.starts), len(self.google.videos)), (1, 1))

    def test_the_percent_moves_with_each_piece(self):
        w = self.window()
        job = self.job()
        seen = []
        self.google.on_put = lambda n: seen.append((posting.get(job["id"])["state"],
                                                    round(posting.get(job["id"])["progress"] or 0, 2)))
        youtube_api_tab.run(job["id"])
        self.assertEqual(seen, [("uploading", 0.0), ("uploading", round(1000 / SIZE, 2)),
                                ("uploading", round(2000 / SIZE, 2)), ("posting", 0.0)])
        self.assertEqual(posting.get(job["id"])["state"], "posted")
        w.svc.jobs_changed()
        self.assertEqual(self.rows(w)["youtube"]["state"], "posted")

    def test_youtube_storing_it_another_way_is_still_posted_and_says_so(self):
        self.google.stores_as = "private"  # what YouTube used to do to uploads from an unverified project
        w = self.window()
        job = self.posted(w, "public")
        self.assertEqual(self.google.starts[0]["body"]["status"]["privacyStatus"], "public")
        self.assertEqual((job["state"], job["post_id"]), ("posted", "vid00000001"))
        self.assertIn("YouTube stored it as private, not public as you chose. Change who can see it in YouTube Studio.",
                      job["detail"])
        self.assertIn("docs/YOUTUBE-API.md", job["detail"])
        self.assertEqual(job["step"], "confirm")  # something for you to look at
        said = self.rows(w)["youtube"]["line"][-1]
        self.assertTrue(said[0].startswith("  YouTube stored it as private, not public"))
        self.assertIn(youtube_api_tab.AMBER, said[1])
        toast = w.sent("toast")[-1]
        self.assertEqual((toast["title"], toast["severity"]), ("Posted to YouTube", "warning"))
        self.assertIn("YouTube stored it as private, not public", toast["body"])
        # Once a stats read has the post's numbers, everyone can see it: the note has done its work.
        w.svc.posts = [{"platform": "youtube", "post_id": "vid00000001", "episode_id": job["episode_id"],
                        "published_at": db.now_iso(), "views": 12, "url": LINK}]
        w.svc.update_posting()
        self.assertEqual(plain(self.rows(w)["youtube"]["line"]).count("YouTube stored it"), 0)

    def test_a_public_post_youtube_stored_as_asked_has_nothing_to_add(self):
        w = self.window()
        job = self.posted(w, "public")
        self.assertEqual((job["state"], job["detail"], json.loads(job["options"])["privacy"]),
                         ("posted", None, "public"))
        self.assertEqual(w.sent("toast")[-1]["body"], f"It is up. {LINK}")

    def test_what_the_read_back_says(self):
        made = {"privacy": "private", "made_for_kids": False}
        self.assertEqual(youtube_api_tab.read_back("private", False, made), "")  # as it was sent: nothing to say
        self.assertEqual(youtube_api_tab.read_back("unlisted", False, {**made, "privacy": "unlisted"}), "")
        self.assertIn("stored it as private, not unlisted", youtube_api_tab.read_back("unlisted", False, made))
        unsaid = youtube_api_tab.read_back("public", False, {**made, "privacy": None})
        self.assertEqual(unsaid, "YouTube didn't say who can see it: check that in YouTube Studio.")
        said = youtube_api_tab.read_back("public", False, {"privacy": "public", "made_for_kids": True})
        self.assertEqual(said, "YouTube has it as made for kids, which isn't what ReelForge sent. Check its audience "
                               "in YouTube Studio.")
        self.assertEqual(youtube_api_tab.read_back("public", True, {"privacy": "public", "made_for_kids": None}), "")

    def test_an_upload_youtube_takes_and_then_turns_down_is_not_a_post(self):
        self.google.upload_status = "rejected"
        w = self.window()
        job = self.posted(w)
        self.assertEqual((job["state"], job["error_code"], job["post_id"]), ("failed", "platform_error", None))
        self.assertEqual(job["error_message"], "YouTube took the upload and rejected it (duplicate). Look at it in "
                                               "YouTube Studio.")
        self.assertEqual(db.all_posts(), [])

    def test_a_video_whose_id_cannot_be_read_is_posted_all_the_same(self):
        job = self.job()
        posting.advance(job["id"], "posting", by="app")
        made = {"id": "not a video id", "privacy": "private", "made_for_kids": False, "upload_status": "uploaded",
                "reason": "", "channel_id": "", "channel_title": ""}
        done = youtube_api_tab.finish(job, {**made, "id": youtube_api._made({"id": made["id"]})["id"]})
        self.assertEqual((done["state"], done["post_id"], done["url"]), ("posted", None, None))
        self.assertTrue(done["detail"].startswith("YouTube took the video, but ReelForge couldn't read its id: find "
                                                  "it in YouTube Studio."))
        self.assertEqual(db.all_posts(), [])
        blocked = posting.create_batch(self.video, ["youtube"], CAPTION, TAGS, self.cfg, via={"youtube": "api"})
        self.assertEqual([b["reason"] for b in blocked["blocked"]], ["posted"])  # it is up: not sent again

    def test_a_refusal_fails_the_job_in_a_sentence_and_retry_goes_through_the_api_again(self):
        self.google.start_script = [(403, "quotaExceeded")]
        w = self.window(connect=False)  # no browser extension anywhere: this way of posting needs none
        job = self.posted(w)
        self.assertEqual((job["state"], job["error_code"]), ("failed", "quota"))
        self.assertEqual(job["error_message"], "Your Google Cloud project has used up today's YouTube API allowance. "
                                               "It starts again at midnight Pacific time. Nothing was posted.")
        row = self.rows(w)["youtube"]
        self.assertEqual((row["state"], row["actions"]), ("failed", ["retry", "post_api"]))
        self.assertIn("× failed: Your Google Cloud project has used up today's", plain(row["line"]))
        toast = w.sent("toast")[-1]
        self.assertEqual((toast["title"], toast["severity"]), ("YouTube: not posted", "warning"))
        self.assertEqual((self.google.sessions, db.all_posts()), ({}, []))
        w.call("youtube_api_defaults", "public")  # (a retry keeps what the failed one was to send)
        w.call("post_action", job["id"], "retry")
        (again,) = [j for j in posting.recent() if j["id"] != job["id"]]
        again = self.over(w, again["id"])
        self.assertEqual((again["state"], again["via"], again["text"], again["options"]),
                         ("posted", "api", job["text"], job["options"]))
        self.assertEqual(self.google.starts[1]["body"]["status"]["privacyStatus"], "private")
        self.assertEqual((self.pages, len(self.google.videos)), ([], 1))  # no page for the extension was opened

    def test_retry_with_the_sign_in_gone_says_connect_again(self):
        self.google.start_script = [(403, "quotaExceeded")]
        w = self.window()
        job = self.posted(w)
        self.google.end_grant()
        youtube_api._access.clear()
        w.call("post_action", job["id"], "retry")
        (again,) = [j for j in posting.recent() if j["id"] != job["id"]]
        again = self.over(w, again["id"])
        self.assertEqual((again["state"], again["error_code"]), ("failed", "not_connected"))
        self.assertEqual(again["error_message"], AGAIN["grant"] + " Nothing was posted.")
        view = w.view("youtube_api")
        self.assertEqual((view["state"], plain(view["line"])), ("again", "● YouTube API: connect again"))
        self.assertEqual(plain(view["lines"][0]), AGAIN["grant"])
        self.assertEqual(self.rows(w)["youtube"]["actions"], ["retry", "connect_api"])  # nothing new until then
        w.call("post_action", again["id"], "retry")
        self.assertEqual(w.toasts()[-1], AGAIN["grant"])
        self.assertEqual(len(posting.recent()), 2)
        self.assertEqual(w.call("youtube_api_info", CAPTION, TAGS)["why"], AGAIN["grant"])
        self.assertTrue(w.call("youtube_api_connect", ""))  # again, with the client on file
        self.assertTrue(w.until(lambda: w.view("youtube_api")["state"] == "on"))

    def test_the_last_answer_lost_for_good_is_unknown_and_nothing_is_sent_twice(self):
        self.google.script, self.google.then = [None, None, None, "lost"], "drop"
        w = self.window()
        job = self.posted(w)
        self.assertEqual((job["state"], job["post_id"]), ("unknown", None))
        self.assertEqual(job["detail"], "The last piece went out, but YouTube didn't say whether it arrived. Check "
                                        "YouTube Studio before trying again.")
        row = self.rows(w)["youtube"]
        self.assertEqual((row["state"], row["actions"]), ("unknown", ["mark_posted", "not_posted"]))
        self.assertEqual(plain(row["line"]),
                         "? did it post? ReelForge lost track: check YouTube Studio before trying again")
        toast = w.sent("toast")[-1]
        self.assertEqual((toast["title"], toast["severity"]), ("YouTube: not sure it posted", "warning"))
        self.google.then = None
        made = self.api_post(w)  # pressing it again doesn't send it again
        self.assertEqual((made["started"], [b["reason"] for b in made["blocked"]]), (False, ["unknown"]))
        info = w.call("youtube_api_info", CAPTION, TAGS)
        self.assertEqual((info["can_post"], info["force"]), (True, True))  # only by saying "again"
        self.assertEqual((len(self.google.starts), len(self.google.sessions), len(self.google.videos)), (1, 1, 1))
        w.call("post_action", job["id"], "mark_posted")  # you looked in YouTube Studio: it is there
        self.assertEqual(self.rows(w)["youtube"]["state"], "posted")

    def test_a_no_that_proves_nothing_after_the_last_piece_went_out_offers_no_retry(self):
        # The last piece went out and no answer came; sent again, it is refused. The first sending may have made
        # the video, so a Retry here could make a second one.
        self.google.script = [None, None, None, "drop", None, (403, "forbidden")]
        w = self.window()
        job = self.posted(w)
        self.assertEqual((job["state"], job["error_code"]), ("unknown", None))
        row = self.rows(w)["youtube"]
        self.assertEqual((row["state"], row["actions"]), ("unknown", ["mark_posted", "not_posted"]))
        w.call("post_action", job["id"], "retry")
        w.call("post_action", "youtube", "post_api")
        self.assertEqual((len(posting.recent()), len(self.google.starts)), (1, 1))
        # A word ReelForge doesn't know, in answer to the last piece, is no proof either.
        w.call("post_action", job["id"], "not_posted")
        self.google.script = [None, None, None, (400, "somethingNobodyHasSeen")]
        self.assertEqual(self.posted(w)["state"], "unknown")

    def test_unknown_that_you_say_did_not_post_can_be_posted(self):
        self.google.script, self.google.then = [None, None, None, "404"], None
        w = self.window()
        job = self.posted(w)
        self.assertEqual(job["state"], "unknown")
        w.call("post_action", job["id"], "not_posted")
        row = self.rows(w)["youtube"]
        self.assertEqual((row["state"], row["actions"]), ("cancelled", ["retry", "post_api"]))
        self.assertEqual(self.posted(w)["state"], "posted")

    def test_a_connection_that_stays_down_before_the_last_piece_fails_and_can_be_tried_again(self):
        self.google.script, self.google.then = [None, "drop"], "drop"
        w = self.window()
        job = self.posted(w)
        self.assertEqual((job["state"], job["error_code"]), ("failed", "offline"))
        self.assertTrue(job["error_message"].endswith("Nothing was posted."))
        self.assertEqual(self.google.videos, [])

    def test_calling_it_off_in_the_middle_sends_no_more(self):
        w = self.window()
        self.google.hold = threading.Event()
        job_id = self.api_post(w)["jobs"][0]["id"]
        self.assertTrue(w.until(lambda: self.rows(w)["youtube"]["state"] == "uploading"))
        w.call("post_action", job_id, "cancel")
        self.google.hold.set()
        job = self.over(w, job_id)
        self.assertEqual((job["state"], job["detail"]), ("cancelled", "you cancelled it"))
        self.assertEqual((len(self.google.puts), self.google.videos), (1, []))
        self.assertNotIn("sessions", json.loads(self.api_store.read_text("utf-8")))
        self.assertEqual(self.rows(w)["youtube"]["actions"], ["retry", "post_api"])
        self.assertEqual([t["title"] for t in w.sent("toast") if "YouTube" in t["title"]], [])  # you know already

    def test_while_the_last_piece_is_on_its_way_the_row_has_no_buttons(self):
        w = self.window()
        job = self.job()
        posting.advance(job["id"], "uploading", by="app", step="upload")
        posting.advance(job["id"], "posting", by="app")
        w.svc.jobs_changed()
        row = self.rows(w)["youtube"]
        self.assertEqual((row["state"], row["actions"]), ("posting", []))
        self.assertEqual(plain(row["line"]), "● the last piece is on its way: waiting for YouTube to say it's up")

    def test_if_the_ledger_cannot_be_told_the_last_piece_does_not_go(self):
        # Everything after a quit rests on this: a job that isn't "posting" never sent its last piece, so it can be
        # called failed and tried again without a second video.
        self.connect_api()
        real = posting.advance
        for error in (sqlite3.OperationalError("database is locked"), posting.Refused("bad_transition", "no")):
            job = self.job()

            def advance(job_id, state, **kw):
                if state == "posting":
                    raise error
                return real(job_id, state, **kw)

            sent, err = len(self.google.puts), io.StringIO()
            with mock.patch.object(posting, "advance", advance), contextlib.redirect_stderr(err):
                left = youtube_api_tab.run(job["id"])
            self.assertEqual([p["range"] for p in self.google.puts[sent:]],
                             [f"bytes 0-999/{SIZE}", f"bytes 1000-1999/{SIZE}", f"bytes 2000-2999/{SIZE}"], error)
            self.assertEqual(self.google.videos, [])
            self.assertIn(left["state"], ("failed", "uploading"), error)  # never posting, posted or unknown
            youtube_api_tab.settle_orphans()  # what the next look does with one left uploading
            done = posting.get(job["id"])
            self.assertEqual((done["state"], done["error_code"]), ("failed", "interrupted"), error)
            self.assertTrue(done["error_message"].endswith("Nothing was posted: start it again."))
            self.assertEqual((len(self.google.puts) - sent, self.google.videos), (3, []))  # and that is true

    def test_a_file_replaced_after_the_press_is_not_sent(self):
        w = self.window()
        job = self.job()
        self.video.write_bytes(BYTES[::-1])
        done = youtube_api_tab.run(job["id"])
        self.assertEqual((done["state"], done["error_code"]), ("failed", "changed"))
        self.assertEqual(self.google.starts, [])
        self.assertEqual(done["error_message"], "The video file changed after you pressed Post. Check it, then "
                                                "post again. Nothing was posted.")
        w.svc.jobs_changed()
        self.assertEqual(self.rows(w)["youtube"]["state"], "none")  # the file there now is another video

    def test_a_job_that_does_not_say_who_may_see_it_is_never_sent(self):
        made = posting.create_batch(self.video, ["youtube"], CAPTION, TAGS, self.cfg, via={"youtube": "api"})
        self.connect_api()
        done = youtube_api_tab.run(made["jobs"][0]["id"])
        self.assertEqual((done["state"], done["error_code"]), ("failed", "odd"))
        self.assertEqual(self.google.starts, [])

    def test_a_fault_of_reelforges_own_ends_the_job_without_its_words(self):
        self.connect_api()
        token = youtube_api.access_token()
        err = io.StringIO()
        for state, ends in (("uploading", "failed"), ("posting", "unknown")):
            job = self.job(force=("youtube",))

            def broken(*args, last, **kw):
                if state == "posting":
                    last()
                raise RuntimeError(f"header value Bearer {token}")

            with mock.patch.object(youtube_api, "upload", broken), contextlib.redirect_stderr(err):
                done = youtube_api_tab.run(job["id"])
            self.assertEqual(done["state"], ends)
            said = done["error_message"] or done["detail"]
            self.assertIn("ReelForge ran into a problem of its own (RuntimeError).", said)
            posting.resolve(done["id"], False) if ends == "unknown" else None
        self.assertIn("Nothing was posted: start it again.", posting.recent()[0]["error_message"])
        self.assertIn("the upload stopped on a RuntimeError", err.getvalue())
        self.assertIn("in broken", err.getvalue())  # where it happened
        self.assertNotIn(token, err.getvalue())     # but not what it said


class OnlyOnce(ApiPosting):
    """What may not be posted twice, whichever way the first post went."""

    def test_a_post_under_way_with_the_extension_blocks_one_through_the_api(self):
        w = self.window()
        (job,) = self.post(w, "youtube")["jobs"]
        made = self.api_post(w)
        self.assertEqual((made["started"], [b["reason"] for b in made["blocked"]]), (False, ["in_progress"]))
        self.assertIn("already under way", w.toasts()[-1])
        self.assertEqual(self.rows(w)["youtube"]["actions"], ["cancel"])  # nor is it offered
        self.assertEqual(self.api_post(w, force=True)["started"], False)  # not even by saying "again"
        self.work(job["id"], "ready", "posted", post_id="dQw4w9WgXcQ", url="https://youtu.be/dQw4w9WgXcQ")
        w.svc.jobs_changed(everywhere=True)
        made = self.api_post(w)
        self.assertEqual([b["reason"] for b in made["blocked"]], ["posted"])
        self.assertEqual(self.google.starts, [])
        info = w.call("youtube_api_info", CAPTION, TAGS)
        self.assertEqual((info["can_post"], info["force"], info["blocked"]["reason"]), (True, True, "posted"))
        self.assertEqual(self.rows(w)["youtube"]["api"], {"force": True})
        again = self.api_post(w, force=True)  # your word that it should go up once more
        self.assertEqual(self.over(w, again["jobs"][0]["id"])["state"], "posted")
        self.assertEqual(posting.get(job["id"])["superseded"], 1)

    def test_a_post_a_stats_read_found_blocks_one_through_the_api(self):
        episode = db.create_episode(CAPTION, str(self.video))
        db.add_post("youtube", "abcdefghijk", episode, CAPTION, "https://youtu.be/abcdefghijk")
        w = self.window()
        w.svc.reload()
        w.call("form_changed", CAPTION, TAGS)
        row = self.rows(w)["youtube"]
        self.assertEqual((row["state"], row["actions"], row["api"]),
                         ("spotted", ["open_post", "post_again"], {"force": True}))  # again: only by the question
        made = self.api_post(w)
        self.assertEqual((made["started"], made["blocked"][0]["reason"], made["blocked"][0]["url"]),
                         (False, "spotted", "https://youtu.be/abcdefghijk"))
        self.assertEqual(self.google.starts, [])
        self.assertEqual(self.over(w, self.api_post(w, force=True)["jobs"][0]["id"])["state"], "posted")

    def test_a_post_through_the_api_blocks_the_extension_and_itself(self):
        w = self.window()
        self.google.hold = threading.Event()
        job_id = self.api_post(w)["jobs"][0]["id"]
        self.assertTrue(w.until(lambda: self.rows(w)["youtube"]["state"] == "uploading"))
        made = self.post(w, "youtube", "tiktok")  # the extension's Post, with YouTube ticked
        self.assertEqual(([j["platform"] for j in made["jobs"]], [b["reason"] for b in made["blocked"]]),
                         (["tiktok"], ["in_progress"]))
        self.google.hold.set()
        self.assertEqual(self.over(w, job_id)["state"], "posted")
        self.assertEqual([b["reason"] for b in self.post(w, "youtube")["blocked"]], ["posted"])
        self.assertEqual([b["reason"] for b in self.api_post(w)["blocked"]], ["posted"])
        self.assertEqual((len(self.google.starts), len(self.google.videos)), (1, 1))

    def test_the_extension_is_never_handed_an_api_job(self):
        job = self.job()
        with self.assertRaises(posting.Refused) as caught:
            posting.claim(job["id"], "install-chrome")
        self.assertEqual(caught.exception.code, "not_claimable")

    def test_two_videos_take_turns(self):
        other = self.root / "videos" / "ep02.mp4"
        other.write_bytes(BYTES[::-1])
        w = self.window()
        w.call("pick_file", str(self.video))
        self.google.hold = threading.Event()
        first = self.api_post(w)["jobs"][0]["id"]
        self.assertTrue(w.until(lambda: self.rows(w)["youtube"]["state"] == "uploading"))
        w.call("pick_file", str(other))
        second = self.api_post(w)["jobs"][0]["id"]
        w.pump(0.5)
        self.assertEqual(posting.get(second)["state"], "queued")  # it waits for the first to finish
        self.assertEqual(plain(self.rows(w)["youtube"]["line"]).split("  ")[0], "● about to start")
        self.assertEqual(len(self.google.starts), 1)
        self.google.hold.set()
        self.assertEqual((self.over(w, first)["state"], self.over(w, second)["state"]), ("posted", "posted"))
        self.assertEqual(sorted(bytes(v["data"]) for v in self.google.videos), sorted([BYTES, BYTES[::-1]]))

    def test_one_that_waited_too_long_for_its_turn_never_starts(self):
        self.connect_api()
        job = self.job()
        self.age(job, 11, "created_at")
        self.assertEqual(posting.expire_stale(), 1)
        self.assertEqual(youtube_api_tab.run(job["id"])["state"], "expired")
        self.assertEqual(self.google.starts, [])


class AfterAQuit(ApiPosting):
    """ReelForge closed in the middle of an upload: the next start, in any window, ends the job."""

    def setUp(self) -> None:
        super().setUp()
        self.connect_api()

    def quit_before_the_last_piece(self) -> dict:
        job = self.job()
        youtube_api_tab.run(job["id"], stop=lambda: len(self.google.puts) >= 2)
        return posting.get(job["id"])

    def quit_on_the_last_piece(self, arrived: bool = True) -> dict:
        """The last piece went out and ReelForge was gone before any answer."""
        job = self.job()
        self.google.script = [None, None, None, "lost" if arrived else "drop"]
        with mock.patch.object(youtube_api, "_where", side_effect=Died), self.assertRaises(Died):
            youtube_api_tab.run(job["id"])
        return posting.get(job["id"])

    def test_before_the_last_piece_the_job_is_left_uploading_with_its_session_on_file(self):
        job = self.quit_before_the_last_piece()
        self.assertEqual((job["state"], round(job["progress"], 2)), ("uploading", round(2000 / SIZE, 2)))
        self.assertEqual(youtube_api.session(job["id"])["size"], SIZE)
        self.assertEqual([j["id"] for j in youtube_api_tab.orphans()], [job["id"]])

    def test_the_next_start_fails_it_because_nothing_can_have_been_posted(self):
        job = self.quit_before_the_last_piece()
        self.assertEqual(youtube_api_tab.settle_orphans(), 1)
        done = posting.get(job["id"])
        self.assertEqual((done["state"], done["error_code"]), ("failed", "interrupted"))
        self.assertEqual(done["error_message"], "ReelForge was closed during the upload. Nothing was posted: start "
                                                "it again.")
        self.assertIsNone(youtube_api.session(job["id"]))
        self.assertEqual((len(self.google.starts), self.google.videos), (1, []))  # it asked nothing and sent nothing
        self.assertEqual(youtube_api_tab.settle_orphans(), 0)

    def test_a_window_that_opens_does_it(self):
        job = self.quit_before_the_last_piece()
        self.reopen()
        w = self.window()
        w.svc.mind_the_jobs()  # what a window does when it starts, and every half minute
        self.assertTrue(w.until(lambda: self.rows(w)["youtube"]["state"] == "failed"))
        self.assertIn("ReelForge was closed during the upload", plain(self.rows(w)["youtube"]["line"]))
        self.assertEqual(self.rows(w)["youtube"]["actions"], ["retry", "connect_api"])
        w.call("post_action", job["id"], "retry")  # not before this window is connected
        self.assertEqual((w.toasts()[-1], len(posting.recent())), (AGAIN["session"], 1))
        w.call("post_action", job["id"], "connect_api")
        self.assertTrue(w.until(lambda: self.rows(w)["youtube"]["actions"] == ["retry", "post_api"]))
        w.call("post_action", job["id"], "retry")
        (again,) = [j for j in posting.recent() if j["id"] != job["id"]]
        self.assertEqual(self.over(w, again["id"])["state"], "posted")
        self.assertEqual(len(self.google.videos), 1)

    def test_on_the_last_piece_a_reelforge_opened_since_has_no_sign_in_to_ask_with(self):
        job = self.quit_on_the_last_piece()
        self.reopen()
        puts = len(self.google.puts)
        self.assertEqual(youtube_api_tab.settle_orphans(), 1)
        done = posting.get(job["id"])
        self.assertEqual((done["state"], len(self.google.puts)), ("unknown", puts))  # nothing was asked, or sent
        self.assertEqual(done["detail"], "ReelForge was closed while the last piece was going up, and the sign-in to "
                                         "YouTube's API went with it, so ReelForge couldn't ask YouTube whether it "
                                         "arrived. Check YouTube Studio before trying again.")
        self.assertIsNone(youtube_api.session(job["id"]))
        self.assertEqual((len(self.google.starts), youtube_api_tab.settle_orphans()), (1, 0))

    def test_on_the_last_piece_a_connected_reelforge_asks_the_saved_session_and_finished_means_posted(self):
        job = self.quit_on_the_last_piece()  # (another window of this channel is still open and connected)
        self.assertEqual(job["state"], "posting")
        self.assertEqual(youtube_api_tab.settle_orphans(), 1)
        done = posting.get(job["id"])
        self.assertEqual((done["state"], done["post_id"], done["url"]), ("posted", "vid00000001", LINK))
        self.assertEqual(self.google.puts[-1]["range"], f"bytes */{SIZE}")  # it asked; it sent nothing
        self.assertEqual((len(self.google.starts), len(self.google.videos)), (1, 1))
        self.assertIsNone(youtube_api.session(job["id"]))
        self.assertEqual([p["post_id"] for p in db.all_posts()], ["vid00000001"])

    def test_on_the_last_piece_and_youtube_cannot_be_asked_is_unknown(self):
        job = self.quit_on_the_last_piece()
        self.google.then = "drop"
        self.assertEqual(youtube_api_tab.settle_orphans(), 1)
        done = posting.get(job["id"])
        self.assertEqual(done["state"], "unknown")
        self.assertEqual(done["detail"], "ReelForge was closed while the last piece was going up, and YouTube can't "
                                         "say whether it arrived. Check YouTube Studio before trying again.")
        self.assertEqual(len(self.google.starts), 1)  # never a second upload
        self.assertEqual(youtube_api_tab.settle_orphans(), 0)

    def test_on_the_last_piece_which_never_arrived_is_unknown_too(self):
        job = self.quit_on_the_last_piece(arrived=False)
        self.assertEqual(youtube_api_tab.settle_orphans(), 1)
        self.assertEqual(posting.get(job["id"])["state"], "unknown")  # the cautious answer: you look, then say
        self.assertEqual((len(self.google.starts), self.google.videos), (1, []))

    def test_on_the_last_piece_with_the_connection_gone_since_is_unknown(self):
        job = self.quit_on_the_last_piece()
        youtube_api.disconnect()
        puts = len(self.google.puts)
        self.assertEqual(youtube_api_tab.settle_orphans(), 1)
        self.assertEqual((posting.get(job["id"])["state"], len(self.google.puts)), ("unknown", puts))

    def test_the_clock_giving_up_first_does_not_stop_the_session_being_asked(self):
        job = self.quit_on_the_last_piece()
        self.age(job, 20)
        self.assertEqual(posting.expire_stale(), 1)  # a quarter of an hour of silence
        self.assertEqual(posting.get(job["id"])["state"], "unknown")
        self.assertEqual(youtube_api_tab.settle_orphans(), 1)
        done = posting.get(job["id"])
        self.assertEqual((done["state"], done["post_id"], done["url"]), ("posted", "vid00000001", LINK))
        self.assertEqual([p["post_id"] for p in db.all_posts()], ["vid00000001"])
        # And when the session can't say, it stays unknown and isn't asked again.
        other = self.root / "videos" / "ep02.mp4"
        other.write_bytes(BYTES[::-1])
        self.video = other
        job = self.quit_on_the_last_piece()
        self.age(job, 20)
        posting.expire_stale()
        self.google.then = "drop"
        self.assertEqual(youtube_api_tab.settle_orphans(), 1)
        self.assertEqual(posting.get(job["id"])["state"], "unknown")
        self.assertEqual(youtube_api_tab.settle_orphans(), 0)

    def test_an_upload_another_reelforge_is_still_sending_is_left_alone(self):
        job = self.quit_before_the_last_piece()
        theirs = youtube_api.upload_lock()  # as a ReelForge in the middle of it holds
        self.assertTrue(theirs.acquire())
        try:
            self.assertEqual(youtube_api_tab.settle_orphans(), 0)
            self.assertEqual(posting.get(job["id"])["state"], "uploading")
        finally:
            theirs.release()
        self.assertEqual(youtube_api_tab.settle_orphans(), 1)

    def test_where_files_cannot_be_locked_only_a_long_silence_counts(self):
        job = self.quit_before_the_last_piece()
        with mock.patch.object(youtube_api.sync, "_lock", side_effect=OSError(38, "no locks on this drive")):
            self.assertEqual(youtube_api_tab.settle_orphans(), 0)
            self.assertEqual(posting.get(job["id"])["state"], "uploading")
            self.age(job, 11)
            self.assertEqual(youtube_api_tab.settle_orphans(), 1)
        self.assertEqual(posting.get(job["id"])["state"], "failed")

    def test_a_job_that_never_started_is_left_to_the_clock(self):
        job = self.job()
        self.assertEqual((youtube_api_tab.orphans(), youtube_api_tab.settle_orphans()), ([], 0))
        self.assertEqual(posting.get(job["id"])["state"], "queued")

    def test_a_database_too_busy_to_note_the_post_does_not_lose_it(self):
        job = self.job()
        real = posting.advance

        def busy(job_id, state, **kw):
            if state == "posted":
                raise sqlite3.OperationalError("database is locked")
            return real(job_id, state, **kw)

        with mock.patch.object(posting, "advance", busy):
            left = youtube_api_tab.run(job["id"])
        self.assertEqual(left["state"], "posting")  # it is up at YouTube, and the ledger couldn't be told
        self.assertEqual(youtube_api.session(job["id"])["size"], SIZE)
        self.assertEqual(youtube_api_tab.settle_orphans(), 1)  # the next look asks the session and finishes it
        done = posting.get(job["id"])
        self.assertEqual((done["state"], done["post_id"]), ("posted", "vid00000001"))
        self.assertEqual((len(self.google.starts), len(self.google.videos)), (1, 1))

    def test_a_post_that_finished_after_its_job_was_called_off_still_counts_as_up(self):
        job = self.quit_on_the_last_piece()
        posting.resolve(job["id"], False)  # "It didn't post", said too soon
        made = {"id": "vid00000001", "privacy": "private", "made_for_kids": False, "upload_status": "uploaded",
                "reason": "", "channel_id": CHANNEL["id"], "channel_title": CHANNEL["title"]}
        done = youtube_api_tab.finish(job, made)
        self.assertEqual(done["state"], "cancelled")
        self.assertEqual([p["post_id"] for p in db.all_posts()], ["vid00000001"])
        blocked = posting.create_batch(self.video, ["youtube"], CAPTION, TAGS, self.cfg, via={"youtube": "api"})
        self.assertEqual([b["reason"] for b in blocked["blocked"]], ["spotted"])  # so it isn't posted twice


class NothingSecretShows(ApiPosting):
    """Everything the window is sent and answered through connecting, posts that end every way, a sign-in that
    runs out and a disconnect, and everything kept in the stats database: none of it carries the client's id or
    secret, a token, a code or a session's address."""

    def test_through_the_window(self):
        err = io.StringIO()
        answers = []
        with contextlib.redirect_stderr(err):
            w = self.window(api=False)
            answers.append(w.call("youtube_api_connect", str(self.client_file)))
            self.assertTrue(w.until(lambda: w.view("youtube_api")["state"] == "on"))
            consent = w.svc.api_consent
            answers += [w.call("youtube_api_info", CAPTION, TAGS), w.call("youtube_api_defaults", "public")]
            self.posted(w)
            for script, then, start in (([None, "503", "401"], None, None), ([None, None, None, "lost"], "drop", None),
                                        ([None, "drop"], "drop", None), ([], None, (403, "somethingNew")),
                                        ([], None, (403, "quotaExceeded"))):
                self.google.script, self.google.then = list(script), then
                self.google.start_script = [start] if start else []
                made = self.api_post(w, "", True)
                answers.append(made)
                job = self.over(w, made["jobs"][0]["id"])
                if job["state"] == "unknown":
                    w.call("post_action", job["id"], "not_posted")
            self.google.then = None
            secret = [CLIENT_ID, CLIENT_SECRET, consent.state, consent.verifier, *self.google.secrets()]
            self.google.end_grant()
            youtube_api._access.clear()
            answers.append(self.api_post(w, "", True))
            self.over(w, answers[-1]["jobs"][0]["id"])
            answers += [w.call("youtube_api_info", CAPTION, TAGS), w.call("youtube_api_connect", "")]
            self.assertTrue(w.until(lambda: w.view("youtube_api")["state"] == "on"))
            secret += [w.svc.api_consent.state, w.svc.api_consent.verifier, *self.google.secrets()]
            w.call("youtube_api_disconnect")
            self.assertTrue(w.until(lambda: w.view("youtube_api")["state"] == "off"))
        sent = json.dumps([w.loop.events, answers], default=str)
        kept = b"".join(p.read_bytes() for p in self.root.glob("stats.db*"))
        self.assertGreater(len(set(secret)), 12)
        self.assertGreater(len(w.sent("youtube_api")), 5)
        self.assertEqual({j["state"] for j in posting.recent()}, {"posted", "failed", "cancelled"})
        for value in set(secret):
            self.assertNotIn(value, sent)
            self.assertNotIn(value, err.getvalue())
            self.assertNotIn(value.encode(), kept)
        self.assertEqual(err.getvalue(), "")


if __name__ == "__main__":
    import unittest

    unittest.main()
