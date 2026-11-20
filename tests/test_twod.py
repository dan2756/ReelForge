"""2D videos (3.0): the video-type question, the notes first-time setup writes for a 2D channel, the voice's word
timing, the spec's beat-relative times, and drawing the 2D demo in a real browser (skipped where there's none)."""

import json
import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest import mock

from studio import generate, questions
from studio import paths as P

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pipeline"))
sys.path.insert(0, str(ROOT / "pipeline" / "twod"))
import config  # noqa: E402
import voice  # noqa: E402

BASE = {"channel_name": "Test Channel", "about": "Money what-ifs with real numbers.", "assistant": ["claude"]}


class Questions(unittest.TestCase):
    def test_a_new_channel_makes_2d_videos_with_a_voice(self):
        a, problems = questions.validate(dict(BASE))
        self.assertEqual(problems, [])
        self.assertEqual(a["video_type"], "2d")
        self.assertEqual(a["voice"], "af_heart")
        self.assertEqual(a["style_2d"], "flat")
        self.assertEqual(a["loudness"], "voice")
        self.assertNotIn("style", a)

    def test_blender_keeps_the_3d_questions(self):
        a, _ = questions.validate({**BASE, "video_type": "blender"})
        self.assertEqual(a["style"], "cinematic")
        self.assertEqual(a["loudness"], "quiet")
        self.assertNotIn("voice", a)

    def test_answers_saved_before_3_0_stay_3d(self):
        # A channel set up with 2.x has no video_type: it must never turn into a 2D channel on its own.
        a, _ = questions.validate({**BASE, "_created": "2026-10-24", "style": "cinematic", "loudness": "quiet"})
        self.assertEqual(a["video_type"], "blender")


