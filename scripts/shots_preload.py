"""Sample data for the README's screenshots (scripts/screenshots.py) and the window's tests (ui/test/), run inside the
window's Python side before it starts (REELFORGE_BRIDGE_PRELOAD, see studio/bridge.py). Nothing on this computer
changes: it only swaps what ReelForge would find here for the same things on a fresh computer.

  - The Publish tab lists the kit's three demo videos, rendered a day ago (their files are made in a scratch folder,
    their sizes those of the real renders).
  - The first-run screens show a neutral folder (C:\\Users\\you\\ReelForge) and what a computer with nothing installed
    sees, and the last screen a new channel's (Night Physics) with nothing turned on here.
  - The channel's questions start from a sample channel's answers, so its later steps can be shown.
  - The Stats tab's picture has a night the computer was off (from 9 PM four days ago until 1 PM the next day), so
    its charts show an estimate, hatched.
  - A sign-up guide's button, Open profile and every other link open nothing: no browser ever opens from a picture
    or a test (a test that sets REELFORGE_OPENED to a file finds the pages listed there), and a second channel's
    private window is always Chrome's. Nor does a file manager open to show a video's file ("reveal <name>" there).
  - Nor does a Copy button touch this computer's clipboard (a test that sets REELFORGE_COPIED to a file finds what
    was copied there).
  - A picture (REELFORGE_SHOT names it) is a Windows computer's wherever it is taken, as the first-run screens'
    folder and list are: ffmpeg from gyan.dev, Explorer and the Recycle Bin, ReelForge's folder in C:\\Users\\you,
    Edge for ReelForge's own browser window, and the folders on the Settings tab written with backslashes.
"""

from __future__ import annotations

import atexit
import copy
import json
import os
import pathlib
import shutil
import tempfile
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRATCH = pathlib.Path(tempfile.mkdtemp(prefix="reelforge-shots-"))
atexit.register(shutil.rmtree, SCRATCH, ignore_errors=True)
RENDERS = {  # file -> (size in MB as the Publish tab shows it, hours since it was rendered)
    "demo_everyone_jumps.mp4": (131.4, 24.2),
    "demo_doubling_duck.mp4": (81.5, 25.1),
    "demo_disaster_pov.mp4": (50.9, 25.3),
}


def _episodes() -> pathlib.Path:
    """The demos as rendered pipeline episodes: episodes/<id>/episode.json and final/<id>.mp4."""
    episodes = SCRATCH / "episodes"
    for name, (_, hours) in RENDERS.items():
        folder = episodes / pathlib.Path(name).stem
        (folder / "final").mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / "examples" / folder.name / "episode.json", folder / "episode.json")
        video = folder / "final" / name
        video.write_bytes(b"\0")
        when = time.time() - hours * 3600
        os.utime(video, (when, when))
    return episodes


def _dashboard() -> None:
    from dashboard import config

    episodes = _episodes()
    config.episodes_dir = lambda cfg: episodes
    config.videos_dir = lambda cfg: SCRATCH / "videos"
    config.completed_dir = lambda cfg: SCRATCH / "none"
    real_stat = pathlib.Path.stat

    def stat(self, *args, **kwargs):
        st = real_stat(self, *args, **kwargs)
        if self.name in RENDERS:  # only the demos' scratch renders are called that here
            fields = list(st[:10])
            fields[6] = int(RENDERS[self.name][0] * 1024 * 1024)  # st_size
            st = os.stat_result(fields)
        return st

    pathlib.Path.stat = stat


# Your accounts as a new channel sees it: the three recommended platforms, one handle typed in and two to sign up for.
ACCOUNTS_ANSWERS = {"creator": "Sam", "assistant": "claude", "channel_name": "Night Physics",
                    "handle_tiktok": "@nightphysics",
                    "about": "Mind-bending what-if videos: everyday things pushed to impossible extremes, with real "
                             "science."}


def _first_run() -> None:
    from studio import ai_prompt, command, setup_flow, setup_service, sounds, stats_schedule, tools
    from studio import paths as P
    from studio import settings as S

    P.ROOT = pathlib.Path(r"C:\Users\you\ReelForge")  # a neutral folder in the picture
    tools.path_too_long = lambda: False
    Item = setup_flow.Item
    setup_flow.plan = lambda: [  # what a computer with nothing installed sees
        Item("blender", "Blender 4.5.5", "builds and renders your 3D scenes", False, "about 400 MB from blender.org",
             400),
        Item("ffmpeg", "ffmpeg", "turns the frames and sound into your video", False, "about 115 MB from gyan.dev", 115),
        Item("sounds", "Sound effects", "booms, whooshes and hits for your videos", False,
             f"{sounds.mixkit_count()} from Mixkit, about 55 MB", 55),
        Item("command", "The reelforge command", "type reelforge in any terminal to open it", False,
             "no admin rights needed"),
        Item("shortcut", "Desktop shortcut", "a ReelForge icon on your desktop", True, "ready"),
    ]
    sample = copy.deepcopy(S.DEFAULTS)  # a new channel's last screen, with nothing turned on here
    sample["channel"]["name"] = "Night Physics"
    sample["ai"].update(assistant=["claude"])
    sample["platforms"]["handles"] = {p: "yourchannel" for p in S.PLATFORMS}
    S.load = lambda path=None: sample
    stats_schedule.enable = lambda minutes=60: (f"Your stats will update every {minutes} minutes while this computer "
                                                "is on, even with ReelForge closed.")
    command.status = lambda: "on"
    answers = json.loads((ROOT / "scripts" / "shots_answers.json").read_text(encoding="utf-8"))
    if os.environ.get("REELFORGE_SHOT") == "setup-accounts":
        answers = ACCOUNTS_ANSWERS
    setup_service.saved_answers = lambda profile=None: dict(answers)
    ai_prompt.has_videos = lambda profile: False  # the Publish tab's demos aren't this new channel's: its first video


