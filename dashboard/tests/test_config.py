"""config.toml as people really save it (dashboard.config.load_config): Windows tools add a byte-order mark or write
UTF-16, a Windows path in double quotes isn't valid TOML, and a hand edit can leave a value of the wrong kind. Each is
either read, or refused with a message that names what to fix."""

from __future__ import annotations

import codecs
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from dashboard import config, switcher, sync
from dashboard.platforms import YouTube
from studio import dashboard_config

ROOT = Path(__file__).resolve().parents[2]
TEXT = 'videos_dir = "vidéos"\nrefresh_minutes = 45\n\n[tiktok]\nhandle = "@mine"\n'


class ConfigTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "config.toml"
        patch = mock.patch.object(config, "CONFIG_PATH", self.path)
        patch.start()
        self.addCleanup(patch.stop)

    def saved(self, text: str | bytes, encoding: str = "utf-8") -> None:
        self.path.write_bytes(text if isinstance(text, bytes) else text.encode(encoding))

    def refused(self) -> str:
        with self.assertRaises(config.ConfigError) as caught:
            config.load_config()
        return str(caught.exception)


class Encodings(ConfigTest):
    def test_a_byte_order_mark_or_utf16_is_read_and_saved_again_as_utf8(self):
        for name, raw in (("utf-8 with a mark", codecs.BOM_UTF8 + TEXT.encode("utf-8")),
                          ("utf-16", TEXT.encode("utf-16")),  # as PowerShell's > writes it
                          ("utf-16 big end first", codecs.BOM_UTF16_BE + TEXT.encode("utf-16-be"))):
            self.saved(raw)
            cfg = config.load_config()
            self.assertEqual((cfg["videos_dir"], cfg["refresh_minutes"], cfg["tiktok"]["handle"]),
                             ("vidéos", 45, "@mine"), name)
            self.assertEqual(self.path.read_bytes(), TEXT.encode("utf-8"), name)  # the same text, for other readers

    def test_plain_utf8_is_left_as_it_is(self):
        self.saved(TEXT.replace("\n", "\r\n"))
        stamp = self.path.stat().st_mtime_ns
        self.assertEqual(config.load_config()["videos_dir"], "vidéos")
        self.assertEqual(self.path.stat().st_mtime_ns, stamp)
        self.assertEqual(self.path.read_bytes(), TEXT.replace("\n", "\r\n").encode("utf-8"))

    def test_another_encoding_is_refused_with_how_to_save_it(self):
        self.saved(TEXT, "cp1252")  # Notepad's ANSI: the é is one byte that isn't UTF-8
        self.assertIn("isn't saved as UTF-8 text", self.refused())
        self.assertIn("Save as", self.refused())
        self.assertEqual(self.path.read_bytes(), TEXT.encode("cp1252"))  # nothing was changed


    def test_the_other_readers_cope_with_a_file_they_cant_read(self):
        """The profile menu and first-time setup read a profile's handles themselves: a file that isn't UTF-8 has
        none they can read, and neither stops."""
        profile = SimpleNamespace(config=self.path, main=False)
        self.saved(TEXT.encode("utf-16"))
        self.assertEqual(switcher.handles(profile), {})
        self.assertIsNone(dashboard_config.handles(profile))
        config.load_config()  # its own window (or the hourly job) saves it again as UTF-8
        self.assertEqual(switcher.handles(profile), {"tiktok": "@mine"})
        self.assertEqual(dashboard_config.handles(profile)["tiktok"], "@mine")


class NoneYet(unittest.TestCase):
    """No config.toml yet: it is made from config.example.toml, in a folder made for it if need be."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        self.kit = Path(tmp.name) / "ReelForge" / "dashboard"

    def load(self, path: Path) -> dict:
        with mock.patch.object(config, "APP_DIR", self.kit), mock.patch.object(config, "CONFIG_PATH", path):
            return config.load_config()

    def test_a_new_profiles_settings_are_made_from_the_example_folder_and_all(self):
        self.kit.mkdir(parents=True)
        (self.kit / "config.example.toml").write_bytes(TEXT.encode("utf-8"))
        path = self.kit.parent / "profiles" / "night-physics" / "dashboard" / "config.toml"
        self.assertEqual(self.load(path)["tiktok"]["handle"], "@mine")
        self.assertEqual(path.read_bytes(), TEXT.encode("utf-8"))

    def test_no_folder_is_made_when_there_is_no_example_to_put_in_it(self):
        """A ReelForge folder that was moved or removed after this process started: the hourly stats read, caught
        starting up. Making the folder for the settings put a stub of the old ReelForge folder back."""
        with self.assertRaises(FileNotFoundError):
            self.load(self.kit / "config.toml")
        self.assertFalse(self.kit.parent.exists())


class Typos(ConfigTest):
    def test_a_windows_path_in_quotes_says_to_use_forward_slashes(self):
        for line in (r'''videos_dir = "C:\Users\DJ'S PC\Videos"''', r"""videos_dir = 'C:\Users\DJ'S PC\Videos'"""):
            self.saved(f"refresh_minutes = 30\n{line}\n")
            said = self.refused()
            self.assertIn("has a typo", said)
            self.assertIn("line 2", said)
            self.assertIn('forward slashes, like "C:/Users/you/Videos"', said)
        self.saved("""videos_dir = "C:/Users/DJ'S PC/Videos"\n""")
        self.assertEqual(config.load_config()["videos_dir"], "C:/Users/DJ'S PC/Videos")

    def test_another_typo_gets_no_path_advice(self):
        self.saved('refresh_minutes = \nvideos_dir = "videos"\n')
        said = self.refused()
        self.assertIn("has a typo", said)
        self.assertNotIn("forward slashes", said)


