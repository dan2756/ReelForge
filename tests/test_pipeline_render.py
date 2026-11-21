"""The stills and render steps: no picture of an older scene is reused without being asked, and a stopped render
is picked up cleanly. Blender is stood in for by a function that writes frames the way it does (skipping any file
that is already there)."""

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from pipeline import framecache, make_episode

FPS, SECONDS = 10, 3.0
TOTAL = int(FPS * SECONDS)


class Steps(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        # In its real spelling, the one make_episode works with: it resolves the episode folder it is given, and a
        # temporary folder is named through a link on macOS (/var) and by its short name on Windows (RUNNER~1)
        self.root = Path(tmp.name).resolve()
        self.code = self.root / "pipeline" / "blender"
        self.code.mkdir(parents=True)
        (self.code / "rf_common.py").write_text("HAZE = 1\n", encoding="utf-8")
        self.ep = self.root / "episodes" / "ep01_test"
        self.build = self.ep / "build"
        self.frames = self.build / "frames"
        self.stills = self.build / "stills"
        self.frames.mkdir(parents=True)
        self.spec = {"id": "ep01_test", "title": "What if a duck doubled?", "fps": FPS, "duration": SECONDS,
                     "render_until": SECONDS, "world": {"type": "street"}, "effect": {"type": "grow"}, "lines": []}
        self.passes = 0             # how many times Blender was started
        self.others = []            # the other commands the steps ran (compose, qa), as lists of words
        self.lost = []              # per pass: the frames that pass fails to write
        self.exit = []              # per pass: the code Blender exits with
        for patch in (mock.patch.object(make_episode, "run", self.blender),
                      mock.patch.object(make_episode.config, "PIPELINE", self.root / "pipeline"),
                      mock.patch.object(make_episode.config, "BLENDER", "blender")):
            patch.start()
            self.addCleanup(patch.stop)

    # ---- the stand-ins

    def blender(self, cmd, log=None, **_):
        """What `blender -b scene.blend -a` and `... -f 1,2` leave on disk."""
        cmd = [str(c) for c in cmd]
        if cmd[0] != "blender":
            self.others.append([Path(c).name if c.endswith(".py") else c for c in cmd[1:]])
            return
        self.blender_cmd = cmd
        self.passes += 1
        lost = self.lost.pop(0) if self.lost else set()
        if "-a" in cmd:
            numbers, folder, prefix = range(1, TOTAL + 1), self.frames, "f"
        else:
            numbers, folder, prefix = [int(n) for n in cmd[cmd.index("-f") + 1].split(",")], self.stills, "s"
        for n in numbers:
            path = folder / f"{prefix}_{n:04d}.jpg"
            if n not in lost and not path.exists():
                path.write_bytes(self.picture(f"pass {self.passes}"))
        code = self.exit.pop(0) if self.exit else 0
        if code:
            raise SystemExit(f"step failed with exit code {code}")

    @staticmethod
    def picture(text):
        return b"\xff\xd8" + text.encode() + b" " * 64 + b"\xff\xd9"

    def built(self, spec=None):
        """The build step: scene.blend and its stamp, for the spec as it is now."""
        (self.ep / "episode.json").write_text(json.dumps(self.spec), encoding="utf-8")
        (self.build / "scene.blend").write_bytes(b"BLENDER")
        stamp = framecache.scene_stamp(spec or self.spec, {"fps", "render_until", "world", "effect"}, self.code,
                                       [self.code / "rf_common.py"], "4.5.5", "BLENDER_EEVEE_NEXT")
        framecache.write_stamp(self.build / framecache.SCENE, stamp)
        return stamp

    def rendered(self, numbers, stamp, text="earlier"):
        for n in numbers:
            (self.frames / f"f_{n:04d}.jpg").write_bytes(self.picture(text))
        if stamp:
            framecache.write_stamp(self.frames / framecache.STAMP, stamp)

    def render(self, **options):
        out = io.StringIO()
        with redirect_stdout(out):
            make_episode.render(self.build, self.spec, **options)
        return out.getvalue()

    def shows(self, n):
        return (self.frames / f"f_{n:04d}.jpg").read_bytes().split(b" ")[0][2:].decode()

    def main(self, *args):
        (self.ep / "episode.json").write_text(json.dumps(self.spec), encoding="utf-8")
        out = io.StringIO()
        with mock.patch.object(sys, "argv", ["make_episode.py", str(self.ep), *args]), redirect_stdout(out):
            make_episode.main()
        return out.getvalue()


class Stills(Steps):
    def test_a_still_is_rendered_again_even_if_it_is_already_there(self):
        stamp = self.built()
        self.stills.mkdir()
        (self.stills / "s_0011.jpg").write_bytes(self.picture("earlier"))
        (self.stills / "s_0021.jpg").write_bytes(self.picture("earlier"))
        framecache.write_stamp(self.stills / framecache.STAMP, stamp)
        self.main("--steps", "stills", "--stills", "1")
        self.assertIn(b"pass 1", (self.stills / "s_0011.jpg").read_bytes())
        self.assertIn(b"earlier", (self.stills / "s_0021.jpg").read_bytes())     # same scene, not asked for: kept

    def test_stills_of_an_older_scene_are_cleared_out(self):
        old = self.built()
        self.stills.mkdir()
        (self.stills / "s_0021.jpg").write_bytes(self.picture("earlier"))
        framecache.write_stamp(self.stills / framecache.STAMP, old)
        self.spec["world"]["type"] = "plaza"
        new = self.built()
        self.main("--steps", "stills", "--stills", "1")
        self.assertEqual(sorted(p.name for p in self.stills.glob("s_*.jpg")), ["s_0011.jpg"])
        self.assertEqual(framecache.read_stamp(self.stills / framecache.STAMP), new)

    def test_stills_wait_for_a_build_when_the_scene_was_edited_since(self):
        self.built()
        self.spec["effect"]["subject"] = "donut"
        with self.assertRaisesRegex(SystemExit, r'stills: episode.json "effect" changed after the scene was built'):
            self.main("--steps", "stills", "--stills", "1")
        self.assertEqual(self.passes, 0)

    def test_as_built_gives_stills_of_the_scene_as_it_was_built(self):
        stamp = self.built()
        self.spec["effect"]["subject"] = "donut"
        out = self.main("--steps", "stills", "--stills", "1", "--as-built")
        self.assertIn('episode.json "effect" changed after the scene was built: using the scene as it was built', out)
        self.assertEqual(self.passes, 1)
        self.assertEqual(framecache.read_stamp(self.stills / framecache.STAMP), stamp)

    def test_the_stills_are_previewed_with_the_text_on(self):
        self.built()
        self.main("--steps", "stills", "--stills", "0.5,2")
        self.assertEqual(self.blender_cmd[self.blender_cmd.index("-f") + 1], "6,21")
        self.assertEqual(self.others, [["compose.py", str(self.ep), "--preview", "0.5,2"]])

    def test_the_lines_preset_looks_where_a_line_can_be_over_the_wrong_shot(self):
        self.spec["lines"] = [[0.2, 1.4, "You are a duck."], [1.6, 2.8, "A bigger duck."], [2.7, 4.0, "Past the end."]]
        self.spec["cover"] = {"time": 1.0}
        self.built()
        (self.build / "series.json").write_text(json.dumps({"events": {"cuts": [1.5]}}), encoding="utf-8")
        # line 1 in and out, the cover between them, both sides of the cut, line 2 in and out; line 3 is post-only
        times = [0.65, 1.0, 1.2, 1.4, 1.6, 2.05, 2.6]
        self.assertEqual(make_episode.line_times(self.spec, self.build), times)
        self.main("--steps", "stills", "--stills", "lines")
        self.assertEqual(self.blender_cmd[self.blender_cmd.index("-f") + 1], "7,11,13,15,17,21,27")
        self.assertEqual(self.others[-1][-1], "0.65,1,1.2,1.4,1.6,2.05,2.6")

    def test_the_lines_preset_works_without_cuts_or_a_build_folder_full_of_data(self):
        self.spec["lines"] = [[0.2, 0.5, "Short."]]
        self.assertEqual(make_episode.line_times(self.spec, self.build), [0.3, 0.5])


class RenderGuard(Steps):
    def test_a_first_render_stamps_its_frames(self):
        stamp = self.built()
        self.render()
        self.assertEqual(framecache.missing(self.frames, TOTAL), [])
        self.assertEqual(framecache.read_stamp(self.frames / framecache.STAMP), stamp)

    def test_frames_of_this_scene_are_kept_and_the_render_carries_on(self):
        stamp = self.built()
        self.rendered(range(1, 13), stamp)
        self.render()
        self.assertEqual((self.shows(12), self.shows(13)), ("earlier", "pass"))

    def test_frames_of_an_older_scene_stop_the_render(self):
        self.rendered(range(1, 13), self.built())
        self.spec["world"]["type"] = "plaza"
        self.built()
        with self.assertRaises(SystemExit) as stop:
            self.render()
        message = str(stop.exception)
        self.assertIn('the 12 frames in build/frames were rendered from an older scene '
                      '(changed since: episode.json "world")', message)
        for choice in ("--rerender all", "--rerender 12-15.5,31-33", "--keep-frames"):
            self.assertIn(choice, message)
        self.assertEqual(self.passes, 0)
        self.assertEqual(len(list(self.frames.glob("f_*.jpg"))), 12)

    def test_frames_with_no_record_stop_the_render_once_the_scene_has_a_stamp(self):
        self.built()
        self.rendered(range(1, 13), None)
        with self.assertRaisesRegex(SystemExit, "have no record of the scene they were rendered from"):
            self.render()
        self.assertEqual(self.passes, 0)

    def test_rerender_all_starts_again(self):
        self.rendered(range(1, 13), self.built())
        self.spec["world"]["type"] = "plaza"
        new = self.built()
        self.assertIn("deleted 12 frames", self.render(rerender="all"))
        self.assertEqual({self.shows(n) for n in range(1, TOTAL + 1)}, {"pass"})
        self.assertEqual(framecache.read_stamp(self.frames / framecache.STAMP), new)

    def test_rerender_seconds_replaces_only_those_frames(self):
        self.rendered(range(1, TOTAL + 1), self.built())
        self.spec["effect"]["subject"] = "donut"
        new = self.built()
        out = self.render(rerender="1-1.5,2.9")
        self.assertIn("rendering frames 11-16 (1.0-1.5 s), 30 (2.9 s) again; the other frames are kept", out)
        again = {n for n in range(1, TOTAL + 1) if self.shows(n) == "pass"}
        self.assertEqual(again, {11, 12, 13, 14, 15, 16, 30})
        self.assertEqual(framecache.read_stamp(self.frames / framecache.STAMP), new)
        self.render()                                   # the rest now counts as this scene's: nothing to ask
        self.assertEqual(self.shows(1), "earlier")

    def test_a_rerender_that_was_stopped_carries_on_with_the_same_command(self):
        """A shell that stops long commands runs `--rerender all` again and again: each run has to add to the last,
        not delete what it rendered."""
        self.rendered(range(1, TOTAL + 1), self.built())
        self.spec["world"]["type"] = "plaza"
        self.built()
        self.lost, self.exit = [set(range(12, TOTAL + 1))], [-15]           # stopped after eleven frames
        with self.assertRaises(SystemExit):
            self.render(rerender="all")
        self.assertEqual(sorted(framecache.present(self.frames)), list(range(1, 12)))
        out = self.render(rerender="all")
        self.assertNotIn("rendering every frame again", out)
        self.assertIn("carrying on the re-render that was stopped", out)
        self.assertIn(b"pass 1", (self.frames / "f_0011.jpg").read_bytes())  # the first run's frames are still there
        self.assertIn(b"pass 2", (self.frames / "f_0012.jpg").read_bytes())
        self.assertFalse((self.frames / framecache.RERENDER).exists())      # done: the next one starts afresh
        self.assertIn(f"deleted {TOTAL} frames", self.render(rerender="all"))

    def test_a_stopped_rerender_of_some_seconds_carries_on_too(self):
        self.rendered(range(1, TOTAL + 1), self.built())
        self.lost, self.exit = [set(range(14, TOTAL + 1))], [-15]
        with self.assertRaises(SystemExit):
            self.render(rerender="1-1.5")                                   # frames 11-16
        self.assertIn("carrying on the re-render that was stopped", self.render(rerender=" 1 - 1.5 "))
        self.assertEqual([n for n in range(1, TOTAL + 1) if b"pass 1" in (self.frames / f"f_{n:04d}.jpg").read_bytes()],
                         [11, 12, 13])
        self.assertEqual([n for n in range(1, TOTAL + 1) if b"pass 2" in (self.frames / f"f_{n:04d}.jpg").read_bytes()],
                         [14, 15, 16])

    def test_a_stopped_rerender_starts_again_for_other_seconds_or_another_scene(self):
        self.rendered(range(1, TOTAL + 1), self.built())
        self.lost, self.exit = [set(range(14, TOTAL + 1))], [-15]
        with self.assertRaises(SystemExit):
            self.render(rerender="1-1.5")                                   # frames 11-16: 11, 12 and 13 came out
        self.lost, self.exit = [set(range(15, TOTAL + 1))], [-15]
        with self.assertRaises(SystemExit):
            self.render(rerender="1.2-1.5")                                 # other seconds: 13 goes, 13 and 14 come
        self.assertIn(b"pass 1", (self.frames / "f_0012.jpg").read_bytes())
        self.assertIn(b"pass 2", (self.frames / "f_0013.jpg").read_bytes())
        self.spec["effect"]["subject"] = "donut"
        self.built()                                                        # the scene was built again
        self.lost, self.exit = [set(range(1, TOTAL + 1))], [-15]
        with self.assertRaises(SystemExit):
            self.render(rerender="1.2-1.5")                                 # the same seconds, of another scene
        self.assertEqual(framecache.missing(self.frames, TOTAL), [(13, 16)])

    def test_keep_frames_accepts_them_as_they_are(self):
        self.rendered(range(1, 13), self.built())
        self.spec["world"]["type"] = "plaza"
        new = self.built()
        self.assertIn("keeping the 12 frames on disk, as asked", self.render(keep=True))
        self.assertEqual((self.shows(12), self.shows(13)), ("earlier", "pass"))
        self.assertEqual(framecache.read_stamp(self.frames / framecache.STAMP), new)

    def test_a_scene_from_before_stamps_renders_as_it_always_did(self):
        (self.build / "scene.blend").write_bytes(b"BLENDER")
        self.rendered(range(1, 13), None)
        self.assertIn("built by an older ReelForge", self.render())
        self.assertEqual((self.shows(12), self.shows(13)), ("earlier", "pass"))
        self.assertFalse((self.frames / framecache.STAMP).exists())

    def test_what_a_killed_render_left_is_rendered_again(self):
        stamp = self.built()
        self.rendered(range(1, 13), stamp)
        (self.frames / "f_0013.jpg").write_bytes(b"")                       # the frame Blender was on
        (self.frames / "f_0012.jpg").write_bytes(self.picture("earlier")[:20])
        self.assertIn("removed 2 empty or cut-off frames", self.render())
        self.assertEqual((self.shows(11), self.shows(12), self.shows(13)), ("earlier", "pass", "pass"))

    def test_an_edit_after_the_build_waits_for_a_new_build(self):
        self.built()
        (self.code / "rf_common.py").write_text("HAZE = 2\n", encoding="utf-8")
        with self.assertRaisesRegex(SystemExit, r"render: pipeline/blender/rf_common.py changed after the scene was "
                                                r"built.*Run --steps build first"):
            self.render()
        self.assertEqual(self.passes, 0)

    def test_as_built_carries_on_with_the_scene_as_it_was_built(self):
        """A module this scene shares with another episode was edited while its render was stopped. scene.blend
        and the frames still belong together, so the render can go on without building again."""
        stamp = self.built()
        self.rendered(range(1, 13), stamp)
        (self.code / "rf_common.py").write_text("HAZE = 1\n# for the other episode\n", encoding="utf-8")
        with self.assertRaisesRegex(SystemExit, "or add --as-built to use the scene as it was built"):
            self.render()
        with self.assertRaisesRegex(SystemExit, "or add --as-built"):
            self.render(keep=True)                              # keeping the frames was never the question
        out = self.render(as_built=True)
        self.assertIn("pipeline/blender/rf_common.py changed after the scene was built: using the scene as it was "
                      "built, as asked", out)
        self.assertEqual((self.shows(12), self.shows(13)), ("earlier", "pass"))
        self.assertEqual(framecache.read_stamp(self.frames / framecache.STAMP), stamp)

    def test_as_built_still_asks_about_frames_of_an_older_scene(self):
        self.rendered(range(1, 13), self.built())
        self.spec["world"]["type"] = "plaza"
        self.built()
        self.spec["effect"]["subject"] = "donut"                # edited again, not built
        with self.assertRaisesRegex(SystemExit, "say what to do with them"):
            self.render(as_built=True)
        self.assertEqual(self.passes, 0)

    def test_as_built_does_not_stand_in_for_a_build_that_did_not_finish(self):
        self.rendered(range(1, 13), self.built())
        (self.build / framecache.SCENE).unlink()
        with self.assertRaisesRegex(SystemExit, "the last build did not finish"):
            self.render(as_built=True)

    def test_a_build_that_did_not_finish_stops_the_render(self):
        self.rendered(range(1, 13), self.built())
        (self.build / framecache.SCENE).unlink()                # frames of a stamped scene, and the scene has none
        with self.assertRaisesRegex(SystemExit, "the last build did not finish"):
            self.render()

    def test_a_build_that_did_not_finish_stops_the_render_and_the_stills_with_no_pictures_on_disk(self):
        """A remake: `ship` cleaned the frames and stills long ago, and the build of the new scene stopped halfway.
        The scene.blend on disk is the old video's, not one from before ReelForge kept stamps."""
        self.built()
        framecache.write_stamp(self.build / framecache.SCENE, {"unfinished": True})    # what build_scene.py starts with
        with self.assertRaisesRegex(SystemExit, "render: the last build did not finish"):
            self.render()
        with self.assertRaisesRegex(SystemExit, "stills: the last build did not finish"):
            self.main("--steps", "stills", "--stills", "1")
        self.assertEqual(self.passes, 0)
        self.assertEqual(list(self.frames.glob("f_*.jpg")), [])

    def test_no_scene_no_render(self):
        with self.assertRaisesRegex(SystemExit, "render: build/scene.blend not found, run --steps build first"):
            self.render()

    def test_the_options_are_checked_before_anything_is_deleted(self):
        self.rendered(range(1, 13), self.built())
        for options, why in (({"rerender": "5-9"}, "starts after the last rendered frame"),
                             ({"rerender": "soon"}, "isn't a range of seconds"),
                             ({"rerender": "inf"}, "isn't a range of seconds"),      # (numbers to float(): each of
                             ({"rerender": "0-1e400"}, "isn't a range of seconds"),  # them ended in a traceback)
                             ({"rerender": "1e308"}, "isn't a range of seconds"),
                             ({"rerender": "nan"}, "isn't a range of seconds"),
                             ({"rerender": "all", "keep": True}, "don't go together")):
            with self.assertRaisesRegex(SystemExit, why):
                self.render(**options)
        self.assertEqual(len(list(self.frames.glob("f_*.jpg"))), 12)

    def test_the_options_reach_the_render_step(self):
        self.rendered(range(1, TOTAL + 1), self.built())
        self.spec["world"]["type"] = "plaza"
        self.built()
        with self.assertRaisesRegex(SystemExit, "say what to do with them"):
            self.main("--steps", "render")
        self.main("--steps", "render", "--rerender", "0-0.4")
        self.assertEqual({n for n in range(1, TOTAL + 1) if self.shows(n) == "pass"}, {1, 2, 3, 4, 5})
        self.spec["world"]["type"] = "street"
        street = self.built()
        self.main("--steps", "render", "--keep-frames")
        self.assertEqual(framecache.read_stamp(self.frames / framecache.STAMP), street)
        self.assertEqual(self.passes, 1)                        # every frame was there, so Blender had nothing to do
        self.spec["effect"]["subject"] = "donut"
        (self.frames / "f_0030.jpg").unlink()
        with self.assertRaisesRegex(SystemExit, 'render: episode.json "effect" changed after the scene was built'):
            self.main("--steps", "render")
        self.assertIn("using the scene as it was built, as asked", self.main("--steps", "render", "--as-built"))
        self.assertEqual(self.passes, 2)


class RenderPasses(Steps):
    def test_nothing_missing_means_blender_is_not_started(self):
        self.rendered(range(1, TOTAL + 1), self.built())
        self.assertIn(f"all {TOTAL} frames are in build/frames", self.render())
        self.assertEqual(self.passes, 0)

    def test_it_goes_back_once_for_frames_that_did_not_come_out(self):
        self.built()
        self.lost = [{7, 8, 9, 21}]
        out = self.render()
        self.assertIn("frames 7-9 (0.6-0.8 s), 21 (2.0 s) did not come out: going back once", out)
        self.assertEqual(self.passes, 2)
        self.assertEqual(framecache.missing(self.frames, TOTAL), [])

    def test_a_frame_cut_off_during_the_render_is_made_again(self):
        self.built()

        def cut_one_off(progress):
            (self.frames / "f_0005.jpg").write_bytes(self.picture("pass 1")[:30])   # the disk was full
            progress()                                          # seen on disk, then swept: it did not come out
        with self.during_the_first_pass(cut_one_off):
            self.render()
        self.assertEqual((self.passes, self.shows(5)), (2, "pass"))
        self.assertIn(b"pass 2", (self.frames / "f_0005.jpg").read_bytes())

    def during_the_first_pass(self, happens):
        """Run the render step with `happens(progress)` called just before Blender's first pass ends."""
        def blender(cmd, log=None, progress=None, **kw):
            self.blender(cmd, log, **kw)
            if self.passes == 1:
                happens(progress)
        return mock.patch.object(make_episode, "run", blender)

    def test_it_does_not_go_back_once_the_scene_is_being_built_again(self):
        """The scene is fixed and built again while its render runs, and the bad shot's frames are deleted behind
        the render for the next run to make. Going back would fill them from the scene.blend that is on disk at
        that moment, which is the old one until the build has saved."""
        self.built()

        def a_build_begins(progress):
            for n in (7, 8, 9):
                (self.frames / f"f_{n:04d}.jpg").unlink()
            framecache.write_stamp(self.build / framecache.SCENE, {"unfinished": True})
        with self.during_the_first_pass(a_build_begins), self.assertRaisesRegex(
                SystemExit, r"render: the scene was built again while this render ran, so it did not go back for "
                            r"frames 7-9 \(0\.6-0\.8 s\)\. Run --steps render again"):
            self.render()
        self.assertEqual(self.passes, 1)
        self.assertEqual(framecache.missing(self.frames, TOTAL), [(7, 9)])

    def test_it_does_not_go_back_once_another_scene_has_been_built(self):
        stamp = self.built()

        def a_build_finishes(progress):
            (self.frames / "f_0007.jpg").unlink()
            self.spec["effect"]["subject"] = "donut"
            self.built()
        with self.during_the_first_pass(a_build_finishes), self.assertRaisesRegex(
                SystemExit, "the scene was built again while this render ran"):
            self.render()
        self.assertEqual(self.passes, 1)
        self.assertEqual(framecache.read_stamp(self.frames / framecache.STAMP), stamp)     # still the old scene's
        with self.assertRaisesRegex(SystemExit, "were rendered from an older scene"):
            self.render()                                       # and the next run asks what to do with them

    def test_it_does_not_go_back_for_frames_deleted_behind_the_render(self):
        """Frames deleted on purpose while the render runs are for a later run, of the scene as it will be then."""
        self.rendered(range(1, 6), self.built())

        def frames_are_deleted(progress):
            progress()                                          # the step looks at the folder as Blender renders
            for n in (3, 12, 13):                               # one from before this run, two it had just made
                (self.frames / f"f_{n:04d}.jpg").unlink()
        with self.during_the_first_pass(frames_are_deleted), self.assertRaisesRegex(
                SystemExit, r"render: frames 3 \(0\.2 s\), 12-13 \(1\.1-1\.2 s\) were deleted while this render ran"):
            self.render()
        self.assertEqual(self.passes, 1)
        self.assertEqual(framecache.missing(self.frames, TOTAL), [(3, 3), (12, 13)])
        self.assertIn(f"all {TOTAL} frames are in build/frames", self.render())    # the next run makes them
        self.assertEqual(self.passes, 2)

    def test_frames_still_missing_after_the_second_pass_fail_the_step_by_name(self):
        self.built()
        self.lost = [{7, 8, 9}, {8}]
        with self.assertRaisesRegex(SystemExit, r"render: frame 8 \(0\.7 s\) is still missing after going back once"):
            self.render()
        self.assertEqual(self.passes, 2)

    def test_a_render_that_fails_is_not_started_again(self):
        self.built()
        self.lost, self.exit = [set(range(12, TOTAL + 1))], [11]
        with self.assertRaisesRegex(SystemExit, "exit code 11"):
            self.render()
        self.assertEqual(self.passes, 1)
        self.render()                                           # the next run carries on where it stopped
        self.assertEqual((self.shows(11), self.shows(12)), ("pass", "pass"))
        self.assertIn(b"pass 2", (self.frames / "f_0012.jpg").read_bytes())

    def test_progress_is_counted_from_the_files(self):
        stamp = self.built()
        self.rendered(range(1, 11), stamp)
        seen = set()
        with mock.patch.object(make_episode.time, "time", side_effect=[100.0, 109.0, 118.0]):
            line = make_episode.render_progress(self.frames, TOTAL, seen)
            self.assertEqual(line(), f"frames 10/{TOTAL}, the first one is still rendering")
            self.rendered(range(11, 21), stamp)
            self.assertEqual(line(), f"frames 20/{TOTAL}, 1.80 s a frame, about 18 s left")
        self.assertEqual(seen, set(range(1, 21)))               # and it remembers every frame it has seen


class RunningAStep(unittest.TestCase):
    """run() itself, with small Python commands in place of Blender."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.log = Path(tmp.name) / "render.log"

    def run_step(self, code, **options):
        out = io.StringIO()
        with redirect_stdout(out):
            try:
                make_episode.run([sys.executable, "-c", code], **options)
            finally:
                self.out = out.getvalue()

    def test_the_log_is_added_to_not_replaced(self):
        self.run_step("print('first run')", log=self.log)
        self.run_step("print('second run')", log=self.log)
        text = self.log.read_text(encoding="utf-8")
        self.assertLess(text.index("first run"), text.index("second run"))
        self.assertEqual(text.count("===="), 2)

    def test_a_failed_step_prints_the_end_of_its_log_and_the_error_lines(self):
        self.run_step("print('an earlier run')", log=self.log)
        code = ("import sys\nprint('Traceback (most recent call last):')\nprint('KeyError: fps')\n"
                "for n in range(30): print('line', n)\nsys.exit(3)")
        with self.assertRaisesRegex(SystemExit, "step failed with exit code 3"):
            self.run_step(code, log=self.log)
        self.assertIn("that mention an error:\n    Traceback (most recent call last):\n    KeyError: fps", self.out)
        self.assertIn("the last lines of", self.out)
        self.assertIn("    line 29", self.out)
        self.assertNotIn("    line 9\n", self.out)             # only the last 20
        self.assertNotIn("an earlier run", self.out)            # and only this run's

    def test_a_step_without_a_log_still_fails_with_its_exit_code(self):
        with self.assertRaisesRegex(SystemExit, "step failed with exit code 4$"):
            self.run_step("import sys; sys.exit(4)")

    @unittest.skipIf(os.name == "nt", "Windows has no signals to report")
    def test_a_step_that_was_killed_says_so(self):
        with self.assertRaisesRegex(SystemExit, "step was stopped by signal 9"):
            self.run_step("import os, signal; os.kill(os.getpid(), signal.SIGKILL)", log=self.log)

    def test_progress_is_printed_while_the_step_runs(self):
        with mock.patch.object(make_episode, "PROGRESS_EVERY", 0.05):
            self.run_step("import time; time.sleep(0.6)", log=self.log, progress=lambda: "frames 3/30")
        self.assertIn("  frames 3/30\n", self.out)
        self.assertIn("done in", self.out)


if __name__ == "__main__":
    unittest.main()
