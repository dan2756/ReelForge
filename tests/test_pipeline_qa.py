"""The checks that run before the video exists (the lint and the sound check), and QA faults that used to crash it."""

import io
import json
import sys
import tempfile
import unittest
import wave
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

import numpy as np

from pipeline import make_episode, qa

SR = 48000
BUNDLED_FONT = qa.config.ROOT / "assets" / "fonts" / "JetBrainsMono-Regular.ttf"
LOUDNESS = {"I": qa.config.TARGET_LUFS, "S_max": qa.config.TARGET_LUFS + 3, "TP": -12.0}


def rows(report):
    return {c["check"]: (c["status"], c["detail"]) for c in report.checks}


class Episode(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.ep = Path(tmp.name) / "ep01_test"
        (self.ep / "build").mkdir(parents=True)
        self.spec = {"id": "ep01_test", "title": "What if a duck doubled every 2 seconds?", "fps": 30,
                     "duration": 10.0, "render_until": 9.0, "hashtags": ["duck"],
                     "lines": [[1.0, 3.0, "You are a duck."], [3.5, 4.7, "A bigger duck."]],
                     "hud": {"vars": {"ft": {"series": "size_ft", "format": "int_commas"}},
                             "chapters": [{"from": 0, "to": 10, "label": "SIZE", "value": "{ft} ft", "sub": ""}]},
                     "audio": {"mixer": "samples", "hits": [{"sound": "glitch_break", "at": 1.0}]},
                     "cover": {"time": 5.0, "kicker": "WHAT IF", "keyword": "Duck"}}
        self.series = {"fps": 30, "frames": 270, "size_ft": [1.0] * 270}
        # the story lines are measured in the serif italic, which not every machine has: the bundled font will do
        patch = mock.patch.object(qa.config, "FONT_SERIF_ITALIC", BUNDLED_FONT)
        patch.start()
        self.addCleanup(patch.stop)

    def save(self, series=True):
        (self.ep / "episode.json").write_text(json.dumps(self.spec), encoding="utf-8")
        if series:
            (self.ep / "build" / "series.json").write_text(json.dumps(self.series), encoding="utf-8")

    def lint_spec(self):
        report = qa.Report()
        qa.lint_spec(self.spec, self.series, report)
        return rows(report)

    def lint(self, series=True):
        self.save(series)
        report = qa.lint(self.ep)
        return report, rows(report)


class Lint(Episode):
    def test_a_finished_spec_passes(self):
        report, got = self.lint()
        self.assertTrue(report.passed, report.checks)
        self.assertEqual({status for status, _ in got.values()}, {"PASS"})
        self.assertEqual(list(got), ["title", "story lines", "HUD covers the timeline", "no piled-up sounds",
                                     "cover frame", "HUD numbers", "sounds in the cue sheet",
                                     "caption rules (from episode.json)"])

    def test_the_placeholder_title_new_episode_writes_fails(self):
        self.spec["title"] = "WRITE THE HOOK LINE HERE (one question, at most 80 characters)"
        status, detail = self.lint_spec()["title"]
        self.assertEqual(status, "FAIL")
        self.assertIn("write the hook line", detail)
        self.spec["title"] = "  "
        self.assertEqual(self.lint_spec()["title"][0], "FAIL")

    def test_a_line_of_exactly_1_2_seconds_is_long_enough(self):
        self.spec["lines"] = [[26.75, 27.95, "Exactly long enough."]]
        self.spec["duration"] = 30.0
        self.assertEqual(self.lint_spec()["story lines"], ("PASS", "1 lines OK"))
        self.spec["lines"] = [[26.75, 27.85, "Too short to read."]]
        status, detail = self.lint_spec()["story lines"]
        self.assertEqual((status, detail), ("WARN", "'Too short to read.' is shorter than 1.2 s"))

    def test_lines_that_overlap_or_run_past_the_end_fail(self):
        self.spec["lines"] = [[1.0, 3.0, "First."], [2.5, 4.5, "Second."]]
        self.assertEqual(self.lint_spec()["story lines"], ("FAIL", "'First.' overlaps 'Second.'"))
        self.spec["lines"] = [[8.0, 10.5, "Too late."]]
        self.assertEqual(self.lint_spec()["story lines"], ("FAIL", "'Too late.' runs past the end"))

    def test_a_cover_frame_past_the_render_would_be_black(self):
        self.spec["cover"]["time"] = 9.5
        status, detail = self.lint_spec()["cover frame"]
        self.assertEqual(status, "FAIL")
        self.assertIn("past render_until (9.0 s): the cover would be black", detail)

    def test_a_hud_number_the_scene_does_not_give_fails(self):
        self.spec["hud"]["vars"]["mi"] = {"series": "size_mi", "format": "furlongs"}
        self.spec["hud"]["chapters"][0]["sub"] = "{earths} Earths"
        report, got = self.lint()
        status, detail = got["HUD numbers"]
        self.assertFalse(report.passed)
        self.assertEqual(status, "FAIL")
        self.assertIn("mi: no format called 'furlongs'", detail)
        self.assertIn("mi: build/series.json has no series 'size_mi'", detail)
        self.assertIn("the chapter from 0 s: KeyError 'earths' in its sub", detail)

    def test_a_sound_that_is_not_in_the_manifest_fails(self):
        self.spec["audio"]["beds"] = [{"sound": "ocean_of_static", "from": 0, "to": 10}]
        self.spec["audio"]["scatter"] = [{"event": "bursts", "pool": ["glitch_break", "no_such_pop"]}]
        self.spec["audio"]["hits"].append({"synth": "pop", "at": 2.0})          # a synth needs no file
        _, got = self.lint()
        self.assertEqual(got["sounds in the cue sheet"],
                         ("FAIL", "not in assets/sfx/manifest.json: no_such_pop, ocean_of_static"))

    def test_the_caption_rules_are_asked_before_the_render(self):
        self.spec["title"] = "What if a duck doubled? #duck"
        self.spec["hashtags"] = ["a", "b", "c", "d", "e", "f"]
        with mock.patch.multiple(qa.config, MAX_HASHTAGS=5, TIKTOK_FIRST_TAG="fyp", PLATFORMS=["tiktok", "x"]):
            report, got = self.lint()
        status, detail = got["caption rules (from episode.json)"]
        self.assertFalse(report.passed)
        self.assertIn("6 hashtags in episode.json, the channel's maximum is 5", detail)
        self.assertIn("put hashtags in episode.json 'hashtags', not in the title", detail)

    def test_before_anything_is_built_the_spec_is_still_checked(self):
        report, got = self.lint(series=False)
        self.assertTrue(report.passed, report.checks)
        self.assertNotIn("HUD numbers", got)


class SoundCheck(Episode):
    def setUp(self):
        super().setUp()
        self.spec["audio"]["hits"] = []
        patch = mock.patch("post.loudness.measure", return_value=LOUDNESS)      # the real one runs ffmpeg
        patch.start()
        self.addCleanup(patch.stop)
        t = np.arange(int(10.0 * SR)) / SR
        bed = 0.02 * np.sin(2 * np.pi * 110 * t)
        self.mix = np.stack([bed, bed], 1).astype(np.float32)

    def hit(self, at, level):
        i = int(at * SR)
        t = np.arange(int(0.2 * SR)) / SR
        self.mix[i:i + len(t)] += (level * np.sin(2 * np.pi * 440 * t) * np.exp(-t * 20))[:, None].astype(np.float32)

    def check(self):
        report = qa.Report()
        per_second = qa.check_audio(self.mix, SR, self.spec, report)
        return report, rows(report), per_second

    def test_a_clean_mix_passes_and_gives_the_loudness_per_second(self):
        report, got, (plain, heard) = self.check()
        self.assertTrue(report.passed, report.checks)
        self.assertEqual((len(plain), len(heard)), (10, 10))
        self.assertEqual(got["no clipping"][0], "PASS")

    def test_a_synthesized_hit_aligned_to_its_peak_is_checked_not_crashed_on(self):
        self.spec["audio"]["hits"] = [{"synth": "pop", "at": 5.0, "align": "peak", "gain": 0.9},
                                      {"sound": "glitch_break", "at": 8.0, "align": "peak"}]
        self.hit(5.0, 0.5)
        _, got, _ = self.check()
        status, detail = got["key sound hits audible on cue"]
        self.assertEqual(status, "WARN")
        self.assertRegex(detail, r"synth pop @ 5\.00s: \+\d+\.\d dB over the 2 s before \(ok\)")
        self.assertRegex(detail, r"glitch_break @ 8\.00s: .*\(NOT AUDIBLE\)")

    def test_clipping_fails(self):
        self.mix[SR:SR + 50] = 1.0
        report, got, _ = self.check()
        self.assertFalse(report.passed)
        self.assertEqual(got["no clipping"][0], "FAIL")

    def test_the_mix_on_disk_is_checked_straight_after_the_audio_step(self):
        self.save()
        with wave.open(str(self.ep / "build" / "audio.wav"), "wb") as w:
            w.setnchannels(2)
            w.setsampwidth(2)
            w.setframerate(SR)
            w.writeframes((self.mix[: 8 * SR] * 32767).astype("<i2").tobytes())
        report = qa.audio_check(self.ep)
        got = rows(report)
        self.assertEqual(got["audio length"], ("FAIL", "8.00 s (the video is 10.0 s)"))
        self.assertEqual(got["no clipping"][0], "PASS")
        self.assertFalse(report.passed)


class VideoWithoutSound(Episode):
    """QA on a video that was encoded without an audio track: it used to stop with ffmpeg's error, no report."""

    class Decoder:
        def __init__(self, cmd, stdout=None):
            frames = [np.full((qa.SHEET_H, qa.SHEET_W, 3), 40 + 6 * (n % 30), np.uint8).tobytes() for n in range(60)]
            self.stdout = io.BytesIO(b"".join(frames))

        def wait(self):
            return 0

    def test_the_report_is_written_and_says_there_is_no_audio_track(self):
        self.spec.update(duration=2.0, render_until=2.0, lines=[])
        del self.spec["hud"], self.spec["cover"]
        self.save()
        (self.ep / "final").mkdir()
        video = self.ep / "final" / "ep01_test.mp4"
        video.write_bytes(b"video without sound")
        streams = {"streams": [{"codec_type": "video", "codec_name": "h264", "width": 1080, "height": 1920,
                                "r_frame_rate": "30/1", "pix_fmt": "yuv420p"}],
                   "format": {"duration": "2.0", "size": "19"}}
        with mock.patch.object(qa, "probe", return_value=streams), \
                mock.patch.object(qa.subprocess, "Popen", self.Decoder), \
                mock.patch.object(qa.subprocess, "run", side_effect=AssertionError("no audio track to decode")):
            spec, report, qa_dir, _ = qa.check_episode(self.ep)
        got = rows(report)
        self.assertEqual(got["audio stream"], ("FAIL", "no audio track"))
        self.assertNotIn("no clipping", got)
        saved = json.loads((qa_dir / "qa.json").read_text(encoding="utf-8"))
        self.assertFalse(saved["pass"])
        self.assertEqual(saved["loudness_per_second_db"], [])
        self.assertIn("| audio stream | FAIL | no audio track |", (qa_dir / "report.md").read_text(encoding="utf-8"))


class StepsRunTheEarlyChecks(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.ep = Path(tmp.name) / "ep01_test"
        (self.ep / "build").mkdir(parents=True)
        spec = {"id": "ep01_test", "title": "What if?", "fps": 10, "duration": 1.0, "render_until": 1.0,
                "audio": {"mixer": "samples"}}
        (self.ep / "episode.json").write_text(json.dumps(spec), encoding="utf-8")
        (self.ep / "build" / "scene.blend").write_bytes(b"BLENDER")
        self.ran = []
        for patch in (mock.patch.object(make_episode, "run", self.run_step),
                      mock.patch.object(make_episode.config, "BLENDER", "blender"),
                      mock.patch.object(make_episode.config, "FFMPEG", "ffmpeg")):
            patch.start()
            self.addCleanup(patch.stop)

    def run_step(self, cmd, log=None, **_):
        cmd = [str(c) for c in cmd]
        self.ran.append(" ".join([Path(cmd[0]).stem if cmd[0] != sys.executable else "python"] +
                                 [Path(c).name for c in cmd[1:] if c.endswith((".py", "-a")) or c.startswith("--")]))
        if "-a" in cmd:
            for n in range(1, 11):
                (self.ep / "build" / "frames" / f"f_{n:04d}.jpg").write_bytes(b"\xff\xd8 frame \xff\xd9")

    def steps(self, names):
        with mock.patch.object(sys, "argv", ["make_episode.py", str(self.ep), "--steps", names]), \
                redirect_stdout(io.StringIO()):
            make_episode.main()
        return self.ran

    def test_the_build_ends_with_the_lint(self):
        self.assertEqual(self.steps("build"), ["blender --factory-startup --python-exit-code build_scene.py --",
                                               "python qa.py --lint"])

    def test_the_render_starts_with_the_lint(self):
        self.assertEqual(self.steps("render"), ["python qa.py --lint", "blender -a"])

    def test_the_mix_is_checked_before_the_video_is_made(self):
        self.assertEqual(self.steps("audio,video"), ["python audio_mix.py", "python qa.py --audio",
                                                     "python compose.py --video"])


if __name__ == "__main__":
    unittest.main()
