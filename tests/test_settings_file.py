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

    def test_a_no_for_the_hourly_read_with_a_colon_or_spelled_as_the_setting_is_taken_but_never_from_a_text(self):
        """With a mistake in studio.toml, the hourly read of your stats goes by the file's own background_stats lines.
        A no written with a colon, as answers.json has it (`"background_stats": false`), wasn't found, nor one spelled
        as ReelForge takes the setting elsewhere (`_background_stats`, `"background stats "`, the singular
        `background_stat`), so the answers' yes kept the read, with your log-ins, on. And words in a text setting or a
        comment (about = "Space facts, background stats = none") were taken for that line: a no nobody gave, which
        writing the file again from your answers then put in them, or a yes over the answers' no."""
        import shlex

        from studio import generate

        self.onboard_main()                     # the answers say yes
        text = P.SETTINGS.read_text(encoding="utf-8")
        line = "background_stats = true"
        typo = '\nsubjects_extra = "a snowball\n'

        def verdict(body: str, who=None) -> tuple[bool, list[str]]:
            P.SETTINGS.write_text(body, encoding="utf-8")
            said: list[str] = []
            return generate.background_stats(profiles.main(), say=said.append), said

        for form in ("background_stats: false", '"background_stats": false', '"background_stats": false,',
                     "'background_stats' : false", "background_stats:false", "background_stat = false",
                     '"background stats " = false', "_background_stats = false", "back-ground_stats = false",
                     '"Background-Stats-" = no', "BackgroundStat = off"):
            with self.subTest(form):
                for body in (text.replace(line, form), text.replace(line, form) + typo):
                    on, said = verdict(body)
                    self.assertIs(on, False, said)
                    self.assertIn("goes by its background_stats line", said[0])
                    yes = form.replace("false", "true").replace("no", "yes").replace("off", "on")
                    self.assertIs(verdict(body.replace(form, yes))[0], True)
        with self.assertRaises(settings.SettingsError) as raised:
            settings.parse(text.replace(line, "background_stat = false"))
        self.assertEqual(raised.exception.reason, "background_stat under [platforms] should be written background_stats")
        # words in a text or a comment: the file's own line has the say, and with none, the answers
        about = 'about = "What-if videos with real science."'
        self.assertIn(about, text)
        for words in ('about = "Space facts, background stats = none, ever"',
                      'about = "Charts, e.g. background stats = boring"',
                      "about = 'Clean look. Backgroundstats: 0 of them'",
                      'about = "What-if videos."   # fewer, background_stats = false'):
            with self.subTest(words):
                on, said = verdict(text.replace(about, words) + typo)
                self.assertIs(on, True, said)
                self.assertIn("goes by its background_stats line", said[0])
                self.onboard_main(background_stats=False)
                on, said = verdict(text.replace(about, words.replace("none", "on").replace("false", "true"))
                                   .replace(line + "\n", "") + typo)
                self.assertIs(on, False, said)
                self.assertIn("goes by its answers", said[0])
                self.onboard_main()
        # and the way out the error gives writes the yes back, not a no nobody gave
        P.SETTINGS.write_text(text.replace(about, 'about = "Space facts, background stats = none"') + typo,
                              encoding="utf-8")
        with self.assertRaises(settings.SettingsError) as raised:
            settings.load()
        argv = shlex.split(str(raised.exception).split("with: ", 1)[1])
        with mock.patch.dict(os.environ, {"STUDIO_DRY_SYSTEM": "1"}), redirect_stdout(io.StringIO()) as out:
            self.assertEqual(main(argv[1:]), 0, out.getvalue())
        self.assertIs(settings.load()["platforms"]["background_stats"], True)
        self.assertNotIn("left off", out.getvalue())

    def test_only_a_text_whose_quotes_are_closed_hides_the_words_in_it(self):
        """With a mistake in studio.toml, words in a text are never taken for the hourly read's background_stats line.
        But a text was taken to be on one line, and one whose quote was left open ran to the end of its line: so a no
        after a quote left open on its line (an inline [platforms] table with a typo in it) wasn't taken, and writing
        the file again from your answers put their yes over it, and the hourly read, with your log-ins, was on; and a
        text of several lines (\"\"\"...\"\"\") hid nothing, so prose in it with the colon or singular forms
        ("Background stats: none on screen") was a no nobody gave, which Change your answers started from and writing
        the file again put in the answers too. Now only a text whose quotes are closed hides what's in it."""
        import json

        from studio import generate

        self.onboard_main()                     # the answers say yes
        text = P.SETTINGS.read_text(encoding="utf-8")
        line = "background_stats = true"
        typo = '\nsubjects_extra = "a snowball\n'
        start = text.index("[platforms]")
        platforms = text[start:text.index("\n[", start + 1)]

        def verdict(body: str, raw: bytes | None = None) -> bool:
            P.SETTINGS.write_bytes(raw if raw is not None else body.encode("utf-8"))
            with self.assertRaises(settings.SettingsError):
                settings.load()
            said: list[str] = []
            on = generate.background_stats(profiles.main(), say=said.append)
            self.assertIn("goes by its background_stats line", said[0])
            return on

        def regenerate() -> tuple[bool, bool]:
            with mock.patch.dict(os.environ, {"STUDIO_DRY_SYSTEM": "1"}), redirect_stdout(io.StringIO()) as out:
                self.assertEqual(main(["-p", "1", "channel", "--regenerate"]), 0, out.getvalue())
            answered = json.loads(P.ANSWERS.read_text(encoding="utf-8"))["background_stats"]
            return answered, settings.load()["platforms"]["background_stats"]

        # a quote left open hides nothing: not the no after it on its line ...
        inline = 'platforms = { enabled = ["tiktok", "youtube], background_stats = false }'
        self.assertIs(verdict(text.replace(platforms, inline)), False)
        self.assertIs(verdict(text.replace(platforms, inline.replace("false", "true"))), True)
        self.assertIs(verdict(text.replace('about = "What-if videos with real science."',
                                           "about = 'What-if videos, background_stats = false")), False)
        self.assertIs(verdict(text.replace(platforms, inline)), False)
        self.assertEqual(regenerate(), (False, False))      # and the way out keeps it
        self.onboard_main()
        # ... nor the lines below a text of several lines left open
        self.assertIs(verdict(text.replace("[format]\n", '[format]\nstyle_long = """\nLow-poly worlds.\n', 1)
                              .replace(line, "background_stats = false")), False)
        # a text of several lines whose quotes are closed hides what's in it, as one on one line does
        closed = {
            "prose with a colon": '"""\nLow-poly worlds.\nBackground stats: none on screen, just the counter.\n"""',
            "in single quotes": "'''\nbackground_stats = false\n'''",
            "with quotes in it": '"""\nShe said \\"""\nbackground_stat = false\n"""',
            "ending with a quote": '""""Calm," she said.\nbackground_stats: off""""',
            "two of them": '"""a"""\nnotes = """\nbackground_stats = false\n"""',
        }
        for what, words in closed.items():
            with self.subTest(what):
                body = text.replace("[format]\n", f"[format]\nstyle_long = {words}\n", 1) + typo
                self.assertIs(verdict(body), True)
                self.assertIs(verdict(body.replace(line, "background_stats = false")), False)   # the file's own no
                # saved by Notepad, with a byte order mark and Windows line endings
                self.assertIs(verdict("", b"\xef\xbb\xbf" + body.replace("\n", "\r\n").encode("utf-8")), True)
        body = text.replace("[format]\n", f"[format]\nstyle_long = {closed['prose with a colon']}\n", 1) + typo
        self.assertIs(verdict(body), True)
        self.assertIs(generate.as_read(json.loads(P.ANSWERS.read_text(encoding="utf-8")), profiles.main())
                      ["background_stats"], True)       # Change your answers starts from the yes
        self.assertEqual(regenerate(), (True, True))
        # with no line of its own, the answers have the say
        self.onboard_main(background_stats=False)
        P.SETTINGS.write_text(body.replace(line + "\n", ""), encoding="utf-8")
        said: list[str] = []
        self.assertIs(generate.background_stats(profiles.main(), say=said.append), False)
        self.assertIn("goes by its answers", said[0])

    def test_a_long_run_of_spaces_in_a_file_with_a_mistake_is_looked_through_at_once(self):
        """While studio.toml has a mistake in it, the hourly read of your stats goes by the file's own background_stats
        lines, which were looked for in a way that took longer and longer on a line of spaces or tabs with no = or
        colon in it (a pasted blank line, say): 3,200 spaces took 46 seconds, and the hourly job, Change your answers
        and writing the file again from your answers all waited for it. Each run of them here is looked through in a
        separate Python, which is stopped if it takes too long."""
        import subprocess
        import sys

        self.onboard_main()
        text = P.SETTINGS.read_text(encoding="utf-8")
        text = text.replace("background_stats = true", "background_stats = false") + '\nsubjects_extra = "a snowball\n'
        runs = {"spaces": " " * 100_000, "tabs": "\t" * 100_000, "spaces between two words": "a" + " " * 100_000 + "b",
                "tabs and spaces between words": "about stats" + " \t" * 50_000 + "background stats"}
        files = []
        for i, (what, run) in enumerate(runs.items()):
            f = P.SETTINGS.with_name(f"studio.{i}.toml")
            f.write_text(text + run + "\n", encoding="utf-8")
            files.append(str(f))
        look = ("import json, sys, time\nfrom pathlib import Path\nfrom studio import generate\nfor f in sys.argv[1:]:\n"
                "    start = time.perf_counter()\n    said = generate._own_background_stats(Path(f))\n"
                "    print(json.dumps([said, time.perf_counter() - start]), flush=True)\n")
        kit = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        try:
            done = subprocess.run([sys.executable, "-c", look, *files], cwd=kit, capture_output=True, text=True,
                                  timeout=60)
        except subprocess.TimeoutExpired as e:
            self.fail(f"still looking after a minute, through {len((e.stdout or b'').splitlines())} of {len(runs)}")
        self.assertEqual(done.returncode, 0, done.stderr)
        for what, line in zip(runs, done.stdout.splitlines(), strict=True):
            with self.subTest(what):
                said, took = json.loads(line)
                self.assertIs(said, False)      # the file's own no, found all the same
                self.assertLess(took, 0.5)

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

    def test_writing_it_again_from_the_answers_keeps_the_files_no_to_the_hourly_read(self):
        """While studio.toml has a mistake in it, the hourly read of your stats goes by the file's own
        background_stats lines. The way out the error gives, writing the file again from your answers, put the
        answers' yes back over a no in it (one in the wrong place, or one under [platforms] in a file with a typo
        elsewhere) without a word, and the read, with your log-ins, was on again."""
        import shlex

        from studio import generate
        from studio.__main__ import apply_background_stats

        self.onboard_main()                     # the answers say yes
        text = P.SETTINGS.read_text(encoding="utf-8")
        line = "background_stats = true"
        typo = '\nsubjects_extra = "a snowball\n'

        def follow_the_advice(body: str) -> str:
            P.SETTINGS.write_text(body, encoding="utf-8")
            with self.assertRaises(settings.SettingsError) as raised:
                settings.load()
            argv = shlex.split(str(raised.exception).split("with: ", 1)[1])   # as a shell splits it
            self.assertEqual(argv[-2:], ["channel", "--regenerate"])
            with mock.patch.dict(os.environ, {"STUDIO_DRY_SYSTEM": "1"}), redirect_stdout(io.StringIO()) as out:
                self.assertEqual(main(argv[1:]), 0, out.getvalue())
            return out.getvalue()

        def answered(who=None) -> bool:
            return json.loads((who or profiles.main()).answers.read_text(encoding="utf-8"))["background_stats"]

        for what, body in {"a no at the end, under [ai]": text + "background_stats = false\n",
                           "a no under [platforms], a typo elsewhere": text.replace(line, "background_stats = false")
                           + typo,
                           "a no spelled with a space": text.replace(line, "background stats = false")}.items():
            with self.subTest(what):
                self.onboard_main()
                printed = follow_the_advice(body)
                self.assertIs(settings.load()["platforms"]["background_stats"], False)
                self.assertIs(generate.background_stats(profiles.main()), False)
                with mock.patch.dict(os.environ, {"STUDIO_DRY_SYSTEM": "1"}):
                    self.assertNotIn("(dry run) would run", apply_background_stats())
                self.assertIs(answered(), False)     # and the next time it's written from them, too
                self.assertIn("so it was written again from your answers, with the hourly read of its stats left "
                              "off, as its background_stats line had it", " ".join(printed.split()))
        # a yes in a file with a mistake in it is the answers' business as before, and so is a file without the line
        for body, answers_say, on in ((text.replace(line, "background_stats = true") + typo, False, False),
                                      (text.replace(line + "\n", "") + typo, True, True)):
            with self.subTest(body=body[-60:], answers_say=answers_say):
                self.onboard_main(background_stats=answers_say)
                printed = follow_the_advice(body)
                self.assertIs(settings.load()["platforms"]["background_stats"], on)
                self.assertNotIn("hourly read", printed)
        # answers given now (the questions asked again, or a file of them) are the say on it
        self.onboard_main()
        P.SETTINGS.write_text(text + "background_stats = false\n", encoding="utf-8")
        given = self.root / "given.json"
        given.write_text(json.dumps({**ANSWERS, "background_stats": True}), encoding="utf-8")
        with mock.patch.dict(os.environ, {"STUDIO_DRY_SYSTEM": "1"}), redirect_stdout(io.StringIO()):
            self.assertEqual(main(["channel", "--answers", str(given)]), 0)
        self.assertIs(settings.load()["platforms"]["background_stats"], True)
        # another channel's way out, from the line the hourly job logs about it
        b = profiles.create("Bee Facts")
        with redirect_stdout(io.StringIO()):
            generate.write_all({**ANSWERS, "channel_name": "Bee Facts"}, b)
        good = b.settings.read_text(encoding="utf-8")
        b.settings.write_text(good + "background_stats = false\n", encoding="utf-8")
        said: list[str] = []
        self.assertIs(generate.background_stats(b, say=said.append), False)
        argv = shlex.split(said[0].split("with: ", 1)[1])
        with mock.patch.dict(os.environ, {"STUDIO_DRY_SYSTEM": "1"}), redirect_stdout(io.StringIO()) as out:
            self.assertEqual(main(argv[1:]), 0, out.getvalue())
        self.assertIs(settings.load(b.settings)["platforms"]["background_stats"], False)
        self.assertIs(generate.background_stats(b), False)
        self.assertIs(answered(b), False)
        self.assertIs(settings.load()["platforms"]["background_stats"], True)     # the first channel's stays

    def test_writing_the_files_again_from_the_answers_keeps_a_no_to_the_hourly_read_in_a_file_that_reads(self):
        """studio.toml invites edits by hand, and the hourly read of your stats goes by its background_stats line. A
        no written there in a file that reads was put back to the answers' yes by channel --regenerate (the notes' own
        step after a brief, and what you run after editing your answers), without a word, so the read, with your
        log-ins, was on again for that channel, a second one's too."""
        import re
        import shlex

        from studio import generate
        from studio.__main__ import apply_background_stats

        def run(argv: list[str]) -> str:
            with mock.patch.dict(os.environ, {"STUDIO_DRY_SYSTEM": "1"}), redirect_stdout(io.StringIO()) as out:
                self.assertEqual(main(argv), 0, out.getvalue())
            return " ".join(out.getvalue().split())

        def answered(who) -> bool:
            return json.loads(who.answers.read_text(encoding="utf-8"))["background_stats"]

        def say_no(who) -> None:
            text = who.settings.read_text(encoding="utf-8")
            who.settings.write_text(text.replace("background_stats = true", "background_stats = false"),
                                    encoding="utf-8")
            self.assertIs(generate.background_stats(who), False)

        self.onboard_main()                     # the answers say yes
        b = profiles.create("Bee Facts")
        with redirect_stdout(io.StringIO()):
            generate.write_all({**ANSWERS, "channel_name": "Bee Facts"}, b)
        first = profiles.main()
        # the brief's step in the first channel's notes: the answers edited, then this channel's files written again
        say_no(first)
        notes = " ".join((P.ROOT / "CLAUDE.md").read_text(encoding="utf-8").split())
        step = re.search(r"On their OK, write them and run `(.+?)`", notes).group(1)
        a = json.loads(P.ANSWERS.read_text(encoding="utf-8"))
        P.ANSWERS.write_text(json.dumps({**a, "about": "What-if videos with real science, from the brief."}),
                             encoding="utf-8")
        printed = run(shlex.split(step)[3:])
        self.assertIs(settings.load()["platforms"]["background_stats"], False)
        self.assertEqual(settings.load()["channel"]["about"], "What-if videos with real science, from the brief.")
        self.assertIs(generate.background_stats(first), False)
        self.assertIs(answered(first), False)   # and the next time it's written from them, too
        self.assertIn("Night Physics's stats still aren't read every hour while ReelForge is closed: its settings "
                      f"file, {P.SETTINGS}, says no to that, so the no is kept, in your answers too. To have them read, "
                      "say yes to it in its questions: reelforge -p 1 channel", printed)
        # every channel's files written again (without -p), and another channel's alone
        for argv in (["channel", "--regenerate"], ["-p", b.kept_flag, "channel", "--regenerate"]):
            with self.subTest(argv=argv):
                b.answers.write_text(json.dumps({**json.loads(b.answers.read_text(encoding="utf-8")),
                                                 "background_stats": True}), encoding="utf-8")
                say_no(b)
                printed = run(argv)
                for who in (first, b):
                    self.assertIs(settings.load(who.settings)["platforms"]["background_stats"], False, who.name)
                    self.assertIs(answered(who), False, who.name)
                with mock.patch.dict(os.environ, {"STUDIO_DRY_SYSTEM": "1"}):
                    self.assertNotIn("(dry run) would run", apply_background_stats())
                self.assertIn(f"Bee Facts's stats still aren't read every hour while ReelForge is closed: its settings "
                              f"file, {b.settings}, says no to that", printed)
                self.assertNotIn("Night Physics's stats", printed)      # (its answers say no already: nothing kept)
        # a yes in the file goes by the answers as before, and so do answers given now
        P.SETTINGS.write_text(P.SETTINGS.read_text(encoding="utf-8").replace("background_stats = false",
                                                                             "background_stats = true"),
                              encoding="utf-8")
        printed = run(["channel", "--regenerate"])
        self.assertIs(settings.load()["platforms"]["background_stats"], False)
        self.assertNotIn("still aren't read", printed)
        given = self.root / "given.json"
        given.write_text(json.dumps({**ANSWERS, "background_stats": True}), encoding="utf-8")
        run(["channel", "--answers", str(given)])
        self.assertIs(settings.load()["platforms"]["background_stats"], True)

    def test_writing_the_files_again_over_a_yes_in_the_file_against_the_answers_no_says_so(self):
        """Writing a channel's files again from its answers keeps a no to the hourly read in its studio.toml, in the
        answers too. A yes written in the file after that, against the answers' no, which turned the read on, was put
        back to the no by the next channel --regenerate without a word: the read was off again, and the yes was only
        in the copy of the file in backups. The no stays (the read uses your log-ins, so the file never turns it on
        against the answers' no), but it's said now, with where the file's yes is and how to say yes."""
        from studio import generate

        def run(argv: list[str]) -> str:
            with mock.patch.dict(os.environ, {"STUDIO_DRY_SYSTEM": "1"}), redirect_stdout(io.StringIO()) as out:
                self.assertEqual(main(argv), 0, out.getvalue())
            return " ".join(out.getvalue().split())

        def edit(who, old: str, new: str) -> None:
            who.settings.write_text(who.settings.read_text(encoding="utf-8").replace(old, new), encoding="utf-8")

        def copies(who) -> set:
            return set(who.backups.glob("studio.*.toml"))

        def said_about(who, flag: str, before: set) -> str:
            copy = (copies(who) - before).pop()
            self.assertIn("background_stats = true", copy.read_text(encoding="utf-8"))      # the file's yes
            return (f"{who.name}'s stats aren't read every hour while ReelForge is closed: its answers say no to that, "
                    f"and its settings file, {who.settings}, was written again from them, so the yes in it is only in "
                    f"the copy of it as it was, {copy}. To have them read, say yes to it in its questions: "
                    f"reelforge -p {flag} channel")

        self.onboard_main()                     # the answers say yes
        first = profiles.main()
        edit(first, "background_stats = true", "background_stats = false")
        run(["channel", "--regenerate"])        # the file's no is kept, in the answers too
        self.assertIs(json.loads(P.ANSWERS.read_text(encoding="utf-8"))["background_stats"], False)
        # a yes written in the file (one that reads, then one with a mistake in it) against the answers' no
        for what, typo in (("in a file that reads", ""), ("in a file with a mistake in it", '\nx = "a typo\n')):
            with self.subTest(what):
                edit(first, "background_stats = false", "background_stats = true")
                P.SETTINGS.write_text(P.SETTINGS.read_text(encoding="utf-8") + typo, encoding="utf-8")
                self.assertIs(generate.background_stats(first), True)
                before = copies(first)
                printed = run(["channel", "--regenerate"])
                self.assertIs(settings.load()["platforms"]["background_stats"], False)    # the no stays
                self.assertIs(generate.background_stats(first), False)
                self.assertIs(json.loads(P.ANSWERS.read_text(encoding="utf-8"))["background_stats"], False)
                self.assertIn(said_about(first, "1", before), printed)
                self.assertEqual(printed.count("stats aren't read every hour"), 1)
        # another channel's alone, by its folder
        b = profiles.create("Bee Facts")
        with redirect_stdout(io.StringIO()):
            generate.write_all({**ANSWERS, "channel_name": "Bee Facts", "background_stats": False}, b)
        edit(b, "background_stats = false", "background_stats = true")
        before = copies(b)
        printed = run(["-p", b.kept_flag, "channel", "--regenerate"])
        self.assertIs(settings.load(b.settings)["platforms"]["background_stats"], False)
        self.assertIn(said_about(b, b.flag, before), printed)
        # the file and the answers agreeing, either way: nothing to say
        for stats in (False, True):
            with self.subTest(agreeing=stats):
                self.onboard_main(background_stats=stats)
                self.assertNotIn("every hour", run(["channel", "--regenerate"]))


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
