"""doctor's "YouTube API" row (studio/doctor.py), in a scratch ReelForge folder with two channels: whether each one
has given ReelForge the client file of its Google Cloud project. It is fine or a warning, never a failure (posting
through YouTube's API is optional), and it never shows anything that is in a channel's file."""

import io
import json
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from dashboard import db
from dashboard.platforms import youtube_api
from studio import doctor, generate, profiles

from test_profiles import ANSWERS, Scratch

ID, SECRET = "1234567890-abc.apps.googleusercontent.com", "GOCSPX-made-up-for-the-tests"


class YouTubeApiRow(Scratch):
    def setUp(self):
        super().setUp()
        self.onboard_main()
        patch = mock.patch.object(db, "_path", None)  # no stats open: the profile says whose file it is
        patch.start()
        self.addCleanup(patch.stop)

    def second(self) -> profiles.Profile:
        with redirect_stdout(io.StringIO()):
            other = profiles.create("Cool Epic Facts")
            generate.write_all({**ANSWERS, "channel_name": "Cool Epic Facts"}, other)
        return other

    def give_client(self, profile: profiles.Profile | None = None) -> Path:
        """The channel's file with a client on it, put there by the command a person runs."""
        downloaded = self.root / "downloads" / "client_secret_1234567890-abc.json"
        downloaded.parent.mkdir(exist_ok=True)
        downloaded.write_text(json.dumps({"installed": {"client_id": ID, "client_secret": SECRET,
                                                        "token_uri": "https://example.invalid/token"}}), "utf-8")
        profiles.use(profile or profiles.main())
        with redirect_stdout(io.StringIO()):
            self.assertEqual(youtube_api.main(["client", str(downloaded)]), 0)
        return youtube_api.store_path()

    def row(self) -> tuple[str, str, str]:
        report = doctor.Report(quiet=True)
        doctor.youtube_api_check(report)
        (row,) = report.rows
        self.assertFalse(report.failed)
        for secret in (ID, SECRET, "GOCSPX", "googleusercontent"):
            self.assertNotIn(secret, " ".join(row))
        return row

    def test_none_is_a_pass_that_says_it_is_optional(self):
        self.assertEqual(self.row(), (doctor.OK, "YouTube API", "not set up (optional: docs/YOUTUBE-API.md)"))
        self.second()
        self.assertEqual(self.row()[2], "not set up (optional: docs/YOUTUBE-API.md)")

    def test_a_client_on_file_is_said_and_nothing_in_the_file_is(self):
        store = self.give_client()
        self.assertEqual(store, self.root / "dashboard" / "data" / "youtube_api.json")
        self.assertEqual(json.loads(store.read_text("utf-8")), {"client_id": ID, "client_secret": SECRET})
        self.assertEqual(self.row(), (doctor.OK, "YouTube API", "client on file. You connect in ReelForge's window "
                                                                "each time it opens (docs/YOUTUBE-API.md)"))

    def test_with_several_channels_it_says_whose(self):
        other = self.second()
        self.give_client()
        self.assertEqual(self.row()[2], "client on file for Night Physics (not set up for Cool Epic Facts). You "
                                        "connect in ReelForge's window each time it opens (docs/YOUTUBE-API.md)")
        store = self.give_client(other)
        self.assertEqual(store, other.data / "youtube_api.json")
        self.assertEqual(self.row()[2], "client on file for Night Physics, Cool Epic Facts. You connect in "
                                        "ReelForge's window each time it opens (docs/YOUTUBE-API.md)")

    def test_choices_without_a_client_are_not_a_client(self):
        youtube_api.set_defaults("unlisted")   # who can see a post, chosen before any client file was given
        self.assertTrue(youtube_api.store_path().is_file())
        self.assertEqual(self.row()[2], "not set up (optional: docs/YOUTUBE-API.md)")
        half = youtube_api.store_path()
        half.write_text(json.dumps({"client_id": ID}), "utf-8")   # an id and no secret can't connect either
        self.assertEqual(self.row()[2], "not set up (optional: docs/YOUTUBE-API.md)")

    def test_a_file_that_cannot_be_read_is_a_warning_that_says_what_to_do(self):
        store = self.give_client()
        for broken in ('{"client_id": "' + ID + '", "client_secret": "' + SECRET + '"', json.dumps([ID, SECRET])):
            store.write_text(broken, "utf-8")
            status, _, said = self.row()   # (row() checks that what is left of the file isn't shown)
            self.assertEqual(status, doctor.WARN)
            self.assertIn(f"{Path('dashboard') / 'data' / 'youtube_api.json'} can't be read", said)
            self.assertTrue(said.endswith("again: reelforge youtube-api client FILE"), said)

    def test_with_several_channels_the_command_it_gives_is_for_the_channel_whose_file_it_is(self):
        # Left to itself the command works on the first channel: it would leave the broken file as it is, and give
        # the first channel a client it never asked for.
        other = self.second()
        theirs = self.give_client(other)
        theirs.write_text("{not json", "utf-8")
        profiles.use(profiles.main())
        status, _, said = self.row()
        self.assertEqual(status, doctor.WARN)
        self.assertEqual(said, f"{Path('profiles') / 'cool-epic-facts' / 'dashboard' / 'data' / 'youtube_api.json'} "
                               "can't be read, so ReelForge has no client there. Give it the client file again: "
                               "reelforge -p profiles/cool-epic-facts youtube-api client FILE")
        # The command as it is printed, with the file: the warning goes, and the first channel has no file still.
        from studio.__main__ import main

        command = said.split("again: ")[1].split()
        self.assertEqual(command[0], "reelforge")
        downloaded = str(self.root / "downloads" / "client_secret_1234567890-abc.json")
        with redirect_stdout(io.StringIO()):
            self.assertEqual(main([downloaded if word == "FILE" else word for word in command[1:]]), 0)
        self.assertEqual(self.row()[2], "client on file for Cool Epic Facts (not set up for Night Physics). You "
                                        "connect in ReelForge's window each time it opens (docs/YOUTUBE-API.md)")
        self.assertFalse((self.root / "dashboard" / "data" / "youtube_api.json").exists())
        # Both broken: each file, and each channel's own command (the first one's too, now that there are two).
        mine = self.give_client()
        for store in (mine, theirs):
            store.write_text("{not json", "utf-8")
        said = self.row()[2]
        first, second = Path("dashboard") / "data" / "youtube_api.json", Path("profiles") / "cool-epic-facts"
        self.assertIn(f"{first}, {second}", said)
        self.assertTrue(said.endswith("again: reelforge -p 1 youtube-api client FILE and reelforge -p "
                                      "profiles/cool-epic-facts youtube-api client FILE"), said)

    def test_whatever_else_goes_wrong_is_a_warning_without_the_errors_own_words(self):
        self.give_client()
        with mock.patch.object(youtube_api, "_usable", side_effect=RuntimeError(f"boom {SECRET}")):
            self.assertEqual(self.row(), (doctor.WARN, "YouTube API", "couldn't be checked (RuntimeError)"))

    def test_it_comes_right_after_the_extensions_row(self):
        with mock.patch.object(doctor, "window_check"), mock.patch.object(doctor.tools, "find_blender", lambda: None), \
                mock.patch.object(doctor.tools, "find_ffmpeg", lambda: None):
            rows = [what for _, what, _ in doctor.checks(quiet=True).rows]
        self.assertEqual(rows[rows.index("Browser extension") + 1], "YouTube API")
