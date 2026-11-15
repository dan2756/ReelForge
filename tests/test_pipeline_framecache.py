"""The scene stamp and the frame folder: which frames belong to the scene, which are broken, which are missing."""

import copy
import json
import tempfile
import unittest
from pathlib import Path

from pipeline import framecache

JPEG = b"\xff\xd8\xff\xe0" + b"picture" * 40 + b"\xff\xd9"


def frame(folder: Path, n: int, data: bytes = JPEG, prefix: str = "f") -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{prefix}_{n:04d}.jpg"
    path.write_bytes(data)
    return path


class SpecReads(unittest.TestCase):
    def spec(self):
        return framecache.Recorder({"fps": 30, "world": {"type": "street"}, "effect": {"type": "grow"},
                                    "lines": [[1, 3, "You are a duck."]], "audio": {"mixer": "samples"}})

    def test_only_the_keys_that_were_read_are_remembered(self):
        spec = self.spec()
        spec["world"]["type"]
        spec.get("render", {})
        self.assertIn("fps", spec)
        self.assertEqual(spec.read, {"world", "render", "fps"})

    def test_a_copy_or_a_loop_has_read_everything(self):
        for walk in (dict, copy.deepcopy, json.dumps, lambda s: [k for k in s], lambda s: s.copy(),
                     lambda s: dict(s, world={}), lambda s: {**s}, lambda s: list(s.items())):
            spec = self.spec()
            walk(spec)
            self.assertEqual(spec.read, set(spec), walk)

    def test_it_still_behaves_like_the_spec(self):
        spec = self.spec()
        self.assertEqual(json.loads(json.dumps(spec)), dict(spec))
        self.assertEqual(spec.setdefault("layout", {}), {})
        self.assertEqual(spec.pop("layout"), {})
        self.assertEqual(spec.get("nothing", 7), 7)
        with self.assertRaises(KeyError):
            spec["nothing"]


class Stamps(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name) / "blender"
        (self.root / "effects").mkdir(parents=True)
        (self.root / "rf_common.py").write_text("HAZE = 1\n", encoding="utf-8")
        (self.root / "effects" / "grow.py").write_text("def apply(spec, info): ...\n", encoding="utf-8")
        self.spec = {"fps": 30, "world": {"type": "street"}, "effect": {"type": "grow", "subject": "duck"},
                     "lines": [[1, 3, "You are a duck."]]}
        self.build = Path(tmp.name) / "ep" / "build"
        self.build.mkdir(parents=True)

    def stamp(self, spec=None, blender="4.5.5", engine="BLENDER_EEVEE_NEXT"):
        return framecache.scene_stamp(spec or self.spec, {"fps", "world", "effect", "render"}, self.root,
                                      [self.root / "rf_common.py", self.root / "effects" / "grow.py"], blender, engine)

    def test_a_story_line_does_not_change_the_stamp(self):
        before = self.stamp()
        self.spec["lines"].append([4, 6, "You are a bigger duck."])
        self.assertEqual(self.stamp()["fingerprint"], before["fingerprint"])

    def test_what_the_scene_reads_changes_the_stamp_and_is_named(self):
        before = self.stamp()
        self.spec["effect"]["subject"] = "donut"
        self.spec["render"] = {"samples": 32}          # a key the scene asked for that wasn't there before
        (self.root / "effects" / "grow.py").write_text("def apply(spec, info): return {}\n", encoding="utf-8")
        after = self.stamp(blender="4.5.16")
        self.assertNotEqual(after["fingerprint"], before["fingerprint"])
        self.assertEqual(framecache.changed(before, after),
                         ['episode.json "effect"', 'episode.json "render"', "pipeline/blender/effects/grow.py",
                          "Blender (4.5.5 then, 4.5.16 now)"])

    def test_the_renderer_is_part_of_the_stamp(self):
        self.assertEqual(framecache.changed(self.stamp(), self.stamp(engine="CYCLES")),
                         ["the renderer (BLENDER_EEVEE_NEXT then, CYCLES now)"])

    def test_a_stamp_survives_being_written_and_read(self):
        stamp = self.stamp()
        framecache.write_stamp(self.build / framecache.SCENE, stamp)
        self.assertEqual(framecache.read_stamp(self.build / framecache.SCENE), stamp)
        self.assertIsNone(framecache.read_stamp(self.build / "frames" / framecache.STAMP))
        (self.build / framecache.SCENE).write_text("{not json", encoding="utf-8")
        self.assertIsNone(framecache.read_stamp(self.build / framecache.SCENE))

    def test_edits_after_the_build_show_as_outdated(self):
        stamp = self.stamp()
        self.assertEqual(framecache.outdated(stamp, self.spec, self.root), [])
        self.spec["lines"] = []
        self.assertEqual(framecache.outdated(stamp, self.spec, self.root), [])
        self.spec["world"]["type"] = "plaza"
        (self.root / "rf_common.py").write_text("HAZE = 2\n", encoding="utf-8")
        self.assertEqual(framecache.outdated(stamp, self.spec, self.root),
                         ['episode.json "world"', "pipeline/blender/rf_common.py"])

    def test_where_the_frames_stand_against_the_scene(self):
        frames = self.build / "frames"
        self.assertEqual(framecache.frames_state(self.build), ("none", []))
        frame(frames, 1)
        self.assertEqual(framecache.frames_state(self.build), ("untracked", []))
        framecache.write_stamp(self.build / framecache.SCENE, self.stamp())
        self.assertEqual(framecache.frames_state(self.build), ("unstamped", []))
        framecache.write_stamp(frames / framecache.STAMP, self.stamp())
        self.assertEqual(framecache.frames_state(self.build), ("match", []))
        self.spec["world"]["type"] = "plaza"
        framecache.write_stamp(self.build / framecache.SCENE, self.stamp())
        self.assertEqual(framecache.frames_state(self.build), ("changed", ['episode.json "world"']))
        (self.build / framecache.SCENE).unlink()
        self.assertEqual(framecache.frames_state(self.build), ("unbuilt", []))

    def test_the_stills_have_their_own_stamp(self):
        frame(self.build / "stills", 31, prefix="s")
        framecache.write_stamp(self.build / framecache.SCENE, self.stamp())
        self.assertEqual(framecache.frames_state(self.build, "stills", "s_*.jpg"), ("unstamped", []))
        self.assertEqual(framecache.frames_state(self.build), ("none", []))


