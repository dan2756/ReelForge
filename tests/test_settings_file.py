"""A studio.toml someone edited by hand and left a typo in (its own first line invites the edit): the error names the
file, doctor reports it as a failed check, the JSON profile listing still lists every channel, and a command that
needs the settings says so in one line. In a scratch ReelForge folder."""

import io
import json
import os
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

from studio import browsers, doctor, profiles, settings, tools
from studio import paths as P
from studio.__main__ import main, onboard

from test_profiles import ANSWERS, Scratch

BROKEN = {
    "an unfinished string": ('[channel]\nname = "Night Physics\n', "line 2"),
    "a word for a number": ('[captions]\nmax_hashtags = "five"\n', "max_hashtags under [captions] should be a whole "
                                                                   "number"),
    "a value where a table goes": ("captions = 3\n", "[captions] should be a table of settings"),
    "a word where a list goes": ('[platforms]\nenabled = "tiktok"\n', "enabled under [platforms] should be a list"),
    "a word for neither yes nor no": ('[platforms]\nbackground_stats = "maybe"\n',
                                      "background_stats under [platforms] should be true or false"),
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
                self.assertIn("reelforge -p 1 channel --regenerate", said)   # the first channel, by its number
                self.assertIsInstance(raised.exception, ValueError)   # what callers already catch for bad settings

    def test_a_yes_or_no_for_the_hourly_read_typed_as_a_word_reads_as_meant(self):
        """`background_stats = "false"` reads as TOML (a word), and every word was a yes: the hourly read of your
        stats, with your log-ins, was switched on against a no that could be read."""
        from studio import generate
        from studio.__main__ import apply_background_stats

        self.onboard_main()
        text = P.SETTINGS.read_text(encoding="utf-8")
        for value, on in (('"false"', False), ("'no'", False), ('"off"', False), ("0", False), ('"No"', False),
                          ('"true"', True), ('"yes"', True), ("1", True), ("true", True), ("false", False)):
            with self.subTest(value=value):
                P.SETTINGS.write_text(text.replace("background_stats = true", f"background_stats = {value}"),
                                      encoding="utf-8")
                self.assertIs(settings.load()["platforms"]["background_stats"], on)
                said: list[str] = []
                self.assertIs(generate.background_stats(profiles.main(), say=said.append), on)
                self.assertEqual(said, [])
                with mock.patch.dict(os.environ, {"STUDIO_DRY_SYSTEM": "1"}):
                    self.assertEqual("(dry run) would run" in apply_background_stats(), on)
        # a word that is neither is a mistake, and while the file has one only a yes on that line is a yes
        P.SETTINGS.write_text(text.replace("background_stats = true", 'background_stats = "maybe"'), encoding="utf-8")
        said = []
        self.assertIs(generate.background_stats(profiles.main(), say=said.append), False)
        self.assertIn("(background_stats under [platforms] should be true or false)", said[0])

    def test_a_no_for_the_hourly_read_written_anywhere_but_under_platforms_is_a_mistake_and_a_no(self):
        """A background_stats line added at the end of studio.toml (where it's under [ai]) or at its top, or spelled
        background-stats or Background_Stats, was left out without a word, and the yes under [platforms] (or the
        default yes) kept the hourly read of your stats, with your log-ins, on. The same file with a typo elsewhere in
        it went by that line, as a no. And with a typo in the file, a no written as a quoted or dotted key, or in an
        inline table, wasn't found, so the answers' yes turned the read on."""
        from studio import generate
        from studio.__main__ import apply_background_stats

        self.onboard_main()                     # the answers say yes
        text = P.SETTINGS.read_text(encoding="utf-8")
        line = "background_stats = true"
        self.assertEqual(text.count(line), 1)
        without = text.replace(line + "\n", "")
        typo = '\nsubjects_extra = "a snowball\n'

        def verdict(body: str) -> tuple[bool, bool, list[str]]:
            P.SETTINGS.write_text(body, encoding="utf-8")
            said: list[str] = []
            on = generate.background_stats(profiles.main(), say=said.append)
            with mock.patch.dict(os.environ, {"STUDIO_DRY_SYSTEM": "1"}):
                job = "(dry run) would run" in apply_background_stats()
            return on, job, said

        at_end = "background_stats under [ai] belongs under [platforms]"
        misplaced = {
            "moved to the end": (without + "background_stats = false\n", at_end),
            "added at the end, the yes left": (text + "background_stats = false\n", at_end),
            "at the top": ("background_stats = false\n" + without,
                           "background_stats at the top of the file belongs under [platforms]"),
            "under [platforms.handles]": (text.replace('youtube = ""', 'youtube = ""\nbackground_stats = false'),
                                          "background_stats under [platforms.handles] belongs under [platforms]"),
            "with a hyphen": (text.replace(line, "background-stats = false"),
                              "background-stats under [platforms] should be written background_stats"),
            "in capitals": (text.replace(line, "Background_Stats = false"),
                            "Background_Stats under [platforms] should be written background_stats"),
            "in capitals at the end": (without + "BACKGROUND-STATS = false\n",
                                       "BACKGROUND-STATS under [ai] belongs under [platforms], written "
                                       "background_stats"),
        }
        for what, (body, reason) in misplaced.items():
            with self.subTest(what):
                P.SETTINGS.write_text(body, encoding="utf-8")
                with self.assertRaises(settings.SettingsError) as raised:
                    settings.load()
                self.assertEqual(raised.exception.reason, reason)
                self.assertIn(f"can't be read: {reason}. Fix it in a text editor", str(raised.exception))
                for where, b in (("as it is", body), ("with a typo too", body + typo)):   # the two paths agree
                    on, job, said = verdict(b)
                    self.assertEqual((on, job), (False, False), where)
                    self.assertEqual(len(said), 1, said)
                    self.assertIn(", so whether its stats are read every hour goes by its background_stats line.",
                                  said[0])
        # a yes written there is a mistake too, and stays a yes
        on, job, said = verdict(without + "background_stats = true\n")
        self.assertEqual((on, job), (True, True))
        self.assertIn(f"has a mistake in it ({at_end})", said[0])
        # with a typo in the file, a no in any form TOML has for the key is a no, and a yes a yes
        inline = ('platforms = { enabled = ["tiktok", "youtube"], background_stats = false, handles = { tiktok = '
                  '"@nightphysics" } }\n' + text.split("[platforms]")[0] + "[schedule]" + text.split("[schedule]")[1])
        forms = {
            "a quoted key": text.replace(line, '"background_stats" = false'),
            "a quoted key in single quotes": text.replace(line, "'background_stats' = false"),
            "a dotted key at the top": "platforms.background_stats = false\n" + without,
            "a dotted key with spaces": 'platforms . "background_stats" = false\n' + without,
            "an inline table": inline,
        }
        for what, body in forms.items():
            with self.subTest(what):
                self.assertEqual(verdict(body + typo)[:2], (False, False))
                self.assertEqual(verdict(body.replace("= false", "= true") + typo)[:2], (True, True))
        self.assertEqual(verdict(inline)[:2], (False, False))   # (which reads as it is: it's under platforms)

    def test_a_no_for_the_hourly_read_with_a_space_or_nothing_between_its_words_is_a_no(self):
        """A no written `background stats = false` (which TOML refuses), `"background stats" = false` or
        `backgroundstats = false` (which it reads) wasn't taken for the setting at all, so the answers' yes, or the
        default yes, kept the hourly read of your stats, with your log-ins, on, without a word."""
        from studio import generate
        from studio.__main__ import apply_background_stats

        self.onboard_main()                     # the answers say yes
        text = P.SETTINGS.read_text(encoding="utf-8")
        line = "background_stats = true"
        without = text.replace(line + "\n", "")
        typo = '\nsubjects_extra = "a snowball\n'

        def verdict(body: str) -> tuple[bool, bool, list[str]]:
            P.SETTINGS.write_text(body, encoding="utf-8")
            said: list[str] = []
            on = generate.background_stats(profiles.main(), say=said.append)
            with mock.patch.dict(os.environ, {"STUDIO_DRY_SYSTEM": "1"}):
                job = "(dry run) would run" in apply_background_stats()
            return on, job, said

        spelled = {
            "a space, in quotes": (text.replace(line, '"background stats" = false'),
                                   "background stats under [platforms] should be written background_stats"),
            "run together": (text.replace(line, "backgroundstats = false"),
                             "backgroundstats under [platforms] should be written background_stats"),
            "a space and capitals, in quotes, at the end": (without + "'Background  Stats' = false\n",
                                                            "Background  Stats under [ai] belongs under [platforms], "
                                                            "written background_stats"),
        }
        for what, (body, reason) in spelled.items():
            with self.subTest(what):
                P.SETTINGS.write_text(body, encoding="utf-8")
                with self.assertRaises(settings.SettingsError) as raised:
                    settings.load()
                self.assertEqual(raised.exception.reason, reason)
                for b in (body, body + typo):
                    on, job, said = verdict(b)
                    self.assertEqual((on, job), (False, False))
                    self.assertIn("goes by its background_stats line", said[0])
                    self.assertEqual(verdict(b.replace("= false", "= true"))[:2], (True, True))
        # with a space and no quotes, TOML refuses the line: it's still the file's own no that counts
        for body in (text.replace(line, "background stats = false"), without + "BACKGROUND STATS = false\n"):
            with self.subTest(body[-30:]):
                on, job, said = verdict(body)
                self.assertEqual((on, job), (False, False))
                self.assertIn("goes by its background_stats line", said[0])
                self.assertEqual(verdict(body.replace("= false", "= true"))[:2], (True, True))
        # a key that only starts or ends like it is another key
        on, job, said = verdict(text + 'background_statistics = false\nmy_background_stats = false\n')
        self.assertEqual((on, job, said), (True, True, []))

    def test_a_file_saved_by_notepad_with_a_byte_order_mark_still_reads(self):
        self.onboard_main()
        P.SETTINGS.write_bytes(b"\xef\xbb\xbf" + b'[captions]\r\nmax_hashtags = 2\r\n')
        self.assertEqual(settings.load()["captions"]["max_hashtags"], 2)

    def test_writing_it_again_from_the_answers_is_the_way_out_the_error_gives(self):
        self.onboard_main()
        P.SETTINGS.write_text(BROKEN["an unfinished string"][0], encoding="utf-8")
        with redirect_stdout(io.StringIO()):
            self.assertEqual(onboard(["--regenerate"]), 0)
        self.assertEqual(settings.load()["channel"]["name"], "Night Physics")


class Commands(Scratch):
    def setUp(self):
        super().setUp()
        self.onboard_main()
        for patch in (mock.patch("studio.reset.finish_pending", return_value=None),
                      mock.patch("studio.move.finish_leftover"),
                      mock.patch.dict(os.environ, {"STUDIO_DRY_SYSTEM": "1"}),
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

    def test_doctor_reports_a_typo_in_the_windows_settings_too(self):
        cfg = P.DASHBOARD / "config.toml"
        cfg.write_text(cfg.read_text(encoding="utf-8").replace("refresh_minutes = 30", "refresh_minutes = 3o"),
                       encoding="utf-8")
        code, out, _ = self.run_main("doctor", "--json")
        rows = {c["what"]: c for c in json.loads(out)["checks"]}
        self.assertEqual((code, rows["Settings"]["status"]), (1, doctor.FAIL))
        self.assertIn(f"{cfg} has a typo (", rows["Settings"]["detail"])
        self.assertIn("line 6", rows["Settings"]["detail"])
        self.assertNotIn("Dashboard", rows)               # not "configured" while it can't be read
        self.assertEqual(rows["First-time setup"]["status"], doctor.OK)

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
