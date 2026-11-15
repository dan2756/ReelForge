"""The files ReelForge writes for you go in whole (studio.paths.write_atomic): a write that's cut short leaves the
old file as it was, and first-time setup checks everything it's about to write before it changes one file. In a
scratch ReelForge folder."""

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from studio import dashboard_config, generate, jsonfmt, sounds
from studio import paths as P
from studio.__main__ import onboard

from test_profiles import ANSWERS, Scratch


class WriteAtomic(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)

    def test_text_goes_out_as_utf8_with_unix_line_endings_and_bytes_as_they_are(self):
        f = self.dir / "new folder" / "notes.md"
        P.write_atomic(f, "Café\nline two\n")
        self.assertEqual(f.read_bytes(), "Café\nline two\n".encode("utf-8"))
        P.write_atomic(f, b"\x00\x01\r\n")
        self.assertEqual(f.read_bytes(), b"\x00\x01\r\n")
        self.assertEqual([p.name for p in f.parent.iterdir()], ["notes.md"])     # no temporary file left

    def test_a_write_that_fails_leaves_the_old_file_whole(self):
        f = self.dir / "studio.toml"
        f.write_text("old and complete\n", encoding="utf-8")
        for stopped in (mock.patch.object(P.os, "replace", side_effect=OSError(28, "No space left on device")),
                        mock.patch.object(P.os, "fsync", side_effect=OSError(5, "Input/output error"))):
            with stopped, self.assertRaises(OSError):
                P.write_atomic(f, "new")
            self.assertEqual(f.read_text(encoding="utf-8"), "old and complete\n")
            self.assertEqual([p.name for p in self.dir.iterdir()], ["studio.toml"])

    def test_a_file_someone_is_reading_is_tried_again_for_a_moment(self):
        # Windows refuses to replace a file another program has open (the hourly job reading config.toml)
        f = self.dir / "config.toml"
        f.write_text("old\n", encoding="utf-8")
        real, tries = os.replace, []

        def busy_twice(src, dest):
            tries.append(dest)
            if len(tries) < 3:
                raise PermissionError(13, "The process cannot access the file because it is being used")
            real(src, dest)

        with mock.patch.object(P.os, "replace", busy_twice), mock.patch("time.sleep"):
            P.write_atomic(f, "new\n")
        self.assertEqual((f.read_text(encoding="utf-8"), len(tries)), ("new\n", 3))
        with mock.patch.object(P.os, "replace", side_effect=PermissionError(13, "in use")), \
                mock.patch("time.sleep"), self.assertRaises(PermissionError):
            P.replace(self.dir / "a", f, wait=0)                                  # still busy: it says so


