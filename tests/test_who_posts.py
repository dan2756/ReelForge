"""What ReelForge says about posting, in the pages a person reads and in the guides their AI reads. A person can post
from the Publish tab in three ways, each started by their own press: by hand, with the browser extension
(docs/EXTENSION.md), and for YouTube through its own API (docs/YOUTUBE-API.md). So no page says any more that
ReelForge never posts, and the AI's rule names every way a post could be started and forbids each one. The rule
about YouTube's API has its own test in dashboard/tests/test_youtube_api.py."""

import io
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from dashboard import extension_api, posting_tab
from studio import generate, profiles, reset

from test_profiles import ANSWERS, Scratch

KIT = Path(__file__).resolve().parent.parent
# Every way to start a post that the guides name, in the guides' own words.
RULE = ("Never post, upload or send anything", "with the browser extension (`docs/EXTENSION.md`)",
        "for YouTube through its API", "own press", "Never start a post for them or get round that press",
        "`python -m studio.bridge`", "`post_with_extension`", "`post_action`", "`reelforge extension serve`",
        "`reelforge extension pair`", "`127.0.0.1` ports 47821 to 47830", "`extension/`", "`dashboard/web/`",
        "`dashboard/data/extension.json`", "switching on Press Post for me")
GUIDES = ("AGENTS.md", "CLAUDE.md", "studio/templates/context.md.j2")
PAGES = ("README.md", "dashboard/README.md", "THIRD_PARTY.md", "docs/PIPELINE.md", "docs/EXTENSION.md",
         "docs/YOUTUBE-API.md", "studio/templates/channel-bible.md.j2", *GUIDES)
# What these pages said while posting by hand was the only way. (A stats read still "never posts, likes or follows
# anything", which is about the browser that reads your numbers and stays true.)
GONE = ("ReelForge never posts", "never uploads anything", "kit never uploads", "posts for you", "posted for you",
        "they post by hand.", "posts by hand from", "posts from, by hand", "uploads from there by hand")


def flat(text: str) -> str:
    return " ".join(text.split())


class TheAisRule(Scratch):
    def test_the_guides_name_every_way_to_start_a_post_and_forbid_it(self):
        for rel in GUIDES:
            text = flat((KIT / rel).read_text(encoding="utf-8"))
            for words in RULE:
                self.assertIn(words, text, rel)

    def test_what_the_guides_forbid_is_what_is_really_there(self):
        for name in ("post_with_extension", "post_action"):
            self.assertTrue(callable(getattr(posting_tab.PostingTab, name)), name)
        self.assertEqual((extension_api.PORTS[0], extension_api.PORTS[-1]), (47821, 47830))
        for word in ("reelforge extension serve", "reelforge extension pair"):
            self.assertIn(word, extension_api.USAGE)
        self.assertEqual(extension_api.folder().name, "extension")
        self.assertEqual(extension_api.WEB, KIT / "dashboard" / "web")
        self.assertEqual(extension_api.store_path(), self.root / "dashboard" / "data" / "extension.json")
        self.assertIn("Press Post for me", posting_tab.HOW_PRESS)

    def test_the_notes_first_time_setup_writes_carry_the_rule_for_every_channel_and_ai(self):
        self.onboard_main(assistant=["claude", "codex"])
        with redirect_stdout(io.StringIO()):
            other = profiles.create("Cool Epic Facts")
            generate.write_all({**ANSWERS, "channel_name": "Cool Epic Facts"}, other)
        for notes in (self.root / "CLAUDE.md", self.root / "AGENTS.md", other.folder / "CLAUDE.md"):
            text = flat(notes.read_text(encoding="utf-8"))
            for words in RULE:
                self.assertIn(words, text, notes.name)
            self.assertIn("**You never post, upload or send anything.**", text)
            self.assertIn("Sam posts from there, by hand", text)
            self.assertIn("Either way it starts with their press, never yours.", text)
            self.assertIn("Never upload or post yourself, and never open the platforms' upload pages.", text)
        bible = flat((self.root / "my-studio" / "channel-bible.md").read_text(encoding="utf-8"))
        self.assertIn("Sam posts from ReelForge's Publish tab, with their own press; the AI never posts.", bible)

    def test_after_a_factory_reset_the_note_in_their_place_still_says_never_to_post(self):
        note = flat(reset.bootstrap_note("15 November 2026", "backups/factory-reset-2026-11-15"))
        self.assertIn("sees their stats and posts from. Never post, upload or send anything yourself, and never "
                      "start a post for them.", note)


