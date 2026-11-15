"""The Publish tab's form and buttons (dashboard.service) on a scratch channel: what the caption counter counts, and
what each button does and records. The browser, the clipboard and the file manager are stand-ins here."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from dashboard import db, demo, desktop, sync, videos
from dashboard.config import PLATFORM_KEYS
from dashboard.platforms import Account, Platform, Post
from dashboard.tests.window import Window, plain

CAPTION, TAGS = "What if gravity doubled every second?", "#whatif #physics #gravity"


class Reader(Platform):
    """A platform whose stats read finds the posts it's given."""

    def __init__(self, key: str, name: str, posts: list[Post] = ()):
        super().__init__({"handle": "me"})
        self.key, self.name, self.posts = key, name, list(posts)

    @property
    def stats_source(self) -> str | None:
        return "public"

    @property
    def uses_browser(self) -> bool:
        return False

    def fetch(self):
        return Account("@me", 1_000), self.posts


class PublishTest(unittest.TestCase):
    """A channel with one finished video, ep01.mp4, picked on the Publish tab."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.addCleanup(setattr, db, "_path", db._path)
        db.init(self.root / "stats.db")
        for state in (sync._attempted, sync.last_errors):
            state.clear()
            self.addCleanup(state.clear)
        (self.root / "videos").mkdir()
        self.video = self.root / "videos" / "ep01.mp4"
        self.video.write_bytes(b"video")
        self.cfg = {"refresh_minutes": 0, "videos_dir": str(self.root / "videos"),
                    "episodes_dir": str(self.root / "episodes"), "completed_dir": str(self.root / "completed"),
                    **{key: {} for key in PLATFORM_KEYS}}
        # What the buttons would do to this computer: recorded instead.
        self.copied: list[str] = []
        self.opened: list[str] = []
        self.browser = True  # whether a browser opens
        for name, stand_in in (("copy", lambda text: self.copied.append(text) or True),
                               ("reveal", lambda path: None),
                               ("open_url", lambda url: self.opened.append(url) or self.browser)):
            patch = mock.patch.object(desktop, name, stand_in)
            patch.start()
            self.addCleanup(patch.stop)

    def window(self, platforms: dict | None = None, **options) -> Window:
        w = Window(self.cfg, platforms or demo.platforms(), tab="publish", **options)
        self.addCleanup(w.close)
        return w


class Form(PublishTest):
    def test_the_caption_counter_counts_the_caption_not_the_hashtags_x_gets(self):
        w = self.window()
        with mock.patch.object(videos, "X_TAGS", True):
            w.call("form_changed", CAPTION, TAGS)
        self.assertIn(f"{len(CAPTION)}/100", plain(w.view("preview")["caption"]))


class Ready(PublishTest):
    """A window that says when it's listening gets nothing before that, so the picked video's caption isn't lost."""

    def test_nothing_is_sent_until_the_window_says_it_is_ready(self):
        w = self.window(ready=True)
        self.assertEqual(w.loop.events, [])
        self.assertEqual(w.init["tab"], "publish")
        w.call("ready")
        form = w.view("form")
        self.assertEqual((form["file"], form["caption"]), (str(self.video), "Ep01"))
        self.assertEqual(len(w.view("files")["items"]), 1)

    def test_ready_starts_it_once(self):
        w = self.window(ready=True)
        w.call("ready")
        w.forget()
        w.call("ready")
        self.assertEqual(w.sent("platforms") + w.sent("form"), [])  # what only starting sends

    def test_a_window_that_does_not_ask_starts_as_before(self):
        w = self.window()
        self.assertEqual(w.view("form")["caption"], "Ep01")
        w.forget()
        w.call("ready")  # harmless there
        self.assertEqual(w.sent("platforms") + w.sent("form"), [])


