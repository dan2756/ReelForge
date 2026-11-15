"""Unpacking a .tar download (Blender and ffmpeg on Linux, a ReelForge update on Linux): studio.tools.untar, on a
Python with tarfile's "data" filter and on one without (3.11.0 to 3.11.3, which Debian 12 ships), where asking for
the filter was a TypeError and nothing could be unpacked at all."""

import importlib.util
import io
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
import warnings
from pathlib import Path
from unittest import mock

from studio import tools, update

KIT = Path(__file__).resolve().parent.parent
FILTERS = hasattr(tarfile, "data_filter")         # False when these tests themselves run on Python 3.11.0 to 3.11.3
PYTHONS = (False, True) if FILTERS else (True,)   # old_python: today's and the old one, or only the old one


def old_python():
    """tarfile as Python 3.11.0 to 3.11.3 has it: no filters, and extractall() takes no filter argument."""
    if not FILTERS:
        return lambda: None
    real = tarfile.TarFile.extractall

    def extractall(self, path=".", members=None, *, numeric_owner=False):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)      # today's Python asks for a filter
            return real(self, path, members, numeric_owner=numeric_owner)

    hide = mock.patch.dict(tarfile.__dict__)
    hide.start()
    del tarfile.__dict__["data_filter"]
    take = mock.patch.object(tarfile.TarFile, "extractall", extractall)
    take.start()
    return lambda: (take.stop(), hide.stop())


