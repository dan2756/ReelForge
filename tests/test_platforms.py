"""macOS and Linux: the downloads setup picks, ReelForge's icon, the hourly job, the Trash, the browser for stats and
the terminal's colours. Most of it is checked on any system by pretending to be the other one; the parts that need
the real thing run only there (the CI workflow runs this file on Windows, macOS and Linux).

  .venv/bin/python -m unittest tests.test_platforms
  REELFORGE_BROWSER_TEST=1 ...    also start the browser ReelForge reads stats with (needs one installed)
"""

import base64
import os
import plistlib
import shlex
import stat
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path, PurePosixPath, PureWindowsPath
from unittest import mock

from studio import browsers, logo, shortcut, stats_schedule, tools
from studio import paths as P

POSIX = os.name != "nt"

BTBN_SUMS = """\
aaa  ffmpeg-master-latest-linux64-gpl.tar.xz
bbb  ffmpeg-n8.1-latest-linux64-gpl-8.1.tar.xz
ccc  ffmpeg-n8.1-latest-linux64-gpl-shared-8.1.tar.xz
ddd  ffmpeg-n9.0-latest-linux64-gpl-9.0.tar.xz
eee  ffmpeg-n9.0-latest-linux64-lgpl-9.0.tar.xz
fff  ffmpeg-n9.0-latest-linuxarm64-gpl-9.0.tar.xz
ggg  ffmpeg-n10.0-latest-win64-gpl-10.0.zip
"""


class Downloads(unittest.TestCase):
    def test_linux_ffmpeg_is_the_newest_static_release_build(self):
        with mock.patch.object(tools, "arm", return_value=False):
            self.assertEqual(tools.linux_ffmpeg_build(BTBN_SUMS), ("ffmpeg-n9.0-latest-linux64-gpl-9.0.tar.xz", "ddd"))
        with mock.patch.object(tools, "arm", return_value=True):
            self.assertEqual(tools.linux_ffmpeg_build(BTBN_SUMS),
                             ("ffmpeg-n9.0-latest-linuxarm64-gpl-9.0.tar.xz", "fff"))
        with self.assertRaises(RuntimeError):
            tools.linux_ffmpeg_build("aaa  ffmpeg-master-latest-linux64-gpl.tar.xz\n")

    def test_each_system_gets_its_own_downloads(self):
        cases = {  # (windows, mac, arm): (Blender file, ffmpeg source, Blender downloadable)
            (True, False, False): ("windows-x64.zip", "gyan.dev", True),
            (False, True, True): ("macos-arm64.dmg", "ffmpeg.martin-riedl.de", True),
            (False, True, False): ("macos-x64.dmg", "ffmpeg.martin-riedl.de", True),
            (False, False, False): ("linux-x64.tar.xz", "BtbN's builds on GitHub", True),
            (False, False, True): ("linux-x64.tar.xz", "BtbN's builds on GitHub", False),
        }
        for (win, mac, arm), (asset, source, downloadable) in cases.items():
            with mock.patch.object(tools, "WINDOWS", win), mock.patch.object(tools, "MAC", mac), \
                    mock.patch.object(tools, "arm", return_value=arm):
                self.assertTrue(tools.blender_asset().endswith(asset), (win, mac, arm))
                self.assertEqual(tools.ffmpeg_source()[0], source)
                self.assertEqual(tools.blender_downloadable(), downloadable)

    def test_setup_offers_ffmpeg_everywhere(self):
        from studio import setup_flow

        for win, mac in ((True, False), (False, True), (False, False)):
            with mock.patch.object(P, "WINDOWS", win), mock.patch.object(P, "MAC", mac), \
                    mock.patch.object(tools, "WINDOWS", win), mock.patch.object(tools, "MAC", mac), \
                    mock.patch.object(tools, "arm", return_value=False), \
                    mock.patch.object(tools, "find_blender", return_value=None), \
                    mock.patch.object(tools, "find_ffmpeg", return_value=None), \
                    mock.patch.object(setup_flow.command, "status", return_value="off"), \
                    mock.patch.object(setup_flow.shortcut, "ready", return_value=False):
                items = {i.key: i for i in setup_flow.plan()}
            self.assertTrue(items["ffmpeg"].can_install, (win, mac))
            self.assertTrue(items["blender"].can_install, (win, mac))
            self.assertIn("shortcut", items)

    def test_arm_linux_is_told_where_blender_comes_from(self):
        from studio import setup_flow

        with mock.patch.object(P, "WINDOWS", False), mock.patch.object(P, "MAC", False), \
                mock.patch.object(tools, "WINDOWS", False), mock.patch.object(tools, "MAC", False), \
                mock.patch.object(tools, "arm", return_value=True), \
                mock.patch.object(tools, "find_blender", return_value=None):
            item = next(i for i in setup_flow.plan() if i.key == "blender")
        self.assertFalse(item.can_install)
        self.assertIn("package manager", item.detail)