class Notes(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        mine = root / "my-studio"
        for name, value in {"ROOT": root, "MY_STUDIO": mine, "ANSWERS": mine / "answers.json",
                            "SETTINGS": mine / "studio.toml", "BIBLE": mine / "channel-bible.md",
                            "BACKUPS": mine / "backups", "COMPLETED": root / "completed videos",
                            "DASHBOARD": root / "dashboard"}.items():
            patch = mock.patch.object(P, name, value)
            patch.start()
            self.addCleanup(patch.stop)

    def test_a_2d_channels_notes_and_settings(self):
        generate.write_all({**BASE, "voice": "am_michael"})
        s = tomllib.loads(P.SETTINGS.read_text(encoding="utf-8"))
        self.assertEqual(s["video"]["engine"], "2d")
        self.assertEqual(s["video"]["voice"], "am_michael")
        self.assertTrue(s["video"]["voiceover"])
        self.assertEqual(s["sound"]["target_lufs"], -16.0)
        notes = (P.ROOT / "CLAUDE.md").read_text(encoding="utf-8")
        self.assertIn("docs/2D.md", notes)
        self.assertIn("scene.js", notes)
        self.assertIn("Michael", notes)
        self.assertNotIn("No voiceover", notes)
        self.assertNotIn("keyframe_insert", notes)
        self.assertIn("voiceover", (P.BIBLE).read_text(encoding="utf-8"))

    def test_a_3d_channels_notes_are_as_before(self):
        generate.write_all({**BASE, "video_type": "blender"})
        notes = (P.ROOT / "CLAUDE.md").read_text(encoding="utf-8")
        self.assertIn("No voiceover", notes)
        self.assertIn("Blender builds and renders each scene", notes)
        self.assertNotIn("docs/2D.md", notes)
        self.assertEqual(tomllib.loads(P.SETTINGS.read_text(encoding="utf-8"))["video"]["engine"], "blender")


class WordTimes(unittest.TestCase):
    def test_one_phoneme_group_per_word_uses_the_groups(self):
        phones = [("h", 0.1, 0.2), ("i", 0.2, 0.3), (" ", 0.3, 0.35), ("j", 0.35, 0.4), ("u", 0.4, 0.6), (".", 0.6, 0.7)]
        words = voice.align("Hi you.", phones, lambda w: w, 0.8)
        self.assertEqual([(w["text"], w["start"], w["end"]) for w in words],
                         [("Hi", 0.1, 0.3), ("you.", 0.35, 0.6)])

    def test_a_number_said_as_several_words_still_gets_one_time(self):
        # "512" is spoken as three words: the text's words follow how many phonemes each has on its own.
        phones, t = [], 0.0
        for group in ("haev", "faiv", "handred", "twelv"):
            for ch in group:
                phones.append((ch, t, t + 0.05))
                t += 0.05
            phones.append((" ", t, t + 0.02))
            t += 0.02
        sizes = {"have": "haev", "512": "faivhandredtwelv"}
        words = voice.align("have 512", phones, lambda w: sizes[w], t)
        self.assertEqual([w["text"] for w in words], ["have", "512"])
        self.assertAlmostEqual(words[0]["start"], 0.0)
        self.assertAlmostEqual(words[1]["start"], 0.22, places=2)
        self.assertAlmostEqual(words[1]["end"], phones[-2][2], places=3)

    def test_show_text_is_spread_over_the_speech(self):
        words = voice.spread("Day 10: $5.12.", 1.0, 3.0)
        self.assertEqual(words[0]["start"], 1.0)
        self.assertAlmostEqual(words[-1]["end"], 3.0, places=3)


class Spec(unittest.TestCase):
    def test_auto_duration_cue_hits_and_times_from_the_end(self):
        with tempfile.TemporaryDirectory() as d:
            ep = Path(d)
            (ep / "build" / "voice").mkdir(parents=True)
            (ep / "episode.json").write_text(json.dumps({
                "id": "x", "engine": "2d", "title": "T", "fps": 30, "duration": "auto",
                "audio": {"cue_hits": [{"sound": "coins", "at": "big", "offset": 0.1}, {"sound": "kaching", "at": 2}]},
                "cta": {"start": -2.5, "end": -0.5, "text": "FOLLOW"}, "cover": {"time": "big:end"}}), encoding="utf-8")
            (ep / "build" / "voice" / "timeline.json").write_text(json.dumps({
                "duration": 62.0, "beats": [{"i": 0, "id": "big", "say": "x", "start": 10.0, "end": 12.5}],
                "words": []}), encoding="utf-8")
            spec = config.load_spec(ep)
            self.assertEqual(spec["duration"], 62.0)
            self.assertEqual(spec["render_until"], 62.0)
            self.assertEqual([h["at"] for h in spec["audio"]["hits"]], [10.1, 2])
            self.assertEqual((spec["cta"]["start"], spec["cta"]["end"]), (59.5, 61.5))
            self.assertEqual(spec["cover"]["time"], 12.5)

    def test_a_3d_spec_is_read_as_it_is(self):
        spec = config.load_spec(ROOT / "examples" / "demo_doubling_duck")
        self.assertEqual(spec["duration"], 67.0)
        self.assertEqual(config.engine(spec), "blender")


class Draw(unittest.TestCase):
    def test_the_2d_demo_draws_in_a_browser(self):
        from studio import browsers
        if browsers.find() is None:
            self.skipTest("no Chrome, Edge or Chromium here")
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
            ep = Path(d) / "demo"
            shutil.copytree(ROOT / "examples" / "demo_2d_penny", ep)
            spec = json.loads((ep / "episode.json").read_text(encoding="utf-8"))
            beats = [{"i": i, "id": b["id"], "say": b["say"], "start": i * 3.0, "end": i * 3.0 + 2.6}
                     for i, b in enumerate(spec["script"])]
            words = [{"text": "Would", "clean": "would", "start": 0.2, "end": 0.5, "beat": 0},
                     {"text": "you", "clean": "you", "start": 0.5, "end": 0.7, "beat": 0}]
            (ep / "build" / "voice").mkdir(parents=True)
            (ep / "build" / "voice" / "timeline.json").write_text(json.dumps(
                {"duration": 55.0, "beats": beats, "words": words}), encoding="utf-8")
            res = subprocess.run([sys.executable, str(ROOT / "pipeline" / "twod" / "render.py"), str(ep), "--stills",
                                  "0.6,20,49"], capture_output=True, text=True)
            self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
            stills = sorted((ep / "build" / "stills").glob("s_*.jpg"))
            self.assertEqual(len(stills), 3)
            self.assertTrue(all(p.stat().st_size > 20_000 for p in stills))


if __name__ == "__main__":
    unittest.main()