class Untar(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        # In its real spelling: the tests below compare where a link leads (os.path.realpath) with this folder,
        # and tempfile names it through a link on macOS (/var) and by its short name on Windows (RUNNER~1)
        self.dir = Path(tmp.name).resolve()
        self.out = self.dir / "out"

    def archive(self, *members, name="a.tar.gz") -> Path:
        """members: (name, text) for a file, or a ready TarInfo for anything else."""
        path = self.dir / name
        with tarfile.open(path, "w:gz") as t:
            for m in members:
                if isinstance(m, tarfile.TarInfo):
                    t.addfile(m)
                else:
                    info = tarfile.TarInfo(m[0])
                    data = m[1].encode("utf-8")
                    info.size, info.mode = len(data), 0o755 if m[0].endswith("bin/ffmpeg") else 0o644
                    t.addfile(info, io.BytesIO(data))
        return path

    @staticmethod
    def link(name: str, to: str, hard: bool = False) -> tarfile.TarInfo:
        info = tarfile.TarInfo(name)
        info.type, info.linkname = (tarfile.LNKTYPE if hard else tarfile.SYMTYPE), to
        return info

    def test_an_ordinary_archive_unpacks_the_same_on_both(self):
        folder = tarfile.TarInfo("ffmpeg-n7.1/bin")
        folder.type, folder.mode = tarfile.DIRTYPE, 0o755
        # links that stay inside the folder are fine too, one by way of another as well (left out on Windows,
        # where making one needs a privilege)
        links = [] if os.name == "nt" else [self.link("ffmpeg-n7.1/bin/ff", "ffmpeg"),
                                            self.link("ffmpeg-n7.1/doc", "../ffmpeg-n7.1/bin"),
                                            self.link("ffmpeg-n7.1/run", "doc/ffmpeg")]
        good = self.archive(folder, ("ffmpeg-n7.1/bin/ffmpeg", "run me"), ("ffmpeg-n7.1/LICENSE.txt", "GPL"), *links)
        for old in PYTHONS:
            with self.subTest(old_python=old):
                out = self.dir / f"out-{old}"
                undo = old_python() if old else (lambda: None)
                try:
                    self.assertEqual(hasattr(tarfile, "data_filter"), not old)
                    names = tools.untar(good, out)
                finally:
                    undo()
                self.assertEqual(names[0].split("/")[0], "ffmpeg-n7.1")
                self.assertEqual((out / "ffmpeg-n7.1" / "bin" / "ffmpeg").read_text(encoding="utf-8"), "run me")
                self.assertEqual((out / "ffmpeg-n7.1" / "LICENSE.txt").read_text(encoding="utf-8"), "GPL")
                if os.name != "nt":
                    self.assertTrue(os.access(out / "ffmpeg-n7.1" / "bin" / "ffmpeg", os.X_OK))
                    self.assertEqual((out / "ffmpeg-n7.1" / "bin" / "ff").read_text(encoding="utf-8"), "run me")
                    self.assertEqual((out / "ffmpeg-n7.1" / "run").read_text(encoding="utf-8"), "run me")

    def test_nothing_lands_outside_the_folder_on_either(self):
        device = tarfile.TarInfo("kit/null")
        device.type = tarfile.CHRTYPE
        for old in PYTHONS:
            for n in range(8):
                box = self.dir / f"box-{old}-{n}"       # the archive, the folder it unpacks into, and nothing else
                box.mkdir()
                what, members = list({
                    "a path that climbs out": [("../outside.txt", "x")],
                    "an absolute path": [(str(box / "outside.txt"), "x")],
                    "a link out, then a file through it": [self.link("kit/up", "../.."), ("kit/up/outside.txt", "x")],
                    "a link to an absolute path": [self.link("kit/etc", str(box))],
                    "a hard link to a file outside": [self.link("kit/hard", "../outside.txt", hard=True)],
                    # each of these links stays inside by where it's named, and leads out once the one before it
                    # is there: kit/d/a is kit, so kit/d/a/b lands as kit/b, and so on up
                    "links that lead out one after another, then a file through them": [
                        self.link("kit/d/a", ".."), self.link("kit/d/a/b", ".."), self.link("kit/d/a/b/c", ".."),
                        ("kit/d/a/b/c/outside.txt", "x")],
                    "a link that leads out by way of another": [self.link("here", "."), self.link("up", "here/..")],
                    "a device": [device]}.items())[n]
                with self.subTest(old_python=old, what=what):
                    bad = self.archive(("kit/ok.txt", "fine"), *members, name=f"{box.name}/bad.tar.gz")
                    undo = old_python() if old else (lambda: None)
                    try:
                        tools.untar(bad, box / "out")
                        refused = False
                    except tarfile.TarError:
                        refused = True
                    finally:
                        undo()
                    self.assertLessEqual({p.name for p in box.iterdir()}, {"bad.tar.gz", "out"})
                    if old:    # every entry is checked before the first is unpacked
                        self.assertTrue(refused)
                        self.assertFalse((box / "out").exists())
                    else:      # Python's own filter refuses these too, or unpacks them inside the folder
                        self.assertTrue(refused or what in ("an absolute path",
                                                            "a link that leads out by way of another"), what)
                        for folder, folders, files in os.walk(box / "out"):
                            for name in folders + files:             # whatever it did unpack leads nowhere else
                                real = os.path.realpath(os.path.join(folder, name))
                                self.assertTrue(Path(real).is_relative_to(os.path.realpath(box / "out")), real)

    def test_an_update_on_linux_unpacks_on_the_old_python_too(self):
        root = self.dir / "ReelForge"
        (root / "studio").mkdir(parents=True)
        (root / "studio" / "__init__.py").write_text('KIT_VERSION = "1.0.0"\n', encoding="utf-8")
        archive = self.archive(("ReelForge-1.1.0/studio/__init__.py", 'KIT_VERSION = "1.1.0"\n'),
                               ("ReelForge-1.1.0/README.md", "new"), name="ReelForge-1.1.0-Linux.tar.gz")
        work = self.dir / "work"
        work.mkdir()
        undo = old_python()
        try:
            update._copy_release(archive, root, work, lambda *a: None)
        finally:
            undo()
        self.assertEqual(update.read_version(root), "1.1.0")
        self.assertEqual((root / "README.md").read_text(encoding="utf-8"), "new")
        bad = self.archive(("ReelForge-1.2.0/studio/__init__.py", 'KIT_VERSION = "1.2.0"\n'),
                           ("../outside.txt", "x"), name="ReelForge-1.2.0-Linux.tar.gz")
        with self.assertRaises(update.UpdateError):       # "The download couldn't be unpacked", not a traceback
            update._copy_release(bad, root, self.dir / "work2", lambda *a: None)
        self.assertEqual(update.read_version(root), "1.1.0")


@unittest.skipIf(os.name == "nt", "setup.sh is the macOS and Linux setup")
class SetupPython(unittest.TestCase):
    """setup.sh passes over a Python without tarfile's filter, as it does one without venv: a new install then gets
    the private Python 3.13, which unpacks with the filter. A copy set up with such a Python before keeps its .venv,
    and setup carries on with that."""

    def usable(self, python: str) -> bool:
        text = (KIT / "setup.sh").read_text(encoding="utf-8")
        function = text[text.index("usable() {"):text.index("find_python() {")]
        return subprocess.run(["bash", "-c", function + '\nusable "$0"', python], timeout=60).returncode == 0

    def test_this_python_is_usable_and_one_without_the_filter_isnt(self):
        self.assertEqual(self.usable(sys.executable), FILTERS)
        with tempfile.TemporaryDirectory() as folder:
            old = Path(folder) / "python3.11"         # this Python, with tarfile as 3.11.0 to 3.11.3 has it
            old.write_text("#!/bin/sh\n"
                           f'exec "{sys.executable}" -c \'import sys, tarfile; del tarfile.data_filter; '
                           'exec(sys.argv[1])\' "$2"\n', encoding="utf-8")
            old.chmod(0o755)
            self.assertFalse(self.usable(str(old)))
            self.assertFalse(self.usable(str(Path(folder) / "no-such-python")))

    def find_python(self, kit: Path, path: Path, **env: str) -> str | None:
        """What setup.sh's find_python prints in this ReelForge folder (None: it found none, and setup would offer
        the private Python), with only the programs in `path` to find."""
        text = (KIT / "setup.sh").read_text(encoding="utf-8")
        functions = text[text.index("usable() {"):text.index("fetch() {")]
        r = subprocess.run([shutil.which("bash"), "-c", functions + '\ncd "$0" && find_python', str(kit)],
                           capture_output=True, text=True, timeout=120, env={"PATH": str(path), **env})
        return r.stdout.strip() if r.returncode == 0 else None

    @unittest.skipUnless(importlib.util.find_spec("pip"), "this Python stands in for a .venv's, which has pip")
    def test_a_working_venv_made_with_an_older_python_is_carried_on_with(self):
        # Debian 12: its Python 3.11.2 is all there is, and ReelForge was set up with it by an earlier version.
        # Setup (which also repairs .venv after an update) asked to download a private Python just to get going,
        # and stopped where nobody could answer.
        with tempfile.TemporaryDirectory() as folder:
            kit, programs = Path(folder) / "ReelForge", Path(folder) / "programs"
            (kit / ".venv" / "bin").mkdir(parents=True)
            programs.mkdir()
            os.symlink(shutil.which("uname"), programs / "uname")
            old = programs / "python3"                 # the only Python: this one, with tarfile as 3.11.2 has it
            old.write_text("#!/bin/sh\n"
                           f'exec "{sys.executable}" -c \'import sys, tarfile; del tarfile.data_filter; '
                           'exec(sys.argv[1])\' "$2"\n' if FILTERS else f'#!/bin/sh\nexec "{sys.executable}" "$@"\n',
                           encoding="utf-8")
            old.chmod(0o755)
            self.assertIsNone(self.find_python(kit, programs))                 # no .venv yet: the private Python
            venv = kit / ".venv" / "bin" / "python"
            venv.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n', encoding="utf-8")
            venv.chmod(0o755)
            self.assertEqual(self.find_python(kit, programs), str(venv))
            self.assertIsNone(self.find_python(kit, programs, STUDIO_OWN_PYTHON="1"))   # the tests' switch still holds
            # one a setup that stopped part way left without pip is made again (bootstrap.py), which takes a Python
            venv.write_text(f'#!/bin/sh\n[ "$1" = -m ] && exit 1\nexec "{sys.executable}" "$@"\n', encoding="utf-8")
            self.assertIsNone(self.find_python(kit, programs))


if __name__ == "__main__":
    unittest.main()