class Icon(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name) / "home"
        self.root = Path(tmp.name) / "Dan's ReelForge $1 %"
        (self.root / "bin").mkdir(parents=True)

    def test_desktop_entry_quotes_any_path(self):
        target = PurePosixPath('/home/a/Dan\'s "Reel" $HOME `x` 100%/bin/reelforge')
        line = shortcut.desktop_exec(target)
        # the string rule doubles each backslash the quoting rule added, and % is doubled
        self.assertEqual(line, '"/home/a/Dan\'s \\\\"Reel\\\\" \\\\$HOME \\\\`x\\\\` 100%%/bin/reelforge"')

    def test_mac_app_opens_this_folder_in_terminal(self):
        with mock.patch.object(P, "MAC", True), mock.patch.object(P, "WINDOWS", False), \
                mock.patch.object(P, "ROOT", self.root), mock.patch.object(Path, "home", return_value=self.home):
            self.assertFalse(shortcut.ready())
            made = Path(shortcut.make())
            self.assertEqual(made, self.home / "Applications" / "ReelForge.app")
            self.assertTrue(shortcut.ready())
            info = plistlib.loads((made / "Contents" / "Info.plist").read_bytes())
            self.assertEqual((info["CFBundleExecutable"], info["CFBundleIconFile"]), ("ReelForge", "reelforge"))
            self.assertTrue((made / "Contents" / "Resources" / "reelforge.icns").stat().st_size > 1000)
            run = (made / "Contents" / "Resources" / "ReelForge.command").read_text(encoding="utf-8")
            self.assertEqual(shlex.split(run.splitlines()[-1]), ["exec", str(self.root / "bin" / "reelforge")])
            exe = (made / "Contents" / "MacOS" / "ReelForge").read_text(encoding="utf-8")
            self.assertIn("open -a Terminal", exe)
            if POSIX:
                self.assertTrue(os.access(made / "Contents" / "MacOS" / "ReelForge", os.X_OK))
            shortcut.make()                     # making it again replaces it
            self.assertTrue(shortcut.remove())
            self.assertFalse(made.exists())

    def test_a_windows_shortcut_is_this_folders_whatever_letters_its_path_has(self):
        # What PowerShell is asked and what's made of its answer (PowerShell itself only runs on Windows). The
        # shortcut's target used to come back as text in the console's code page: "José" arrived as "Jos" and a
        # stray byte, never equal to this folder, so its own icon counted as another copy's.
        root = PureWindowsPath("C:\\Users\\José 田中\\ReelForge")
        link = self.home / "Desktop" / "ReelForge.lnk"
        link.parent.mkdir(parents=True)
        link.write_bytes(b"a shortcut")
        asked, answer = [], [""]

        def powershell(cmd, **kwargs):
            asked.append((cmd[-1], kwargs["env"]["RF_LINK"]))
            return subprocess.CompletedProcess(cmd, 0, stdout=answer[0], stderr="")

        def as_powershell_prints(target: str) -> str:
            return base64.b64encode(target.encode("utf-8")).decode("ascii") + "\r\n"

        with mock.patch.object(P, "WINDOWS", True), mock.patch.object(P, "MAC", False), \
                mock.patch.object(P, "ROOT", root), mock.patch.dict(os.environ, {"USERPROFILE": str(self.home)}), \
                mock.patch.object(shortcut.subprocess, "run", powershell):
            answer[0] = as_powershell_prints(str(root / "ReelForge.bat"))
            self.assertTrue(shortcut.ours())
            script, about = asked[0]
            self.assertIn("[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes(", script)   # plain ASCII back
            self.assertIn("CreateShortcut($env:RF_LINK).TargetPath", script)
            self.assertEqual(about, str(link))
            answer[0] = as_powershell_prints("C:\\Users\\José 田中\\Another ReelForge\\ReelForge.bat")
            self.assertFalse(shortcut.ours())                       # another copy's icon
            for nothing_to_go_by in ("", "\r\n", "C:\\Users\\Jos\ufffd\\ReelForge\\ReelForge.bat\r\n", "!!!"):
                answer[0] = nothing_to_go_by
                self.assertFalse(shortcut.ours(), nothing_to_go_by)

    def test_linux_menu_entry(self):
        with mock.patch.object(P, "MAC", False), mock.patch.object(P, "WINDOWS", False), \
                mock.patch.object(P, "ROOT", self.root), mock.patch.dict(os.environ, {"XDG_DATA_HOME": str(self.home)}), \
                mock.patch.object(shortcut.shutil, "which", return_value=None):
            made = Path(shortcut.make())
            self.assertEqual(made, self.home / "applications" / "reelforge.desktop")
            text = made.read_text(encoding="utf-8")
            self.assertIn("Terminal=true", text)
            self.assertIn(f"Exec={shortcut.desktop_exec(self.root / 'bin' / 'reelforge')}", text)
            self.assertTrue(shortcut.ready())
            self.assertTrue(shortcut.remove())


