"""The sound cache: a mix needs the sounds its own cue sheet names, not every sound in the manifest."""

import io
import json
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from pipeline import qa  # noqa: F401  (puts pipeline/ on the path, as the scripts do for themselves)
from post import audio_mix, sfx_cache


class SoundCache(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.sfx = Path(tmp.name) / "sfx"
        (self.sfx / "kenney").mkdir(parents=True)
        (self.sfx / "kenney" / "pop.ogg").write_bytes(b"a sound that ships with the kit")
        (self.sfx / "kenney" / "bent.ogg").write_bytes(b"a download that arrived broken")
        sounds = {"pop": {"file": "kenney/pop.ogg"}, "bent": {"file": "kenney/bent.ogg"},
                  "city_traffic": {"file": "mixkit/city_traffic.wav"}, "engine_hum": {"file": "mixkit/engine_hum.wav"}}
        (self.sfx / "manifest.json").write_text(json.dumps({"sounds": sounds}), encoding="utf-8")
        self.converted = []
        for patch in (mock.patch.object(sfx_cache, "SFX", self.sfx),
                      mock.patch.object(sfx_cache.config, "FFMPEG", "ffmpeg"),
                      mock.patch.object(sfx_cache.subprocess, "run", self.ffmpeg)):
            patch.start()
            self.addCleanup(patch.stop)

    def ffmpeg(self, cmd, check=True):
        src, out = Path(cmd[cmd.index("-i") + 1]), Path(cmd[-1])
        if src.name == "bent.ogg":
            out.write_bytes(b"half a file")
            raise subprocess.CalledProcessError(1, cmd)
        out.write_bytes(b"RIFF")
        self.converted.append(out.stem)

    def build(self, **options):
        out = io.StringIO()
        with redirect_stdout(out):
            sfx_cache.build_cache(**options)
        return out.getvalue()

    def test_a_mix_only_needs_its_own_sounds(self):
        out = self.build(needed={"pop"})
        self.assertEqual(self.converted, ["pop"])
        self.assertIn("note: 2 sounds aren't downloaded (city_traffic, engine_hum); this episode doesn't use them", out)
        self.assertIn("note: kenney/bent.ogg could not be read; this episode doesn't use it", out)
        self.assertFalse(sfx_cache.cached("bent").exists())             # no half-written file left in the cache

    def test_a_sound_the_mix_uses_and_does_not_have_stops_it(self):
        with self.assertRaisesRegex(FileNotFoundError, r"1 sounds aren't downloaded yet \(engine_hum\)"):
            self.build(needed={"pop", "engine_hum"})

    def test_a_sound_the_mix_uses_and_cannot_read_stops_it(self):
        with self.assertRaises(subprocess.CalledProcessError):
            self.build(needed={"bent"})

    def test_a_name_that_is_not_in_the_manifest_is_named(self):
        with self.assertRaisesRegex(FileNotFoundError, "aren't in assets/sfx/manifest.json: popp"):
            self.build(needed={"popp"})

    def test_run_by_hand_it_still_wants_every_sound(self):
        with self.assertRaisesRegex(FileNotFoundError, r"2 sounds aren't downloaded yet \(city_traffic, engine_hum\)"):
            self.build()

    def test_the_cue_sheets_sounds(self):
        cues = {"beds": [{"sound": "city_traffic", "from": 0, "to": 9}],
                "hits": [{"sound": "pop", "at": 1}, {"synth": "ding", "at": 2}],
                "scatter": [{"event": "bursts", "pool": ["pop", "glass_a"]}], "ticks": {"event": "doublings"}}
        self.assertEqual(sfx_cache.cue_sounds(cues), {"city_traffic", "pop", "glass_a"})
        self.assertEqual(sfx_cache.cue_sounds({"mixer": "samples"}), set())


class Mixing(unittest.TestCase):
    def test_the_mixer_asks_for_the_sounds_of_its_own_cue_sheet(self):
        with tempfile.TemporaryDirectory() as tmp:
            ep = Path(tmp) / "ep01_test"
            (ep / "build").mkdir(parents=True)
            spec = {"id": "ep01_test", "duration": 1.0,
                    "audio": {"mixer": "samples", "hits": [{"synth": "pop", "at": 0.3, "gain": 0.8}]}}
            (ep / "episode.json").write_text(json.dumps(spec), encoding="utf-8")
            (ep / "build" / "series.json").write_text(json.dumps({"events": {}}), encoding="utf-8")
            level = {"I": -25.5, "S_max": -22.0, "TP": -8.0}
            with mock.patch.object(audio_mix, "build_cache") as cache, redirect_stdout(io.StringIO()), \
                    mock.patch.object(audio_mix, "master", side_effect=lambda buf: (buf, level, level)):
                audio_mix.build(ep)
            cache.assert_called_once_with(needed=set())             # a synth needs no file: nothing to download
            self.assertGreater((ep / "build" / "audio.wav").stat().st_size, 48000 * 4 * 0.9)


if __name__ == "__main__":
    unittest.main()
