"""An update that stops part way (studio.update), and what the Updates tab says about it (dashboard.service): the
folder counts as the new version only once every file is in, a busy file is waited for, the Python packages are
installed whenever they aren't the ones requirements.txt asks for, a release without checksums isn't installed, and
the Codex settings you edited stay yours."""

from __future__ import annotations

import contextlib
import io
import os
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from dashboard import db, demo
from dashboard.config import PLATFORM_KEYS
from dashboard.tests.window import Window, plain
from studio import update


class PartWayTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.root = self.dir / "ReelForge"
        self.root.mkdir()

    def kit(self, version: str, where: Path, **files: str) -> Path:
        """A ReelForge folder (or a new version's files) with these files, and its version."""
        for rel, text in {"studio/__init__.py": f'KIT_VERSION = "{version}"\n', **files}.items():
            (where / rel).parent.mkdir(parents=True, exist_ok=True)
            (where / rel).write_text(text, encoding="utf-8")
        return where

    @contextlib.contextmanager
    def busy(self, *names: str, times: int = 10 ** 6):
        """Replacing these files fails as it does on Windows while another program has them open (times: how often,
        before it lets go). The waiting takes no time here: the clock it goes by moves on by itself."""
        real, left, clock = os.replace, {name: times for name in names}, [0.0]

        def replace(src, dest):
            name = Path(dest).name
            if left.get(name, 0) > 0:
                left[name] -= 1
                raise PermissionError(13, "The process cannot access the file because it is being used", str(dest))
            real(src, dest)

        def sleep(seconds):
            clock[0] += seconds

        with mock.patch.object(update.paths.os, "replace", replace), mock.patch("time.sleep", sleep), \
                mock.patch("time.monotonic", lambda: clock[0]):
            yield

    def test_the_version_changes_last_and_only_when_every_file_is_in(self):
        self.kit("1.0.0", self.root, **{"README.md": "old", "dashboard/service.py": "old", "requirements.txt": "a\n"})
        new = self.kit("1.1.0", self.dir / "new", **{"README.md": "new", "dashboard/service.py": "new",
                                                    "requirements.txt": "a\nb\n", "ui/src/main.tsx": "new"})
        with self.busy("service.py"), self.assertRaises(update.UpdateError) as stopped:
            update._copy_tree(new, self.root, lambda *a: None)
        said = str(stopped.exception)
        self.assertTrue(stopped.exception.partial)
        self.assertIn("1 of the new files couldn't be put in place (dashboard/service.py)", said)
        self.assertIn("this copy still counts as the version you had", said)
        self.assertIn("update again to finish", said)
        # part new, part old, and it says so: the Updates tab still offers 1.1.0 after a restart
        self.assertEqual(update.read_version(self.root), "1.0.0")
        self.assertTrue(update.is_newer("1.1.0", update.read_version(self.root)))
        self.assertEqual((self.root / "README.md").read_text(encoding="utf-8"), "new")
        self.assertEqual((self.root / "dashboard" / "service.py").read_text(encoding="utf-8"), "old")
        self.assertEqual(list(self.root.rglob("*.rf-new")), [])      # nothing left beside the file it stopped at
        update._copy_tree(new, self.root, lambda *a: None)           # update again: it finishes
        self.assertEqual(update.read_version(self.root), "1.1.0")
        self.assertEqual((self.root / "dashboard" / "service.py").read_text(encoding="utf-8"), "new")

    def test_a_file_thats_busy_for_a_moment_is_waited_for(self):
        self.kit("1.0.0", self.root, **{"README.md": "old"})
        new = self.kit("1.1.0", self.dir / "new", **{"README.md": "new"})
        with self.busy("README.md", times=3):                        # a virus scanner reading it, say
            update._copy_tree(new, self.root, lambda *a: None)
        self.assertEqual((update.read_version(self.root), (self.root / "README.md").read_text(encoding="utf-8")),
                         ("1.1.0", "new"))

    def test_when_no_file_could_be_replaced_it_says_nothing_changed(self):
        self.kit("1.0.0", self.root, **{"README.md": "old"})
        new = self.kit("1.1.0", self.dir / "new", **{"README.md": "new"})
        (self.root / "README.md.rf-new").write_text("left by an update that was killed", encoding="utf-8")
        with self.busy("README.md"), self.assertRaises(update.UpdateError) as stopped:
            update._copy_tree(new, self.root, lambda *a: None)
        self.assertFalse(stopped.exception.partial)
        self.assertIn("Nothing was changed.", str(stopped.exception))
        self.assertEqual(update.read_version(self.root), "1.0.0")
        self.assertEqual(list(self.root.rglob("*.rf-new")), [])
        self.assertFalse((self.root / "dashboard" / "data").exists())   # no note of a part-way update either

    def test_updating_again_while_the_file_is_still_busy_doesnt_say_nothing_changed(self):
        # The first message asks you to update again. When the same file is still busy then, that attempt replaces
        # nothing itself, and it said "Nothing was changed" (and the Updates tab "The version you have keeps
        # working") of a folder the first attempt had left part old and part new.
        self.kit("1.0.0", self.root, **{"README.md": "old", "dashboard/service.py": "old"})
        new = self.kit("1.1.0", self.dir / "new", **{"README.md": "new", "dashboard/service.py": "new"})
        part_way = self.root / "dashboard" / "data" / "update-part-way"
        for attempt in (1, 2):
            with self.subTest(attempt=attempt):
                with self.busy("service.py"), self.assertRaises(update.UpdateError) as stopped:
                    update._copy_tree(new, self.root, lambda *a: None)
                self.assertTrue(stopped.exception.partial)
                self.assertIn("The other new files are in", str(stopped.exception))
                self.assertNotIn("Nothing was changed", str(stopped.exception))
                self.assertTrue(part_way.exists())
        # so is an attempt that stops before it copies anything: the folder is still the first attempt's mix
        rel = update.Release("1.1.0", "v1.1.0", assets={update.asset_name("1.1.0"): ("https://x/z", 1)})

        def offline(*args):
            raise update.UpdateError("The download stopped (timed out). Check your connection and try again.")

        with mock.patch.object(update, "_download", offline), self.assertRaises(update.UpdateError) as stopped:
            update.apply(rel, root=self.root)
        self.assertTrue(stopped.exception.partial)
        update._copy_tree(new, self.root, lambda *a: None)           # the file is let go of: it finishes
        self.assertEqual(update.read_version(self.root), "1.1.0")
        self.assertFalse(part_way.exists())
        with mock.patch.object(update, "_download", offline), self.assertRaises(update.UpdateError) as stopped:
            update.apply(rel, root=self.root)                        # and a whole folder is called whole again
        self.assertFalse(stopped.exception.partial)

    def test_it_finishes_beside_an_older_versions_paths_and_tools(self):
        # 2.2.1's own update copied in no set order and waited for nothing, so one that stopped could leave this
        # update.py in place beside 2.2.1's paths.py and tools.py, which have no replace and no untar. Every attempt
        # after that ended in an AttributeError, from the window and from the terminal alike.
        self.kit("2.2.1", self.root, **{"README.md": "old"})
        new = self.kit("2.3.0", self.dir / "new", **{"README.md": "new"})
        raw = io.BytesIO()
        with tarfile.open(fileobj=raw, mode="w:gz") as t:
            for name, data in (("ReelForge-2.3.1/studio/__init__.py", b'KIT_VERSION = "2.3.1"\n'),
                               ("ReelForge-2.3.1/README.md", b"newer")):
                info = tarfile.TarInfo(name)
                info.size = len(data)
                t.addfile(info, io.BytesIO(data))
        archive = self.dir / "ReelForge-2.3.1-Linux.tar.gz"
        archive.write_bytes(raw.getvalue())
        with mock.patch.dict(update.paths.__dict__), mock.patch.dict(update.tools.__dict__):
            del update.paths.__dict__["replace"], update.tools.__dict__["untar"]
            update._copy_tree(new, self.root, lambda *a: None)
            self.assertEqual((update.read_version(self.root), (self.root / "README.md").read_text(encoding="utf-8")),
                             ("2.3.0", "new"))
            if hasattr(tarfile, "data_filter"):                      # 2.2.1 unpacked a .tar on such a Python only
                update._copy_release(archive, self.root, self.dir, lambda *a: None)
                self.assertEqual(update.read_version(self.root), "2.3.1")

    def after(self, pip: int = 0) -> tuple[list[list[str]], list[str]]:
        """Run the steps after the copy with nothing really run: (the commands, what was logged)."""
        ran, logged = [], []

        def run(cmd, **kwargs):
            ran.append([str(c) for c in cmd[1:]])
            return subprocess.CompletedProcess(cmd, pip if "pip" in cmd else 0, stdout="", stderr="")

        with mock.patch.object(update.subprocess, "run", run):
            try:
                update._after(self.root, update._hash(self.root / "requirements.txt"), lambda *a: None, logged.append)
            finally:
                self.ran = ran
        return ran, logged

    def test_packages_are_installed_whenever_they_arent_the_ones_requirements_asks_for(self):
        # The update that stopped above had copied the new requirements.txt. Run again, it found "no change" since
        # its own start and never installed the packages. What .venv was installed from is what counts.
        self.kit("1.1.0", self.root, **{"requirements.txt": "a\nb\n", ".venv/installed.txt": "a\n",
                                        "my-studio/studio.toml": ""})
        ran, logged = self.after()
        self.assertEqual([c[:3] for c in ran], [["-m", "pip", "install"], ["-m", "studio.ui", "install"],
                                                ["-m", "studio", "channel"]])
        self.assertEqual(ran[2], ["-m", "studio", "channel", "--regenerate", "--preserve-settings"])
        self.assertEqual((self.root / ".venv" / "installed.txt").read_text(encoding="utf-8"), "a\nb\n")
        self.assertEqual(logged, [])
        ran, _ = self.after()                                        # in step now: nothing to install
        self.assertEqual([c[:2] for c in ran], [["-m", "studio.ui"], ["-m", "studio"]])
        (self.root / ".venv" / "installed.txt").unlink()            # no record of what was installed: as before,
        ran, _ = self.after()                                        # only a file this update changed counts
        self.assertEqual([c[:2] for c in ran], [["-m", "studio.ui"], ["-m", "studio"]])

    def test_packages_that_dont_install_are_said_truthfully_once_the_windows_are_in(self):
        self.kit("1.1.0", self.root, **{"requirements.txt": "a\nb\n", ".venv/installed.txt": "a\n",
                                        "my-studio/studio.toml": ""})
        with self.assertRaises(update.UpdateError) as stopped:
            self.after(pip=1)
        self.assertTrue(stopped.exception.partial)
        self.assertIn("The new version's files are in place, but its Python packages didn't install",
                      str(stopped.exception))
        self.assertIn("Close ReelForge and open it again: setup starts and installs them.", str(stopped.exception))
        self.assertEqual([c[:2] for c in self.ran], [["-m", "pip"], ["-m", "studio.ui"]])   # the window's still done
        # still out of step, which is what the next start goes by (bin/reelforge.py, tests/test_studio.py)
        self.assertEqual((self.root / ".venv" / "installed.txt").read_text(encoding="utf-8"), "a\n")
        # a copy with no .venv of its own (a git copy run with another Python) has no setup to do it: it says how
        (self.root / ".venv" / "installed.txt").unlink()
        with mock.patch.object(update.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, "", "")), \
                self.assertRaises(update.UpdateError) as stopped:
            update._after(self.root, "another requirements.txt", lambda *a: None, lambda text: None)
        self.assertIn("-m pip install -r requirements.txt", str(stopped.exception))
        self.assertNotIn("setup starts", str(stopped.exception))

    def test_a_release_without_checksums_isnt_installed(self):
        archive = self.dir / "ReelForge-9.0.0-Linux.tar.gz"
        archive.write_bytes(b"not checked")
        rel = update.Release("9.0.0", "v9.0.0", assets={archive.name: ("https://x/z", 11)})
        with self.assertRaises(update.UpdateError) as stopped:
            update._verify(rel, archive.name, archive, lambda *a: None)
        self.assertIn("no SHA256SUMS.txt", str(stopped.exception))
        self.assertFalse(stopped.exception.partial)

    def test_the_codex_settings_you_edited_stay_and_come_where_there_are_none(self):
        # .codex/config.toml ships with the kit and says "raise it further if your model supports that"
        mine = 'model_reasoning_effort = "xhigh"\n'
        self.kit("1.0.0", self.root, **{".codex/config.toml": mine})
        new = self.kit("1.1.0", self.dir / "new", **{".codex/config.toml": 'model_reasoning_effort = "high"\n'})
        update._copy_tree(new, self.root, lambda *a: None)
        self.assertEqual((self.root / ".codex" / "config.toml").read_text(encoding="utf-8"), mine)
        older = self.kit("1.0.0", self.dir / "older")               # a copy from before the kit shipped one
        update._copy_tree(new, older, lambda *a: None)
        self.assertEqual((older / ".codex" / "config.toml").read_text(encoding="utf-8"),
                         'model_reasoning_effort = "high"\n')


