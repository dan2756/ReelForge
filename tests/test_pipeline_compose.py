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
        if self.fed == Ffmpeg.dies_at:
            self.code = 1
            raise BrokenPipeError("ffmpeg went away")
        return len(data)

    def wait(self):
        if self.code is None:
            self.code = Ffmpeg.exit
            Path(self.cmd[-1]).write_bytes(b"a new video" if self.code == 0 else b"half a vid")
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
        Ffmpeg.last, Ffmpeg.dies_at, Ffmpeg.exit = None, None, 0
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


class LastGoodVideo(Composing):
    """The video step writes beside the last video and replaces it only when the new one is whole."""

    def setUp(self):
        super().setUp()
        self.built()
        self.rendered(range(1, TOTAL + 1))
        (self.build / "audio.wav").write_bytes(b"RIFF")

    def left_over(self):
        return sorted(p.name for p in self.video.parent.iterdir())

    def test_a_finished_encode_takes_the_place_of_the_last_video(self):
        out = self.make_video()
        self.assertEqual(Ffmpeg.last.cmd[-3:], ["-f", "mp4", str(self.video) + ".part"])
        self.assertEqual(self.video.read_bytes(), b"a new video")
        self.assertEqual(self.left_over(), [self.video.name])
        self.assertNotIn("no sound", out)

    def test_ffmpeg_failing_leaves_the_last_video_alone(self):
        Ffmpeg.exit = 1
        with self.assertRaisesRegex(SystemExit, "ffmpeg failed, so no new video was made"):
            self.make_video()
        self.assertEqual(self.video.read_bytes(), b"the last good video")
        self.assertEqual(self.left_over(), [self.video.name])

    def test_ffmpeg_stopping_midway_leaves_the_last_video_alone(self):
        Ffmpeg.dies_at = 5
        with self.assertRaisesRegex(SystemExit, "ffmpeg failed.*ep01_test.mp4 was left as it was"):
            self.make_video()
        self.assertEqual(self.video.read_bytes(), b"the last good video")
        self.assertEqual(self.left_over(), [self.video.name])

    def test_a_fault_while_drawing_a_frame_stops_ffmpeg_and_leaves_the_last_video_alone(self):
        ep = self.episode()
        real = ep.frame

        def frame(idx):
            if idx == 7:
                raise KeyError("size_ft")           # a HUD number the scene never gave
            return real(idx)
        ep.frame = frame
        Path(str(self.video) + ".part").write_bytes(b"what ffmpeg had written so far")
        with self.assertRaises(KeyError), redirect_stdout(io.StringIO()):
            compose.make_video(ep)
        self.assertTrue(Ffmpeg.last.killed)
        self.assertEqual(self.video.read_bytes(), b"the last good video")
        self.assertEqual(self.left_over(), [self.video.name])

    def test_a_video_that_cannot_be_replaced_keeps_the_new_one_beside_it(self):
        with mock.patch.object(compose.os, "replace", side_effect=PermissionError("in use")), \
                self.assertRaisesRegex(SystemExit, "rename ep01_test.mp4.part to ep01_test.mp4"):
            self.make_video()
        self.assertEqual(self.left_over(), [self.video.name, self.video.name + ".part"])
        self.assertEqual(self.video.read_bytes(), b"the last good video")

    def test_encoding_without_sound_says_so(self):
        (self.build / "audio.wav").unlink()
        self.assertIn("note: build/audio.wav not found, so this video has no sound", self.make_video())
        self.assertNotIn("-c:a", Ffmpeg.last.cmd)


