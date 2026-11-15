"""An answers file written the way JSON invites (an AI running the interview in chat writes my-studio/answers.json):
arrays for the one-per-line questions, null for one left out. Through questions.validate, the settings made from
them, and reelforge channel --answers in a scratch ReelForge folder."""

import io
import json
import tomllib
import unittest
from contextlib import redirect_stdout
from unittest import mock

from studio import generate, profiles, questions
from studio import paths as P
from studio.__main__ import onboard

from test_profiles import ANSWERS, Scratch


class Lists(Scratch):
    def test_an_array_for_a_one_per_line_question_is_its_lines(self):
        given = {**ANSWERS, "subjects": ["a jump's height", " a snowball ", ""], "references": ["@a", "@b"],
                 "about": ["Line one.", "Line two."], "default_tags": ["fyp", "#whatif"]}
        a, problems = questions.validate(given)
        self.assertEqual(problems, [])
        self.assertEqual(a["subjects"], "a jump's height\na snowball")
        self.assertEqual(a["references"], "@a\n@b")
        self.assertEqual(a["about"], "Line one.\nLine two.")
        self.assertEqual(a["default_tags"], "fyp #whatif")       # a one-line question: its words on one line
        s = generate.settings_from(a)
        self.assertEqual(s["format"]["subjects"], ["a jump's height", "a snowball"])
        self.assertEqual(s["format"]["references"], ["@a", "@b"])
        self.assertEqual(s["captions"]["default_tags"], ["fyp", "whatif"])

    def test_the_array_isnt_written_back_as_its_python_text(self):
        with redirect_stdout(io.StringIO()):
            generate.write_all({**ANSWERS, "subjects": ["a jump's height", "a snowball"]})
        self.assertEqual(json.loads(P.ANSWERS.read_text(encoding="utf-8"))["subjects"], "a jump's height\na snowball")
        self.assertEqual(tomllib.loads(P.SETTINGS.read_text(encoding="utf-8"))["format"]["subjects"],
                         ["a jump's height", "a snowball"])
        bible = P.BIBLE.read_text(encoding="utf-8")
        self.assertIn("a jump's height", bible)
        self.assertNotIn("['", bible)
        self.assertNotIn('["', bible)


class Nulls(Scratch):
    def test_null_is_an_answer_not_given_so_its_default_applies(self):
        a, problems = questions.validate({**ANSWERS, "platforms": None, "tone": None, "hud": None,
                                          "max_hashtags": None, "custom_format": None, "style_notes": None})
        self.assertEqual(problems, [])
        self.assertEqual((a["platforms"], a["tone"], a["hud"], a["max_hashtags"]),
                         (["tiktok", "youtube", "instagram"], ["awe", "suspense"], True, 5))
        s = generate.settings_from(a)
        self.assertEqual((s["format"]["custom"], s["video"]["style_notes"]), ("", ""))
        _, problems = questions.validate({**ANSWERS, "channel_name": None})
        self.assertEqual(problems, ["channel_name: What's your channel called? (required)"])

    def test_an_answer_to_a_question_that_doesnt_apply_still_comes_out_as_text(self):
        # the format isn't custom and neither is the style, so these two aren't asked: they used to stay as written,
        # and making the settings from them stopped with a traceback
        a, problems = questions.validate({**ANSWERS, "custom_format": ["a", "b"], "style_notes": 7,
                                          "hashtags_on_x": "perhaps"})
        self.assertEqual(problems, [])                     # X isn't one of its platforms: nothing to fix there
        self.assertEqual((a["custom_format"], a["style_notes"]), ("a\nb", "7"))
        self.assertEqual(generate.settings_from(a)["format"]["custom"], "a\nb")
        _, problems = questions.validate({**ANSWERS, "platforms": ["x"], "hashtags_on_x": "perhaps"})
        self.assertEqual(problems, ["hashtags_on_x: answer yes or no"])

    def test_channel_answers_with_nulls_writes_the_files(self):
        src = self.root / "answers.json"
        src.write_text(json.dumps({**ANSWERS, "custom_format": None, "style_notes": None, "twist": None}),
                       encoding="utf-8")
        with redirect_stdout(io.StringIO()) as out:
            self.assertEqual(onboard(["--answers", str(src)]), 0, out.getvalue())
        self.assertTrue(P.SETTINGS.exists())
        self.assertNotIn("Traceback", out.getvalue())


class NewProfile(Scratch):
    def new(self, answers: dict) -> tuple[int, str]:
        src = self.root / "new.json"
        src.write_text(json.dumps(answers), encoding="utf-8")
        with redirect_stdout(io.StringIO()) as out:
            code = onboard(["--new", "--answers", str(src)])
        return code, out.getvalue()

    def test_a_new_profile_whose_files_cant_be_written_leaves_no_folder_behind(self):
        self.onboard_main()
        answers = {**ANSWERS, "channel_name": "Second Channel", "handle_tiktok": "@second"}
        for error, shown in ((ValueError("that answer needs fixing"), "that answer needs fixing"),
                             (RuntimeError("AGENTS.md came out too long"), "AGENTS.md came out too long")):
            with self.subTest(error=error):
                real = generate._guides

                def guides(a, s, w, out, error=error):
                    if not w.profile.main:
                        raise error
                    return real(a, s, w, out)

                with mock.patch.object(generate, "_guides", guides):
                    code, said = self.new(answers)
                self.assertEqual(code, 1)
                self.assertIn(shown, said)
                self.assertEqual(profiles.extra(), [])
                self.assertFalse((profiles.FOLDER / "second-channel").exists())
        # the same command works once whatever stopped it is fixed: before, the half-made profile had the name taken
        code, said = self.new(answers)
        self.assertEqual(code, 0, said)
        self.assertEqual([p.name for p in profiles.extra()], ["Second Channel"])
        self.assertTrue((profiles.FOLDER / "second-channel" / "my-studio" / "studio.toml").exists())

    def test_something_unexpected_still_takes_the_folder_away_and_is_raised(self):
        self.onboard_main()
        with mock.patch.object(generate, "write_all", side_effect=KeyError("boom")), \
                self.assertRaises(KeyError):
            self.new({**ANSWERS, "channel_name": "Second Channel"})
        self.assertEqual(profiles.extra(), [])


if __name__ == "__main__":
    unittest.main()
