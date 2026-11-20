"""2D videos (3.0): the video-type question, the notes first-time setup writes for a 2D channel, the voice's word
timing, the spec's beat-relative times, a 2D episode in the pipeline's steps that were written for 3D ones (3.1), and
drawing the 2D demo in a real browser (skipped where there's none)."""

import io
import json
import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from studio import generate, profiles, questions
from studio import paths as P

from test_profiles import ANSWERS, Scratch

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


class WithTheRestOfThePipeline(unittest.TestCase):
    """3.1 brought --status, a sound check straight after the audio step and --stills lines, all written for 3D
    episodes. A 2D episode goes through the same make_episode.py: its length comes from its voice ("duration":
    "auto"), it has no scene.blend, and its frames carry their own stamp."""

    def episode(self, voiced: bool = True) -> Path:
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        ep = Path(tmp.name) / "ep01_penny"
        ep.mkdir()
        spec = {"id": "ep01_penny", "engine": "2d", "title": "A penny doubled for 30 days?", "fps": 30,
                "duration": "auto", "scene": "scene.js", "script": [{"id": "hook", "say": "One penny."}],
                "audio": {"mixer": "samples", "cue_hits": [{"sound": "bubble_pop", "at": "hook"}]}}
        (ep / "episode.json").write_text(json.dumps(spec), encoding="utf-8")
        (ep / "scene.js").write_text("rf.scene(() => {})\n", encoding="utf-8")
        if voiced:
            (ep / "build" / "voice").mkdir(parents=True)
            (ep / "build" / "voice" / "timeline.json").write_text(json.dumps({
                "script_hash": voice.script_hash(spec), "duration": 3.0, "words": [],
                "beats": [{"i": 0, "id": "hook", "say": "One penny.", "start": 0.3, "end": 1.4}]}), encoding="utf-8")
        return ep

    def test_status_says_where_a_2d_episode_stands(self):
        import make_episode
        import render

        ep = self.episode(voiced=False)
        said = make_episode.status(ep, config.load_spec(ep))
        self.assertIn("voiced     no (run --steps build)", said)
        self.assertIn("frames     0, of a video whose length isn't known until it is voiced", said)
        self.assertNotIn("built", said)          # no scene.blend to build: that row is a 3D episode's

        ep = self.episode()
        frames = ep / "build" / "frames"
        frames.mkdir()
        for n in (1, 2):
            (frames / f"f_{n:04d}.jpg").write_bytes(b"\xff\xd8 a picture \xff\xd9")
        (frames / render.FRAMES_STAMP).write_text(render.frames_stamp(ep, config.load_spec(ep)) + "\n",
                                                  encoding="utf-8")
        said = make_episode.status(ep, config.load_spec(ep))
        self.assertIn("voiced     yes\n", said)
        self.assertIn("frames     2 of 90, they match the scene and the script", said)

        (ep / "scene.js").write_text("rf.scene(() => { /* drawn again */ })\n", encoding="utf-8")
        self.assertIn("frames     2 of 90, drawn before the scene, the script or the spec changed",
                      make_episode.status(ep, config.load_spec(ep)))
        spec = json.loads((ep / "episode.json").read_text(encoding="utf-8"))
        spec["script"][0]["say"] = "Two pennies."
        (ep / "episode.json").write_text(json.dumps(spec), encoding="utf-8")
        self.assertIn("voiced     yes, but the script or the voice changed since: run --steps build again",
                      make_episode.status(ep, config.load_spec(ep)))

    def test_the_render_keeps_the_stamp_3_0_gave_its_frames(self):
        # frames a 3.0 render left are still this scene's after the update: the stamp is worked out the same way
        import hashlib
        import render

        ep = self.episode()
        spec = config.load_spec(ep)
        drawn = json.dumps({k: spec.get(k) for k in ("title", "captions", "duration", "render_until", "fps", "scene")},
                           sort_keys=True).encode()
        parts = (ep / "scene.js", ep / "build" / "voice" / "timeline.json", ROOT / "pipeline" / "twod" / "runtime.js")
        self.assertEqual(render.frames_stamp(ep, spec),
                         hashlib.sha1(drawn + b"".join(p.read_bytes() for p in parts)).hexdigest())

    def test_the_sound_check_after_the_audio_step_takes_a_2d_episodes_length_from_its_voice(self):
        import wave

        import numpy as np
        import qa

        ep = self.episode()
        tone = (np.sin(np.arange(3 * 48000) * 2 * np.pi * 220 / 48000) * 3000).astype("<i2")
        with wave.open(str(ep / "build" / "audio.wav"), "wb") as w:
            w.setnchannels(2)
            w.setsampwidth(2)
            w.setframerate(48000)
            w.writeframes(np.repeat(tone, 2).tobytes())
        length = next(c for c in qa.audio_check(ep).checks if c["check"] == "audio length")
        self.assertEqual((length["status"], length["detail"]), ("PASS", "3.00 s (the video is 3.0 s)"))

    def test_the_lint_asks_for_the_voice_first(self):
        import qa

        with self.assertRaises(SystemExit) as stop:
            qa.lint(self.episode(voiced=False))
        self.assertIn("isn't voiced yet", str(stop.exception))

    def test_stills_lines_is_answered_in_a_sentence(self):
        import make_episode

        ep = self.episode()
        with self.assertRaises(SystemExit) as stop:
            make_episode.steps_2d(ep, config.load_spec(ep), ["stills"], "lines")
        self.assertIn("--stills lines follows a 3D scene's story lines and cuts", str(stop.exception))


class NotesForAnAddedChannel(Scratch):
    def test_a_second_2d_channels_commands_name_its_own_episode_folder(self):
        # 3.0's notes for a 2D channel and 3.1's way of naming an added channel's folder, in one file
        self.onboard_main()
        with redirect_stdout(io.StringIO()):
            other = profiles.create("Cool Epic Facts")
            generate.write_all({**ANSWERS, "channel_name": "Cool Epic Facts"}, other)
        self.assertEqual(tomllib.loads(other.settings.read_text(encoding="utf-8"))["video"]["engine"], "2d")
        notes = (other.folder / "CLAUDE.md").read_text(encoding="utf-8")
        where = str(Path("profiles") / "cool-epic-facts" / "episodes" / "<id>")
        self.assertIn(f"make_episode.py {where} --steps build` speaks the script", notes)      # Making a video
        for steps in ("build ", "stills --stills 1,15,31", "render", "post", "ship"):          # Commands
            self.assertIn(f"make_episode.py {where} --steps {steps}", notes)
        self.assertIn("(voice the script)", notes)
        self.assertIn("checks the voice, the browser, ffmpeg", notes)
        self.assertNotIn("keyframe_insert", notes)


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
