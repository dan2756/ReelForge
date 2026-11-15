"""The reelforge command's own answers (studio/__main__.py), in a scratch ReelForge folder: a word it doesn't know."""

import io
import unittest
from contextlib import redirect_stdout
from unittest import mock

from studio.__main__ import main

from test_profiles import Scratch


class Commands(Scratch):
    def setUp(self):
        super().setUp()
        for patch in (mock.patch("studio.reset.finish_pending", return_value=None),
                      mock.patch("studio.move.finish_leftover")):
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


if __name__ == "__main__":
    unittest.main()