class HourlyJob(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name) / "Dan's 100% Re$el"
        (self.root / "dashboard").mkdir(parents=True)
        self.py, self.script = self.root / ".venv" / "bin" / "python", self.root / "dashboard" / "refresh.pyw"
        for patch in (mock.patch.object(P, "ROOT", self.root), mock.patch.object(P, "DASHBOARD", self.root / "dashboard"),
                      mock.patch.object(P, "WINDOWS", False)):
            patch.start()
            self.addCleanup(patch.stop)

    def test_launchd_plist(self):
        data = plistlib.loads(stats_schedule.mac_plist(self.py, self.script, 60))
        self.assertEqual(data["ProgramArguments"], [str(self.py), str(self.script)])
        self.assertEqual(data["StartInterval"], 3600)
        with mock.patch.object(P, "MAC", True), mock.patch.object(stats_schedule, "_plist",
                                                                   return_value=self.root / "job.plist"):
            (self.root / "job.plist").write_bytes(stats_schedule.mac_plist(self.py, self.script))
            self.assertEqual(stats_schedule._job(), self.script)

    def test_systemd_timer(self):
        units = stats_schedule.systemd_units(self.py, self.script, 60)
        service, timer = units["reelforge-stats.service"], units["reelforge-stats.timer"]
        self.assertIn("OnUnitActiveSec=60min", timer)
        self.assertIn("WantedBy=timers.target", timer)
        self.assertIn("100%%", service)
        self.assertIn("Re$$el", service)
        folder = self.root / "units"
        folder.mkdir()
        (folder / "reelforge-stats.service").write_text(service, encoding="utf-8")
        with mock.patch.object(P, "MAC", False), mock.patch.object(stats_schedule, "_units", return_value=folder):
            self.assertEqual(stats_schedule._job(), self.script)

    def test_crontab_line(self):
        line = stats_schedule.cron_line(self.py, self.script)
        self.assertTrue(line.startswith("0 * * * * cd "))
        self.assertIn("100\\%", line)                    # cron turns a bare % into a new line
        crontab = "MAILTO=me\n" + line + "\n"
        with mock.patch.object(P, "MAC", False), \
                mock.patch.object(stats_schedule, "_units", return_value=self.root / "none"), \
                mock.patch.object(stats_schedule.shutil, "which", return_value="/usr/bin/crontab"), \
                mock.patch.object(stats_schedule, "_systemd", return_value=False), \
                mock.patch.object(stats_schedule, "_crontab", return_value=crontab):
            self.assertEqual(stats_schedule._job(), self.script)
        with mock.patch.object(P, "MAC", False), \
                mock.patch.object(stats_schedule, "_units", return_value=self.root / "none"), \
                mock.patch.object(stats_schedule.shutil, "which", return_value=None), \
                mock.patch.object(stats_schedule, "_systemd", return_value=False), \
                mock.patch.dict(os.environ, {"STUDIO_DRY_SYSTEM": ""}), \
                mock.patch.object(P, "TEST_COPY", self.root / "no-marker"):
            self.assertEqual(stats_schedule.status(), "unsupported")

    def test_older_crontab_lines_still_read(self):
        old = f'0 * * * * cd "{self.root / "dashboard"}" && "{self.py}" "{self.script}" # reelforge-stats'
        with mock.patch.object(P, "MAC", False), \
                mock.patch.object(stats_schedule, "_units", return_value=self.root / "none"), \
                mock.patch.object(stats_schedule.shutil, "which", return_value="/usr/bin/crontab"), \
                mock.patch.object(stats_schedule, "_systemd", return_value=False), \
                mock.patch.object(stats_schedule, "_crontab", return_value=old + "\n"):
            self.assertEqual(stats_schedule._job(), self.script)


