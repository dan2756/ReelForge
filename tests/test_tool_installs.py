"""A tool download that stops part way (the window closed, a full disk, the second of two downloads failing) is never
taken for an install: Blender, ffmpeg and Bun are unpacked beside where they go and moved into place whole
(studio/tools.py, studio/ui.py), and setup counts ffmpeg as ready only with its ffprobe. No downloads: the archives
are made here, in a scratch tools/ folder."""

import io
import os
import shutil
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from studio import setup_flow, tools, ui


class Scratch(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.tools = self.dir / "tools"
        for patch in (mock.patch.object(tools, "TOOLS", self.tools),
                      mock.patch.object(tools, "WINDOWS", False), mock.patch.object(tools, "MAC", False),
                      mock.patch.object(tools, "_fetch_text", lambda url: "".join(     # every checksum file
                          f"{'0' * 64}  {name}\n" for name in (tools.blender_asset(), ui.bun_asset()))),
                      mock.patch.object(tools, "_say", lambda progress, text: None),
                      mock.patch.dict(os.environ, {"STUDIO_ONLY_KIT_TOOLS": "1"}),   # never this computer's own
                      mock.patch.object(tools.shutil, "which", lambda name: None)):
            patch.start()
            self.addCleanup(patch.stop)
        os.environ.pop("STUDIO_FFMPEG", None)
        os.environ.pop("STUDIO_BLENDER", None)

    def serve(self, *archives: Path | Exception):
        """tools.download hands out these ready-made archives in turn (an exception: that download fails)."""
        queue = list(archives)

        def download(url, dest, label="", sha256=None, progress=None):
            item = queue.pop(0)
            if isinstance(item, Exception):
                raise item
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(item, dest)
            return dest

        patch = mock.patch.object(tools, "download", download)
        patch.start()
        self.addCleanup(patch.stop)

    def tar(self, name: str, *files: str) -> Path:
        path = self.dir / name
        with tarfile.open(path, "w:xz") as t:
            for f in files:
                info = tarfile.TarInfo(f)
                info.size, info.mode = 4, 0o755
                t.addfile(info, io.BytesIO(b"prog"))
        return path

    def zip(self, name: str, *files: str) -> Path:
        path = self.dir / name
        with zipfile.ZipFile(path, "w") as z:
            for f in files:
                z.writestr(f, "prog")
        return path

    def rel(self, path) -> str:
        return Path(path).relative_to(self.tools).as_posix()

    def left(self) -> list[str]:
        """What's in tools/, but for the downloads folder."""
        return sorted(p.name for p in self.tools.iterdir() if p.name != "downloads") if self.tools.exists() else []


class Ffmpeg(Scratch):
    BUILD = "ffmpeg-n7.1-latest-linux64-gpl-7.1"

    def linux(self, *archives):
        self.serve(*archives)
        with mock.patch.object(tools, "linux_ffmpeg_build", lambda sums: (self.BUILD + ".tar.xz", "0" * 64)):
            return tools.install_ffmpeg()

    def test_it_goes_into_place_whole_with_its_ffprobe(self):
        good = self.tar("good.tar.xz", f"{self.BUILD}/bin/ffmpeg", f"{self.BUILD}/bin/ffprobe", f"{self.BUILD}/LICENSE")
        self.assertEqual(self.rel(self.linux(good)), "ffmpeg/bin/ffmpeg")
        self.assertTrue((self.tools / "ffmpeg" / "bin" / "ffprobe").is_file())
        self.assertEqual(self.left(), ["ffmpeg"])                  # no staging folder, no folder named after the build

    def test_an_unpack_that_stops_part_way_isnt_found_as_an_install(self):
        good = self.tar("good.tar.xz", f"{self.BUILD}/bin/ffmpeg", f"{self.BUILD}/bin/ffprobe")
        real = tools.untar

        def cut_short(archive, dest):
            real(archive, dest)
            (dest / self.BUILD / "bin" / "ffprobe").unlink()       # as far as it got
            raise OSError(28, "No space left on device")

        with mock.patch.object(tools, "untar", cut_short), self.assertRaises(OSError):
            self.linux(good)
        self.assertIsNone(tools.find_ffmpeg())
        self.assertEqual(self.rel(self.linux(good)), "ffmpeg/bin/ffmpeg")   # setup again: the half is cleared away
        self.assertEqual(self.left(), ["ffmpeg"])

    def test_a_download_without_ffprobe_never_replaces_the_one_you_have(self):
        good = self.tar("good.tar.xz", f"{self.BUILD}/bin/ffmpeg", f"{self.BUILD}/bin/ffprobe")
        self.linux(good)
        (self.tools / "ffmpeg" / "mine.txt").write_text("the install from before", encoding="utf-8")
        with self.assertRaises(RuntimeError) as raised:
            self.linux(self.tar("bad.tar.xz", f"{self.BUILD}/bin/ffmpeg"))
        self.assertIn("no ffprobe in it", str(raised.exception))
        self.assertTrue((self.tools / "ffmpeg" / "mine.txt").exists())
        self.assertEqual(self.rel(tools.find_ffmpeg()), "ffmpeg/bin/ffmpeg")

    def test_on_a_mac_a_second_download_that_fails_leaves_no_ffmpeg_without_ffprobe(self):
        ffmpeg, ffprobe = self.zip("ffmpeg.zip", "ffmpeg"), self.zip("ffprobe.zip", "ffprobe")
        opened = mock.MagicMock()
        opened.__enter__.return_value.geturl.return_value = "https://example.invalid/build.zip"
        for patch in (mock.patch.object(tools, "MAC", True), mock.patch.object(tools.net, "open_url", lambda url: opened)):
            patch.start()
            self.addCleanup(patch.stop)
        self.serve(ffmpeg, OSError("the connection dropped"), ffmpeg, ffprobe)
        with self.assertRaises(OSError):
            tools.install_ffmpeg()
        self.assertIsNone(tools.find_ffmpeg())                     # it used to find the lone ffmpeg here
        self.assertFalse((self.tools / "ffmpeg").exists())
        self.assertEqual(self.rel(tools.install_ffmpeg()), "ffmpeg/bin/ffmpeg")
        self.assertTrue(os.access(self.tools / "ffmpeg" / "bin" / "ffprobe", os.X_OK) or os.name == "nt")
        self.assertEqual(self.left(), ["ffmpeg"])

    def test_a_copy_with_its_ffprobe_is_found_before_one_without(self):
        for folder, both in (("ffmpeg", False), ("ffmpeg-7.1-full_build", True)):
            (self.tools / folder / "bin").mkdir(parents=True)
            (self.tools / folder / "bin" / "ffmpeg").write_text("prog", encoding="utf-8")
            if both:
                (self.tools / folder / "bin" / "ffprobe").write_text("prog", encoding="utf-8")
        self.assertEqual(self.rel(tools.find_ffmpeg()), "ffmpeg-7.1-full_build/bin/ffmpeg")
        self.assertTrue(tools.has_ffprobe(tools.find_ffmpeg()))
        self.assertFalse(tools.has_ffprobe(str(self.tools / "ffmpeg" / "bin" / "ffmpeg")))

    def test_setup_counts_ffmpeg_as_ready_only_with_ffprobe(self):
        (self.tools / "ffmpeg" / "bin").mkdir(parents=True)
        (self.tools / "ffmpeg" / "bin" / "ffmpeg").write_text("prog", encoding="utf-8")   # what an old setup left
        ready = mock.MagicMock(return_value=True)
        with mock.patch.object(tools, "find_blender", lambda: None), \
                mock.patch.object(setup_flow.sounds, "missing", lambda: []), \
                mock.patch.object(setup_flow.sounds, "manifest", lambda: {"sounds": {}}), \
                mock.patch.object(setup_flow.browsers, "installed", ready), \
                mock.patch.object(setup_flow.command, "status", lambda: "on"), \
                mock.patch.object(setup_flow.shortcut, "ready", ready):
            item = next(i for i in setup_flow.plan() if i.key == "ffmpeg")
            self.assertFalse(item.ready)
            self.assertIn("the ffmpeg here has no ffprobe", item.detail)
            self.assertIn("ffmpeg", [i.key for i in setup_flow.todo(setup_flow.plan())])   # so setup repairs it
            (self.tools / "ffmpeg" / "bin" / "ffprobe").write_text("prog", encoding="utf-8")
            self.assertTrue(next(i for i in setup_flow.plan() if i.key == "ffmpeg").ready)


class Blender(Scratch):
    TOP = f"blender-{tools.BLENDER_VERSION}-linux-x64"

    def install(self, *archives):
        self.serve(*archives)
        with mock.patch.object(tools, "arm", lambda: False):
            return tools.install_blender()

    def test_it_goes_into_place_whole_with_the_kits_version_stamp(self):
        path = self.install(self.tar("blender.tar.xz", f"{self.TOP}/blender", f"{self.TOP}/4.5/scripts/startup.py"))
        self.assertEqual(self.rel(path), "blender/blender")
        self.assertEqual((self.tools / "blender" / "kit-version.txt").read_text(encoding="utf-8").strip(),
                         tools.BLENDER_VERSION)
        self.assertEqual(self.left(), ["blender"])

    def test_an_unpack_that_stops_part_way_isnt_found_as_an_install(self):
        good = self.tar("blender.tar.xz", f"{self.TOP}/blender", f"{self.TOP}/4.5/scripts/startup.py")
        real = tools.untar

        def cut_short(archive, dest):
            real(archive, dest)
            raise OSError(28, "No space left on device")

        with mock.patch.object(tools, "untar", cut_short), self.assertRaises(OSError):
            self.install(good)
        self.assertEqual(tools.blender_candidates(), [])           # it used to find tools/blender-4.5.5-linux-x64
        with self.assertRaises(RuntimeError):                      # nor an archive with no Blender in it
            self.install(self.tar("empty.tar.xz", f"{self.TOP}/readme.txt"))
        self.assertEqual(tools.blender_candidates(), [])
        self.assertEqual(self.rel(self.install(good)), "blender/blender")


    def test_the_kits_own_blender_comes_before_one_found_elsewhere(self):
        # A newer 4.5 unzipped in Downloads used to outrank the copy setup downloaded and checked.
        own = self.tools / "blender" / "blender"
        own.parent.mkdir(parents=True)
        own.write_text("prog", encoding="utf-8")
        (own.parent / "kit-version.txt").write_text("4.5.5\n", encoding="utf-8")
        newer = str(self.dir / "Downloads" / "blender-4.5.9-linux-x64" / "blender")
        other = str(self.dir / "Downloads" / "blender-5.0.1-linux-x64" / "blender")
        self.assertEqual(tools._prefer_45([other, newer, str(own)]), [str(own), newer, other])
        (own.parent / "kit-version.txt").write_text("4.4.3\n", encoding="utf-8")   # only a 4.5 comes first
        self.assertEqual(tools._prefer_45([other, str(own), newer]), [newer, str(own), other])


class Bun(Scratch):
    def setUp(self):
        super().setUp()
        for patch in (mock.patch.object(ui, "BUN_DIR", self.tools / "bun"), mock.patch.object(ui, "TOOLS", self.tools),
                      mock.patch.object(ui, "WINDOWS", False), mock.patch.object(ui.shutil, "which", lambda name: None)):
            patch.start()
            self.addCleanup(patch.stop)
        os.environ.pop("REELFORGE_BUN", None)
        self.archive = self.zip(ui.bun_asset(), "bun-linux-x64-baseline/bun")

    def test_a_copy_cut_short_leaves_no_bun_so_the_next_run_downloads_it_again(self):
        self.serve(self.archive, self.archive)

        def cut_short(src, out):
            out.write(b"pr")
            raise OSError(28, "No space left on device")

        with mock.patch.object(ui.shutil, "copyfileobj", cut_short), self.assertRaises(OSError):
            ui.install_bun()
        self.assertIsNone(ui.bun_path())                           # it used to be the two bytes, for good
        self.assertEqual(list((self.tools / "bun").iterdir()), [])
        bun = ui.install_bun()
        self.assertEqual((ui.bun_path(), bun.read_bytes()), (bun, b"prog"))
        self.assertTrue(os.access(bun, os.X_OK) or os.name == "nt")
        self.assertEqual([p.name for p in (self.tools / "bun").iterdir()], ["bun"])


if __name__ == "__main__":
    unittest.main()