class FrameFiles(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.frames = Path(tmp.name) / "frames"

    def test_empty_and_cut_off_frames_are_swept(self):
        whole = frame(self.frames, 1)
        padded = frame(self.frames, 2, JPEG + b"\x00\x00")       # a few bytes after the end marker are fine
        empty = frame(self.frames, 3, b"")                      # Blender's placeholder for the frame it was on
        cut = frame(self.frames, 4, JPEG[:120])
        (self.frames / framecache.STAMP).write_text("{}", encoding="utf-8")
        self.assertEqual(framecache.sweep_empty(self.frames), [empty, cut])
        self.assertEqual(sorted(p.name for p in self.frames.iterdir()), [".scene", whole.name, padded.name])
        self.assertEqual(framecache.sweep_empty(self.frames), [])

    def test_missing_frames_come_back_as_ranges(self):
        for n in (1, 2, 3, 7, 9, 10):
            frame(self.frames, n)
        frame(self.frames, 5, b"")                              # an empty file is not a frame
        self.assertEqual(framecache.missing(self.frames, 12), [(4, 6), (8, 8), (11, 12)])
        self.assertEqual(framecache.count(framecache.missing(self.frames, 12)), 6)
        self.assertEqual(framecache.missing(self.frames, 3), [])
        self.assertEqual(framecache.missing(self.frames.parent / "nowhere", 2), [(1, 2)])

    def test_seconds_become_the_frames_the_stills_step_would_number(self):
        self.assertEqual(framecache.frames_for("12-15.5,31-33", 30, 1935), [(361, 466), (931, 991)])
        self.assertEqual(framecache.frames_for("21", 30, 1935), [(631, 631)])
        self.assertEqual(framecache.frames_for(" 0-1 , 0.5-2 ", 30, 1935), [(1, 61)])
        self.assertEqual(framecache.frames_for("63-70", 30, 1935), [(1891, 1935)])     # clipped to the render

    def test_seconds_that_make_no_sense_are_refused_in_words(self):
        for text, why in (("15-12", "ends before it starts"), ("all of it", "isn't a range of seconds"),
                          ("70-72", "after the last rendered frame"), (" , ", "no seconds given")):
            with self.assertRaisesRegex(ValueError, why):
                framecache.frames_for(text, 30, 1935)

    def test_deleting_a_range_leaves_the_rest(self):
        for n in range(1, 11):
            frame(self.frames, n)
        (self.frames / framecache.STAMP).write_text("{}", encoding="utf-8")
        self.assertEqual(framecache.delete(self.frames, [(3, 5), (9, 9)]), 4)
        self.assertEqual(framecache.missing(self.frames, 10), [(3, 5), (9, 9)])
        self.assertTrue((self.frames / framecache.STAMP).exists())

    def test_ranges_are_described_in_frames_and_seconds(self):
        self.assertEqual(framecache.describe([(361, 466), (931, 931)], 30),
                         "frames 361-466 (12.0-15.5 s), 931 (31.0 s)")
        self.assertEqual(framecache.describe([(31, 31)], 30), "frame 31 (1.0 s)")
        self.assertIn("and 2 more ranges", framecache.describe([(n, n) for n in range(1, 17, 2)], 30))

    def test_the_progress_line(self):
        for (done, made, seconds), line in (((412, 400, 360.0), "frames 412/1935, 0.9 s a frame, about 23 min left"),
                                            ((1930, 100, 90.0), "frames 1930/1935, 0.9 s a frame, about 4 s left"),
                                            ((12, 0, 40.0), "frames 12/1935, the first one is still rendering")):
            self.assertEqual(framecache.progress(done, 1935, made, seconds), line)


if __name__ == "__main__":
    unittest.main()