class Trash(unittest.TestCase):
    def setUp(self):
        from dashboard import desktop

        self.desktop = desktop
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)

    def test_freedesktop_trash_remembers_where_files_came_from(self):
        trash = self.dir / "Trash"
        for _ in range(2):
            video = self.dir / "my video 100%.mp4"
            video.write_bytes(b"x")
            self.assertTrue(self.desktop.freedesktop_trash(video, trash))
            self.assertFalse(video.exists())
        self.assertEqual(sorted(p.name for p in (trash / "files").iterdir()),
                         ["my video 100% 2.mp4", "my video 100%.mp4"])
        note = (trash / "info" / "my video 100% 2.mp4.trashinfo").read_text(encoding="utf-8")
        self.assertIn("Path=", note)
        self.assertIn("my%20video%20100%25.mp4", note)
        self.assertIn("DeletionDate=", note)

    def test_words_for_this_system(self):
        self.assertIn(self.desktop.BIN, ("Recycle Bin", "Trash"))
        self.assertTrue(self.desktop.FILES_WINDOW.startswith("the "))

    @unittest.skipUnless(sys.platform == "darwin", "macOS")
    def test_mac_trash(self):
        video = self.dir / "gone.mp4"
        video.write_bytes(b"x")
        self.assertTrue(self.desktop.recycle(video))
        self.assertFalse(video.exists())