class ThePages(unittest.TestCase):
    def test_no_page_still_says_that_reelforge_never_posts(self):
        for rel in PAGES:
            text = flat((KIT / rel).read_text(encoding="utf-8"))
            for words in GONE:
                self.assertNotIn(words, text, rel)
        self.assertNotIn("by hand", flat(reset.bootstrap_note("", "")))

    def test_the_pages_a_person_reads_say_nothing_is_posted_without_their_press_and_name_both_guides(self):
        for rel, said in (("README.md", "Not by itself, and never without your press."),
                          ("dashboard/README.md", "Nothing is posted without your press"),
                          ("THIRD_PARTY.md", "Nothing is posted without your press"),
                          ("docs/PIPELINE.md", "every post starts with your own press")):
            text = flat((KIT / rel).read_text(encoding="utf-8"))
            self.assertIn(said, text, rel)
            if rel != "THIRD_PARTY.md":
                for guide in ("EXTENSION.md", "YOUTUBE-API.md"):
                    self.assertIn(guide, text, rel)
        for guide in ("docs/EXTENSION.md", "docs/YOUTUBE-API.md"):
            self.assertTrue((KIT / guide).is_file(), guide)

    def test_the_extensions_guide_calls_the_windows_buttons_what_the_window_calls_them(self):
        guide = flat((KIT / "docs" / "EXTENSION.md").read_text(encoding="utf-8"))
        window = "".join((KIT / "ui" / "src" / "dashboard" / name).read_text(encoding="utf-8")
                         for name in ("Posting.tsx", "Publish.tsx")) + " ".join(posting_tab.LABELS.values())
        for label in ("Post…", "Start", "BY HAND", "Set it up", "Set up…", "Connect a browser",
                      "Connect another browser", "Browsers…", "Show the extension folder", "Disconnect",
                      "Stop before the Post button", "Try again", "Cancel", "It's posted", "It didn't post", "Open",
                      "Post again…"):
            self.assertIn(f"**{label}**", guide, label)
            self.assertIn(label, window, label)
        for words in ("POST", "BROWSER EXTENSION"):   # a heading and a dialog's title, as the window writes them
            self.assertIn(words, guide)
            self.assertIn(words, window)
        self.assertNotIn("by-hand button", guide)

    def test_a_page_that_says_the_extension_stops_before_post_says_when_it_presses_it(self):
        # It presses Post itself where you switched that on and asked for it (docs/EXTENSION.md, "Two ways to
        # post"), so "it stops there" alone says less than can happen.
        for rel in ("README.md", "dashboard/README.md"):
            text = flat((KIT / rel).read_text(encoding="utf-8")).replace("**", "")
            self.assertIn("It stops there: you check the page and press Post. (It presses Post itself only where",
                          text, rel)
            self.assertIn("Press Post for me", text, rel)
            self.assertIn("and never on X.)", text, rel)

    def test_neither_optional_way_is_said_to_work_on_the_live_sites(self):
        # They are tested against copies of the upload pages and a stand-in for Google. A page that names one says
        # so, or sends you to the guide that does.
        readme = flat((KIT / "README.md").read_text(encoding="utf-8"))
        self.assertIn("neither has been tried on the live sites yet", readme)
        window = flat((KIT / "dashboard" / "README.md").read_text(encoding="utf-8"))
        self.assertIn("tested against copies of the upload pages, not yet on the live sites", window)
        self.assertIn("has never uploaded a video to YouTube itself", window)


if __name__ == "__main__":
    unittest.main()
