"""The updater (studio.update): versions, how a copy was installed, and copying a new version over a folder."""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from dashboard import db, demo
from dashboard.config import PLATFORM_KEYS
from dashboard.tests.window import Window, plain
from studio import update


class VersionTest(unittest.TestCase):
    def test_compare(self):
        self.assertEqual(update.version_tuple("v1.2"), (1, 2, 0))
        self.assertEqual(update.version_tuple("1.10.0-beta"), (1, 10, 0))
        self.assertTrue(update.is_newer("v1.0.1", "1.0.0"))
        self.assertTrue(update.is_newer("1.10.0", "1.9.9"))
        self.assertFalse(update.is_newer("v1.0.0", "1.0.0"))
        self.assertFalse(update.is_newer("0.9", "1.0.0"))

    def test_asset_names(self):
        self.assertEqual(update.asset_name("1.2.0", platform="win32"), "ReelForge-1.2.0-Windows.zip")
        self.assertEqual(update.asset_name("1.2.0", platform="darwin"), "ReelForge-1.2.0-macOS.zip")
        self.assertEqual(update.asset_name("1.2.0", platform="linux"), "ReelForge-1.2.0-Linux.tar.gz")

    def test_offline_check_is_calm(self):
        with mock.patch.object(update, "latest", side_effect=update.UpdateError("Couldn't reach GitHub (offline?)")):
            info = update.check()
        self.assertFalse(info["newer"])
        self.assertIn("offline", info["error"])

    def test_check_round_trips_to_a_release(self):
        rel = update.Release("9.0.0", "v9.0.0", "notes", assets={"ReelForge-9.0.0-Windows.zip": ("https://x/z", 10)})
        with mock.patch.object(update, "latest", return_value=rel):
            info = update.check()
        self.assertTrue(info["newer"])
        again = update.release_from(json.loads(json.dumps(info)))  # as the dashboard stores it
        self.assertEqual((again.version, again.assets), ("9.0.0", {"ReelForge-9.0.0-Windows.zip": ("https://x/z", 10)}))


class InstallTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.root = self.dir / "ReelForge"
        self.root.mkdir()

    def test_install_kind(self):
        self.assertEqual(update.install_kind(self.root), "zip")
        (self.root / "unins000.exe").write_bytes(b"")   # the old Windows installer's copies update the zip way too
        self.assertEqual(update.install_kind(self.root), "zip")
        (self.root / ".git").mkdir()
        self.assertEqual(update.install_kind(self.root), "git")

    def test_kept_paths(self):
        for rel in ("my-studio/studio.toml", "episodes/ep01/episode.json", "completed videos/x/x.mp4", "CLAUDE.md",
                    "dashboard/data/stats.db", "dashboard/config.toml", ".venv/Scripts/python.exe", "tools/blender/b",
                    "ui/node_modules/@opentui/core/package.json"):
            self.assertTrue(update.kept(rel), rel)
        for rel in ("dashboard/service.py", "ui/src/main.tsx", "ui/bun.lock", "studio/__init__.py", "README.md"):
            self.assertFalse(update.kept(rel), rel)

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

    def test_zip_update_copies_kit_files_and_leaves_yours(self):
        mine = {"my-studio/studio.toml": "my settings", "CLAUDE.md": "my instructions",
                "dashboard/config.toml": "my handles", "episodes/ep01/episode.json": "{}"}
        for rel, text in {**mine, "studio/__init__.py": 'KIT_VERSION = "1.0.0"\n', "README.md": "old",
                          "bin/reelforge.cmd": "same launcher"}.items():
            (self.root / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.root / rel).write_text(text, encoding="utf-8")
        launcher = self.root / "bin" / "reelforge.cmd"
        before = launcher.stat().st_mtime_ns
        archive = self.dir / "ReelForge-1.1.0-Windows.zip"
        with zipfile.ZipFile(archive, "w") as z:
            for rel, text in {"studio/__init__.py": 'KIT_VERSION = "1.1.0"\n', "README.md": "new",
                              "studio/new_module.py": "x = 1\n", "bin/reelforge.cmd": "same launcher",
                              "my-studio/studio.toml": "kit default", "CLAUDE.md": "bootstrap"}.items():
                z.writestr(f"ReelForge-1.1.0/{rel}", text)
        work = self.dir / "work"
        work.mkdir()
        update._copy_release(archive, self.root, work, lambda *a: None)
        self.assertEqual(update.read_version(self.root), "1.1.0")
        self.assertEqual((self.root / "README.md").read_text(encoding="utf-8"), "new")
        self.assertTrue((self.root / "studio" / "new_module.py").exists())
        for rel, text in mine.items():
            self.assertEqual((self.root / rel).read_text(encoding="utf-8"), text, rel)
        self.assertEqual(launcher.stat().st_mtime_ns, before)  # unchanged files aren't rewritten

    @unittest.skipIf(os.name == "nt", "file permissions are a macOS and Linux thing")
    def test_mac_zip_update_keeps_scripts_runnable(self):
        """ZipFile unpacks without permissions; the update gives the zip's executable files theirs back."""
        (self.root / "studio").mkdir()
        (self.root / "studio" / "__init__.py").write_text('KIT_VERSION = "1.0.0"\n', encoding="utf-8")
        (self.root / "setup.sh").write_text("old\n", encoding="utf-8")
        (self.root / "setup.sh").chmod(0o755)
        archive = self.dir / "ReelForge-1.1.0-macOS.zip"
        with zipfile.ZipFile(archive, "w") as z:
            z.writestr("ReelForge-1.1.0/studio/__init__.py", 'KIT_VERSION = "1.1.0"\n')
            z.writestr("ReelForge-1.1.0/README.md", "plain\n")
            for rel in ("setup.sh", "bin/reelforge"):
                info = zipfile.ZipInfo(f"ReelForge-1.1.0/{rel}")
                info.external_attr = 0o100755 << 16
                z.writestr(info, "#!/bin/sh\necho new\n")
        work = self.dir / "work"
        work.mkdir()
        update._copy_release(archive, self.root, work, lambda *a: None)
        for rel in ("setup.sh", "bin/reelforge"):
            self.assertTrue(os.access(self.root / rel, os.X_OK), rel)
        self.assertFalse(os.access(self.root / "README.md", os.X_OK))

    # --- an update that stops part way ---------------------------------------------------------

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

    def test_packages_that_dont_install_are_said_truthfully_after_the_other_steps(self):
        self.kit("1.1.0", self.root, **{"requirements.txt": "a\nb\n", ".venv/installed.txt": "a\n",
                                        "my-studio/studio.toml": ""})
        with self.assertRaises(update.UpdateError) as stopped:
            self.after(pip=1)
        self.assertTrue(stopped.exception.partial)
        self.assertIn("The new version's files are in place, but its Python packages didn't install",
                      str(stopped.exception))
        self.assertIn("Close ReelForge and open it again: setup starts and installs them.", str(stopped.exception))
        self.assertEqual([c[:2] for c in self.ran], [["-m", "pip"], ["-m", "studio.ui"], ["-m", "studio"]])
        # still out of step, which is what the next start goes by (bin/reelforge.py, tests/test_studio.py)
        self.assertEqual((self.root / ".venv" / "installed.txt").read_text(encoding="utf-8"), "a\n")

    def test_a_release_without_checksums_isnt_installed(self):
        archive = self.dir / "ReelForge-9.0.0-Linux.tar.gz"
        archive.write_bytes(b"not checked")
        rel = update.Release("9.0.0", "v9.0.0", assets={archive.name: ("https://x/z", 11)})
        with self.assertRaises(update.UpdateError) as stopped:
            update._verify(rel, archive.name, archive, lambda *a: None)
        self.assertIn("no SHA256SUMS.txt", str(stopped.exception))
        self.assertFalse(stopped.exception.partial)

    def test_something_else_is_refused(self):
        archive = self.dir / "other.zip"
        with zipfile.ZipFile(archive, "w") as z:
            z.writestr("Other/readme.txt", "hi")
        work = self.dir / "work"
        work.mkdir()
        with self.assertRaises(update.UpdateError):
            update._copy_release(archive, self.root, work, lambda *a: None)

    def test_marker_is_read_once(self):
        update._write_marker(self.root, "1.0.0", "1.1.0")
        self.assertEqual(update.take_marker(self.root)["to"], "1.1.0")
        self.assertIsNone(update.take_marker(self.root))

    def test_install_line_updates_an_old_installer_copy_in_place(self):
        """install.ps1 runs the new download's updater (--into) on a copy the old Windows installer made."""
        mine = {"my-studio/studio.toml": "my settings", "CLAUDE.md": "my instructions",
                "dashboard/data/stats.db": "my stats", "episodes/ep01/episode.json": "{}", "unins000.exe": "uninstaller",
                "unins000.dat": "its list"}
        for rel, text in {**mine, "studio/__init__.py": 'KIT_VERSION = "1.0.5"\n', "README.md": "old",
                          "requirements.txt": "rich\n"}.items():
            (self.root / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.root / rel).write_text(text, encoding="utf-8")
        new = self.dir / "ReelForge-1.1.0"     # the download the install line unpacked
        for rel, text in {"studio/__init__.py": 'KIT_VERSION = "1.1.0"\n', "README.md": "new", "requirements.txt": "rich\n",
                          "studio/new_module.py": "x = 1\n", "my-studio/studio.toml": "kit default",
                          "CLAUDE.md": "bootstrap", "START-HERE.txt": "start"}.items():
            (new / rel).parent.mkdir(parents=True, exist_ok=True)
            (new / rel).write_text(text, encoding="utf-8")
        with mock.patch.object(update, "ROOT", new), mock.patch.object(update, "_after") as after:
            said = update.move_in(self.root)
        self.assertEqual(said, "Updated ReelForge from 1.0.5 to 1.1.0.")
        self.assertEqual(update.read_version(self.root), "1.1.0")
        self.assertEqual((self.root / "README.md").read_text(encoding="utf-8"), "new")
        self.assertTrue((self.root / "studio" / "new_module.py").exists())
        for rel, text in mine.items():
            self.assertEqual((self.root / rel).read_text(encoding="utf-8"), text, rel)
        after.assert_called_once()
        marker = update.take_marker(self.root)      # the restarted window says "Updated to 1.1.0"
        self.assertEqual((marker["from"], marker["to"]), ("1.0.5", "1.1.0"))

    def test_install_line_leaves_a_folder_without_reelforge(self):
        (self.root / "notes.txt").write_text("mine", encoding="utf-8")
        with self.assertRaises(update.UpdateError):
            update.move_in(self.root)
        self.assertEqual([p.name for p in self.root.iterdir()], ["notes.txt"])