class Browser(unittest.TestCase):
    def test_remembered_browser_wins_while_installed(self):
        chrome, edge = browsers.Browser("Chrome", Path("/c")), browsers.Browser("Edge", Path("/e"), True)
        with mock.patch.object(browsers, "installed", return_value=[chrome, edge]):
            self.assertEqual(browsers.find(), chrome)
            self.assertEqual(browsers.find(browsers.key(edge)), edge)
            self.assertEqual(browsers.find("/gone"), chrome)
        with mock.patch.object(browsers, "installed", return_value=[]):
            self.assertIsNone(browsers.find(browsers.key(edge)))

    def test_user_agent_matches_the_system(self):
        b = browsers.Browser("Chrome", None)
        with mock.patch.object(browsers, "major_version", return_value=150):
            for win, mac, system in ((True, False, "Windows NT 10.0"), (False, True, "Macintosh"),
                                     (False, False, "X11; Linux x86_64")):
                with mock.patch.object(browsers, "WINDOWS", win), mock.patch.object(browsers, "MAC", mac):
                    agent = browsers.user_agent(b)
                self.assertIn(system, agent)
                self.assertIn("Chrome/150.0.0.0", agent)
                self.assertNotIn("Headless", agent)
            self.assertTrue(browsers.user_agent(browsers.Browser("Edge", Path("/e"), True)).endswith("Edg/150.0.0.0"))

    def test_playwright_chromium_is_found_in_its_cache(self):
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.dict(os.environ, {"PLAYWRIGHT_BROWSERS_PATH": tmp}), \
                mock.patch.object(browsers, "_playwright_chromium_info", return_value={"revision": "9"}):
            self.assertFalse(browsers.playwright_chromium())
            exe = Path(tmp, "chromium-9", "chrome-mac-arm64", "Google Chrome for Testing.app", "Contents", "MacOS",
                       "Google Chrome for Testing")
            exe.parent.mkdir(parents=True)
            exe.write_bytes(b"")
            self.assertTrue(browsers.playwright_chromium())

    def test_version_from_the_folder_beside_windows_browsers(self):
        with tempfile.TemporaryDirectory() as tmp:
            for v in ("141.0.3537.71", "99.0.1150.2", "SetupMetrics"):
                Path(tmp, v).mkdir()
            exe = Path(tmp, "msedge.exe")
            with mock.patch.object(browsers, "WINDOWS", True):
                self.assertEqual(browsers.major_version(browsers.Browser("Edge", exe, True)), 141)

    def test_the_version_is_looked_up_once_per_browser(self):
        """On Linux it means running the browser with --version, which every background read used to do again."""
        asked = []

        def run(cmd, **how):
            asked.append(cmd)
            return mock.Mock(stdout="Google Chrome 153.0.7500.12\n")

        chrome = browsers.Browser("Chrome", Path("/opt/somewhere/chrome"))
        with mock.patch.object(browsers, "WINDOWS", False), mock.patch.object(browsers, "MAC", False), \
                mock.patch.object(browsers.subprocess, "run", run):
            self.assertEqual([browsers.major_version(chrome) for _ in range(3)], [153, 153, 153])
            self.assertIn("Chrome/153.0.0.0", browsers.user_agent(chrome))
        self.assertEqual(asked, [["/opt/somewhere/chrome", "--version"]])

    def test_the_version_follows_the_browser_when_it_is_updated(self):
        """The version was kept for as long as ReelForge ran, so a window left open across a browser update went on
        presenting the old one, and a look-up that failed once stayed "unknown". It is asked again when the
        browser's program file has changed, and until it has answered."""
        said = ["", "Google Chrome 153.0.7500.12\n", "Google Chrome 154.0.7600.3\n"]
        asked = []

        def run(cmd, **how):
            asked.append(cmd)
            return mock.Mock(stdout=said[len(asked) - 1])

        with tempfile.TemporaryDirectory() as tmp:
            exe = Path(tmp, "chrome")
            exe.write_bytes(b"153")
            chrome = browsers.Browser("Chrome", exe)
            with mock.patch.object(browsers, "WINDOWS", False), mock.patch.object(browsers, "MAC", False), \
                    mock.patch.object(browsers.subprocess, "run", run):
                self.assertEqual(browsers.major_version(chrome), 0)  # it didn't say: not kept
                self.assertEqual([browsers.major_version(chrome) for _ in range(3)], [153, 153, 153])
                exe.write_bytes(b"154")  # the update replaced the program
                os.utime(exe, ns=(exe.stat().st_atime_ns, exe.stat().st_mtime_ns + 10 ** 9))
                self.assertEqual([browsers.major_version(chrome) for _ in range(3)], [154, 154, 154])
                self.assertIn("Chrome/154.0.0.0", browsers.user_agent(chrome))
            self.assertEqual(len(asked), 3)
            # Windows reads the folder beside the program each time, so there is nothing to go stale.
            for v in ("141.0.3537.71", "142.0.3595.5"):
                Path(tmp, v).mkdir()
                with mock.patch.object(browsers, "WINDOWS", True):
                    self.assertEqual(browsers.major_version(browsers.Browser("Edge", Path(tmp, "msedge.exe"), True)),
                                     int(v[:3]))

    @unittest.skipUnless(os.environ.get("REELFORGE_BROWSER_TEST") == "1", "set REELFORGE_BROWSER_TEST=1")
    def test_the_browser_starts_headless(self):
        """The browser ReelForge would read stats with starts with its own profile and shows its user agent."""
        b = browsers.find()
        self.assertIsNotNone(b, "no Chrome, Edge or Playwright Chromium here")
        from playwright.sync_api import sync_playwright

        with tempfile.TemporaryDirectory() as profile, sync_playwright() as pw:
            ctx = pw.chromium.launch_persistent_context(
                profile, executable_path=str(b.exe) if b.exe else None, channel=None if b.exe else "chromium",
                headless=True, user_agent=browsers.user_agent(b))
            try:
                page = ctx.new_page()
                page.goto("data:text/html,<title>ok</title>")
                self.assertEqual(page.title(), "ok")
                self.assertEqual(page.evaluate("navigator.userAgent"), browsers.user_agent(b))
            finally:
                ctx.close()
        print(f"\n  stats browser: {b.describe()}, version {browsers.major_version(b)}", file=sys.stderr)