class UploadPage(PublishTest):
    """A platform's button: its text copied, the file shown, its upload page opened, and a note of it kept."""

    def test_a_page_that_opened_is_noted(self):
        w = self.window()
        w.call("open_upload", "tiktok", CAPTION, TAGS)
        self.assertEqual(self.opened, [desktop.UPLOAD_PAGES["tiktok"]])
        self.assertEqual(self.copied, [f"{CAPTION} #fyp {TAGS}"])
        self.assertEqual([t["title"] for t in w.sent("toast")], ["Upload page opened"])
        self.assertEqual([(u["platform"], u["video"], u["status"]) for u in db.uploads()],
                         [("tiktok", "ep01.mp4", "manual")])
        self.assertEqual(len(w.svc.post_check_timers), 2)

    def test_no_browser_means_no_note_and_a_message_that_says_so(self):
        self.browser = False
        w = self.window()
        w.call("open_upload", "tiktok", CAPTION, TAGS)
        toast = w.sent("toast")[-1]
        self.assertEqual((toast["title"], toast["severity"]), ("TikTok's upload page didn't open", "warning"))
        self.assertIn(desktop.UPLOAD_PAGES["tiktok"], toast["body"])
        self.assertEqual(self.copied, [f"{CAPTION} #fyp {TAGS}"])  # the text is still copied for you
        self.assertEqual(db.uploads(), [])
        self.assertEqual(db.episodes(), [])
        self.assertEqual(w.svc.post_check_timers, [])

    def test_open_all_notes_only_the_pages_that_opened(self):
        w = self.window()
        opens = {desktop.UPLOAD_PAGES["youtube"]}  # the only one a browser opens for
        with mock.patch.object(desktop, "open_url", lambda url: url in opens):
            w.call("open_all", CAPTION, TAGS)
            self.assertEqual([u["platform"] for u in db.uploads()], ["youtube"])
            titles = [t["title"] for t in w.sent("toast")]
            self.assertEqual(titles, ["Upload pages didn't open", "Upload page opened"])
            w.forget()
            opens.clear()
            w.call("open_all", CAPTION, TAGS)
            self.assertEqual([t["title"] for t in w.sent("toast")], ["Upload pages didn't open"])
            self.assertEqual(len(db.uploads()), 1)

    def test_open_all_is_as_it_was_when_every_page_opens(self):
        w = self.window()
        w.call("open_all", CAPTION, TAGS)
        self.assertEqual(self.opened, [desktop.UPLOAD_PAGES[k] for k in w.svc.platforms])
        self.assertEqual([t["title"] for t in w.sent("toast")], ["4 upload pages opened"])
        self.assertEqual(len(db.uploads()), 4)

    def test_a_video_whose_posts_were_spotted_first_gets_them_not_a_second_episode(self):
        # Posted on two platforms before any upload page was opened here: ReelForge filed the posts under an episode
        # of their own, with no video. Opening the third platform's page must not make a second one.
        twin = db.create_episode("Ep01", None, db.now_iso())
        for key in ("tiktok", "youtube"):
            db.add_post(key, f"{key}-1", None, "Ep01 #whatif", f"https://example.invalid/{key}")
            db.link_post(key, f"{key}-1", twin, "twin")
        w = self.window()
        self.assertEqual(plain(w.view("files")["items"][0]["lines"][0]), "  ep01.mp4")
        w.call("open_upload", "instagram", "Ep01", TAGS)
        self.assertEqual([(e["id"], e["video"]) for e in db.episodes()], [(twin, str(self.video))])
        self.assertEqual(w.svc.posted_platforms(), {"ep01.mp4": {"tiktok", "youtube"}})
        self.assertEqual(plain(w.view("files")["items"][0]["lines"][0]), "✓ ep01.mp4")


class Remove(PublishTest):
    def test_the_demo_never_moves_a_real_video_to_the_bin(self):
        w = self.window()  # the tests' window is the demo
        with mock.patch.object(desktop, "recycle") as recycle:
            w.call("remove_video", str(self.video), "recycle")
            recycle.assert_not_called()
            self.assertTrue(any("Demo mode" in t for t in w.toasts()))
            self.assertEqual(len(w.view("files")["items"]), 1)
            w.svc.demo = False  # your own window still does
            w.call("remove_video", str(self.video), "recycle")
            recycle.assert_called_once_with(self.video)

    def test_the_demo_can_still_hide_one_from_its_list(self):
        w = self.window()
        w.call("remove_video", str(self.video), "hide")
        self.assertEqual(w.view("files")["items"], [])
        self.assertTrue(self.video.exists())


class Posted(PublishTest):
    def test_the_list_marks_a_video_this_windows_own_read_finds_posted(self):
        db.create_episode(CAPTION, str(self.video))
        youtube = Reader("youtube", "YouTube")
        w = self.window({"youtube": youtube})
        self.assertEqual(plain(w.view("files")["items"][0]["lines"][0]), "  ep01.mp4")
        youtube.posts = [Post("yt-1", f"{CAPTION} #shorts", "https://example.invalid/yt-1", db.now_iso(), views=5)]
        w.forget()
        w.call("refresh", True)
        self.assertTrue(w.until(lambda: w.view("posted") and w.view("posted")["done"]["youtube"]))
        self.assertTrue(w.sent("files"), "the video list wasn't sent again")
        self.assertEqual(plain(w.view("files")["items"][0]["lines"][0]), "✓ ep01.mp4")
