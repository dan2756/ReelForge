"""Posting to YouTube through its own API as the window shows and starts it (dashboard.youtube_api_tab): the
youtube_api view and the calls behind its buttons, a window that has just opened and has to connect before it can
post, a post from your press to "posted" with what YouTube's answer says, every way one ends (failed, unknown,
called off), the same rules as the extension's posts about what may not be posted twice, an upload a ReelForge
closed in the middle of, and that nothing secret is ever sent to the window.
On a scratch channel; "Google" is the stand-in in dashboard/tests/google.py and "the browser" follows its consent
page (test_youtube_api.google_for)."""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from unittest import mock

from dashboard import db, posting, posting_tab, youtube_api_tab
from dashboard.platforms import Account, Post, youtube_api
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

    def connect_api(self, kids: bool | None = False) -> None:
        """Connect, as a ReelForge whose person has said their videos are not made for kids (kids: that answer;
        None: they haven't given one)."""
        consent = youtube_api.connect(self.client_file)
        self.assertTrue(consent.done.wait(10))
        self.assertTrue(consent.outcome["ok"])
        if kids is not None:
            youtube_api.set_defaults(made_for_kids=kids)

    def window(self, api: bool = True, **options) -> Window:
        if api and not self.api_store.exists():
            self.connect_api()
        w = super().window(**options)
        self.addCleanup(self.finish, w)  # (before the window closes: no upload outlives its test)
        # On another tab than Publish, where a pop-up says how an upload ended. (On Publish the video's row does.)
        w.svc.tab = "overview"
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

    @staticmethod
    def yes_to(info: dict) -> dict:
        """What the window sends back with a yes: what its question (youtube_api_info) showed."""
        return {"video": info["video"]["path"], "sha256": info["video"]["sha256"],
                "made_for_kids": info["send"]["made_for_kids"]}

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
                         ("off", False, None, "private", None))
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
        # Your two choices are the first rows: they show without scrolling in the smallest window.
        self.assertEqual([(f["label"], f["text"]) for f in info["fields"]],
                         [("Who can see it", "Private"), ("Made for kids", "No"), ("Title", TITLE),
                          ("Description", TAGS), ("Category", "People & Blogs")])
        self.assertEqual([f["key"] for f in info["fields"]], list(info["send"]))  # each row is one thing sent
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
        # (Its first piece says it alone, for the Publish tab's line in a window with no room for the rest.)
        self.assertEqual(view["line"][0][0], "○ YouTube API: not connected")
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
                         {"client_id": CLIENT_ID, "client_secret": CLIENT_SECRET, "made_for_kids": False,
                          "channel": {"id": CHANNEL["id"], "title": CHANNEL["title"]}})  # still no sign-in on file

    def test_until_you_answer_no_made_for_kids_answer_is_sent_and_everything_says_so(self):
        self.connect_api(kids=None)
        w = self.window()
        self.assertIsNone(w.view("youtube_api")["made_for_kids"])
        info = w.call("youtube_api_info", CAPTION, TAGS)
        self.assertIsNone(info["send"]["made_for_kids"])
        self.assertEqual(info["fields"][1], {"key": "made_for_kids", "label": "Made for kids",
                                             "text": "Not sent: you haven't answered, so your channel's own setting "
                                                     "applies"})
        job = self.posted(w)
        self.assertEqual(self.google.starts[0]["body"]["status"], {"privacyStatus": "private"})  # as the question said
        self.assertEqual(json.loads(job["options"]), {"privacy": "private", "made_for_kids": None})
        self.assertEqual((job["state"], job["step"], job["detail"]),
                         ("posted", "note", "ReelForge sent no made-for-kids answer, so your channel's own audience "
                                            "setting applies. YouTube has it as not made for kids."))
        said = self.rows(w)["youtube"]["line"][-1]  # said quietly, with the rest: nothing is wrong
        self.assertEqual(said[0], "  It is private, as you chose. Change it in YouTube Studio. " + job["detail"])
        self.assertNotIn(youtube_api_tab.AMBER, said[1])
        self.assertEqual(w.sent("toast")[-1]["severity"], "information")
        # Once you have answered, your answer goes with every post, and there is nothing to add.
        self.assertIs(w.call("youtube_api_defaults", None, False)["made_for_kids"], False)
        job = self.over(w, self.api_post(w, force=True)["jobs"][0]["id"])
        self.assertEqual(self.google.starts[1]["body"]["status"],
                         {"privacyStatus": "private", "selfDeclaredMadeForKids": False})
        self.assertEqual((job["step"], job["detail"]), (None, None))

    def test_a_video_youtube_calls_made_for_kids_without_your_answer_is_something_to_look_at(self):
        self.connect_api(kids=None)
        self.google.channel_kids = True  # the channel's own audience setting, in YouTube Studio
        w = self.window()
        job = self.posted(w)
        self.assertEqual((job["state"], job["step"]), ("posted", "confirm"))
        self.assertEqual(job["detail"], "YouTube has it as made for kids. ReelForge sent no answer to that, because "
                                        "you haven't given one, so this is your channel's own setting. Check its "
                                        "audience in YouTube Studio.")
        self.assertIn(youtube_api_tab.AMBER, self.rows(w)["youtube"]["line"][-1][1])
        self.assertEqual(w.sent("toast")[-1]["severity"], "warning")

    def test_the_view_has_the_sentences_the_windows_dialog_puts_beside_its_controls(self):
        """The window draws these and writes none of them: what the box for the client file is for, what waiting
        for Google's page means, what Disconnect asks first, and the two choices for posts to come."""
        w = self.window(api=False)
        words = w.view("youtube_api")["words"]
        self.assertEqual(set(words), {"caveat", "file", "waiting", "leave", "start_as", "kids"})
        self.assertEqual((words["caveat"], words["file"]), ("", youtube_api_tab.FILE))  # no post on offer yet
        self.assertIn("Its name starts with client_secret_ and ends in .json.", words["file"])
        self.assertTrue(w.view("youtube_api")["wants_file"])  # nothing to connect with yet
        self.assertIn("You have ten minutes", words["waiting"])
        self.assertEqual(youtube_api.CONSENT_FOR, 10 * 60)  # (the ten minutes that sentence promises)
        self.assertIn("the browser has to be on this computer", words["waiting"])
        self.assertIn("ReelForge never makes it for you", words["kids"])
        self.assertIn("You can choose otherwise for one post, in its question.", words["start_as"])
        # Connected: the Publish tab's line says how far this is tested, since the row now offers a post, and
        # Disconnect says that Google is told.
        self.connect_api()
        w.svc.follow_youtube_api(changed=True)
        words = w.view("youtube_api")["words"]
        self.assertEqual((words["caveat"], w.view("youtube_api")["wants_file"]), ("tested on a stand-in only", False))
        self.assertIn("then tells Google that this window's access has ended", words["leave"])
        # A ReelForge opened since has no sign-in of its own to end, and no post on offer.
        self.reopen()
        w.svc.follow_youtube_api(changed=True)
        words = w.view("youtube_api")["words"]
        view = w.view("youtube_api")
        self.assertEqual((view["state"], words["caveat"], view["wants_file"]), ("again", "", False))  # Connect will do
        # It doesn't say there is nothing to end at Google: an earlier window's sign-in may still be listed there,
        # which is what the pop-up after Disconnect says (youtube_api.NO_GRANT), with the same address.
        self.assertIn("This window is not connected, so it can't end ReelForge's access at Google. If you ever "
                      "allowed it there, take it off yourself at myaccount.google.com/connections.", words["leave"])
        self.assertIn("myaccount.google.com/connections", youtube_api.NO_GRANT)
        for said in words.values():
            self.assertNotIn("—", said)

    def test_a_client_google_no_longer_knows_asks_for_another_client_file(self):
        self.google.start_script = [(403, "quotaExceeded")]
        w = self.window()
        self.posted(w)
        self.google.token_script = ["invalid_client"]  # the client was deleted in Google Cloud
        youtube_api._access.clear()
        self.posted(w)
        view = w.view("youtube_api")
        self.assertEqual((view["state"], view["client"], view["wants_file"]), ("again", True, True))
        self.assertEqual(plain(view["lines"][0]), AGAIN["client"])
        self.assertEqual(view["words"]["file"], youtube_api_tab.GONE_FILE)
        self.assertIn("Google no longer knows the client on file.", view["words"]["file"])

    def test_the_question_says_what_happens_after_your_yes_and_what_again_means(self):
        w = self.window()
        info = w.call("youtube_api_info", CAPTION, TAGS)
        self.assertEqual(info["how"], "ReelForge uploads the video itself, through Google's API, with exactly this. "
                                      "Keep ReelForge open until its row says posted.")
        self.assertEqual(info["again"], "It would be a second video.")
        self.assertEqual(info["words"], w.view("youtube_api")["words"])

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
        # Its row offers no Post with API…, as Post… is greyed out for it: the POST line says why, and the button
        # only opened a question with nothing but Close to press.
        row = self.rows(w)["youtube"]
        self.assertEqual((row["actions"], row.get("api")), ([], None))
        self.assertIn("no review yet", w.view("posting")["why"])
        made = self.api_post(w)
        self.assertEqual((made["started"], [b["reason"] for b in made["blocked"]]), (False, ["review"]))
        self.assertEqual(self.google.starts, [])
        approve(ep, video)
        w.svc.update_posting(fresh=True)
        self.assertEqual(self.rows(w)["youtube"]["actions"], ["post_api"])  # reviewed: the button is there
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

    def test_a_connect_asked_for_in_the_dialog_puts_no_pop_up_over_it_while_the_dialog_can_say_it(self):
        """The dialog for the connection says that Google's page is open, and then that it is connected, with what
        posts start as and how far this is tested. A pop-up saying the same sat on its buttons for twenty seconds.
        So did the one for a client file that wasn't taken: its sentence is the call's answer, for the dialog to
        show under the box."""
        w = self.window(api=False)
        self.assertTrue(w.call("youtube_api_connect", str(self.client_file), True))
        self.assertTrue(w.until(lambda: w.view("youtube_api")["state"] == "on"))
        w.pump(0.2)
        self.assertEqual(w.sent("toast"), [])
        view = w.view("youtube_api")
        self.assertIn("posts start as private", plain(view["line"]))  # what the pop-up would have said
        self.assertEqual(plain(view["lines"][-1]), UNPROVEN)
        # A file that isn't one: the answer says why, and is never True.
        nothing = self.root / "nothing.json"
        self.assertEqual(w.call("youtube_api_connect", str(nothing), True), f"There is no file at {nothing}.")
        web = self.root / "client_secret_web.json"
        web.write_text(json.dumps({"web": {"client_id": CLIENT_ID, "client_secret": CLIENT_SECRET}}), "utf-8")
        self.assertEqual(w.call("youtube_api_connect", str(web), True),
                         "client_secret_web.json is the file of a Web application client. ReelForge needs a Desktop "
                         "app client: make one in Google Cloud and download its JSON.")
        self.assertEqual(w.sent("toast"), [])
        # Asked for anywhere else, the same refusal is a pop-up and the answer is False.
        self.assertIs(w.call("youtube_api_connect", str(nothing)), False)
        self.assertEqual(w.toasts(), [f"There is no file at {nothing}."])
        # What goes wrong once Google's page is open is a pop-up either way: a no there.
        self.google.consent = "no"
        self.assertIs(w.call("youtube_api_connect", "", True), True)
        self.assertTrue(w.until(lambda: "You said no on Google's page" in (w.toasts() or [""])[-1]))
        # Asked for anywhere else (the YouTube row's button), both pop-ups come as before.
        self.google.consent = "yes"
        w.forget()
        w.call("post_action", "youtube", "connect_api")
        self.assertTrue(w.until(lambda: len(w.sent("toast")) == 2))
        self.assertEqual([t["title"] for t in w.sent("toast")], ["Connecting YouTube's API", "YouTube's API"])

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

    def test_while_the_dialog_is_open_it_says_how_connecting_and_disconnecting_went_and_no_pop_up_does(self):
        """A pop-up comes up over the dialog's own buttons (Close, in a window 120 columns by 30 rows), and its first
        click only puts the pop-up away. The dialog tells the Python side when it opens and closes."""
        w = self.window()
        w.call("youtube_api_dialog", True)
        self.assertEqual(w.view("youtube_api")["said"], [])
        w.forget()
        w.call("youtube_api_disconnect")
        self.assertTrue(w.until(lambda: w.view("youtube_api")["state"] == "off" and w.view("youtube_api")["said"]))
        self.assertEqual(plain(w.view("youtube_api")["said"]),
                         "Disconnected. ReelForge has forgotten the connection, and Google has ended it.")
        self.assertEqual(w.sent("toast"), [])
        # A no on Google's page, the same way. A new try starts with nothing said.
        self.google.consent = "no"
        self.assertIs(w.call("youtube_api_connect", str(self.client_file), True), True)
        self.assertTrue(w.until(lambda: "You said no" in plain(w.view("youtube_api")["said"])))
        self.assertEqual(plain(w.view("youtube_api")["said"]), "You said no on Google's page, so nothing was connected.")
        self.assertEqual(w.sent("toast"), [])
        # Closed, the dialog has nothing left to say, and what happens from then on is a pop-up as before.
        w.call("youtube_api_dialog", False)
        self.assertEqual(w.view("youtube_api")["said"], [])
        w.call("youtube_api_disconnect")
        self.assertTrue(w.until(lambda: w.toasts() == ["YouTube's API wasn't connected."]))

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
        self.assertEqual([f["text"] for f in fields[:2]], ["Unlisted", "Yes"])
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
        self.assertIn("It is private, as you chose. Change it in YouTube Studio.", said)
        # A stats read only sees what everyone can: a private video's numbers don't "come with the next refresh".
        self.assertRegex(said, r"^✓ posted  \S+ ago, its numbers come once everyone can see it  It is private")
        toast = w.sent("toast")[-1]
        self.assertEqual((toast["title"], toast["severity"]), ("Posted to YouTube", "information"))
        self.assertTrue(toast["body"].startswith("ep01.mp4: It is private, as you chose."), toast["body"])
        self.assertIn(LINK, toast["body"])
        self.assertIn(f"posted with its API · {LINK}", plain(w.view("uploads")["rows"][0]["cells"][4]))
        # The channel it went to is known from now on, and the upload's session is off the file.
        self.assertEqual(w.view("youtube_api")["channel"], CHANNEL)
        self.assertIn("last upload went to Night Physics", plain(w.view("youtube_api")["line"]))
        self.assertNotIn("sessions", json.loads(self.api_store.read_text("utf-8")))

    def test_a_yes_to_a_question_about_another_video_than_the_picked_one_starts_nothing(self):
        """The window sends back the video its question showed. The list picks another by itself when that one
        leaves it (hidden in another window, here): a yes must not upload the new pick with the old one's title."""
        other = self.root / "videos" / "ep02.mp4"
        other.write_bytes(BYTES[::-1])
        w = self.window()
        w.call("pick_file", str(other))
        info = w.call("youtube_api_info", "The second video", TAGS)
        shown = self.yes_to(info)
        self.assertEqual(shown, {"video": str(other), "sha256": posting.digest(other), "made_for_kids": False})
        db.set_meta("hidden_videos", [str(other)])  # what another window's Hide writes
        w.call("scan_files")                        # ... and what this window does when it sees the change
        self.assertEqual(w.view("posting")["video"]["name"], "ep01.mp4")
        self.assertIsNone(w.call("post_with_api", "The second video", TAGS, "private", False, shown))
        toast = w.sent("toast")[-1]
        self.assertEqual((toast["title"], toast["severity"]), ("Nothing was started", "warning"))
        self.assertEqual(toast["body"], "The question was about ep02.mp4, and ep01.mp4 is the picked video now. "
                                        f"Press {posting_tab.LABELS['post_api']} again to post this one.")
        self.assertEqual((posting.recent(), self.google.starts, db.uploads()), ([], [], []))
        # The same yes about the video that is picked goes ahead.
        made = w.call("post_with_api", CAPTION, TAGS, "private", False,
                      {"video": str(self.video), "sha256": posting.digest(self.video), "made_for_kids": False})
        job = self.over(w, made["jobs"][0]["id"])
        self.assertEqual((job["state"], job["video"], len(self.google.videos)), ("posted", "ep01.mp4", 1))

    def test_a_yes_to_a_question_about_a_file_that_was_replaced_since_starts_nothing(self):
        """A video is its bytes, not its name. The question shows a file, and through the API nobody sees the video
        again before it is up. When another file has that name by the time of the yes (a new render, a copy over
        it), the yes was not to this one. A pipeline video's review stops that by itself; a video dropped into the
        videos folder has no review."""
        w = self.window()
        info = w.call("youtube_api_info", CAPTION, TAGS)
        self.assertEqual((info["review"]["state"], info["can_post"]), ("none", True))
        self.assertEqual(info["video"]["sha256"], hashlib.sha256(BYTES).hexdigest())
        self.video.write_bytes(BYTES[::-1] + b"another cut")
        self.assertIsNone(w.call("post_with_api", CAPTION, TAGS, "private", False, self.yes_to(info)))
        toast = w.sent("toast")[-1]
        self.assertEqual((toast["title"], toast["severity"]), ("Nothing was started", "warning"))
        self.assertEqual(toast["body"], "ep01.mp4 changed after the question was shown: it is another file now, so "
                                        "nothing was sent. Check the video, then press "
                                        f"{posting_tab.LABELS['post_api']} again.")
        self.assertEqual((posting.recent(), self.google.starts, db.uploads()), ([], [], []))
        # A yes that says nothing of the file it was to is no yes to this one either.
        self.assertIsNone(w.call("post_with_api", CAPTION, TAGS, "private", False,
                                 {"video": str(self.video), "made_for_kids": False}))
        self.assertEqual((posting.recent(), self.google.starts), ([], []))
        # Asked again, the question is about the file that is there now, and its yes goes ahead.
        info = w.call("youtube_api_info", CAPTION, TAGS)
        made = w.call("post_with_api", CAPTION, TAGS, "private", False, self.yes_to(info))
        job = self.over(w, made["jobs"][0]["id"])
        self.assertEqual((job["state"], bytes(self.google.videos[0]["data"])), ("posted", BYTES[::-1] + b"another cut"))

    def test_a_made_for_kids_answer_changed_since_the_question_starts_nothing(self):
        """The question shows the made-for-kids answer a post will carry, or that none will be sent. When that is
        another one by the time of the yes (changed in another window), what was shown is not what would be sent."""
        self.connect_api(kids=None)
        w = self.window()
        info = w.call("youtube_api_info", CAPTION, TAGS)
        shown = self.yes_to(info)
        self.assertIsNone(shown["made_for_kids"])  # the question said: none will be sent
        youtube_api.set_defaults(made_for_kids=True)  # another window's choice, on the same file
        self.assertIsNone(w.call("post_with_api", CAPTION, TAGS, "private", False, shown))
        toast = w.sent("toast")[-1]
        self.assertEqual((toast["title"], toast["severity"]), ("Nothing was started", "warning"))
        self.assertIn("Your made-for-kids answer changed after the question was shown, so nothing was sent.",
                      toast["body"])
        self.assertEqual((posting.recent(), self.google.starts), ([], []))
        # Asked again, the question shows the new answer, and a yes to that one is sent with it.
        info = w.call("youtube_api_info", CAPTION, TAGS)
        self.assertEqual(info["fields"][1], {"key": "made_for_kids", "label": "Made for kids", "text": "Yes"})
        made = w.call("post_with_api", CAPTION, TAGS, "private", False, self.yes_to(info))
        self.over(w, made["jobs"][0]["id"])
        self.assertEqual(self.google.starts[0]["body"]["status"],
                         {"privacyStatus": "private", "selfDeclaredMadeForKids": True})

    def test_every_button_of_a_post_that_went_through_the_api_works_from_its_row(self):
        """What the window's row offers for a job whose via is "api", each pressed the way the window presses it
        (post_action with the row's job): Cancel, It didn't post, It's posted and Open. A try that ended without a
        post offers a new post, which is the question and your yes (post_with_api)."""
        w = self.window()
        self.google.hold = threading.Event()
        first = self.api_post(w)["jobs"][0]["id"]
        self.assertTrue(w.until(lambda: self.rows(w)["youtube"]["state"] == "uploading"))
        row = self.rows(w)["youtube"]
        self.assertEqual((row["via"], row["job"], row["actions"]), ("api", first, ["cancel"]))
        w.call("post_action", row["job"], "cancel")
        self.google.hold.set()
        self.assertEqual(self.over(w, first)["state"], "cancelled")
        self.google.hold = None
        # A new try, with the last answer lost for good this time: nobody knows whether it posted.
        self.google.script, self.google.then = [None, None, None, "lost"], "drop"
        row = self.rows(w)["youtube"]
        self.assertEqual((row["via"], row["job"], row["actions"]), ("api", first, ["post_api"]))
        second = self.posted(w)
        self.assertEqual((second["state"], second["via"]), ("unknown", "api"))
        row = self.rows(w)["youtube"]
        self.assertEqual((row["via"], row["job"], row["actions"]),
                         ("api", second["id"], ["mark_posted", "not_posted"]))
        w.call("post_action", row["job"], "not_posted")
        self.assertEqual(self.rows(w)["youtube"]["state"], "cancelled")
        # Once more, and this one goes up: Open opens the video's own link.
        self.google.script, self.google.then = [], None
        third = self.posted(w)
        row = self.rows(w)["youtube"]
        self.assertEqual((third["state"], row["via"], row["url"], row["actions"]),
                         ("posted", "api", third["url"], ["open_post", "post_again"]))
        opened = len(self.opened)
        w.call("post_action", row["job"], "open_post")
        self.assertEqual(self.opened[opened:], [third["url"]])
        self.assertEqual(len(self.google.videos), 2)  # the one that was lost on its last piece, and this one
        # One that ReelForge lost track of and you found in YouTube Studio: It's posted.
        other = self.root / "videos" / "ep02.mp4"
        other.write_bytes(BYTES[::-1])
        w.call("pick_file", str(other))
        self.google.script, self.google.then = [None, None, None, "lost"], "drop"
        lost = self.posted(w)
        self.assertEqual(lost["state"], "unknown")
        w.call("post_action", self.rows(w)["youtube"]["job"], "mark_posted")
        row = self.rows(w)["youtube"]
        self.assertEqual((row["state"], row["via"], row["actions"]), ("posted", "api", ["post_again"]))  # no link

    def test_a_video_that_may_be_up_by_hand_goes_through_the_api_only_as_a_post_again(self):
        """BY HAND's YouTube button opened the upload page. A post made there shows nowhere here until a stats read
        finds it: the row offers no plain Post with API…, and the question says what a yes would mean."""
        w = self.window()
        w.call("tab_opened", "publish")
        w.call("pick_file", str(self.video))
        self.assertEqual(self.rows(w)["youtube"]["actions"], ["post_api"])
        w.call("open_upload", "youtube", CAPTION, TAGS)
        row = self.rows(w)["youtube"]
        self.assertEqual((row["state"], row["actions"], row["api"]), ("none", [], {"force": True}))
        info = w.call("youtube_api_info", CAPTION, TAGS)
        self.assertEqual((info["can_post"], info["force"], info["blocked"]["reason"]), (True, True, "by_hand"))
        self.assertEqual(info["again"], "If you posted it there, this would be a second video.")
        refused = w.call("post_with_api", CAPTION, TAGS, "", False, self.yes_to(info))
        self.assertEqual((refused["started"], [b["reason"] for b in refused["blocked"]]), (False, ["by_hand"]))
        self.assertEqual(self.google.videos, [])
        made = w.call("post_with_api", CAPTION, TAGS, "", True, self.yes_to(info))  # "Post it again"
        self.assertEqual(self.over(w, made["jobs"][0]["id"])["state"], "posted")

    def test_a_cancel_that_arrives_with_the_last_piece_on_its_way_ends_nothing(self):
        """The row still showed Cancel (the window redraws once a second) when the last piece went out. A job that
        click freed would be offered again as if nothing had been sent, with the video maybe up: a second one."""
        w = self.window()
        w.call("tab_opened", "publish")
        w.call("pick_file", str(self.video))
        at_google, let_go = threading.Event(), threading.Event()
        self.addCleanup(let_go.set)

        def last_piece(before: int) -> None:
            if before == 3:  # the fourth of four pieces: YouTube makes the video with it
                at_google.set()
                let_go.wait(10)

        self.google.script, self.google.then, self.google.on_put = [None, None, None, "lost"], "drop", last_piece
        job_id = self.api_post(w)["jobs"][0]["id"]
        self.assertTrue(at_google.wait(10))
        self.assertEqual(posting.get(job_id)["state"], "posting")
        w.forget()
        w.call("post_action", job_id, "cancel")
        self.assertEqual(posting.get(job_id)["state"], "posting")
        self.assertEqual(w.toasts()[-1], "The last piece of the video is already on its way, so this can't be "
                                         "called off any more. Its row says how it ended.")
        w.call("post_action", job_id, "not_posted")  # nor is "It didn't post" anyone's to say yet
        self.assertEqual(posting.get(job_id)["state"], "posting")
        let_go.set()
        self.google.on_put = None
        self.assertEqual(self.over(w, job_id)["state"], "unknown")  # the answer was lost: nobody knows
        self.assertEqual(len(self.google.videos), 1)
        row = self.rows(w)["youtube"]
        self.assertEqual((row["state"], row["actions"]), ("unknown", ["mark_posted", "not_posted"]))
        info = w.call("youtube_api_info", CAPTION, TAGS)
        self.assertEqual((info["blocked"]["reason"], info["force"]), ("unknown", True))  # only as a post again
        self.google.script, self.google.then = [], None
        again = w.call("post_with_api", CAPTION, TAGS, "", False, self.yes_to(info))
        self.assertEqual((again["started"], [b["reason"] for b in again["blocked"]]), (False, ["unknown"]))
        self.assertEqual(len(self.google.videos), 1)

    def test_on_the_publish_tab_the_row_says_how_it_ended_and_no_pop_up_covers_it(self):
        """A pop-up with the row's own words sat on the row's buttons for twenty seconds. With the Publish tab
        open on that video, the row says it. Anywhere else the pop-up is how you hear of it."""
        other = self.root / "videos" / "ep02.mp4"
        other.write_bytes(BYTES[::-1])
        w = self.window()
        w.call("tab_opened", "publish")
        w.call("pick_file", str(self.video))
        w.forget()
        self.assertEqual(self.posted(w)["state"], "posted")
        self.assertEqual(w.sent("toast"), [])
        self.assertIn("It is private, as you chose.", plain(self.rows(w)["youtube"]["line"]))
        # A refusal, and a post nobody is sure of: the same.
        w.call("pick_file", str(other))
        self.google.start_script = [(403, "quotaExceeded")]
        self.assertEqual(self.posted(w)["state"], "failed")
        self.assertEqual(w.sent("toast"), [])
        self.assertIn("× failed: Your Google Cloud project has used up", plain(self.rows(w)["youtube"]["line"]))
        self.google.script, self.google.then = [None, None, None, "lost"], "drop"
        self.assertEqual(self.posted(w)["state"], "unknown")
        self.assertEqual(w.sent("toast"), [])
        # With another video picked by the time it ends, or another tab open, the pop-up says it.
        w.call("post_action", self.rows(w)["youtube"]["job"], "not_posted")
        self.google.script, self.google.then, self.google.hold = [], None, threading.Event()
        self.assertTrue(self.api_post(w)["started"])
        self.assertTrue(w.until(lambda: self.rows(w)["youtube"]["state"] == "uploading"))
        w.call("pick_file", str(self.video))
        self.google.hold.set()
        self.assertTrue(w.until(lambda: [t["title"] for t in w.sent("toast")] == ["Posted to YouTube"], 15))

    def test_a_post_again_whose_row_does_not_say_how_it_ended_is_said_in_a_pop_up(self):
        """On the Publish tab the video's row says how an upload ended, so no pop-up does. Not after a post again
        that failed: the post that is up has the row again, and the row says "posted" and nothing of the try."""
        w = self.window()
        w.call("tab_opened", "publish")
        w.call("pick_file", str(self.video))
        first = self.posted(w)
        w.forget()
        self.google.start_script = [(403, "quotaExceeded")]
        again = self.over(w, self.api_post(w, force=True)["jobs"][0]["id"])
        row = self.rows(w)["youtube"]
        self.assertEqual((again["state"], row["state"], row["job"]), ("failed", "posted", first["id"]))
        self.assertNotIn("failed", plain(row["line"]))
        (toast,) = w.sent("toast")
        self.assertEqual((toast["title"], toast["severity"], toast["body"]),
                         ("YouTube: not posted", "warning", f"ep01.mp4: {again['error_message']}"))  # which video
        self.assertIn("used up today's YouTube API allowance", toast["body"])
        # One that goes up, while the row shows the first post's numbers from a stats read: the row has no room
        # for what the new post has to add (how YouTube stored it), so the pop-up says it, with the new link.
        db.record_snapshot("youtube", Account("@me", 1_000), [Post("vid00000001", CAPTION, LINK, db.now_iso(),
                                                                   views=12)])
        w.svc.reload()
        w.forget()
        self.google.stores_as = "private"
        second = self.over(w, self.api_post(w, "public", force=True)["jobs"][0]["id"])
        row = self.rows(w)["youtube"]
        self.assertEqual((second["state"], second["step"], row["state"]), ("posted", "confirm", "posted"))
        self.assertIn("12 views", plain(row["line"]))
        self.assertNotIn("YouTube stored it", plain(row["line"]))
        (toast,) = w.sent("toast")
        self.assertEqual((toast["title"], toast["severity"]), ("Posted to YouTube", "warning"))
        self.assertIn("YouTube stored it as private, not public as you chose.", toast["body"])
        self.assertIn("https://youtube.com/shorts/vid00000002", toast["body"])
        # A video a stats read found, posted again without success: its row stays "posted" too.
        other = self.root / "videos" / "ep02.mp4"
        other.write_bytes(BYTES[::-1])
        db.add_post("youtube", "abcdefghijk", db.create_episode("The second video", str(other)), "The second video",
                    "https://youtu.be/abcdefghijk")
        w.svc.reload()
        w.call("pick_file", str(other))
        w.forget()
        self.google.start_script = [(403, "quotaExceeded")]
        again = self.over(w, self.api_post(w, force=True)["jobs"][0]["id"])
        self.assertEqual((again["state"], self.rows(w)["youtube"]["state"]), ("failed", "spotted"))
        self.assertEqual([t["title"] for t in w.sent("toast") if "YouTube" in t["title"]], ["YouTube: not posted"])

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
        self.assertEqual(w.sent("toast")[-1]["body"], f"ep01.mp4: It is up. {LINK}")

    def test_what_the_read_back_says(self):
        made = {"privacy": "private", "made_for_kids": False, "for_kids": False}
        self.assertEqual(youtube_api_tab.read_back("private", False, made), "")  # as it was sent: nothing to say
        self.assertEqual(youtube_api_tab.read_back("unlisted", False, {**made, "privacy": "unlisted"}), "")
        self.assertIn("stored it as private, not unlisted", youtube_api_tab.read_back("unlisted", False, made))
        unsaid = youtube_api_tab.read_back("public", False, {**made, "privacy": None})
        self.assertEqual(unsaid, "YouTube didn't say who can see it: check that in YouTube Studio.")
        kids = {"privacy": "public", "made_for_kids": True, "for_kids": True}
        said = youtube_api_tab.read_back("public", False, kids)
        self.assertEqual(said, "YouTube has it as made for kids, which isn't what ReelForge sent. Check its audience "
                               "in YouTube Studio.")
        self.assertEqual(youtube_api_tab.read_back("public", True, {**kids, "made_for_kids": None}), "")
        # With no answer of yours sent, there is nothing to compare: only "made for kids" is worth a look.
        unsaid = {"privacy": "public", "made_for_kids": None, "for_kids": False}
        self.assertEqual(youtube_api_tab.read_back("public", None, unsaid), "")
        self.assertIn("ReelForge sent no answer to that", youtube_api_tab.read_back("public", None, kids))
        self.assertEqual(youtube_api_tab.left_to_youtube(False, made), "")
        self.assertEqual(youtube_api_tab.left_to_youtube(None, {**unsaid, "for_kids": None}),
                         "ReelForge sent no made-for-kids answer, so your channel's own audience setting applies.")

    def test_an_upload_youtube_takes_and_then_turns_down_is_not_a_post(self):
        self.google.upload_status = "rejected"
        w = self.window()
        job = self.posted(w)
        self.assertEqual((job["state"], job["error_code"], job["post_id"]), ("failed", "platform_error", None))
        self.assertEqual(job["error_message"], "YouTube took the upload and rejected it (duplicate). Look at it in "
                                               "YouTube Studio.")
        self.assertEqual(db.all_posts(), [])

    def test_an_ok_that_names_no_video_is_never_called_posted(self):
        # In answer to the first piece: one of four has gone, so the upload carries on and ends with its id.
        self.google.script = [(200, "notAVideo")]
        w = self.window()
        job = self.posted(w)
        self.assertEqual((job["state"], job["post_id"], job["url"], job["detail"]),
                         ("posted", "vid00000001", LINK, None))
        self.assertEqual((len(self.google.videos), bytes(self.google.videos[0]["data"])), (1, BYTES))
        # In answer to the last piece, and to every question after it: nobody knows, and nothing says "posted".
        self.google.script, self.google.then = [None, None, None, (200, "notAVideo")], (200, "notAVideo")
        job = self.over(w, self.api_post(w, force=True)["jobs"][0]["id"])
        self.assertEqual((job["state"], job["post_id"], job["url"]), ("unknown", None, None))
        self.assertEqual(self.rows(w)["youtube"]["actions"], ["mark_posted", "not_posted"])

    def test_a_refusal_fails_the_job_in_a_sentence_and_the_row_offers_a_new_post(self):
        self.google.start_script = [(403, "quotaExceeded")]
        w = self.window(connect=False)  # no browser extension anywhere: this way of posting needs none
        job = self.posted(w)
        self.assertEqual((job["state"], job["error_code"]), ("failed", "quota"))
        self.assertEqual(job["error_message"], "Your Google Cloud project has used up today's YouTube API allowance. "
                                               "It starts again at midnight Pacific time. Nothing was posted.")
        row = self.rows(w)["youtube"]
        self.assertEqual((row["state"], row["via"], row["actions"]), ("failed", "api", ["post_api"]))
        self.assertIn("× failed: Your Google Cloud project has used up today's", plain(row["line"]))
        toast = w.sent("toast")[-1]
        self.assertEqual((toast["title"], toast["severity"]), ("YouTube: not posted", "warning"))
        self.assertEqual((self.google.sessions, db.all_posts()), ({}, []))
        again = self.posted(w)
        self.assertEqual((again["state"], again["via"]), ("posted", "api"))
        self.assertEqual((self.pages, len(self.google.videos)), ([], 1))  # no page for the extension was opened

    def test_a_try_that_ended_without_a_post_is_never_sent_again_as_it_was(self):
        """Try again on one of the extension's posts hands the same text to the extension, which stops at the
        platform's own Post button. Through the API the same press would upload at once, with the old try's caption,
        choice of who can see it and made-for-kids answer, whatever you have changed since. So a row whose try went
        through the API has no Try again, the call behind it uploads nothing, and a new try sends what the form and
        your choices say now."""
        self.google.start_script = [(403, "quotaExceeded")]
        w = self.window(connect=False)
        job = self.posted(w, "public")
        self.assertEqual((job["state"], json.loads(job["options"])),
                         ("failed", {"privacy": "public", "made_for_kids": False}))
        # Since then: the caption is corrected, posts are private from now on, and the videos are made for kids.
        w.call("form_changed", "Typed since", TAGS)
        w.call("youtube_api_defaults", "private", True)
        w.forget()
        w.call("post_action", job["id"], "retry")
        (toast,) = w.sent("toast")
        self.assertEqual((toast["title"], toast["severity"], toast["body"]), (
            "Nothing was started", "warning",
            f"Press {posting_tab.LABELS['post_api']} on the YouTube row to try again. Its question shows what will "
            "be sent now."))
        self.assertEqual((len(posting.recent()), len(self.google.starts), self.pages), (1, 1, []))
        # Whichever way a try ended without a post: failed, called off, or never started.
        for end in (lambda j: posting.cancel(j["id"], "you cancelled it"),
                    lambda j: (self.age(j, 11, "created_at"), posting.expire_stale())):
            other = self.job()
            end(other)
            w.svc.jobs_changed()
            row = self.rows(w)["youtube"]
            self.assertEqual((row["job"], row["via"], row["actions"]), (other["id"], "api", ["post_api"]))
            w.call("post_action", other["id"], "retry")
            self.assertEqual(posting.get(other["id"])["state"], row["state"])
        self.assertEqual((len(posting.recent()), len(self.google.starts)), (3, 1))
        # The new try: the question shows the form and the choices as they are now, and that is what goes out.
        info = w.call("youtube_api_info", "", "")
        self.assertEqual(info["send"], {"title": "Typed since #shorts", "description": TAGS, "privacy": "private",
                                        "made_for_kids": True, "category": "22"})
        made = w.call("post_with_api", "Typed since", TAGS, "", False, self.yes_to(info))
        self.assertEqual(self.over(w, made["jobs"][0]["id"])["state"], "posted")
        sent = self.google.starts[1]["body"]
        self.assertEqual((sent["snippet"]["title"], sent["status"]),
                         ("Typed since #shorts", {"privacyStatus": "private", "selfDeclaredMadeForKids": True}))

    def test_a_post_with_the_sign_in_gone_says_connect_again(self):
        self.google.start_script = [(403, "quotaExceeded")]
        w = self.window()
        job = self.posted(w)
        self.google.end_grant()
        youtube_api._access.clear()
        again = self.posted(w)
        self.assertNotEqual(again["id"], job["id"])
        self.assertEqual((again["state"], again["error_code"]), ("failed", "not_connected"))
        self.assertEqual(again["error_message"], AGAIN["grant"] + " Nothing was posted.")
        view = w.view("youtube_api")
        self.assertEqual((view["state"], plain(view["line"])), ("again", "● YouTube API: connect again"))
        self.assertEqual(plain(view["lines"][0]), AGAIN["grant"])
        self.assertEqual(self.rows(w)["youtube"]["actions"], ["connect_api"])  # nothing new until then
        self.assertIsNone(self.api_post(w))
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
        self.assertEqual((row["state"], row["actions"]), ("cancelled", ["post_api"]))
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
        self.assertEqual(self.rows(w)["youtube"]["actions"], ["post_api"])
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
        row = self.rows(w)["youtube"]
        self.assertEqual(plain(row["line"]), "● waiting its turn  up to ten minutes, while another video uploads")
        self.assertEqual(posting.QUEUED_FOR, timedelta(minutes=10))  # (what the row says it waits)
        self.assertEqual((row["busy"], row["actions"]), (True, ["cancel"]))
        self.assertEqual(len(self.google.starts), 1)
        self.google.hold.set()
        self.assertEqual((self.over(w, first)["state"], self.over(w, second)["state"]), ("posted", "posted"))
        self.assertEqual(sorted(bytes(v["data"]) for v in self.google.videos), sorted([BYTES, BYTES[::-1]]))

    def test_a_turn_that_does_not_come_in_time_ends_the_job_there_and_then(self):
        """The wait for another upload is as long as a queued job lives (ten minutes; a third of a second here).
        When it is over the job ended nowhere: it sat queued until the clock's next half minute."""
        self.connect_api()
        job = self.job()
        theirs = youtube_api.upload_lock()  # as a ReelForge in the middle of another video's upload holds
        self.assertTrue(theirs.acquire())
        try:
            with mock.patch.object(posting, "QUEUED_FOR", timedelta(seconds=0.3)):
                done = youtube_api_tab.run(job["id"])
                self.assertEqual((done["state"], done["detail"]),
                                 ("expired", "it waited ten minutes for another upload"))
                # A ReelForge that is closing leaves its queued job as it is, for the clock.
                closing = self.job()
                self.assertEqual(youtube_api_tab.run(closing["id"], stop=lambda: True)["state"], "queued")
                # One that was called off while it waited stays called off.
                posting.cancel(closing["id"], "you cancelled it")
                self.assertEqual(youtube_api_tab.run(closing["id"])["detail"], "you cancelled it")
        finally:
            theirs.release()
        self.assertEqual((self.google.starts, db.uploads()), ([], []))  # nothing went out, and nothing says it did

    def test_one_whose_turn_never_came_says_so_when_the_wait_is_over(self):
        """A second video waits ten minutes for the first one's upload, and no longer. The end of that wait was
        silent: no pop-up, and a row that said "it never started" some time in the next half minute. (The wait is
        half a second here.)"""
        other = self.root / "videos" / "ep02.mp4"
        other.write_bytes(BYTES[::-1])
        w = self.window()
        w.call("tab_opened", "publish")
        w.call("pick_file", str(self.video))
        self.google.hold = threading.Event()
        first = self.api_post(w)["jobs"][0]["id"]
        self.assertTrue(w.until(lambda: self.rows(w)["youtube"]["state"] == "uploading"))
        w.call("pick_file", str(other))
        w.forget()
        with mock.patch.object(posting, "QUEUED_FOR", timedelta(seconds=0.5)):
            second = self.api_post(w)["jobs"][0]["id"]
            # With its own row on show, the row says it, with what to do next. No pop-up covers that.
            self.assertTrue(w.until(lambda: self.rows(w)["youtube"]["state"] == "expired", 10))
            self.assertTrue(w.until(lambda: len(w.loop.live(("youtube-api",))) == 1, 10))  # (the first one's)
            w.pump(0.1)
            row = self.rows(w)["youtube"]
            self.assertEqual((row["job"], row["actions"]), (second, ["post_api"]))
            self.assertTrue(plain(row["line"]).startswith(
                "○ it never started (it waited ten minutes for another upload)"), plain(row["line"]))
            self.assertEqual([t["title"] for t in w.sent("toast") if "YouTube" in t["title"]], [])
            # With the first video's row on show when the wait ends, a pop-up says it.
            third = self.api_post(w)["jobs"][0]["id"]
            w.call("pick_file", str(self.video))
            self.assertTrue(w.until(lambda: posting.get(third)["state"] == "expired", 10))
            self.assertTrue(w.until(lambda: any("YouTube" in t["title"] for t in w.sent("toast")), 10))
        (toast,) = [t for t in w.sent("toast") if "YouTube" in t["title"]]
        self.assertEqual((toast["title"], toast["severity"], toast["body"]), (
            "YouTube: not posted", "warning",
            "ep02.mp4 waited ten minutes for another upload and never started. Nothing was posted."))
        self.google.hold.set()
        self.assertEqual(self.over(w, first)["state"], "posted")
        self.assertEqual((len(self.google.starts), len(self.google.videos)), (1, 1))  # the first, and only it

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

    def refused_as_pressed(self, job: dict) -> None:
        with self.assertRaises(posting.Refused) as caught:
            posting.resolve(job["id"], False)
        self.assertEqual(caught.exception.code, "pressed")

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
        self.assertEqual(self.rows(w)["youtube"]["actions"], ["connect_api"])
        self.assertIsNone(self.api_post(w))  # not before this window is connected
        self.assertEqual((w.toasts()[-1], len(posting.recent())), (AGAIN["session"], 1))
        w.call("post_action", job["id"], "connect_api")
        self.assertTrue(w.until(lambda: self.rows(w)["youtube"]["actions"] == ["post_api"]))
        self.assertEqual(self.posted(w)["state"], "posted")
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
        # While the last piece is on its way nobody can call the job off (posting.advance). The clock can stop
        # waiting for it, and from then on the answer is yours.
        self.refused_as_pressed(job)
        self.age(job, 16)
        self.assertEqual(posting.expire_stale(), 1)
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