@unittest.skipUnless(shutil.which("git"), "needs git")
class GitPullTest(unittest.TestCase):
    """A git clone that onboarding has written its CLAUDE.md and AGENTS.md into still updates."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.kit = self.dir / "kit"
        self.kit.mkdir()
        self.git(self.kit, "init", "-q")
        self.commit({"CLAUDE.md": "placeholder\n", "AGENTS.md": "placeholder\n", "README.md": "1.0\n"})
        self.git(self.dir, "clone", "-q", str(self.kit), "copy")
        self.copy = self.dir / "copy"

    def git(self, where: Path, *args: str) -> None:
        env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
               "GIT_COMMITTER_EMAIL": "t@t"}
        subprocess.run(["git", "-C", str(where), *args], check=True, capture_output=True, env=env)

    def commit(self, files: dict[str, str]) -> None:
        for rel, text in files.items():
            (self.kit / rel).write_text(text, encoding="utf-8")
        self.git(self.kit, "add", "-A")
        self.git(self.kit, "commit", "-q", "-m", "kit")

    def test_onboarded_files_are_kept(self):
        (self.copy / "CLAUDE.md").write_text("my channel\n", encoding="utf-8")
        (self.copy / "AGENTS.md").write_text("my channel\n", encoding="utf-8")
        self.commit({"README.md": "1.1\n", "CLAUDE.md": "new placeholder\n"})
        update._git_pull(self.copy, lambda *a: None)
        self.assertEqual((self.copy / "README.md").read_text(encoding="utf-8"), "1.1\n")
        self.assertEqual((self.copy / "CLAUDE.md").read_text(encoding="utf-8"), "my channel\n")
        self.assertEqual((self.copy / "AGENTS.md").read_text(encoding="utf-8"), "my channel\n")

    def test_codex_settings_you_edited_dont_stop_it_and_stay(self):
        (self.kit / ".codex").mkdir()
        self.commit({".codex/config.toml": 'model_reasoning_effort = "high"\n'})
        self.git(self.copy, "pull", "-q")
        (self.copy / ".codex" / "config.toml").write_text('model_reasoning_effort = "xhigh"\n', encoding="utf-8")
        self.commit({"README.md": "1.1\n"})
        update._git_pull(self.copy, lambda *a: None)                # "You've changed files in this copy", before
        self.assertEqual((self.copy / "README.md").read_text(encoding="utf-8"), "1.1\n")
        self.assertEqual((self.copy / ".codex" / "config.toml").read_text(encoding="utf-8"),
                         'model_reasoning_effort = "xhigh"\n')

    def test_other_changes_stop_it(self):
        (self.copy / "README.md").write_text("edited\n", encoding="utf-8")
        self.commit({"README.md": "1.1\n"})
        with self.assertRaises(update.UpdateError):
            update._git_pull(self.copy, lambda *a: None)
        self.assertEqual((self.copy / "README.md").read_text(encoding="utf-8"), "edited\n")


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


class WhatsNewTest(unittest.TestCase):
    NOTES = Path(__file__).resolve().parents[2] / ".github" / "release-notes.md"

    def test_the_updates_tab_shows_only_whats_new(self):
        from dashboard.service import whats_new

        shown = whats_new(self.NOTES.read_text(encoding="utf-8"))
        self.assertTrue(shown.startswith("## What's new in "))
        for gone in ("## Download", "| Your computer", "Already have ReelForge?", "---"):
            self.assertNotIn(gone, shown)
        self.assertEqual(whats_new("- one\r\n\r\nAlready have ReelForge? Update.\r\n---\r\nguide"), "- one")
        self.assertEqual(whats_new(""), "")

    def test_whats_new_is_short_and_says_first_time_setup(self):
        shown = self.NOTES.read_text(encoding="utf-8").split("\n---")[0]
        for word in ("onboarding", "ultracode", "advanced settings"):
            self.assertNotIn(word, shown.lower())
        for line in shown.splitlines():
            # Each bullet on one line, a bold lead first: the Updates tab before 1.0.6 (which people read these notes
            # in until they've updated) shows a bullet's wrapped lines as loose lines at the left edge.
            self.assertRegex(line, r"^(|## What's new in .+|- \*\*[^*]+\*\* .+|  - \S.+|Already have ReelForge\? .+)$")
            self.assertLessEqual(len(line), 140, line)


if __name__ == "__main__":
    unittest.main()