def _list_modules() -> None:
    """For docs/screenshots/SOURCES.txt: the modules this process drew with, listed as it exits."""
    import sys

    path = os.environ.get("REELFORGE_SHOT_MODULES")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.writelines(f"{m.__file__}\n" for m in list(sys.modules.values()) if getattr(m, "__file__", None))


def _no_browser() -> None:
    """Sign-up pages open nothing, and a second channel's private window is Chrome's on every computer (so the pictures
    and tests say the same everywhere). Nor does any other page: each goes in REELFORGE_OPENED's file, if it's set."""
    from dashboard import desktop
    from studio import signup

    signup.open_page = lambda url: None
    signup.launch = lambda command: None
    signup.private_browser = lambda: signup.Private("Chrome", ("chrome", "--incognito"))

    def opened(url: str) -> bool:
        if os.environ.get("REELFORGE_OPENED"):
            with open(os.environ["REELFORGE_OPENED"], "a", encoding="utf-8", newline="\n") as f:
                f.write(url + "\n")
        return True  # as a browser that opened would answer

    desktop.open_url = opened
    # ... and no file manager: an upload button would show the video's file in one.
    desktop.reveal = lambda path: opened(f"reveal {pathlib.Path(path).name}") and None


def _no_clipboard() -> None:
    """Copy buttons copy nothing here: what they would copy goes in REELFORGE_COPIED's file, if it's set (one copy
    after another, each followed by a line of its own saying so)."""
    from dashboard import desktop

    def copied(text: str) -> bool:
        if os.environ.get("REELFORGE_COPIED"):
            with open(os.environ["REELFORGE_COPIED"], "a", encoding="utf-8", newline="\n") as f:
                f.write(text + "\n-- copied --\n")
        return True

    desktop.copy = copied


def _windows() -> None:
    """A picture is a Windows computer's wherever it is taken, so the README's pictures say the same after every
    retake and agree with each other (the first-run screens' folder and list are a Windows computer's already): where
    ffmpeg comes from, what shows your files and where deleted things go, ReelForge's folder, the browser ReelForge
    logs in with, and the backslashes in the folders the Settings tab lists."""
    if not os.environ.get("REELFORGE_SHOT"):
        return
    from dashboard import desktop, service, settings_tab
    from dashboard.platforms import browser, instagram, x
    from studio import tools

    tools.ffmpeg_source = lambda: ("gyan.dev", 115)  # as the list in _first_run says
    desktop.FILES, desktop.FILES_WINDOW, desktop.BIN = "Explorer", "the Explorer window", "Recycle Bin"
    settings_tab.DEMO_FOLDER = r"C:\Users\you\ReelForge"  # the folder the first-run screens show
    settings_tab.OPEN_FOLDER = f"Open in {desktop.FILES}"
    for platform in (instagram.Instagram, x.X):
        platform.setup_note = platform.setup_note.replace(f"own {browser.NAME} window", "own Edge window")
    browser.NAME = "Edge"
    listed = service.Dashboard.profiles_settings

    def profiles_settings(self) -> dict:
        view = listed(self)
        for row in view["rows"]:
            if not row["main"]:  # (the first channel's line is a sentence, the others' a folder)
                row["note"] = row["note"].replace("/", "\\")
        for gone in view["deleted"]:
            gone["where"] = gone["where"].replace("/", "\\")
        return view

    service.Dashboard.profiles_settings = profiles_settings


def _night_off() -> None:
    """The Stats tab's picture: the demo's readings with a night the computer was off taken out, from 9 PM four days ago
    until 1 PM the next day, so its charts show what an estimate looks like."""
    if os.environ.get("REELFORGE_SHOT") != "app-stats":
        return
    from datetime import datetime, timedelta

    from dashboard import db, demo

    seed = demo.seed

    def seeded(*args, **kwargs) -> None:
        seed(*args, **kwargs)
        off = (datetime.now().astimezone() - timedelta(days=4)).replace(hour=21, minute=0, second=0, microsecond=0)
        with db.connect() as conn:
            conn.execute("DELETE FROM account_stats WHERE ts > ? AND ts < ?",
                         (db.to_iso(off), db.to_iso(off + timedelta(hours=16))))

    demo.seed = seeded


atexit.register(_list_modules)
_dashboard()
_first_run()
_no_browser()
_no_clipboard()
_windows()
_night_off()
