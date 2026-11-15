"""Blender entry point: build an episode's scene from its spec and save build/scene.blend + build/series.json.

Usage (normally via make_episode.py):
  blender -b --factory-startup -P pipeline/blender/build_scene.py -- <episode_dir>

It also writes build/scene.json, the scene's stamp (pipeline/framecache.py): which episode.json keys the world and
the effect read, the code that built it, and this Blender. The render and video steps use it to tell frames of this
scene from frames of an older one.
"""
import importlib
import json
import sys
from pathlib import Path

import bpy

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))    # pipeline/, for framecache
sys.path.insert(0, str(HERE))           # this folder first: the scene modules import each other by name

import framecache  # noqa: E402
import rf_common as rf  # noqa: E402

ep_dir = Path(sys.argv[sys.argv.index("--") + 1]).resolve()
on_disk = (ep_dir / "episode.json").read_text(encoding="utf-8")
spec = framecache.Recorder(json.loads(on_disk))
build = ep_dir / "build"
(build / "frames").mkdir(parents=True, exist_ok=True)
# Until this build has finished there is no stamp: a build that stops halfway leaves an old scene.blend behind, and
# nothing should take it for the scene episode.json describes now.
(build / framecache.SCENE).unlink(missing_ok=True)

rf.clear_scene()
scene = bpy.context.scene
rf.setup_render(scene, spec, build)

world = importlib.import_module("worlds." + spec["world"]["type"])
info = world.build(spec)
effect = importlib.import_module("effects." + spec["effect"]["type"])
series = effect.apply(spec, info)

(build / "series.json").write_text(json.dumps(series), encoding="utf-8")
bpy.ops.wm.save_as_mainfile(filepath=str(build / "scene.blend"))
sources = [__file__] + [m.__file__ for m in list(sys.modules.values())
                        if getattr(m, "__file__", None) and HERE in Path(m.__file__).resolve().parents]
# The stamp is taken from episode.json as it is on disk, not from the dict the scene code was handed: a world or an
# effect may fill in defaults there, and the stamp has to match what the render step reads back from the file.
framecache.write_stamp(build / framecache.SCENE, framecache.scene_stamp(
    json.loads(on_disk), spec.read, HERE, sources, bpy.app.version_string, scene.render.engine))
print(f"[build_scene] saved {build / 'scene.blend'}")
