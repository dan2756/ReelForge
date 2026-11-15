"""Several ReelForge windows open at once, and the hourly job: one of them reads the stats at a time, a read you ask
for in one window while another is reading waits for that one instead of reading again, and every window shows what
any of them read, with its notifications and errors (dashboard.sync, dashboard.service).

"Another ReelForge" here is a second hold on the read lock (as a second process has, with its own file handle) and
what it saves to the store; one test starts a real second process.
"""

from __future__ import annotations

import contextlib
import io
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import httpx

from dashboard import __main__ as launcher
from dashboard import config, db, demo, notify, sync
from dashboard.__main__ import refresh as job_refresh
from dashboard.config import PLATFORM_KEYS
from dashboard.platforms import Account, Platform, PlatformError, Post
from dashboard.service import WATCH_EVERY
from dashboard.tests.window import Window, plain

ROOT = Path(__file__).resolve().parents[2]
POSTED = "2026-10-20T10:00:00+00:00"


class Fake(Platform):
    """A platform that counts its reads: it can take a while, or fail."""

    def __init__(self, key: str, name: str, followers: int = 1_000, delay: float = 0, error: str = ""):
        super().__init__({"handle": "me"})
        self.key, self.name = key, name
        self.followers, self.delay, self.error = followers, delay, error
        self.reads = 0

    @property
    def stats_source(self) -> str | None:
        return "public"

    @property
    def uses_browser(self) -> bool:
        return False

    def fetch(self):
        self.reads += 1
        time.sleep(self.delay)
        if self.error:
            raise PlatformError(self.error)
        return Account("@me", self.followers), [Post(f"{self.key}-1", "What if it doubled?", None, POSTED,
                                                     views=500, likes=20, comments=2, shares=1)]


def save_read(key: str, followers: int) -> None:
    """What another ReelForge's read saves: a snapshot of the platform."""
    db.record_snapshot(key, Account("@me", followers), [Post(f"{key}-1", "What if it doubled?", None, POSTED,
                                                             views=900, likes=30, comments=3, shares=2)])


class Other:
    """Another ReelForge, reading: it holds the read lock and says what it's reading in the shared state."""

    def __init__(self, what: str = "TikTok", who: str = "window"):
        self.lock = sync.ReadLock(sync.lock_path())
        self.what, self.who = what, who

    def start(self) -> Other:
        assert self.lock.acquire()
        db.set_meta(sync.STATE, {"attempted": {}, "errors": {}, "running": {
            "by": "another-reelforge", "who": self.who, "what": self.what, "since": db.now_iso()}})
        return self

    def finish(self, reads: dict[str, int] | None = None, errors: dict | None = None) -> None:
        """Save what it read (platform -> followers), then let go, the way sync._refresh does."""
        for key, followers in (reads or {}).items():
            save_read(key, followers)
        if reads:
            db.bump()
        now = db.now_iso()
        db.set_meta(sync.STATE, {"attempted": {k: now for k in (reads or {})}, "errors": errors or {},
                                 "running": None})
        self.lock.release()


class StoreTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        self.addCleanup(setattr, db, "_path", db._path)
        self.dir = Path(tmp.name)
        db.init(self.dir / "stats.db")
        for state in (sync._attempted, sync.last_errors, sync.last_warnings):
            state.clear()
            self.addCleanup(state.clear)
        self.addCleanup(setattr, sync, "_stale", None)
        self.cfg = {"refresh_minutes": 30, **{key: {} for key in PLATFORM_KEYS}}

    def fakes(self, **kw) -> dict[str, Fake]:
        return {"tiktok": Fake("tiktok", "TikTok", **kw), "youtube": Fake("youtube", "YouTube", **kw)}


