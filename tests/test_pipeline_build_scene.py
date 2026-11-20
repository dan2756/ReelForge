"""Blender's entry point, build_scene.py, run without Blender: a stand-in bpy and a one-line world and effect, to
see that it stamps the scene with what the scene code really read."""

import json
import runpy
import shutil
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from pipeline import framecache

KIT = Path(__file__).resolve().parent.parent

RF_COMMON = '''
def clear_scene():
    pass

def setup_render(scene, spec, build_dir):
    scene.frame_end = int(round(spec["render_until"] * spec["fps"]))
    scene.samples = spec.get("render", {}).get("samples", 16)
'''
WORLD = '''
def build(spec):
    return {"type": spec["world"]["type"]}
'''
EFFECT = '''
def apply(spec, info):
    spec["effect"].setdefault("yaw", 200)       # scene code may fill in defaults in the dict it is given
    if spec["effect"].get("broken"):
        raise RuntimeError("the effect stopped halfway")
    return {"fps": spec["fps"], "frames": 3, "world": info["type"]}
'''


class BuildScene(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.code = root / "pipeline" / "blender"
        for folder in ("worlds", "effects"):
            (self.code / folder).mkdir(parents=True)
            (self.code / folder / "__init__.py").write_text("", encoding="utf-8")
        shutil.copy(KIT / "pipeline" / "blender" / "build_scene.py", self.code)
        shutil.copy(KIT / "pipeline" / "framecache.py", self.code.parent)
        (self.code / "rf_common.py").write_text(RF_COMMON, encoding="utf-8")
        (self.code / "worlds" / "plain.py").write_text(WORLD, encoding="utf-8")
        (self.code / "effects" / "still.py").write_text(EFFECT, encoding="utf-8")
        self.ep = root / "episodes" / "ep01_test"
        self.ep.mkdir(parents=True)
        self.build = self.ep / "build"
        self.spec = {"id": "ep01_test", "title": "What if?", "fps": 30, "duration": 4.0, "render_until": 3.0,
                     "world": {"type": "plain"}, "effect": {"type": "still"}, "lines": [[1, 3, "You are here."]],
                     "audio": {"mixer": "samples"}}

    def blender(self):
        """Run build_scene.py the way `blender -P build_scene.py -- <episode>` does, with a bpy that only saves."""
        (self.ep / "episode.json").write_text(json.dumps(self.spec), encoding="utf-8")
        scene = types.SimpleNamespace(render=types.SimpleNamespace(engine="BLENDER_EEVEE_NEXT"))
        bpy = types.SimpleNamespace(
            context=types.SimpleNamespace(scene=scene), app=types.SimpleNamespace(version_string="4.5.5 LTS"),
            ops=types.SimpleNamespace(wm=types.SimpleNamespace(
                save_as_mainfile=lambda filepath: Path(filepath).write_bytes(b"BLENDER"))))
        script = self.code / "build_scene.py"
        # sys.modules and sys.path are put back afterwards, so each run imports the stand-in scene modules afresh
        with mock.patch.dict(sys.modules, {"bpy": bpy}), mock.patch.object(sys, "path", list(sys.path)), \
                mock.patch.object(sys, "argv", ["blender", "-b", "-P", str(script), "--", str(self.ep)]), \
                mock.patch("builtins.print"):
            runpy.run_path(str(script), run_name="__main__")
        return framecache.read_stamp(self.build / framecache.SCENE)

    def test_the_stamp_holds_what_the_scene_read(self):
        stamp = self.blender()
        self.assertEqual(sorted(stamp["spec"]), ["effect", "fps", "render", "render_until", "world"])
        self.assertIsNone(stamp["spec"]["render"])              # asked for, and not in episode.json
        self.assertEqual(sorted(stamp["code"]), ["build_scene.py", "effects/__init__.py", "effects/still.py",
                                                 "rf_common.py", "worlds/__init__.py", "worlds/plain.py"])
        self.assertEqual((stamp["blender"], stamp["engine"]), ("4.5.5 LTS", "BLENDER_EEVEE_NEXT"))
        self.assertEqual(framecache.outdated(stamp, self.spec, self.code), [])     # it matches the file on disk
        self.assertEqual(json.loads((self.build / "series.json").read_text(encoding="utf-8")),
                         {"fps": 30, "frames": 3, "world": "plain"})
        self.assertTrue((self.build / "scene.blend").exists())

    def test_text_and_sound_leave_the_stamp_alone_and_the_scene_does_not(self):
        first = self.blender()
        self.spec["lines"].append([3.2, 3.9, "Still here."])
        self.spec["audio"]["hits"] = [{"synth": "pop", "at": 1.0}]
        self.spec["title"] = "What if you stayed?"
        self.assertEqual(self.blender()["fingerprint"], first["fingerprint"])
        self.spec["render"] = {"samples": 32}
        third = self.blender()
        self.assertEqual(framecache.changed(first, third), ['episode.json "render"'])
        (self.code / "worlds" / "plain.py").write_text(WORLD + "\n# a taller church\n", encoding="utf-8")
        self.assertEqual(framecache.changed(third, self.blender()), ["pipeline/blender/worlds/plain.py"])

    def test_a_build_that_stops_halfway_leaves_no_stamp(self):
        stamp = self.blender()
        framecache.write_stamp(self.build / "frames" / framecache.STAMP, stamp)
        (self.build / "frames" / "f_0001.jpg").write_bytes(b"\xff\xd8 a frame \xff\xd9")
        self.spec["effect"]["broken"] = True
        with self.assertRaisesRegex(RuntimeError, "stopped halfway"):
            self.blender()
        self.assertTrue((self.build / "scene.blend").exists())          # the earlier scene is still on disk
        self.assertIsNone(framecache.read_stamp(self.build / framecache.SCENE))     # nothing calls it the current one
        self.assertTrue(framecache.unfinished(self.build))              # or one from before there were stamps
        self.assertEqual(framecache.frames_state(self.build)[0], "unbuilt")
        (self.build / "frames" / framecache.STAMP).unlink()             # as after `ship` has cleaned the frames
        self.assertEqual(framecache.frames_state(self.build)[0], "unbuilt")
        del self.spec["effect"]["broken"]
        self.assertEqual(self.blender()["fingerprint"], stamp["fingerprint"])       # the next good build stamps it
        self.assertFalse(framecache.unfinished(self.build))


if __name__ == "__main__":
    unittest.main()