class Preview(Composing):
    """compose.py --preview: text over whatever picture there is, before the render as well as after it."""

    COLOURS = {"frame": (40, 90, 160), "still": (200, 40, 40), "grey": (96, 96, 96)}

    def still(self, numbers, stamped=True):
        (self.build / "stills").mkdir(exist_ok=True)
        for n in numbers:
            Image.new("RGB", (54, 96), self.COLOURS["still"]).save(self.build / "stills" / f"s_{n:04d}.jpg")
        if stamped:
            framecache.write_stamp(self.build / "stills" / framecache.STAMP, self.stamp())

    def preview(self, *seconds):
        out = io.StringIO()
        with redirect_stdout(out):
            compose.make_preview(self.episode(), list(seconds))
        return out.getvalue()

    def picture(self, second):
        """Which picture the preview of that second was drawn over, by its colour."""
        r, g, b = Image.open(self.build / "preview" / f"p_{second:06.2f}.jpg").convert("RGB").getpixel((5, 5))
        return min(self.COLOURS, key=lambda k: sum(abs(a - c) for a, c in zip(self.COLOURS[k], (r, g, b))))

    def test_before_the_render_it_draws_over_stills_and_grey_instead_of_failing(self):
        self.built()
        self.still([6])
        out = self.preview(0.5, 1.5)
        self.assertEqual((self.picture(0.5), self.picture(1.5)), ("still", "grey"))
        self.assertIn("0.5 s: not rendered yet, drawn over the still", out)
        self.assertIn("1.5 s: no picture yet, drawn over grey (--steps stills --stills 1.5 renders them)", out)
        self.assertTrue((self.build / "preview" / "sheet_1.jpg").exists())

    def test_a_rendered_frame_is_preferred_to_its_still(self):
        self.built()
        self.rendered([6])
        self.still([6, 16])
        out = self.preview(0.5, 1.5)
        self.assertEqual((self.picture(0.5), self.picture(1.5)), ("frame", "still"))
        self.assertNotIn("0.5 s", out)

    def test_pictures_of_an_older_scene_are_not_previewed_over(self):
        self.built()
        self.rendered([6])
        self.still([6])
        self.spec["world"]["type"] = "plaza"
        self.built()
        out = self.preview(0.5)
        self.assertEqual(self.picture(0.5), "grey")
        self.assertIn("the pictures in build/frames are of an older scene, so they were not used", out)
        self.assertIn("the pictures in build/stills are of an older scene", out)

    def test_an_empty_frame_is_not_a_picture(self):
        self.built()
        self.rendered([6])
        (self.frames / "f_0006.jpg").write_bytes(b"")
        self.preview(0.5)
        self.assertEqual(self.picture(0.5), "grey")

    def test_an_earlier_rounds_previews_are_cleared(self):
        self.built()
        self.preview(*[n / 10 for n in range(13)])
        self.assertEqual(len(list((self.build / "preview").glob("p_*.jpg"))), 13)
        self.assertEqual(sorted(p.name for p in (self.build / "preview").glob("sheet_*.jpg")),
                         ["sheet_1.jpg", "sheet_2.jpg"])                      # 12 to a sheet
        self.preview(0.5)
        self.assertEqual(sorted(p.name for p in (self.build / "preview").iterdir()), ["p_000.50.jpg", "sheet_1.jpg"])

    def test_each_tile_is_labelled_with_what_it_shows(self):
        self.spec["lines"] = [[0.2, 1.4, "You are a duck."]]
        (self.build / "series.json").write_text(json.dumps({"fps": FPS, "frames": TOTAL, "events": {"cuts": [1.5]}}),
                                                encoding="utf-8")
        ep = self.episode()
        self.assertEqual(compose.sheet_label(ep, 0.65), ("0.65 s", "You are a duck."))
        self.assertEqual(compose.sheet_label(ep, 1.4), ("1.40 s  [cut at 1.5]", ""))
        ep.stand_in = "grey"
        self.assertEqual(compose.sheet_label(ep, 1.0), ("1.00 s  [cover]  [no picture yet]", "You are a duck."))


class HudMemory(unittest.TestCase):
    def test_a_counter_that_changes_every_frame_does_not_fill_the_text_caches(self):
        import overlay
        canvas = Image.new("RGBA", (compose.W, compose.H), (30, 60, 90, 255))
        kept = overlay.text_layer.cache_info().currsize, overlay.glow_layer.cache_info().currsize
        for n in range(40):
            overlay.draw_hud(canvas, n / 30, "SIZE", f"{n * 1234:,} ft", f"{n} DOUBLINGS", "")
        self.assertEqual((overlay.text_layer.cache_info().currsize, overlay.glow_layer.cache_info().currsize), kept)
        self.assertLessEqual(overlay._hud_text.cache_info().currsize, 24)
        self.assertGreater(overlay._hud_text.cache_info().hits, 30)        # the label never changed: still cached

    def test_the_hud_is_drawn_exactly_as_before(self):
        import overlay
        args = ("1,234 ft", compose.config.FONT_MONO_BOLD, 80)
        style = {"fill": (255, 255, 255), "shadow": 200, "blur": 7, "align": "left", "outline": 5}
        self.assertEqual(overlay._hud_text(*args, **style).tobytes(), overlay.text_layer(*args, **style).tobytes())


class Fonts(unittest.TestCase):
    def test_a_face_the_machine_lacks_falls_back_to_the_regular_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            serif, bold, mono = (Path(tmp) / name for name in ("DejaVuSerif.ttf", "DejaVuSerif-Bold.ttf", "Mono.ttf"))
            for font in (serif, bold, mono):
                font.write_bytes(b"font")
            # find_fonts answers with another system's path when it found nothing. A folder that is never made
            # stands in for that here: C:/Windows/Fonts is a real one on a Windows machine, with both faces in it.
            elsewhere = Path(tmp) / "another system's fonts"
            found = (serif, elsewhere / "georgiai.ttf", bold, mono, elsewhere / "consolab.ttf")
            self.assertEqual(compose.config._faces(found), (serif, serif, bold, mono, mono))
            self.assertEqual(compose.config._faces((serif, serif, bold, mono, mono)), (serif, serif, bold, mono, mono))


if __name__ == "__main__":
    unittest.main()
