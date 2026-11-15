"""A studio.toml someone edited by hand and left a typo in (its own first line invites the edit): the error names the
file, doctor reports it as a failed check, the JSON profile listing still lists every channel, and a command that
needs the settings says so in one line. In a scratch ReelForge folder."""

import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

from studio import browsers, doctor, profiles, settings, tools
from studio import paths as P
from studio.__main__ import main

from test_profiles import ANSWERS, Scratch

BROKEN = {
    "an unfinished string": ('[channel]\nname = "Night Physics\n', "line 2"),
    "a word for a number": ('[captions]\nmax_hashtags = "five"\n', "max_hashtags under [captions] should be a whole "
                                                                   "number"),
    "a value where a table goes": ("captions = 3\n", "[captions] should be a table of settings"),
    "a word where a list goes": ('[platforms]\nenabled = "tiktok"\n', "enabled under [platforms] should be a list"),
}


class Loading(Scratch):
    def test_a_typo_is_one_error_that_names_the_file(self):
        self.onboard_main()
        for what, (text, reason) in BROKEN.items():
            with self.subTest(what=what):
                P.SETTINGS.write_text(text, encoding="utf-8")
                with self.assertRaises(settings.SettingsError) as raised:
                    settings.load()
                said = str(raised.exception)
                self.assertTrue(said.startswith(f"{P.SETTINGS} can't be read: "), said)
                self.assertIn(reason, said)
                self.assertIn("reelforge channel --regenerate", said)
                self.assertIsInstance(raised.exception, ValueError)   # what callers already catch for bad settings

    def test_a_file_saved_by_notepad_with_a_byte_order_mark_still_reads(self):
        self.onboard_main()
        P.SETTINGS.write_bytes(b"\xef\xbb\xbf" + b'[captions]\r\nmax_hashtags = 2\r\n')
        self.assertEqual(settings.load()["captions"]["max_hashtags"], 2)

    def test_writing_it_again_from_the_answers_is_the_way_out_the_error_gives(self):
        self.onboard_main()
        P.SETTINGS.write_text(BROKEN["an unfinished string"][0], encoding="utf-8")
        with redirect_stdout(io.StringIO()):
            self.assertEqual(main(["channel", "--regenerate"]), 0)
        self.assertEqual(settings.load()["channel"]["name"], "Night Physics")


class Commands(Scratch):
    def setUp(self):
        super().setUp()
        self.onboard_main()
        for patch in (mock.patch("studio.reset.finish_pending", return_value=None),
                      mock.patch("studio.move.finish_leftover"),
                      # doctor's other checks, without starting Blender, Bun or a browser search
                      mock.patch.object(doctor, "window_check", lambda r, fix: r.add(doctor.OK, "Window", "")),
                      mock.patch.object(tools, "find_blender", return_value=None),
                      mock.patch.object(tools, "find_ffmpeg", return_value=None),
                      mock.patch.object(browsers, "find", return_value=None)):
            patch.start()
            self.addCleanup(patch.stop)

    def run_main(self, *argv: str) -> tuple[int, str, str]:
        with redirect_stdout(io.StringIO()) as out, redirect_stderr(io.StringIO()) as err:
            code = main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_doctor_reports_it_as_a_failed_check(self):
        P.SETTINGS.write_text(BROKEN["an unfinished string"][0], encoding="utf-8")
        code, out, err = self.run_main("doctor", "--json")
        self.assertEqual((code, err), (1, ""))
        rows = {c["what"]: c for c in json.loads(out)["checks"]}
        self.assertEqual(rows["Settings"]["status"], doctor.FAIL)
        self.assertIn(str(P.SETTINGS), rows["Settings"]["detail"])
        self.assertIn("line 2", rows["Settings"]["detail"])
        self.assertNotIn("First-time setup", rows)       # that row says "done for <channel>", which it can't read
        self.assertIn("CLAUDE.md", rows)                  # the checks after it still run
        code, out, _ = self.run_main("doctor")
        self.assertEqual(code, 1)
        self.assertIn(f"[ FAIL ] Settings: {P.SETTINGS} can't be read", out)

    def test_doctor_reports_another_channels_too(self):
        extra = profiles.create("Cool Epic Facts")
        with redirect_stdout(io.StringIO()):
            from studio import generate
            generate.write_all({**ANSWERS, "channel_name": extra.name}, extra)
        extra.settings.write_text(BROKEN["a value where a table goes"][0], encoding="utf-8")
        code, out, _ = self.run_main("doctor", "--json")
        rows = [c for c in json.loads(out)["checks"] if c["what"] == "Settings"]
        self.assertEqual(code, 1)
        self.assertEqual([c["status"] for c in rows], [doctor.FAIL])
        self.assertIn(str(extra.settings), rows[0]["detail"])

    def test_the_json_listing_still_lists_every_channel(self):
        extra = profiles.create("Cool Epic Facts")
        extra.my_studio.mkdir(parents=True)
        extra.settings.write_text(BROKEN["a word for a number"][0], encoding="utf-8")
        code, out, err = self.run_main("profiles", "--json")
        self.assertEqual((code, err), (0, ""))
        first, second = json.loads(out)["profiles"]
        self.assertEqual((first["platforms"], "error" in first), (["tiktok", "youtube"], False))
        self.assertEqual(second["platforms"], [])
        self.assertIn(str(extra.settings), second["error"])

    def test_a_command_that_needs_the_settings_says_so_in_one_line(self):
        P.SETTINGS.write_text(BROKEN["an unfinished string"][0], encoding="utf-8")
        for argv in (["new-episode", "snowball"], ["publish-check", "episodes/ep01_x"]):
            with self.subTest(argv=argv):
                (P.EPISODES / "ep01_x").mkdir(parents=True, exist_ok=True)
                (P.EPISODES / "ep01_x" / "episode.json").write_text('{"id": "ep01_x", "title": "T"}', encoding="utf-8")
                code, out, err = self.run_main(*argv)
                self.assertEqual((code, err), (1, ""))
                self.assertEqual(out.strip().splitlines()[-1][:len(str(P.SETTINGS)) + 15],
                                 f"{P.SETTINGS} can't be read:")
                self.assertNotIn("Traceback", out)


if __name__ == "__main__":
    unittest.main()
