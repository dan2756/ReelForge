"""Delivering, and --status: an approval of an earlier cut does not ship the video that replaced it, and one
command says where an episode stands."""

import io
import json
import os
import sys
import tempfile
import unittest
import wave
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from pipeline import framecache, make_episode, qa


class Episode(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.ep = self.root / "episodes" / "ep01_test"
        self.build, self.final = self.ep / "build", self.ep / "final"
        (self.build / "qa").mkdir(parents=True)
        self.final.mkdir()
        self.spec = {"id": "ep01_test", "title": "What if a duck doubled?", "fps": 10, "duration": 3.0,
                     "render_until": 3.0, "world": {"type": "street"}, "effect": {"type": "grow"}}
        self.video = self.final / "ep01_test.mp4"
        self.completed = self.root / "completed videos"
        for patch in (mock.patch.object(make_episode.config, "COMPLETED", self.completed),
                      mock.patch.object(make_episode.config, "PIPELINE", self.root / "pipeline")):
            patch.start()
            self.addCleanup(patch.stop)

    def cut(self, content, at):
        """A video made at `at` (seconds since 1970), with QA passed on it."""
        self.video.write_bytes(content)
        os.utime(self.video, (at, at))
        (self.final / "caption.txt").write_text("What if a duck doubled?\n", encoding="utf-8")
        report = {"pass": True, "fingerprint": qa.video_fingerprint(self.video), "checks": []}
        (self.build / "qa" / "qa.json").write_text(json.dumps(report), encoding="utf-8")

    def review(self, text, at):
        path = self.build / "qa" / "review.md"
        path.write_text(text, encoding="utf-8")
        os.utime(path, (at, at))

    def deliver(self):
        with redirect_stdout(io.StringIO()):
            make_episode.deliver(self.spec, self.final, self.build)

    def delivered(self):
        return self.completed / "ep01_test - What if a duck doubled" / "ep01_test.mp4"


class ReviewGate(Episode):
    def test_a_reviewed_video_is_delivered(self):
        self.cut(b"first cut", 1_000_000)
        self.review("Looked at every shot.\nAPPROVED", 1_000_600)
        self.deliver()
        self.assertEqual(self.delivered().read_bytes(), b"first cut")

    def test_a_video_made_after_the_review_is_not(self):
        self.cut(b"first cut", 1_000_000)
        self.review("Looked at every shot.\nAPPROVED", 1_000_600)
        self.cut(b"second cut, nobody has looked at it", 1_001_200)          # a fix pass, then QA again
        with self.assertRaisesRegex(SystemExit, "deliver: the video changed after the review in build/qa/review.md"):
            self.deliver()
        self.assertFalse(self.completed.exists())
        self.review("Looked at the second cut too.\nAPPROVED", 1_001_800)
        self.deliver()
        self.assertEqual(self.delivered().read_bytes(), b"second cut, nobody has looked at it")

    def test_the_other_refusals_say_what_they_said(self):
        self.cut(b"first cut", 1_000_000)
        with self.assertRaisesRegex(SystemExit, "write your own review first"):
            self.deliver()
        self.review("REJECTED: the loop jumps", 1_000_600)
        with self.assertRaisesRegex(SystemExit, "write your own review first"):
            self.deliver()
        self.review("APPROVED", 1_000_600)
        self.video.write_bytes(b"changed after QA and after the review")
        os.utime(self.video, (1_000_300, 1_000_300))
        with self.assertRaisesRegex(SystemExit, "QA failed or is out of date"):
            self.deliver()


class Status(Episode):
    """make_episode.py <episode> --status: one line for each place there used to be to look."""

    def status(self):
        got = make_episode.status(self.ep, self.spec).splitlines()
        self.assertEqual(got[0], "ep01_test  What if a duck doubled?")
        return {line.split()[0]: line[13:] for line in got[1:]}

    def stamp(self):
        (self.root / "pipeline" / "blender").mkdir(parents=True, exist_ok=True)
        return framecache.scene_stamp(self.spec, {"fps", "world", "effect"}, self.root / "pipeline" / "blender", [],
                                      "4.5.5")

    def frames(self, numbers):
        (self.build / "frames").mkdir(exist_ok=True)
        for n in numbers:
            (self.build / "frames" / f"f_{n:04d}.jpg").write_bytes(b"\xff\xd8 a frame \xff\xd9")

    def test_a_new_episode(self):
        self.assertEqual(self.status(), {
            "built": "no (run --steps build)", "frames": "0 of 30", "audio": "no", "video": "no", "QA": "not run",
            "review": "not written yet (build/qa/review.md)", "delivered": "no"})
        self.assertEqual(sorted(p.name for p in self.build.iterdir()), ["qa"])      # it only reads

    def test_halfway_through_a_render(self):
        (self.build / "scene.blend").write_bytes(b"BLENDER")
        framecache.write_stamp(self.build / framecache.SCENE, self.stamp())
        self.frames(range(1, 13))
        framecache.write_stamp(self.build / "frames" / framecache.STAMP, self.stamp())
        (self.build / "frames" / "f_0013.jpg").write_bytes(b"")
        got = self.status()
        self.assertEqual(got["built"], "yes")
        self.assertEqual(got["frames"], "12 of 30, they match the scene; 1 empty or cut off (the render step sweeps "
                                        "them)")
        self.assertTrue((self.build / "frames" / "f_0013.jpg").exists())

    def test_the_scene_was_edited_and_built_again_after_the_render(self):
        (self.build / "scene.blend").write_bytes(b"BLENDER")
        self.frames(range(1, 31))
        framecache.write_stamp(self.build / "frames" / framecache.STAMP, self.stamp())
        self.spec["world"]["type"] = "plaza"
        framecache.write_stamp(self.build / framecache.SCENE, self.stamp())
        self.assertEqual(self.status()["frames"],
                         '30 of 30, rendered from an older scene (changed since: episode.json "world")')
        self.spec["effect"]["subject"] = "donut"                        # edited again, not built yet
        self.assertEqual(self.status()["built"], 'yes, but episode.json "effect" changed since: build again')

    def test_a_finished_and_delivered_episode(self):
        with wave.open(str(self.build / "audio.wav"), "wb") as w:
            w.setnchannels(2)
            w.setsampwidth(2)
            w.setframerate(48000)
            w.writeframes(b"\x00" * 4 * 48000 * 3)
        self.cut(b"first cut", 1_000_000)
        self.review("APPROVED", 1_000_600)
        self.deliver()
        got = self.status()
        self.assertEqual(got["audio"], "yes, 3.0 s")
        self.assertRegex(got["video"], r"^final/ep01_test\.mp4, 0 MB, made 1970-01-1\d \d\d:\d\d$")
        self.assertEqual((got["QA"], got["review"]), ("passed on this video", "APPROVED"))
        self.assertEqual(got["delivered"], "yes, in completed videos/ep01_test - What if a duck doubled/")

    def test_a_cut_made_after_qa_the_review_and_the_delivery(self):
        self.cut(b"first cut", 1_000_000)
        self.review("APPROVED", 1_000_600)
        self.deliver()
        self.video.write_bytes(b"second cut")
        os.utime(self.video, (1_001_200, 1_001_200))
        got = self.status()
        self.assertEqual(got["QA"], "passed, but on an earlier video: run --steps qa")
        self.assertEqual(got["review"], "APPROVED, but of an earlier cut: the video changed after it")
        self.assertEqual(got["delivered"], "an earlier cut is in completed videos/ep01_test - What if a duck doubled/, "
                                           "this one is not")

    def test_failed_qa_names_the_checks(self):
        self.cut(b"first cut", 1_000_000)
        report = {"pass": False, "fingerprint": qa.video_fingerprint(self.video),
                  "checks": [{"check": "duration", "status": "FAIL", "detail": ""},
                             {"check": "story lines", "status": "WARN", "detail": ""},
                             {"check": "no clipping", "status": "FAIL", "detail": ""}]}
        (self.build / "qa" / "qa.json").write_text(json.dumps(report), encoding="utf-8")
        self.review("REJECTED: it clips", 1_000_600)
        got = self.status()
        self.assertEqual((got["QA"], got["review"]), ("FAILED (duration, no clipping)", "REJECTED"))

    def test_the_option_prints_it_and_runs_no_step(self):
        (self.ep / "episode.json").write_text(json.dumps(self.spec), encoding="utf-8")
        out = io.StringIO()
        with mock.patch.object(sys, "argv", ["make_episode.py", str(self.ep), "--status"]), redirect_stdout(out), \
                mock.patch.object(make_episode, "run", side_effect=AssertionError("a step ran")):
            make_episode.main()
        self.assertEqual(out.getvalue().strip(), make_episode.status(self.ep, self.spec))


if __name__ == "__main__":
    unittest.main()