class EveryFile(Scratch):
    """What first-time setup, the Accounts tab and new-episode write: each through write_atomic."""

    def stop_at(self, name: str):
        real = P.replace

        def replace(src, dest, wait=3.0):
            if Path(dest).name == name:
                raise OSError(28, "No space left on device")
            real(src, dest, wait)

        return mock.patch.object(P, "replace", replace)

    def test_a_disk_that_fills_up_part_way_never_leaves_half_a_file(self):
        self.onboard_main()
        for f in (P.ANSWERS, P.SETTINGS, P.BIBLE, P.ROOT / "CLAUDE.md", P.ROOT / "AGENTS.md",
                  dashboard_config.config_toml()):
            with self.subTest(file=f.name):
                before = f.read_bytes()
                with self.stop_at(f.name), self.assertRaises(OSError):
                    generate.write_all({**ANSWERS, "channel_name": "Renamed", "handle_tiktok": "@renamed"})
                self.assertEqual(f.read_bytes(), before)
                self.assertEqual(list(f.parent.glob("*.tmp")), [])
                self.onboard_main()                      # back to where it started

    def test_the_other_writers_too(self):
        self.onboard_main()
        cfg = dashboard_config.config_toml()
        before = cfg.read_bytes()
        with self.stop_at("config.toml"), self.assertRaises(OSError):
            dashboard_config.set_handle("youtube", "@night")
        self.assertEqual(cfg.read_bytes(), before)
        spec = self.root / "episode.json"
        jsonfmt.write(spec, {"id": "ep01"})
        with self.stop_at("episode.json"), self.assertRaises(OSError):
            jsonfmt.write(spec, {"id": "ep02"})
        self.assertEqual(json.loads(spec.read_text(encoding="utf-8")), {"id": "ep01"})
        claude = P.ROOT / ".claude" / "settings.local.json"
        claude.unlink()
        with self.stop_at("settings.local.json"), self.assertRaises(OSError):
            generate.write_claude_settings({"ai": {"assistant": ["claude"]}})
        self.assertFalse(claude.exists())

    def test_a_sound_cut_short_isnt_left_looking_downloaded(self):
        sfx = self.root / "sfx"
        meta = {"file": "mixkit/boom.wav", "id": "1", "source": "mixkit"}
        with mock.patch.object(sounds, "SFX", sfx), mock.patch.object(sounds, "manifest", lambda: {"sounds": {}}), \
                mock.patch.object(sounds, "missing", lambda: [("boom", meta)]), \
                mock.patch.object(sounds.net, "fetch_bytes", return_value=b"RIFF" * 100), \
                self.stop_at("boom.wav"):
            self.assertEqual(sounds.fetch_mixkit(log=lambda s: None), ["boom"])
        self.assertEqual(list(sfx.rglob("*.*")), [])


class CheckedFirst(Scratch):
    """Whatever stops first-time setup's writing stops it before a file has changed, and names the file at fault."""

    def files(self) -> dict:
        return {p: p.read_bytes() for p in (P.ANSWERS, P.SETTINGS, P.BIBLE, P.ROOT / "CLAUDE.md", P.ROOT / "AGENTS.md",
                                           dashboard_config.config_toml())}

    def test_a_typo_in_the_dashboards_settings_is_named_and_nothing_is_written(self):
        self.onboard_main()
        cfg = dashboard_config.config_toml()
        cfg.write_text(cfg.read_text(encoding="utf-8").replace("refresh_minutes = 30", "refresh_minutes = 3o"),
                       encoding="utf-8")
        self.assertIn("refresh_minutes = 3o", cfg.read_text(encoding="utf-8"))
        before = self.files()
        changed = {**ANSWERS, "channel_name": "Renamed", "about": "Something else."}
        with self.assertRaises(ValueError) as raised:
            generate.write_all(changed)
        self.assertTrue(str(raised.exception).startswith(f"{cfg} has a typo ("), raised.exception)
        self.assertEqual(self.files(), before)
        self.assertFalse(P.BACKUPS.exists())            # not even a copy of the bible "as it was"
        with self.assertRaises(ValueError) as raised:   # the Accounts tab's own write says the same
            dashboard_config.set_handle("youtube", "@night")
        self.assertIn(str(cfg), str(raised.exception))
        src = self.root / "answers.json"
        src.write_text(json.dumps(changed), encoding="utf-8")
        with redirect_stdout(io.StringIO()) as out, mock.patch.dict(os.environ, {"STUDIO_DRY_SYSTEM": "1"}):
            self.assertEqual(onboard(["--answers", str(src)]), 1)
        self.assertIn(f"{cfg} has a typo (", out.getvalue())

    def test_notes_too_long_for_codex_are_a_message_and_nothing_is_written(self):
        self.onboard_main()
        before = self.files()
        long = {**ANSWERS, "format": "custom", "series": "Top 5",
                "custom_format": "A very long description of the format. " * 800}
        with self.assertRaises(ValueError) as raised:
            generate.write_all(long)
        self.assertIn("AGENTS.md came out at", str(raised.exception))
        self.assertIn("Codex reads only 32 KiB", str(raised.exception))
        self.assertEqual(self.files(), before)


if __name__ == "__main__":
    unittest.main()
