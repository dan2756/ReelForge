"""An answers file written the way JSON invites (an AI running the interview in chat writes my-studio/answers.json):
arrays for the one-per-line questions. Through questions.validate and the settings and files made from them, in a
scratch ReelForge folder."""

import io
import json
import tomllib
import unittest
from contextlib import redirect_stdout

from studio import generate, questions
from studio import paths as P

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


if __name__ == "__main__":
    unittest.main()
