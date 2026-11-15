"""Captions copied for a post (dashboard.videos): hashtags move to the tag list, and never into a title."""

import unittest
from pathlib import Path
from unittest import mock

from dashboard import videos
from dashboard.platforms import PublishItem


class StripHashtags(unittest.TestCase):
    def test_tacked_on_tags_go_and_a_word_in_the_sentence_stays(self):
        self.assertEqual(videos.strip_hashtags("Gravity flipped? #fyp #whatif"), "Gravity flipped?")
        self.assertEqual(videos.strip_hashtags("What if #gravity doubled"), "What if gravity doubled")
        self.assertEqual(videos.strip_hashtags("What if gravity doubled #fyp"), "What if gravity doubled fyp")
        self.assertEqual(videos.strip_hashtags("Hello. #fyp"), "Hello.")
        self.assertEqual(videos.strip_hashtags("#fyp #whatif"), "")

    def test_glued_hashtags_leave_the_caption(self):
        self.assertEqual(videos.strip_hashtags("Hello #fyp#whatif"), "Hello")
        self.assertEqual(videos.strip_hashtags("See #fyp#whatif now"), "See now")
        self.assertNotIn("#", videos.strip_hashtags("See #fyp#whatif now"))

    def test_a_post_does_not_keep_a_broken_glued_tag_in_the_line(self):
        text = videos.post_text("tiktok", "Hello #fyp#whatif", "#physics")
        self.assertTrue(text.startswith("Hello #"))
        self.assertNotIn("fyp#", text)
        self.assertIn("#fyp", text)
        self.assertIn("#whatif", text)


class Title(unittest.TestCase):
    """A channel that uses hashtags on X ("Use hashtags on X?" in first-time setup): the X post gets them, a title
    doesn't."""

    CAPTION, TAGS = "What if gravity doubled every second?", "#whatif #physics #gravity"

    def test_the_title_has_no_hashtags_when_x_gets_them(self):
        item = PublishItem(Path("ep01.mp4"), self.CAPTION, self.TAGS)
        with mock.patch.object(videos, "X_TAGS", True):
            self.assertEqual(item.text_for("x"), f"{self.CAPTION} {self.TAGS}")
            self.assertEqual(item.title, self.CAPTION)
            self.assertEqual(item.youtube_title, f"{self.CAPTION} #shorts")
        self.assertEqual(item.title, self.CAPTION)  # and as it always was without them

    def test_a_long_caption_is_cut_in_the_caption_not_in_the_hashtags(self):
        caption = "What if every drop of rain in a storm doubled in size every ten seconds on the way down to you?"
        with mock.patch.object(videos, "X_TAGS", True):
            item = PublishItem(Path("ep01.mp4"), caption, self.TAGS)
            self.assertEqual(item.title, caption[:100])
            self.assertEqual(item.youtube_title, f"{caption[:92].rstrip()} #shorts")
            self.assertEqual(item.youtube_title.count("#"), 1)