class Colours(unittest.TestCase):
    def test_256_colour_fallback(self):
        self.assertEqual([logo.xterm256(c) for c in ("#e8213a", "#ffffff", "#000000", "#8a94a3")], [161, 231, 16, 103])
        with mock.patch.dict(os.environ, {"REELFORGE_TRUECOLOR": "0"}):
            self.assertEqual(logo.sgr("#e8213a"), "\x1b[38;5;161m")
            self.assertTrue(all("38;2;" not in line for line in logo.ansi(logo.big())))
        with mock.patch.dict(os.environ, {"REELFORGE_TRUECOLOR": "1"}):
            self.assertEqual(logo.sgr("#e8213a", background=True), "\x1b[48;2;232;33;58m")

    def test_apple_terminal_gets_256_colours(self):
        env = {"REELFORGE_TRUECOLOR": "", "TERM_PROGRAM": "Apple_Terminal", "COLORTERM": ""}
        with mock.patch.dict(os.environ, env), mock.patch.object(logo.sys, "platform", "darwin"):
            self.assertFalse(logo.truecolor())
        with mock.patch.dict(os.environ, {**env, "COLORTERM": "truecolor"}), \
                mock.patch.object(logo.sys, "platform", "darwin"):
            self.assertTrue(logo.truecolor())


class Launchers(unittest.TestCase):
    @unittest.skipUnless(POSIX, "file permissions are a macOS and Linux thing")
    def test_launchers_are_made_runnable_again(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "bin").mkdir()
            for name in ("setup.sh", "bin/reelforge", "Open ReelForge.command", "README.md"):
                (root / name).write_text("#!/bin/sh\n", encoding="utf-8")
                (root / name).chmod(0o644)
            with mock.patch.object(P, "ROOT", root):
                P.keep_runnable()
            for name in ("setup.sh", "bin/reelforge", "Open ReelForge.command"):
                self.assertTrue(os.access(root / name, os.X_OK), name)
            self.assertFalse(os.access(root / "README.md", os.X_OK))

    def test_the_old_launchers_an_update_left_behind_go_while_they_are_the_kits_own(self):
        # onboard.bat and onboard.sh as ReelForge 1.0.0 to 1.0.5 shipped them
        bat = ('@echo off\nrem ReelForge onboarding: the interview that sets up your channel and writes CLAUDE.md / '
               'AGENTS.md.\nrem Run it again any time to change your answers. In any terminal, "reelforge onboard" '
               'does the same.\ncall "%~dp0bin\\reelforge.cmd" onboard %*\nset code=%errorlevel%\n'
               'if not "%code%"=="0" pause\nexit /b %code%\n')
        sh = ('#!/usr/bin/env bash\n# ReelForge onboarding: the interview that sets up your channel and writes '
              'CLAUDE.md / AGENTS.md.\n# Run it again any time to change your answers. "reelforge onboard" does the '
              'same.\nexec "$(dirname "$0")/bin/reelforge" onboard "$@"\n')
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "onboard.bat").write_bytes(bat.replace("\n", "\r\n").encode("utf-8"))
            (root / "onboard.sh").write_bytes((sh + "# my own line\n").encode("utf-8"))  # one someone changed: stays
            (root / "channel.bat").write_bytes(b"@echo off\r\n")
            with mock.patch.object(P, "ROOT", root):
                P.keep_runnable()
            self.assertEqual(sorted(p.name for p in root.iterdir()), ["channel.bat", "onboard.sh"])
            (root / "onboard.sh").write_bytes(sh.encode("utf-8"))
            with mock.patch.object(P, "ROOT", root):
                P.keep_runnable()
            self.assertEqual([p.name for p in root.iterdir()], ["channel.bat"])

    def test_the_downloads_mark_scripts_executable(self):
        sys.path.insert(0, str(P.ROOT / "scripts"))
        try:
            import package
        finally:
            sys.path.pop(0)
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "ReelForge-0-macOS.zip"
            package.build_zip(dest, "ReelForge-0", ["setup.sh", "bin/reelforge", "README.md"],
                              package.extras("macos", "0"))
            with zipfile.ZipFile(dest) as z:
                modes = {i.filename.split("/", 1)[1]: (i.external_attr >> 16) & 0o777 for i in z.infolist()}
            self.assertEqual(modes["setup.sh"] & stat.S_IXUSR, stat.S_IXUSR)
            self.assertEqual(modes["Set up ReelForge.command"] & stat.S_IXUSR, stat.S_IXUSR)
            self.assertEqual(modes["README.md"] & stat.S_IXUSR, 0)
            self.assertIn("printf '\\033[8;42;132t'", package.mac_launcher("setup.sh"))


if __name__ == "__main__":
    unittest.main()
