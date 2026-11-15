"""Delivering: an approval of an earlier cut does not ship the video that replaced it."""

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from pipeline import make_episode, qa


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


if __name__ == "__main__":
    unittest.main()