class Kinds(ConfigTest):
    def test_minutes_in_quotes_are_read_as_the_number(self):
        self.saved('refresh_minutes = "30"\n')
        cfg = config.load_config()
        self.assertEqual(cfg["refresh_minutes"], 30)
        # ... so working out when YouTube is read next doesn't trip over text
        self.assertEqual(sync.gap_minutes(YouTube({"handle": "@me"}), cfg, manual=False), 30)

    def test_minutes_below_zero_mean_only_when_you_press_refresh(self):
        self.saved("refresh_minutes = -5\n")
        self.assertEqual(config.load_config()["refresh_minutes"], 0)

    def test_what_the_released_version_opened_with_still_opens(self):
        """ReelForge 2.2.1 had no check of these kinds. A config.toml it ran with must not stop the window, and the
        hourly read with it, on the first start after an update."""
        youtube = YouTube({"handle": "@me"})
        for line, minutes in (("refresh_minutes = false", 0), ("refresh_minutes = true", 1)):
            self.saved(line + "\n")
            cfg = config.load_config()
            self.assertEqual((cfg["refresh_minutes"], type(cfg["refresh_minutes"])), (minutes, float), line)
            self.assertEqual(sync.gap_minutes(youtube, cfg, manual=False), minutes)
        for line, tags in (('default_tags = ["#fyp", "#whatif"]', "#fyp #whatif"),
                           ('default_tags = ["fyp", "whatif", ""]', "#fyp #whatif"), ("default_tags = []", "")):
            self.saved(line + "\n")
            self.assertEqual(config.load_config()["default_tags"], tags, line)

    def test_a_value_of_the_wrong_kind_is_named(self):
        for line, key in (('refresh_minutes = "often"', "refresh_minutes"),
                          ("refresh_minutes = [30]", "refresh_minutes"), ("videos_dir = 5", "videos_dir"),
                          ("completed_dir = ['a']", "completed_dir"), ("default_tags = 3", "default_tags"),
                          ("default_tags = [1, 2]", "default_tags"), ("videos_dir = ['a']", "videos_dir")):
            self.saved(line + "\n")
            said = self.refused()
            self.assertTrue(said.startswith(f"{key} in config.toml should be "), said)
            self.assertIn(f"like {key} = ", said)

    def test_settings_made_another_way_never_stop_the_schedule(self):
        """sync works out its timing from whatever the settings hold (the tests, an older window's own)."""
        youtube = YouTube({"handle": "@me"})
        for value, minutes in (("30", 30), ("soon", 30), (None, 30), (float("nan"), 0), (float("inf"), 0), (0, 0)):
            self.assertEqual(sync.gap_minutes(youtube, {"refresh_minutes": value}, manual=False), minutes, value)
        self.assertEqual(sync.gap_minutes(youtube, {}, manual=False), 30)


class Tokens(unittest.TestCase):
    def test_windows_and_the_hourly_job_dont_lose_each_others_log_ins(self):
        """tokens.json is one file for every ReelForge on a profile. Three of them writing at once used to overwrite
        each other's entries (each read the file, added its own and saved), or trip over one shared temporary
        file."""
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "tokens.json"
        script = ("import sys\nfrom pathlib import Path\nfrom dashboard import config\n"
                  "config.TOKENS_PATH = Path(sys.argv[1])\n"
                  "for i in range(8):\n    config.set_token(f'{sys.argv[2]}:{i}', {'since': i})\n")
        writers = [subprocess.Popen([sys.executable, "-c", script, str(path), name], cwd=ROOT)
                   for name in ("window", "another-window", "job")]
        self.assertEqual([w.wait(60) for w in writers], [0, 0, 0])
        tokens = json.loads(path.read_text("utf-8"))
        self.assertEqual(len(tokens), 24, sorted(tokens))
        self.assertEqual([f.name for f in path.parent.iterdir() if f.suffix == ".tmp"], [])


if __name__ == "__main__":
    unittest.main()
