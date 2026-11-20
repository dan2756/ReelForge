"""The one-line install for Linux (scripts/install.sh), run for real against a "release" that is two files in a
scratch folder (curl reads them with file://, so nothing goes over the network): the download is checked, unpacked
beside the folder it's for and renamed into place, and an unpack or a move that fails, or the line being stopped,
installs nothing and leaves nothing behind."""

import hashlib
import io
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

KIT = Path(__file__).resolve().parent.parent
NAME = "ReelForge-9.9.9-Linux.tar.gz"


@unittest.skipUnless(sys.platform.startswith("linux") and all(shutil.which(t) for t in ("bash", "curl", "tar")),
                     "the Linux install line, with bash, curl and tar")
class InstallLine(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.release = self.dir / "release"
        self.release.mkdir()
        self.home = self.dir / "home"
        self.home.mkdir()
        (self.dir / "tmp").mkdir()
        self.target = self.home / "My ReelForge"                    # a space in it, as a real folder may have

    def publish(self, cut_at: float | None = None) -> None:
        """The release's download and its SHA256SUMS.txt. cut_at: keep only that share of the download, as a disk
        that filled up mid-unpack would (its checksum is the cut file's, so the check before the unpack passes)."""
        raw = io.BytesIO()
        with tarfile.open(fileobj=raw, mode="w:gz") as t:
            for name, data in (("ReelForge-9.9.9/setup.sh", b"#!/bin/sh\necho setup\n"),
                               ("ReelForge-9.9.9/studio/__init__.py", b'KIT_VERSION = "9.9.9"\n'),
                               ("ReelForge-9.9.9/assets/big.bin", os.urandom(400_000))):   # doesn't compress
                info = tarfile.TarInfo(name)
                info.size, info.mode = len(data), 0o755 if name.endswith(".sh") else 0o644
                t.addfile(info, io.BytesIO(data))
        data = raw.getvalue()
        if cut_at is not None:
            data = data[:int(len(data) * cut_at)]
        (self.release / NAME).write_bytes(data)
        (self.release / "SHA256SUMS.txt").write_text(f"{hashlib.sha256(data).hexdigest()}  {NAME}\n", encoding="utf-8")

    def install(self, **instead: str) -> subprocess.CompletedProcess:
        """Run the install line. instead: a program to stand in for one it runs (mv="..."), as a shell script that
        finds the real one in $REAL."""
        path = os.environ["PATH"]
        for name, script in instead.items():
            fake = self.dir / "instead" / name
            fake.parent.mkdir(exist_ok=True)
            fake.write_text(f"#!/bin/sh\nREAL='{shutil.which(name)}'\n{script}\n", encoding="utf-8")
            fake.chmod(0o755)
            path = f"{fake.parent}{os.pathsep}{path}"
        env = {"PATH": path, "HOME": str(self.home), "TMPDIR": str(self.dir / "tmp"),
               "REELFORGE_RELEASE": self.release.as_uri(), "REELFORGE_DIR": str(self.target),
               "REELFORGE_NO_SETUP": "1"}
        return subprocess.run(["bash", str(KIT / "scripts" / "install.sh")], env=env, capture_output=True, text=True,
                              timeout=120, stdin=subprocess.DEVNULL)

    def left(self) -> list[str]:
        """What the install line left: in the folder the target goes in, and in the temporary folder."""
        return sorted(p.name for folder in (self.home, self.dir / "tmp") for p in folder.iterdir())

    def test_it_installs_the_checked_download(self):
        self.publish()
        r = self.install()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn(f"ReelForge is in {self.target}", r.stdout)
        self.assertEqual((self.target / "studio" / "__init__.py").read_text(encoding="utf-8"),
                         'KIT_VERSION = "9.9.9"\n')
        self.assertTrue(os.access(self.target / "setup.sh", os.X_OK))
        self.assertEqual(self.left(), [self.target.name])            # its work folders are gone

    def test_an_unpack_that_fails_part_way_installs_nothing(self):
        self.publish(cut_at=0.6)       # setup.sh and studio/ unpack, then the archive ends in the middle of a file
        r = self.install()
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn(f"ReelForge wasn't installed: {NAME} couldn't be unpacked", r.stdout)
        self.assertNotIn("ReelForge is in", r.stdout)
        self.assertEqual(self.left(), [])                            # so running the line again isn't refused
        self.publish()
        self.assertEqual(self.install().returncode, 0)

    def test_its_put_in_place_with_a_rename_even_when_the_temporary_folder_is_another_disk(self):
        # /tmp is its own filesystem on many Linux systems (and always, for a ReelForge on another drive). mv out of
        # it is then a copy, and one that stopped (a full disk) left half a ReelForge, which the next run took for an
        # install: "There's already a ReelForge folder". This mv is that copy stopping, for a move out of $TMPDIR.
        self.publish()
        other_disk = ('case "$1" in "$TMPDIR"/*) mkdir -p "$2" && cp "$1/setup.sh" "$2/"\n'
                      'echo "mv: error writing: No space left on device" >&2; exit 1 ;; esac\nexec "$REAL" "$@"')
        r = self.install(mv=other_disk)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual((self.target / "assets" / "big.bin").stat().st_size, 400_000)
        self.assertEqual(self.left(), [self.target.name])            # the folder it unpacked in, beside it, is gone

    def test_a_move_that_fails_installs_nothing(self):
        self.publish()
        r = self.install(mv="exit 1")                                # a rename happens or it doesn't
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn(f"ReelForge wasn't installed: it couldn't be moved into {self.target}", r.stdout)
        self.assertEqual(self.left(), [])                            # so running the line again isn't refused
        self.assertEqual(self.install().returncode, 0)

    def test_stopping_it_while_it_unpacks_leaves_nothing_behind(self):
        self.publish()
        r = self.install(tar='"$REAL" "$@"; kill -TERM $PPID')       # unpacked, then the terminal is closed
        self.assertEqual(r.returncode, 130, r.stdout + r.stderr)
        self.assertNotIn("ReelForge is in", r.stdout)
        self.assertEqual(self.left(), [])
        self.assertEqual(self.install().returncode, 0)

    def test_a_download_that_doesnt_match_its_checksum_installs_nothing(self):
        self.publish()
        with (self.release / NAME).open("ab") as f:
            f.write(b"changed on the way")
        r = self.install()
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("doesn't match its checksum", r.stdout)
        self.assertEqual(self.left(), [])


if __name__ == "__main__":
    unittest.main()