class UpdatesTabTest(unittest.TestCase):
    """What the Updates tab tells you about an update (dashboard.service), in a window on demo data."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        self.addCleanup(setattr, db, "_path", db._path)
        root = Path(tmp.name)
        cfg = {"refresh_minutes": 0, "videos_dir": str(root / "none"), "episodes_dir": str(root / "none"),
               "completed_dir": str(root / "none"), **{key: {} for key in PLATFORM_KEYS}}
        db.init(root / "demo.db")
        self.w = Window(cfg, demo.platforms(), tab="updates")
        self.addCleanup(self.w.close)

    def test_it_says_what_an_update_leaves_alone_and_what_it_writes_again(self):
        # true since an update stopped writing studio.toml and the accounts again from the first-time answers
        # (tests/test_upgrade_preservation.py): the notes for your AI are the one thing of yours it rewrites
        how = plain(self.w.view("update")["how"])
        self.assertIn("Your channel, videos, stats and settings are never touched: only your AI's notes (CLAUDE.md, "
                      "AGENTS.md) are written again, for the new version.", how)

    def failed(self, error: Exception) -> str:
        """Update and restart, with the update itself stopping this way: what the tab then says."""
        self.w.svc.update_info = {"latest": "9.9.9", "newer": True}
        self.w.svc.updating = True
        with mock.patch("dashboard.service.kit_update.apply", side_effect=error):
            self.w.svc._update_apply()
            self.w.pump(0.05)
        step = self.w.view("update_step")
        self.assertTrue(step["failed"])
        self.assertFalse(self.w.svc.updating)                        # the button works again
        return plain(step["step"])

    def test_a_failed_update_only_says_your_version_keeps_working_when_nothing_changed(self):
        said = self.failed(update.UpdateError("The download stopped (timed out). Check your connection and try again."))
        self.assertEqual(said, "The update didn't finish: The download stopped (timed out). Check your connection and "
                               "try again.\nThe version you have keeps working.")
        part = update.UpdateError("1 of the new files couldn't be put in place (README.md). Update again to finish.",
                                  partial=True)
        said = self.failed(part)
        self.assertEqual(said, f"The update didn't finish: {part}")
        self.assertNotIn("keeps working", said)
        said = self.failed(RuntimeError("something unexpected"))     # anywhere, maybe mid-copy: no promise either
        self.assertEqual(said, "The update didn't finish: RuntimeError: something unexpected. Update again to finish "
                               "it.")


if __name__ == "__main__":
    unittest.main()
