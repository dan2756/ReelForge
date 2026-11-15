"""The reelforge command's own answers (studio/__main__.py), in a scratch ReelForge folder: a word it doesn't know,
the next command new-episode gives for a channel you added, and publish-check on an episode.json with a typo."""

import io
import os
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from studio import generate, profiles
from studio.__main__ import main

from test_profiles import ANSWERS, Scratch


class Commands(Scratch):
    def setUp(self):
        super().setUp()
        for patch in (mock.patch("studio.reset.finish_pending", return_value=None),
                      mock.patch("studio.move.finish_leftover"),
                      mock.patch.dict(os.environ, {"STUDIO_DRY_SYSTEM": "1"})):
            patch.start()
            self.addCleanup(patch.stop)

    def run_main(self, *argv: str) -> tuple[int, str]:
        with redirect_stdout(io.StringIO()) as out:
            code = main(list(argv))
        return code, out.getvalue()

    def test_a_word_it_doesnt_know_gets_one_line_with_the_nearest_command(self):
        for typed, meant in (("publsh", "publish"), ("doctr", "doctor"), ("chanel", "channel"),
                             ("new-episod", "new-episode"), ("profile", "profiles"), ("uninstal", "uninstall"),
                             ("acounts", "accounts"), ("publish-chek", "publish-check")):
            with self.subTest(typed=typed):
                code, out = self.run_main(typed)
                self.assertEqual(code, 2)
                self.assertEqual(out, f"ReelForge: there's no command or tab called {typed!r}. Did you mean reelforge "
                                      f"{meant}? reelforge help lists them all.\n")   # one line, not the whole help

    def test_a_word_near_nothing_is_one_line_too(self):
        code, out = self.run_main("xyzzy", "--now")
        self.assertEqual((code, out), (2, "ReelForge: there's no command or tab called 'xyzzy'. reelforge help lists "
                                          "them all.\n"))

    def test_help_still_lists_everything(self):
        code, out = self.run_main("help")
        self.assertEqual(code, 0)
        for line in ("reelforge doctor", "reelforge channel", "reelforge publish-check", "reelforge profiles new"):
            self.assertIn(line, out)

    def test_new_episode_for_an_added_channel_gives_a_next_command_for_that_channels_episode(self):
        self.onboard_main()
        extra = profiles.create("Cool Epic Facts")
        with redirect_stdout(io.StringIO()):
            generate.write_all({**ANSWERS, "channel_name": extra.name}, extra)
        code, out = self.run_main("-p", "cool-epic-facts", "new-episode", "snowball")
        self.assertEqual(code, 0, out)
        where = Path("profiles") / "cool-epic-facts" / "episodes" / "ep01_snowball"
        self.assertTrue((self.root / where / "episode.json").is_file())
        # it used to print episodes/ep01_snowball, the first channel's folder, where there is no such episode
        self.assertIn(f'{Path("pipeline") / "make_episode.py"} "{where}" --steps build,stills', out)
        profiles.use(profiles.main())
        code, out = self.run_main("new-episode", "snowball")
        self.assertEqual(code, 0, out)
        self.assertIn(f'"{Path("episodes") / "ep01_snowball"}" --steps build,stills', out)

    def test_publish_check_on_an_episode_json_with_a_typo_is_one_fail_line(self):
        self.onboard_main()
        ep = self.root / "episodes" / "ep01_snowball"
        ep.mkdir(parents=True)
        for text, why in (('{"id": "ep01_snowball", "title": "What if"', "Expecting"), ('{"title": "No id"}', 'no "id"'),
                          ("[]", 'no "id"')):
            with self.subTest(text=text):
                (ep / "episode.json").write_text(text, encoding="utf-8")
                code, out = self.run_main("publish-check", "episodes/ep01_snowball")
                self.assertEqual(code, 1)
                self.assertEqual(len(out.strip().splitlines()), 1, out)
                self.assertIn(f"FAIL  {(ep / 'episode.json').resolve()} can't be read:", out)
                self.assertIn(why, out)


if __name__ == "__main__":
    unittest.main()