class ReadLockTest(StoreTest):
    def test_one_holder_at_a_time(self):
        mine, theirs = sync.ReadLock(sync.lock_path()), sync.ReadLock(sync.lock_path())
        self.assertTrue(mine.acquire())
        self.assertTrue(theirs.held())
        self.assertFalse(theirs.acquire(0.3))
        mine.release()
        self.assertFalse(theirs.held())
        self.assertTrue(theirs.acquire())
        theirs.release()

    def test_another_process_holds_it_until_it_closes_even_without_letting_go(self):
        script = ("import sys, time\nfrom pathlib import Path\nfrom dashboard import sync\n"
                  "lock = sync.ReadLock(Path(sys.argv[1]))\nassert lock.acquire()\nprint('held', flush=True)\n"
                  "time.sleep(60)\n")  # killed long before: it never lets go itself
        other = subprocess.Popen([sys.executable, "-c", script, str(sync.lock_path())], cwd=ROOT,
                                 stdout=subprocess.PIPE, text=True)
        self.addCleanup(other.stdout.close)
        self.addCleanup(other.wait)
        self.addCleanup(other.kill)
        self.assertEqual(other.stdout.readline().strip(), "held")
        lock = sync.ReadLock(sync.lock_path())
        self.assertTrue(lock.held())
        other.kill()  # as if its window were closed mid-read
        other.wait()
        self.assertTrue(lock.acquire(5))
        lock.release()


