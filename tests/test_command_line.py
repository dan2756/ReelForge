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
        self.assertIn(f'{Path("pipeline") / "make_episode.py"} {where} --steps build,stills', out)
        profiles.use(profiles.main())
        code, out = self.run_main("new-episode", "snowball")
        self.assertEqual(code, 0, out)
        self.assertIn(f'make_episode.py {Path("episodes") / "ep01_snowball"} --steps build,stills', out)

    def test_new_episode_starts_a_3d_channel_from_a_3d_example_whatever_its_format(self):
        # "custom" and "disappear" have no starter of their own, and took the first example there is. Since 3.0 that
        # is the 2D demo: a channel that makes 3D videos got a script, a voice and a scene.js, and a build that asks
        # for the voice.
        import json

        for n, answers in enumerate(({"format": "disappear"},
                                     {"format": "custom", "series": "Snowballs",
                                      "custom_format": "Each video: a question at the top, a 3D scene that escalates."}),
                                    start=1):
            with self.subTest(format=answers["format"]):
                self.onboard_main(video_type="blender", **answers)
                code, out = self.run_main("new-episode", "snowball")
                self.assertEqual(code, 0, out)
                ep = self.root / "episodes" / f"ep{n:02d}_snowball"
                spec = json.loads((ep / "episode.json").read_text(encoding="utf-8"))
                self.assertNotEqual(spec.get("engine"), "2d")
                self.assertNotIn("script", spec)
                self.assertFalse((ep / "scene.js").exists())
                self.assertIn("demo_disaster_pov", out.splitlines()[0])  # the one it took before there was a 2D demo
                self.assertIn("Next: write the title, lines, HUD, sound cues", out)

    def test_answers_written_out_fresh_keep_the_kind_of_video_the_channel_makes(self):
        # An AI that redoes the interview writes what it asked over my-studio/answers.json, as the guides tell it,
        # and runs channel --answers on that file: no date in it, and nothing about the kind of video. A channel that
        # makes 3D videos became a 2D one, its notes with it, and nothing was said.
        import json
        import tomllib

        self.onboard_main(video_type="blender")
        answers = self.root / "my-studio" / "answers.json"
        settings = self.root / "my-studio" / "studio.toml"
        for argv in (["channel", "--answers", str(answers)], ["channel", "--regenerate"]):
            with self.subTest(argv=argv[1]):
                answers.write_text(json.dumps({**ANSWERS, "about": "What-ifs, told faster."}), encoding="utf-8")
                code, out = self.run_main(*argv)
                self.assertEqual(code, 0, out)
                self.assertEqual(tomllib.loads(settings.read_text(encoding="utf-8"))["video"]["engine"], "blender")
                self.assertEqual(json.loads(answers.read_text(encoding="utf-8"))["video_type"], "blender")
                self.assertIn("Blender builds and renders each scene", (self.root / "CLAUDE.md").read_text("utf-8"))
        # ... and the kind is still theirs to change, by saying so
        answers.write_text(json.dumps({**ANSWERS, "video_type": "2d"}), encoding="utf-8")
        code, out = self.run_main("channel", "--answers", str(answers))
        self.assertEqual(code, 0, out)
        self.assertEqual(tomllib.loads(settings.read_text(encoding="utf-8"))["video"]["engine"], "2d")

    def test_new_episode_starts_a_2d_channel_from_the_2d_demo(self):
        import json

        self.onboard_main(format="custom", series="Pennies",
                          custom_format="Each video: a money question, answered day by day.")
        code, out = self.run_main("new-episode", "penny")
        self.assertEqual(code, 0, out)
        ep = self.root / "episodes" / "ep01_penny"
        self.assertEqual(json.loads((ep / "episode.json").read_text(encoding="utf-8"))["engine"], "2d")
        self.assertTrue((ep / "scene.js").is_file())
        self.assertIn(f'make_episode.py {Path("episodes") / "ep01_penny"} --steps build,stills', out)

    def test_new_episode_says_so_when_no_example_is_of_the_channels_kind(self):
        from studio import paths as P

        self.onboard_main(video_type="blender", format="disappear")
        none = self.root / "no-examples"
        none.mkdir()
        with mock.patch.object(P, "EXAMPLES", none):
            code, out = self.run_main("new-episode", "snowball")
        self.assertEqual(code, 1, out)
        self.assertIn("examples/ has no 3D video to start from", out)       # it said: no example or episode called ''
        self.assertFalse((self.root / "episodes" / "ep01_snowball").exists())

    def test_answers_that_are_not_a_set_of_answers_get_one_line(self):
        # A list, a line of text and a number are JSON too: each went on into a traceback ('list' object has no
        # attribute 'items').
        self.onboard_main()
        file = self.root / "answers-again.json"
        for text in ("[]", '"hello"', "3"):
            for argv in (["channel", "--answers", str(file)], ["channel", "--new", "--answers", str(file)]):
                with self.subTest(text=text, new="--new" in argv):
                    file.write_text(text, encoding="utf-8")
                    code, out = self.run_main(*argv)
                    self.assertEqual(code, 1, out)
                    self.assertIn("isn't a set of answers: it has to be one JSON object", out)

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
