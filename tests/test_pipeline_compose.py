"""The video and cover steps: they only use frames of the scene as it is now, and they say what is wrong before
ffmpeg starts. ffmpeg is stood in for by an object that keeps what it was fed."""

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from PIL import Image

from pipeline import framecache, qa

sys.path.insert(0, str(qa.config.PIPELINE / "post"))       # the way qa.py itself reaches compose and overlay
import compose  # noqa: E402

FPS, SECONDS = 10, 2.0
TOTAL = int(FPS * SECONDS)


class Ffmpeg:
    """A stand-in for the ffmpeg process compose pipes raw frames into."""

    def __init__(self, cmd, stdin=None):
        self.cmd = [str(c) for c in cmd]
        self.stdin = io.BytesIO()
        self.fed = 0
        self.stdin.write = self.feed
        self.code = None
        self.killed = False
        Ffmpeg.last = self

    def feed(self, data):
        self.fed += 1
        return len(data)

    def wait(self):
        if self.code is None:
            self.code = 0
            Path(self.cmd[-1]).write_bytes(b"a new video")
        return self.code

    def poll(self):
        return self.code

    def kill(self):
        self.killed = True
        self.code = -9


class Composing(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.ep = Path(tmp.name) / "ep01_test"
        self.build = self.ep / "build"
        self.frames = self.build / "frames"
        self.frames.mkdir(parents=True)
        (self.ep / "final").mkdir()
        self.video = self.ep / "final" / "ep01_test.mp4"
        self.video.write_bytes(b"the last good video")
        self.spec = {"id": "ep01_test", "title": "What if a duck doubled?", "fps": FPS, "duration": SECONDS,
                     "render_until": SECONDS, "world": {"type": "street"}, "effect": {"type": "grow"},
                     "cover": {"time": 1.0, "kicker": "DAY 12", "keyword": "DUCK"}}
        (self.build / "series.json").write_text(json.dumps({"fps": FPS, "frames": TOTAL}), encoding="utf-8")
        Ffmpeg.last = None
        for patch in (mock.patch.object(compose.subprocess, "Popen", Ffmpeg),
                      mock.patch.object(compose.config, "FFMPEG", "ffmpeg")):
            patch.start()
            self.addCleanup(patch.stop)

    def stamp(self):
        return framecache.scene_stamp(self.spec, {"fps", "world", "effect"}, self.ep, [], "4.5.5")

    def built(self):
        framecache.write_stamp(self.build / framecache.SCENE, self.stamp())

    def rendered(self, numbers, stamped=True):
        for n in numbers:
            Image.new("RGB", (54, 96), (40, 90, 160)).save(self.frames / f"f_{n:04d}.jpg")
        if stamped:
            framecache.write_stamp(self.frames / framecache.STAMP, self.stamp())

    def episode(self):
        (self.ep / "episode.json").write_text(json.dumps(self.spec), encoding="utf-8")
        return compose.Episode(self.ep)

    def make_video(self):
        out = io.StringIO()
        with redirect_stdout(out):
            compose.make_video(self.episode())
        return out.getvalue()

    def assert_untouched(self):
        self.assertIsNone(Ffmpeg.last, "ffmpeg was started")
        self.assertEqual(self.video.read_bytes(), b"the last good video")
        self.assertEqual([p.name for p in self.video.parent.iterdir()], [self.video.name])


class FrameGuard(Composing):
    def test_frames_of_an_older_scene_are_refused(self):
        self.built()
        self.rendered(range(1, TOTAL + 1))
        self.spec["world"]["type"] = "plaza"
        self.built()
        with self.assertRaises(SystemExit) as stop:
            self.make_video()
        self.assertIn('video: the frames in build/frames were rendered from an older scene '
                      '(changed since: episode.json "world")', str(stop.exception))
        self.assertIn("--steps render --rerender all", str(stop.exception))
        self.assert_untouched()

    def test_frames_with_no_record_are_refused_once_the_scene_has_a_stamp(self):
        self.built()
        self.rendered(range(1, TOTAL + 1), stamped=False)
        with self.assertRaisesRegex(SystemExit, "have no record of the scene they were rendered from"):
            self.make_video()
        self.assert_untouched()

    def test_a_build_that_did_not_finish_is_refused(self):
        self.rendered(range(1, TOTAL + 1))
        with self.assertRaisesRegex(SystemExit, "video: the last build did not finish"):
            self.make_video()
        self.assert_untouched()

    def test_missing_and_broken_frames_are_named_before_ffmpeg_starts(self):
        self.built()
        self.rendered(range(1, TOTAL + 1))
        (self.frames / "f_0007.jpg").write_bytes(b"")
        (self.frames / "f_0008.jpg").write_bytes((self.frames / "f_0008.jpg").read_bytes()[:100])
        (self.frames / "f_0020.jpg").unlink()
        with self.assertRaisesRegex(SystemExit, r"video: frames 7-8 \(0\.6-0\.7 s\), 20 \(1\.9 s\) are not rendered\. "
                                                r"Run --steps render"):
            self.make_video()
        self.assertFalse((self.frames / "f_0007.jpg").exists())         # swept, so the render makes them again
        self.assert_untouched()

    def test_frames_from_before_stamps_are_used_as_they_always_were(self):
        self.rendered(range(1, TOTAL + 1), stamped=False)
        self.make_video()
        self.assertEqual(Ffmpeg.last.fed, TOTAL)

    def test_the_cover_is_not_made_from_an_older_scene_either(self):
        self.built()
        self.rendered(range(1, TOTAL + 1))
        self.spec["effect"]["subject"] = "donut"
        self.built()
        with self.assertRaisesRegex(SystemExit, "cover: the frames in build/frames were rendered from an older scene"):
            compose.make_cover(self.episode())
        self.assertFalse((self.ep / "final" / "cover.jpg").exists())

    def test_the_cover_says_which_frame_it_is_waiting_for(self):
        self.built()
        self.rendered(range(1, 6))
        with self.assertRaisesRegex(SystemExit, r"cover: frame 11 \(1\.0 s\) is not rendered"):
            compose.make_cover(self.episode())


if __name__ == "__main__":
    unittest.main()