class RefreshTest(StoreTest):
    def test_an_automatic_read_leaves_it_to_the_one_reading(self):
        other = Other().start()
        ps = self.fakes()
        result = sync.refresh(ps, self.cfg)
        self.assertTrue(result.busy)
        self.assertEqual([p.reads for p in ps.values()], [0, 0])
        other.finish()
        result = sync.refresh(ps, self.cfg)  # its turn now
        self.assertEqual((result.done, result.busy), (["TikTok", "YouTube"], False))

    def test_a_read_you_ask_for_waits_then_leaves_out_what_the_other_read(self):
        other = Other("TikTok").start()
        ps = self.fakes()
        out = {}
        reader = threading.Thread(target=lambda: out.update(result=sync.refresh(ps, self.cfg, manual=True)))
        reader.start()
        time.sleep(1.1)  # its read is saved a second or more after you asked, as a real one is
        self.assertTrue(reader.is_alive())  # waiting for the other one
        other.finish({"tiktok": 1_234})
        reader.join(10)
        result = out["result"]
        self.assertEqual((result.done, result.waited), (["YouTube"], "another ReelForge window"))
        self.assertEqual((ps["tiktok"].reads, ps["youtube"].reads), (0, 1))  # TikTok was read once, by the other
        self.assertEqual(db.latest_accounts()["tiktok"]["followers"], 1_234)

    def test_force_waits_too_and_leaves_out_what_was_read_after_you_asked(self):
        other = Other("TikTok, YouTube", who="job").start()
        ps = self.fakes()
        out = {}
        reader = threading.Thread(target=lambda: out.update(result=sync.refresh(ps, self.cfg, True, True)))
        reader.start()
        time.sleep(1.1)
        other.finish({"tiktok": 1_234, "youtube": 55})
        reader.join(10)
        self.assertEqual((out["result"].done, out["result"].waited), ([], "the hourly stats job"))
        self.assertEqual([p.reads for p in ps.values()], [0, 0])
        # Asked again later, force reads them all.
        time.sleep(1.1)
        self.assertEqual(sync.refresh(ps, self.cfg, True, True).done, ["TikTok", "YouTube"])

    def test_a_read_you_ask_for_stops_waiting_after_a_while(self):
        other = Other().start()
        self.addCleanup(other.lock.release)
        with mock.patch.object(sync, "WAIT", 0.5):
            result = sync.refresh(self.fakes(), self.cfg, manual=True)
        self.assertEqual((result.busy, result.waited, result.done), (True, "another ReelForge window", []))

    def test_closing_the_window_stops_the_wait(self):
        other = Other().start()
        self.addCleanup(other.lock.release)
        closing = threading.Event()
        threading.Timer(0.5, closing.set).start()
        start = time.monotonic()
        result = sync.refresh(self.fakes(), self.cfg, manual=True, stop=closing.is_set)
        self.assertLess(time.monotonic() - start, 2)
        self.assertTrue(result.busy)

    def test_attempts_and_errors_are_every_windows(self):
        ps = {"tiktok": Fake("tiktok", "TikTok", error="TikTok didn't load @me's profile")}
        self.assertEqual(sync.refresh(ps, self.cfg).errors, ["TikTok: TikTok didn't load @me's profile"])
        # A window that opens later (a new process) starts with nothing of its own, and takes them on.
        sync._attempted.clear()
        sync.last_errors.clear()
        self.assertTrue(sync.adopt(sync.shared()))
        self.assertEqual(sync.last_errors["tiktok"][1], "TikTok didn't load @me's profile")
        self.assertFalse(sync.due(ps["tiktok"], self.cfg, None, manual=False))  # it waits like the first one
        self.assertEqual(sync.refresh(ps, self.cfg).done, [])
        self.assertEqual(ps["tiktok"].reads, 1)
        ps["tiktok"].error = ""
        self.assertEqual(sync.refresh(ps, self.cfg, True, True).done, ["TikTok"])
        sync.last_errors.clear()
        sync.adopt(sync.shared())
        self.assertNotIn("tiktok", sync.last_errors)  # the good read cleared it for everyone

    def test_errors_are_sentences_and_their_own_words_are_kept_for_the_log(self):
        class Odd(Fake):
            def fetch(self):
                raise KeyError("createTime")  # a page that changed under ReelForge

        ps = {"tiktok": Fake("tiktok", "TikTok", error="TikTok didn't load @me's profile"),
              "youtube": Unreachable("youtube", "YouTube"), "x": Odd("x", "X")}
        result = sync.refresh(ps, self.cfg)
        again = "ReelForge tries again at the next refresh."
        self.assertEqual(result.errors, [
            "TikTok: TikTok didn't load @me's profile",
            f"YouTube couldn't be reached (is this computer online?). {again}",
            f"X wasn't read: its page wasn't what ReelForge expected. {again}"])
        self.assertEqual(result.failed, {"TikTok": "said", "YouTube": "offline", "X": "odd"})
        self.assertEqual(result.details, ["TikTok: PlatformError: TikTok didn't load @me's profile",
                                          "YouTube: ConnectError: [Errno -2] Name or service not known",
                                          "X: KeyError: 'createTime'"])
        self.assertEqual(sync.last_errors["x"][1], result.errors[2])
        # Summed up for one pop-up: who wasn't read, and where each one's reason is.
        self.assertEqual(sync.sum_up(result), f"TikTok, YouTube and X weren't read this time. {again} Each one's "
                                              "panel on the Accounts tab says why.")
        self.assertEqual(sync.sum_up(sync.Result()), "")
        self.assertEqual(sync.sum_up(sync.Result(errors=result.errors[:1], failed={"TikTok": "said"})),
                         result.errors[0])
        matching = f"New posts couldn't be matched to your videos this time. {again}"
        with mock.patch.object(sync.analytics, "auto_link", side_effect=sqlite3.OperationalError("locked")):
            ps = {"tiktok": Fake("tiktok", "TikTok"), "youtube": Unreachable("youtube", "YouTube")}
            result = sync.refresh(ps, self.cfg, True, True)
        self.assertEqual(result.errors[1], matching)
        self.assertEqual(sync.sum_up(result), f"{result.errors[0]} {matching}")
        self.assertEqual(result.details[1], "matching posts to videos: OperationalError: locked")
        # A refresh that broke off whole (the store was busy) is a sentence too.
        broke = sync.broke(sqlite3.OperationalError("database is locked"))
        self.assertEqual(broke.errors, ["The stats database was busy, so this refresh didn't finish. ReelForge tries "
                                        "again at the next one."])
        self.assertEqual(broke.details, ["the refresh stopped: OperationalError: database is locked"])

    def test_a_platform_that_needs_a_log_in_keeps_its_own_sentence_in_the_pop_up(self):
        """X logged ReelForge's browser out and YouTube couldn't be reached: the one pop-up used to say "X and
        YouTube weren't read this time. ReelForge tries again at the next refresh." X isn't read again until you
        log in."""
        class LoggedOut(Fake):
            def fetch(self):
                raise PlatformError(f"{self.name} logged ReelForge's browser out. Log in again on the Accounts tab.",
                                    needs_login=True)

        again = "ReelForge tries again at the next refresh."
        log_in = "X: X logged ReelForge's browser out. Log in again on the Accounts tab."
        ps = {"x": LoggedOut("x", "X"), "youtube": Unreachable("youtube", "YouTube")}
        result = sync.refresh(ps, self.cfg)
        self.assertEqual(result.failed, {"X": "login", "YouTube": "offline"})
        self.assertEqual(sync.sum_up(result),
                         f"{log_in} YouTube couldn't be reached (is this computer online?). {again}")
        ps = {"tiktok": Fake("tiktok", "TikTok", error="TikTok didn't load @me's profile"), **ps}
        result = sync.refresh(ps, self.cfg, True, True)
        self.assertEqual(sync.sum_up(result), f"{log_in} TikTok and YouTube weren't read this time. {again} Each "
                                              "one's panel on the Accounts tab says why.")
        ps = {"instagram": LoggedOut("instagram", "Instagram"), "x": ps["x"]}  # both: each says so, nothing else
        self.assertEqual(sync.sum_up(sync.refresh(ps, self.cfg, True, True)),
                         "Instagram: Instagram logged ReelForge's browser out. Log in again on the Accounts tab. "
                         + log_in)

    def test_the_change_count_goes_up_once_per_read_that_saved_something(self):
        ps = self.fakes()
        self.assertEqual(db.changes(), 0)
        result = sync.refresh(ps, self.cfg)
        self.assertEqual((result.changes, db.changes()), (1, 1))
        self.assertEqual(sync.refresh(ps, self.cfg).changes, None)  # nothing due
        failing = {"x": Fake("x", "X", error="X didn't load")}
        self.assertEqual(sync.refresh(failing, self.cfg).changes, None)
        self.assertEqual(db.changes(), 1)
        self.assertEqual(db.bump(), 2)

    def test_the_read_under_way_is_shared_and_cleared_after(self):
        ps = {"tiktok": Fake("tiktok", "TikTok", delay=0.8)}
        reader = threading.Thread(target=lambda: sync.refresh(ps, self.cfg))
        reader.start()
        time.sleep(0.4)
        running = sync.shared()["running"]
        self.assertEqual((running["by"], running["what"], running["who"]), (sync.ME, "TikTok", "window"))
        self.assertIsNone(sync.reading_elsewhere(sync.shared()))  # this ReelForge's own read
        reader.join(5)
        self.assertIsNone(sync.shared()["running"])

    def test_a_read_whose_reelforge_closed_mid_read_isnt_shown(self):
        other = Other("Instagram").start()
        self.assertEqual(sync.reading_elsewhere(sync.shared())["what"], "Instagram")
        other.lock.release()  # it closed without clearing the state
        self.assertIsNone(sync.reading_elsewhere(sync.shared()))
        self.assertIsNone(sync.reading_elsewhere(sync.shared()))  # and isn't looked at again

    def test_a_read_that_breaks_off_still_clears_up(self):
        ps = self.fakes()
        with mock.patch.object(sync.notify, "check_milestones", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                sync.refresh(ps, self.cfg)
        self.assertIsNone(sync.shared()["running"])
        self.assertEqual(db.changes(), 1)  # what it saved still counts
        self.assertFalse(sync.ReadLock(sync.lock_path()).held())

    def test_the_hourly_job_waits_for_a_window_and_says_so(self):
        other = Other("TikTok, YouTube").start()
        threading.Timer(1.1, lambda: other.finish({"tiktok": 1_234, "youtube": 55})).start()
        with mock.patch.object(config, "DATA_DIR", self.dir), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(job_refresh(self.fakes(), self.cfg, demo=True), 0)
        log = (self.dir / "refresh.log").read_text("utf-8")
        self.assertIn("refreshed: nothing due (another ReelForge window had just read them)", log)


class Unreachable(Fake):
    """A platform whose site can't be reached: the read fails the way httpx says it with no internet."""

    def fetch(self):
        self.reads += 1
        raise httpx.ConnectError("[Errno -2] Name or service not known")


class HourlyJobTest(StoreTest):
    """reelforge refresh, the hourly job (dashboard/__main__.py, started by dashboard/refresh.pyw): it has no window
    and, under the scheduler, no terminal, so data/refresh.log says how each run went, one that couldn't start
    included."""

    def job(self, platforms: dict) -> tuple[int, str, str]:
        """One run: its exit code, what it printed, and the log afterwards."""
        out = io.StringIO()
        with mock.patch.object(config, "DATA_DIR", self.dir), contextlib.redirect_stdout(out):
            code = job_refresh(platforms, self.cfg, demo=True)
        return code, out.getvalue(), (self.dir / "refresh.log").read_text("utf-8")

    def test_the_terminal_gets_a_sentence_and_the_log_the_errors_own_words(self):
        code, said, log = self.job({"tiktok": Fake("tiktok", "TikTok"), "youtube": Unreachable("youtube", "YouTube")})
        self.assertEqual(code, 1)
        self.assertIn("refreshed: TikTok\n", said)
        self.assertIn("error: YouTube couldn't be reached (is this computer online?). ReelForge tries again at the "
                      "next refresh.", said)
        self.assertNotIn("ConnectError", said)
        self.assertIn("  refreshed: TikTok\n", log)
        self.assertIn("  error: YouTube: ConnectError: [Errno -2] Name or service not known\n", log)
        self.assertEqual(db.get_meta("job")["problems"], 1)

    def test_a_run_that_goes_wrong_after_its_read_has_still_logged_it(self):
        with mock.patch.object(db, "compact", side_effect=sqlite3.OperationalError("database is locked")):
            code, _, log = self.job(self.fakes())
        self.assertEqual(code, 0)  # the read itself went fine
        self.assertIn("  refreshed: TikTok, YouTube\n", log)
        self.assertIn("  error: tidying the stats database: OperationalError: database is locked\n", log)
        job = db.get_meta("job")
        self.assertEqual((job["read"], job["problems"]), ("TikTok, YouTube", 1))

    def test_a_run_that_cant_start_says_why_in_the_log(self):
        settings = self.dir / "config.toml"
        settings.write_text('videos_dir = "C:\\Users\\me\\Videos"\n', "utf-8")  # a Windows path, as people type it
        out = io.StringIO()
        with mock.patch.object(config, "DATA_DIR", self.dir), mock.patch.object(config, "CONFIG_PATH", settings), \
                contextlib.redirect_stdout(out):
            self.assertEqual(launcher.main(["refresh"]), 2)
            settings.write_text('videos_dir = "videos"\n', "utf-8")
            with mock.patch.object(launcher, "refresh_profiles",
                                   side_effect=sqlite3.DatabaseError("file is not a database")):
                self.assertEqual(launcher.main(["refresh"]), 1)
        log = (self.dir / "refresh.log").read_text("utf-8").splitlines()
        self.assertIn("  error: the stats read didn't run: config.toml has a typo", log[0])
        self.assertTrue(log[1].endswith("  error: the stats read didn't run: DatabaseError: file is not a database"))

    def test_refresh_pyw_logs_what_stops_it_outside_the_read(self):
        """The script the scheduler starts, in a stand-in ReelForge folder whose dashboard fails in turn the ways a
        real one can: an error nothing caught, the kit's packages missing (exit code 3), and not at all."""
        kit = self.dir / "kit" / "dashboard"
        kit.mkdir(parents=True)
        shutil.copy(ROOT / "dashboard" / "refresh.pyw", kit / "refresh.pyw")
        (kit / "__init__.py").write_text("", "utf-8")
        log = kit / "data" / "refresh.log"
        for stand_in, logged in (("def main(argv):\n    raise RuntimeError('the disk went away')\n",
                                  "error: the stats read didn't run: RuntimeError: the disk went away"),
                                 ("import sys\nsys.exit(3)\n", "error: the stats read didn't run: ReelForge's Python "
                                                               "packages are missing: run setup again"),
                                 ("def main(argv):\n    return 1\n", None)):
            (kit / "__main__.py").write_text(stand_in, "utf-8")
            before = log.read_text("utf-8").splitlines() if log.exists() else []
            subprocess.run([sys.executable, str(kit / "refresh.pyw")], cwd=self.dir, check=True, timeout=60)
            added = (log.read_text("utf-8").splitlines() if log.exists() else [])[len(before):]
            self.assertEqual([line.split("  ", 1)[1] for line in added], [logged] if logged else [])

    @unittest.skipIf(os.name == "nt", "Windows keeps a running program's folder where it is")
    def test_refresh_pyw_makes_nothing_where_its_folder_was(self):
        """reelforge move renamed the folder while the script the scheduler had just started was still starting
        up. The run then fails (its files are gone from under the name it knows them by), and it used to log
        that in data/refresh.log by the old path, folders and all: a stub of the old ReelForge folder was back
        beside the new one. Here the stand-in dashboard renames its own ReelForge folder, then fails the way an
        import does once the folder has gone."""
        old, new = self.dir / "old place", self.dir / "new place"
        kit = old / "dashboard"
        kit.mkdir(parents=True)
        shutil.copy(ROOT / "dashboard" / "refresh.pyw", kit / "refresh.pyw")
        (kit / "__init__.py").write_text("", "utf-8")
        (kit / "__main__.py").write_text(
            "import os\nfrom pathlib import Path\n\n"
            "here = Path(__file__).resolve().parent.parent\n"
            "os.rename(here, here.with_name('new place'))\n"
            "import dashboard.sync  # not loaded yet, and no longer where Python looks for it\n", "utf-8")
        ran = subprocess.run([sys.executable, str(kit / "refresh.pyw")], cwd=self.dir, timeout=60,
                             capture_output=True, text=True)
        self.assertEqual((ran.returncode, ran.stdout, ran.stderr), (0, "", ""))  # and not a word about it
        self.assertFalse(old.exists())
        # Nor is anything new at the new place: no log, and none of Python's bytecode files either. Python makes
        # the folders on the way to one of those, so a run that wrote them put the old folder back that way too.
        self.assertEqual(sorted(f.name for f in (new / "dashboard").iterdir()),
                         ["__init__.py", "__main__.py", "refresh.pyw"])

    def test_a_read_whose_folder_has_gone_stops_and_makes_no_folder_there(self):
        """The same read, caught a moment later or as it finishes: everything it needs is loaded, and what it does
        next it does by the old folder's name. Loading the settings made a folder for fresh ones (with nothing to
        put in it: the example they are copied from went with the rest), and looking for new videos made the
        videos folder. Either put a stub of the old ReelForge folder back."""
        gone = self.dir / "old place" / "dashboard"
        cfg = {**self.cfg, "videos_dir": str(gone / "videos"), "episodes_dir": str(self.dir / "none"),
               "completed_dir": str(self.dir / "none")}
        out = io.StringIO()
        with mock.patch.object(config, "APP_DIR", gone), mock.patch.object(config, "DATA_DIR", gone / "data"), \
                mock.patch.object(config, "CONFIG_PATH", gone / "config.toml"), contextlib.redirect_stdout(out):
            self.assertEqual(launcher.main(["refresh"]), 1)
            self.assertEqual(notify.check_videos(cfg), 0)
        self.assertEqual(out.getvalue(), "")
        self.assertFalse((self.dir / "old place").exists())
        notify.check_videos(cfg)  # while ReelForge is where it was started, the look makes the folder as ever
        self.assertTrue((gone / "videos").is_dir())


class WindowsTest(unittest.TestCase):
    """A window on demo data (its Python side), with another ReelForge reading and saving next to it."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        self.addCleanup(setattr, db, "_path", db._path)
        root = Path(tmp.name)
        self.cfg = {"refresh_minutes": 0, "videos_dir": str(root / "none"), "episodes_dir": str(root / "none"),
                    "completed_dir": str(root / "none"), **{key: {} for key in PLATFORM_KEYS}}
        db.init(root / "demo.db")
        demo.seed()
        for state in (sync._attempted, sync.last_errors, sync.last_warnings):
            state.clear()
            self.addCleanup(state.clear)
        self.addCleanup(setattr, sync, "_stale", None)

    def window(self) -> Window:
        """A window reading nothing by itself (refresh_minutes = 0): only r, or what another ReelForge reads."""
        w = Window(self.cfg, demo.platforms())
        self.addCleanup(w.close)
        w.pump(0.5)
        return w

    @staticmethod
    def status(w: Window) -> str:
        """Under the Refresh button: the read under way, here or in another window, or "" when there's none."""
        return w.view("refreshed")["reading"]

    def test_a_read_in_another_window_shows_here(self):
        w = self.window()
        followers = w.svc.latest["tiktok"]["followers"] + 5_000
        self.assertTrue(w.view("refreshed")["ago"].endswith(" ago"))
        other = Other("TikTok").start()
        self.assertTrue(w.until(lambda: "refreshing TikTok…" in self.status(w)))
        other.finish({"tiktok": followers})
        self.assertTrue(w.until(lambda: w.svc.latest["tiktok"]["followers"] == followers))
        self.assertTrue(any(c["values"]["followers"] >= followers for c in w.view("cards")["cards"]
                            if c["id"] == "tiktok"))  # the card shows it
        self.assertEqual(self.status(w), "")
        self.assertEqual(w.view("refreshed")["ago"], "0m ago")
        self.assertEqual(w.svc.changes, db.changes())

    def test_refresh_while_another_window_reads_waits_for_it(self):
        w = self.window()
        followers = {key: w.svc.latest[key]["followers"] + 100 for key in w.svc.platforms}
        other = Other("TikTok, YouTube, Instagram, X").start()
        w.call("refresh", False)  # r
        w.pump(1.1)
        self.assertEqual(w.svc.reading.count, 1)  # waiting
        other.finish(followers)
        self.assertTrue(w.until(lambda: any("just read your stats" in t for t in w.toasts())))
        self.assertIn("Up to date: another ReelForge window just read your stats.", w.toasts())
        self.assertTrue(w.until(lambda: w.svc.latest["x"]["followers"] == followers["x"]))
        self.assertEqual({key: w.svc.latest[key]["followers"] for key in w.svc.platforms}, followers)

    def test_errors_from_another_windows_read_show_here(self):
        w = self.window()
        other = Other("Instagram").start()
        other.finish(errors={"instagram": [db.now_iso(), "Log in to Instagram again"]})
        self.assertTrue(w.until(lambda: "instagram" in sync.last_errors))
        self.assertEqual(sync.last_errors["instagram"][1], "Log in to Instagram again")
        panel = next(p for p in w.view("accounts")["panels"] if p["key"] == "instagram")
        self.assertIn("last read failed: Log in to Instagram again", plain(panel["body"]))
        # the foot of the window names it (and only it), and a click on it opens Accounts
        self.assertTrue(w.until(lambda: "Instagram" in plain(w.view("header")["line"])))
        foot = w.view("header")
        self.assertIn("● Instagram: last refresh failed  see Accounts", plain(foot["line"]))
        self.assertIn("● IG: last refresh failed", plain(foot["short"]))
        self.assertNotIn("TikTok", plain(foot["line"]))
        self.assertIn(["tab", "accounts"], [seg[2] for seg in foot["line"] if len(seg) > 2])

    def test_a_failed_refresh_is_one_pop_up_in_plain_words(self):
        """No internet: every platform fails the same way. That's one pop-up saying so, not one per platform with the
        error's own words, which go to data/refresh.log; each panel on Accounts says why in a sentence."""
        every = {"tiktok": Unreachable("tiktok", "TikTok"), "youtube": Unreachable("youtube", "YouTube"),
                 "x": Unreachable("x", "X")}
        w = Window(self.cfg, every)
        self.addCleanup(w.close)
        w.pump(0.5)
        w.forget()
        w.call("refresh", True)
        self.assertTrue(w.until(lambda: w.toasts()))
        w.pump(0.3)
        offline = ("TikTok, YouTube and X couldn't be reached (is this computer online?). ReelForge tries again at the "
                   "next refresh.")
        self.assertEqual([(t["title"], t["body"]) for t in w.sent("toast")], [("Refresh failed", offline)])
        panel = next(p for p in w.view("accounts")["panels"] if p["key"] == "youtube")
        self.assertIn("last read failed: YouTube couldn't be reached (is this computer online?). ReelForge tries "
                      "again at the next refresh.", plain(panel["body"]))
        self.assertNotIn("ConnectError", str(w.loop.events))
        log = sync.log_path().read_text("utf-8")
        for name in ("TikTok", "YouTube", "X"):
            self.assertIn(f"  error: {name}: ConnectError: [Errno -2] Name or service not known\n", log)

    def test_an_automatic_refresh_doesnt_repeat_what_it_said(self):
        w = self.window()
        w.forget()

        def after(result: sync.Result, manual: bool) -> list[str]:
            w.forget()
            w.svc.after_refresh(result, manual, None)
            w.pump(0.05)
            return [t["body"] for t in w.sent("toast") if t["title"] == "Refresh failed"]

        slow = "YouTube took too long to answer. ReelForge tries again at the next refresh."
        failed = sync.Result(errors=[slow], failed={"YouTube": "slow"})
        self.assertEqual(after(failed, False), [slow])
        self.assertEqual(after(sync.Result(), False), [])  # nothing was due
        self.assertEqual(after(failed, False), [])  # the same again, half an hour later: said already
        self.assertEqual(after(failed, True), [slow])  # you pressed Refresh: it always answers
        offline = sync.Result(errors=[slow.replace("took too long to answer", "couldn't be reached")],
                              failed={"YouTube": "offline"})
        self.assertEqual(len(after(offline, False)), 1)  # something else went wrong: said
        w.svc.after_refresh(sync.Result(done=["YouTube"]), False, None)  # read again
        self.assertEqual(after(failed, False), [slow])  # and when it next fails, that's news

    def test_the_bell_follows_the_other_windows(self):
        w = self.window()
        unread = w.view("notes")["unread"]
        notify.add("milestone", "TikTok passed 25K followers", "You have 25,010 followers on TikTok.",
                   key="milestone:followers:tiktok:25000")  # another window's refresh found it
        self.assertTrue(w.until(lambda: w.view("notes")["unread"] == unread + 1))
        self.assertTrue(w.until(lambda: "You have 25,010 followers on TikTok." in w.toasts()))  # popped up here too
        notify.mark_all_read()  # Mark all read, in another window
        self.assertTrue(w.until(lambda: w.view("notes")["unread"] == 0))

    def test_a_new_version_another_window_found_shows_on_the_updates_tab_here_too(self):
        w = self.window()
        w.svc.demo = False  # the demo's Updates tab shows a sample
        info = {"current": "1.0.4", "latest": "9.9.9", "newer": True, "notes": "What's new", "url": "",
                "error": "", "checked": db.now_iso()}
        db.set_meta("update_check", info)  # another window's check
        notify.update_available("9.9.9")
        self.assertTrue(w.until(lambda: (w.svc.update_info or {}).get("latest") == "9.9.9"))
        self.assertEqual(w.svc.update_info, info)
        self.assertTrue(w.view("update")["newer"])  # the tab gets its red dot

    def test_a_change_made_here_doesnt_reload_this_window_again(self):
        w = self.window()
        w.svc.tell_others()
        self.assertEqual(w.svc.changes, db.changes())
        with mock.patch.object(w.svc, "_load_shared") as load:
            w.pump(WATCH_EVERY + 0.5)  # a look round, at least
        load.assert_not_called()


if __name__ == "__main__":
    unittest.main()
