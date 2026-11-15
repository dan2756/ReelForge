"""The channel bible belongs to your AI as much as to first-time setup: it keeps the slate and the research notes in
it. Writing the bible again (your answers changed, or you connected an account on the Accounts tab) carries both
over. In a scratch ReelForge folder."""

import io
import json
import unittest
from contextlib import redirect_stdout

from studio import generate
from studio import paths as P

from test_profiles import ANSWERS, Scratch


class Slate(Scratch):
    def setUp(self):
        super().setUp()
        self.onboard_main(subjects=["a jump's height", "a snowball"])

    def bible(self) -> str:
        return P.BIBLE.read_text(encoding="utf-8")

    def slate(self) -> list[str]:
        text = self.bible()
        section = text[text.index("## Slate"):text.index("Title pattern examples:")]
        return [ln for ln in section.splitlines() if ln.startswith("- ")]

    def answers(self) -> dict:
        return json.loads(P.ANSWERS.read_text(encoding="utf-8"))

    def backups(self) -> list[str]:
        return sorted(p.name for p in P.BACKUPS.glob("channel-bible.*")) if P.BACKUPS.exists() else []

    def ai_edits(self) -> None:
        """What an AI does to the bible between videos: marks an idea used, adds one of its own, notes what it learned."""
        text = self.bible().replace("- a snowball\n", "- ~~a snowball~~ (ep01)\n- the Moon's distance\n")
        P.BIBLE.write_text(text + "\nTikTok: the doubling hooks pull three times the views.\n", encoding="utf-8")

    def test_connecting_an_account_keeps_the_slate_and_the_research_notes(self):
        self.assertEqual(self.slate()[:3], ["- a jump's height", "- a snowball",
                                            "- a snowball rolling downhill *(suggested)*"])
        self.ai_edits()
        before = self.slate()
        generate.set_account("tiktok", "@nightphysics2")          # as the Accounts tab does
        self.assertIn("- **Platforms:** TikTok (@nightphysics2), YouTube Shorts", self.bible())   # it was rewritten
        self.assertEqual(self.slate(), before)
        self.assertEqual(self.slate()[:3], ["- a jump's height", "- ~~a snowball~~ (ep01)", "- the Moon's distance"])
        self.assertTrue(self.bible().rstrip().endswith("TikTok: the doubling hooks pull three times the views."))
        self.assertEqual(len(self.backups()), 1)                   # the bible as it was before the handle changed

    def test_new_answers_add_their_ideas_and_a_new_format_brings_its_own_suggestions(self):
        self.ai_edits()
        with redirect_stdout(io.StringIO()):
            generate.write_all({**self.answers(), "subjects": "a jump's height\na snowball\na volcano\nthe Moon"})
        ideas = self.slate()
        # yours ahead of the format's suggestions; the one marked used isn't back as a new idea; "the Moon" is a new
        # idea although "the Moon's distance" has its words
        self.assertEqual(ideas[:6], ["- a jump's height", "- ~~a snowball~~ (ep01)", "- the Moon's distance",
                                     "- a volcano", "- the Moon", "- a snowball rolling downhill *(suggested)*"])
        with redirect_stdout(io.StringIO()):
            generate.write_all({**self.answers(), "format": "disaster_pov"})
        ideas = self.slate()
        self.assertEqual(ideas[:5], ["- a jump's height", "- ~~a snowball~~ (ep01)", "- the Moon's distance",
                                     "- a volcano", "- the Moon"])
        self.assertNotIn("- a snowball rolling downhill *(suggested)*", ideas)   # the old format's, never touched
        self.assertIn("- a supervolcano *(suggested)*", ideas)
        self.assertTrue(all(ln.endswith("*(suggested)*") for ln in ideas[5:]))

    def test_a_new_idea_is_added_though_another_idea_has_its_words(self):
        # an idea counted as there already when its words stood anywhere in the slate, so these two never arrived
        text = self.bible().replace("- a snowball\n", "- ~~the Moon landing~~ (ep02)\n")
        P.BIBLE.write_text(text, encoding="utf-8")
        with redirect_stdout(io.StringIO()):
            generate.write_all({**self.answers(), "subjects": "a jump's height\na snowball\nthe Moon"})
        # "a snowball rolling downhill" is the format's suggestion, "the Moon landing" one the AI added and used
        self.assertEqual(self.slate()[:5], ["- a jump's height", "- ~~the Moon landing~~ (ep02)", "- a snowball",
                                            "- the Moon", "- a snowball rolling downhill *(suggested)*"])
        # and an idea is the same one however it's marked: none of these comes back as new
        for marked in ("- ~~a snowball~~", "- ~~a snowball~~ ep03, 2M views", "- a snowball (ep03)",
                       "- [x] A Snowball"):
            with self.subTest(marked=marked):
                P.BIBLE.write_text(self.bible().replace(self.slate()[2] + "\n", marked + "\n"), encoding="utf-8")
                with redirect_stdout(io.StringIO()):
                    generate.write_all(self.answers())
                self.assertEqual(self.slate()[:4], ["- a jump's height", "- ~~the Moon landing~~ (ep02)", marked,
                                                    "- the Moon"])

    def test_an_idea_you_take_out_of_your_answers_leaves_the_slate_unless_its_been_marked(self):
        with redirect_stdout(io.StringIO()):
            generate.write_all({**self.answers(), "subjects": "a jump's height\na snowball\na volcano"})
        self.ai_edits()                                             # a snowball is marked used
        # The slate kept every idea of yours for good once it was on it, so `reelforge channel` could no longer
        # take one off, and your AI went on picking it. The answers file edited in place counts too: what you had
        # listed before is read from studio.toml, which still has it.
        P.ANSWERS.write_text(json.dumps({**self.answers(), "subjects": "a jump's height"}), encoding="utf-8")
        with redirect_stdout(io.StringIO()):
            generate.write_all(self.answers())
        self.assertEqual(self.slate()[:4], ["- a jump's height", "- ~~a snowball~~ (ep01)", "- the Moon's distance",
                                            "- a snowball rolling downhill *(suggested)*"])
        self.assertNotIn("- a volcano", self.slate())               # untouched, and no longer in your answers
        with redirect_stdout(io.StringIO()):
            generate.write_all(self.answers())                      # again: what the AI added is never taken out
        self.assertEqual(self.slate()[:3], ["- a jump's height", "- ~~a snowball~~ (ep01)", "- the Moon's distance"])

    def test_writing_it_again_unchanged_keeps_no_copy(self):
        self.ai_edits()
        text = self.bible()
        with redirect_stdout(io.StringIO()):
            generate.write_all(self.answers())
            generate.write_all(self.answers())
        self.assertEqual(self.bible(), text)
        self.assertEqual(self.backups(), [])
        # another day's date in its first lines is no change either
        P.BIBLE.write_text(text.replace(" on 20", " on 19", 1), encoding="utf-8")
        self.assertNotEqual(self.bible(), text)
        with redirect_stdout(io.StringIO()):
            generate.write_all(self.answers())
        self.assertEqual((self.bible(), self.backups()), (text, []))

    def test_the_bible_says_what_rewrites_it_and_what_stays(self):
        head = self.bible().split("-->")[0]
        self.assertIn("or connecting an account rewrites this file but keeps the slate and the research notes", head)


class Without(Scratch):
    def test_a_bible_with_no_slate_or_no_ideas_is_written_as_new(self):
        self.onboard_main()
        fresh = P.BIBLE.read_text(encoding="utf-8")
        for old in ("# Night Physics\n\nMy own notes.\n",                       # someone's own file
                    fresh[:fresh.index("## Slate")] + "## Slate (ideas, in order)\n\nNone yet.\n\n"
                    + fresh[fresh.index("## Captions"):]):                      # a slate emptied by hand
            with self.subTest(old=old[:20]):
                P.BIBLE.write_text(old, encoding="utf-8")
                with redirect_stdout(io.StringIO()):
                    generate.write_all({**ANSWERS})
                self.assertEqual(P.BIBLE.read_text(encoding="utf-8"), fresh)


if __name__ == "__main__":
    unittest.main()
