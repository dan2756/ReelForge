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
