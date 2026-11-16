"""Only connected platforms show anywhere in ReelForge, and the Accounts tab connects and disconnects them
(dashboard.platforms.base.Platform.connected, dashboard.service): a platform you haven't connected has no card, chart
key, Videos filter, upload button, best-time line or notification, and connecting it switches all of that on in place.
The window's own Accounts tab (the Connect box, Sign up, Disconnect) is tested in ui/test/dashboard.test.tsx."""

from __future__ import annotations

import shutil
import sqlite3
import tempfile
import threading
import unittest
from datetime import timedelta
from pathlib import Path
from unittest import mock

from dashboard import analytics, config, db, demo, notify, sync
from dashboard import service as service_module
from dashboard.config import PLATFORM_KEYS
from dashboard.platforms import Account, Instagram, Post, TikTok, X, YouTube, browser, build, connected
from dashboard.service import NOTHING_CONNECTED, Dashboard
from dashboard.tests.window import Window, plain


def text(parts: list | None) -> str:
    """The text of (segments, importance) parts."""
    return "".join(plain(p[0]) for p in parts or [])


class ConnectedRule(unittest.TestCase):
    """Which platforms are on."""

    def test_a_handle_and_a_way_to_read_it(self):
        with mock.patch.object(browser, "logged_in", lambda key: False):
            self.assertFalse(TikTok({}).connected())
            self.assertTrue(TikTok({"handle": "@me"}).connected())
            self.assertTrue(YouTube({"handle": "me"}).connected())
            self.assertFalse(Instagram({"handle": "@me"}).connected())  # its one log-in is still to come
            self.assertTrue(Instagram({"handle": "@me"}).connected({"instagram"}))  # stats already: a lapsed log-in
            self.assertFalse(X({}).connected({"x"}))  # disconnected: its stats stay, hidden
        with mock.patch.object(browser, "logged_in", lambda key: True):
            self.assertTrue(X({"handle": "@me"}).connected())

    def test_in_reelforges_order(self):
        cfg = {"tiktok": {"handle": "@a"}, "youtube": {}, "instagram": {"handle": "@b"}, "x": {"handle": "@c"}}
        with mock.patch.object(browser, "logged_in", lambda key: key == "x"):
            self.assertEqual(list(connected(build(cfg))), ["tiktok", "x"])

    def test_what_a_notification_is_about(self):
        names = {"tiktok": "TikTok", "instagram": "Instagram", "x": "X"}
        about = lambda key, **note: notify.platform_of({"key": key, **note}, names)  # noqa: E731
        self.assertEqual(about("hot:instagram|123"), "instagram")
        self.assertEqual(about("milestone:video:x|9:1000"), "x")
        self.assertEqual(about("badge:day:tiktok:2026-10-25:views:10"), "tiktok")
        self.assertEqual(about("milestone:followers:youtube:1000"), "youtube")
        self.assertEqual(about("login:x"), "x")
        self.assertEqual(about(None, kind="login", title="Log in to Instagram again"), "instagram")
        for everyone in ("milestone:views:100000", "video:ep1.mp4", "update:1.0.5"):
            self.assertIsNone(about(everyone))


class WindowTest(unittest.TestCase):
    """A window's Python side on demo data, with some platforms not connected."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        self.addCleanup(setattr, db, "_path", db._path)
        self.root = root = Path(tmp.name)
        self.cfg = {"refresh_minutes": 0, "videos_dir": str(root / "none"), "episodes_dir": str(root / "none"),
                    "completed_dir": str(root / "none"), **{key: {} for key in PLATFORM_KEYS}}
        db.init(root / "demo.db")
        demo.seed()
        for state in (sync._attempted, sync.last_errors, sync.last_warnings):
            state.clear()
            self.addCleanup(state.clear)

    def forget(self, *keys: str) -> None:
        """A channel that never had these connected: no stats for them."""
        with db.connect() as c:
            for table in ("account_stats", "post_stats", "posts", "uploads"):
                c.execute(f"DELETE FROM {table} WHERE platform IN ({','.join('?' * len(keys))})", keys)

    def open(self, platforms, tab: str | None = "overview", demo_mode: bool = True) -> Window:
        w = Window(self.cfg, platforms, demo=demo_mode, tab=tab, drawer=True)
        self.addCleanup(w.close)
        return w

    @staticmethod
    def panel(w: Window, key: str) -> dict:
        return next(p for p in w.view("accounts")["panels"] if p["key"] == key)

    @staticmethod
    def cards(w: Window) -> list[str]:
        return [c["id"] for c in w.view("cards")["cards"]]

    @staticmethod
    def buttons(w: Window) -> list[str]:
        """The platforms with upload buttons on Publish (the window draws them from this view)."""
        return [p["key"] for p in w.view("platforms")["platforms"]]

    def test_only_the_connected_ones_anywhere(self):
        self.forget("instagram", "x")
        notify.add("milestone", "X passed 1K followers", "You have 1,000 followers on X.",
                   key="milestone:followers:x:1000")
        notify.add("milestone", "TikTok passed 50K followers", "You have 50,000 followers on TikTok.",
                   key="milestone:followers:tiktok:50000")
        w = self.open(demo.platforms(unconnected=("instagram", "x")))
        self.assertEqual(list(w.svc.platforms), ["tiktok", "youtube"])
        self.assertEqual(self.cards(w), ["total", "tiktok", "youtube"])
        self.assertEqual(self.buttons(w), ["tiktok", "youtube"])
        self.assertEqual(len(w.view("best")["lines"]), 2)
        header = plain(w.view("header")["line"])
        self.assertNotIn("TikTok", header)  # the foot of the window doesn't list the platforms (DJ, 2026-10-28)
        self.assertNotIn("Instagram", header)
        key = plain(w.view("trends")["legend"])
        self.assertIn("YouTube", key)
        self.assertNotIn("Instagram", key)
        titles = [plain(n["title"]) for n in w.view("notes")["items"]]
        self.assertIn("TikTok passed 50K followers", titles)
        self.assertNotIn("X passed 1K followers", titles)
        w.call("tab_opened", "videos")
        filters = text(w.view("videos")["hint"])
        self.assertIn("YouTube", filters)
        self.assertNotIn("Instagram", filters)
        self.assertEqual({p["platform"] for p in w.svc.posts}, {"tiktok", "youtube"})
        self.assertEqual(self.panel(w, "x")["state"], "off")
        self.assertIsNone(self.panel(w, "x")["disconnect"])
        self.assertIn("Connect Instagram or X here", text(w.view("accounts")["hint"]))
        # Without X's button, the caption has a copy button of its own: under YouTube's button, with #shorts after it
        # (DJ, 2026-11-01), or by itself when YouTube isn't connected either.
        self.assertEqual([b[1] for b in w.init["copies"]["youtube"]], ["Copy caption", "Copy YouTube hashtags"])
        copied = []
        with mock.patch.object(service_module.desktop, "copy", lambda t: copied.append(t) or True):
            w.call("copy_field", "copy-youtube-caption", "What if your coffee doubled every 2 seconds?", "#fyp #whatif")
            w.call("copy_field", "copy-caption-alone", "What if your coffee doubled every 2 seconds?", "")
        self.assertEqual(copied, ["What if your coffee doubled every 2 seconds? #shorts",
                                  "What if your coffee doubled every 2 seconds?"])

    def test_everything_shows_for_four_connected_platforms(self):
        """A ReelForge with all four connected: nothing is hidden and Accounts has no hint."""
        w = self.open(demo.platforms())
        self.assertEqual(list(w.svc.platforms), list(PLATFORM_KEYS))
        self.assertEqual(self.cards(w), ["total", *PLATFORM_KEYS])
        self.assertEqual(self.buttons(w), list(PLATFORM_KEYS))
        for key in PLATFORM_KEYS:
            self.assertEqual(self.panel(w, key)["state"], "on")
            self.assertTrue(self.panel(w, key)["disconnect"])
        self.assertIsNone(w.view("accounts")["hint"])

    def test_one_platform_is_its_own_total(self):
        self.forget("youtube", "instagram", "x")
        w = self.open(demo.platforms(unconnected=("youtube", "instagram", "x")))
        self.assertEqual(self.cards(w), ["tiktok"])

    def test_nothing_connected_opens_on_accounts(self):
        self.forget(*PLATFORM_KEYS)
        w = self.open(demo.platforms(unconnected=PLATFORM_KEYS), tab=None)
        self.assertEqual(w.init["tab"], "accounts")
        self.assertIn("No accounts connected yet", plain(w.view("header")["line"]))
        self.assertIn("Make a Gmail first", text(w.view("accounts")["hint"]))
        for key in PLATFORM_KEYS:
            panel = self.panel(w, key)
            self.assertEqual(panel["state"], "off")
            self.assertTrue(panel["signup"])
        self.assertEqual(self.cards(w), ["total"])
        self.assertIn("no accounts connected yet", plain(w.view("cards")["cards"][0]["message"]))
        self.assertEqual(w.view("trends")["metrics"]["views"]["empty"], NOTHING_CONNECTED)
        self.assertEqual(self.buttons(w), [])
        for tab in ("videos", "compare", "publish", "updates"):  # every tab copes with no platform at all
            w.call("tab_opened", tab)
        # ... and a table with nothing in it says where to go, in place of a row of headings over nothing.
        self.assertEqual(w.view("top")["empty"], NOTHING_CONNECTED)
        self.assertEqual((w.view("videos")["rows"], w.view("videos")["empty"]), ([], NOTHING_CONNECTED))
        # The Compare tab's scoreboard too: its three bars were headings with nothing under them, and no words.
        self.assertEqual(w.view("compare")["empty"], NOTHING_CONNECTED)

    def test_a_table_with_nothing_in_it_says_what_goes_there_and_what_to_do(self):
        """Connected, and nothing read yet (a new channel before its first video): each empty table says what to do
        next, and the chart under the Videos table doesn't ask you to pick a video there isn't."""
        with db.connect() as c:
            for table in ("account_stats", "post_stats", "posts", "uploads", "episodes"):
                c.execute(f"DELETE FROM {table}")
        w = self.open(demo.platforms())
        self.assertEqual(w.view("top")["rows"], [])
        self.assertIn("50 views", w.view("top")["empty"])
        w.call("tab_opened", "videos")
        self.assertEqual(w.view("videos")["rows"], [])
        self.assertIn("Post one from the Publish tab", w.view("videos")["empty"])
        self.assertNotIn("Pick a video", w.view("detail")["empty"])
        self.assertIn("once there is one above", w.view("detail")["empty"])
        w.call("set_filter", "youtube")
        self.assertIn("No videos read on YouTube yet", w.view("videos")["empty"])
        self.assertEqual(w.view("uploads")["rows"], [])
        self.assertIn("BY HAND", w.view("uploads")["empty"])

    def test_hiding_every_platform_empties_the_charts(self):
        """Every platform hidden in the chart keys: each chart says how to show one again, and showing one brings its
        numbers back."""
        w = self.open(demo.platforms())
        for key in list(w.svc.platforms):
            w.call("toggle_line", key)
        for metric in ("views", "followers", "likes"):
            self.assertIn("Every platform is hidden", w.view("trends")["metrics"][metric]["empty"])
        w.call("toggle_line", "tiktok")
        self.assertTrue(w.view("trends")["metrics"]["views"]["variants"])

    def test_the_followers_chart_doesnt_hatch_thinned_history(self):
        """Old readings are kept one a day (db.compact), a day and a few seconds apart: those days were measured, so
        the chart doesn't draw them as estimates. Days nothing was read on still are."""
        now = analytics.now()
        with db.connect() as c:
            c.execute("DELETE FROM account_stats WHERE platform = 'tiktok'")
            t, followers = now - timedelta(days=45), 1000
            while t < now - timedelta(days=27):
                c.execute("INSERT INTO account_stats (platform, ts, handle, followers) VALUES ('tiktok', ?, '@me', ?)",
                          (t.isoformat(timespec="seconds"), followers))
                t, followers = t + timedelta(days=1, seconds=40), followers + 5
            c.execute("INSERT INTO account_stats (platform, ts, handle, followers) VALUES ('tiktok', ?, '@me', 2000)",
                      (now.isoformat(timespec="seconds"),))
        w = self.open(demo.platforms())
        guessed = self.panel(w, "tiktok")["chart"]["guessed"]
        self.assertEqual(len(guessed), 30)
        self.assertFalse(guessed[0])  # 29 days ago: read the day before and the day after
        self.assertTrue(guessed[15])  # nothing was read for four weeks after that

    def test_sign_up_guides(self):
        w = self.open(demo.platforms(unconnected=("x",)))
        guide = w.call("signup_guide", "x")
        self.assertTrue(guide["account"])
        self.assertTrue(guide["steps"][0].startswith("Press Open X sign-up"))
        self.assertIn("one log-in", guide["steps"][-1])
        self.assertFalse(w.call("signup_guide", "gmail")["account"])
        opened = []
        with mock.patch.object(service_module.signup, "open_page", opened.append):
            w.call("open_signup", "x")
        self.assertEqual(opened, ["https://x.com/i/flow/signup"])

    def real_platforms(self, handles: dict[str, str]):
        """TikTok, YouTube, Instagram and X as they are, on a config.toml in the scratch folder."""
        path = self.root / "config.toml"
        shutil.copy(config.APP_DIR / "config.example.toml", path)
        self.logged_in: dict[str, bool] = {}
        for patch in (mock.patch.object(config, "CONFIG_PATH", path),
                      mock.patch.object(browser, "logged_in", lambda key: self.logged_in.get(key, False)),
                      mock.patch.object(Dashboard, "start_updates", lambda self: None)):  # nothing asks GitHub
            patch.start()
            self.addCleanup(patch.stop)
        for key, handle in handles.items():
            config.save_handle(key, handle)
        fresh = config.load_config()
        self.cfg.update({key: fresh[key] for key in PLATFORM_KEYS})
        return build(self.cfg)

    def test_connect_and_disconnect_in_place(self):
        """A handle and Connect saves it and switches the platform on everywhere; Disconnect hides it again and keeps
        its stats. Nothing is read from the internet here: the reads are stood in for."""
        self.forget("youtube", "instagram", "x")
        every = self.real_platforms({"tiktok": "@mine"})
        reads, logins = [], []

        def no_read(platforms, cfg, *args, **kwargs):
            reads.append(sorted(platforms))
            return sync.Result()

        with mock.patch.object(sync, "refresh", no_read), \
                mock.patch.object(Dashboard, "_browser_login", lambda self, p: logins.append(p.key)):
            w = self.open(every, demo_mode=False)
            self.assertEqual(list(w.svc.platforms), ["tiktok"])
            self.assertEqual(self.cards(w), ["tiktok"])  # one platform: its own card is the total
            # A typo first: nothing is saved, and the panel says why.
            self.assertIn("isn't a handle", w.call("connect", "youtube", "my channel!"))
            self.assertEqual(config.load_config()["youtube"].get("handle", ""), "")
            # A pasted link works.
            self.assertIsNone(w.call("connect", "youtube", "https://www.youtube.com/@NewChannel"))
            self.assertTrue(w.until(lambda: "youtube" in w.svc.platforms))
            self.assertEqual(config.load_config()["youtube"]["handle"], "@NewChannel")
            self.assertEqual(list(w.svc.platforms), ["tiktok", "youtube"])
            self.assertEqual(self.buttons(w), ["tiktok", "youtube"])
            self.assertEqual(self.panel(w, "youtube")["state"], "on")
            self.assertTrue(w.until(lambda: ["youtube"] in reads))  # its first read, straight away
            # Instagram: the handle, then its one log-in.
            self.assertIsNone(w.call("connect", "instagram", "@mine"))
            self.assertTrue(w.until(lambda: logins == ["instagram"]))
            self.assertEqual(self.panel(w, "instagram")["state"], "login")
            self.assertNotIn("instagram", w.svc.platforms)
            self.logged_in["instagram"] = True  # the log-in went through
            w.call("follow_connections")
            self.assertIn("instagram", w.svc.platforms)
            self.assertIn("instagram", self.cards(w))
            # X the same way.
            self.assertIsNone(w.call("connect", "x", "x.com/mine"))
            self.assertTrue(w.until(lambda: logins == ["instagram", "x"]))
            self.assertEqual(config.load_config()["x"]["handle"], "@mine")
            self.assertEqual(self.panel(w, "x")["state"], "login")
            self.assertNotIn("x", w.svc.platforms)
            # Disconnect TikTok: hidden everywhere, its stats kept.
            asked = w.call("disconnect_info", "tiktok")
            self.assertEqual(asked["title"], "DISCONNECT TIKTOK?")
            said = "".join(seg[0] for seg in asked["note"])
            self.assertIn("so connecting it again, on this tab with the same @handle, brings it all back.", said)
            self.assertNotIn("(", said)  # the window's word wrap ends a row right after an opening bracket
            w.call("disconnect", "tiktok")
            self.assertTrue(w.until(lambda: "tiktok" not in w.svc.platforms))
            self.assertEqual(config.load_config()["tiktok"]["handle"], "")
            self.assertNotIn("tiktok", self.cards(w))
            self.assertNotIn("tiktok", self.buttons(w))
            self.assertEqual(self.panel(w, "tiktok")["state"], "off")
            self.assertIn("tiktok", db.stored_platforms())

    def test_a_read_that_got_only_part_shows_on_its_panel(self):
        w = self.open(demo.platforms())
        said = "Only TikTok's totals were read this time, not its videos. TikTok's video list took too long to answer."

        def warned() -> bool:
            # (As a read leaves it: in this ReelForge's mind and shared with the others. A window that has just
            # opened may be taking the shared state as its own at this moment, which empties what is only in mind.)
            sync.last_warnings["tiktok"] = (db.now_iso(), said)
            sync._share(None)
            w.call("update_accounts")
            return said in plain(self.panel(w, "tiktok")["body"])

        self.assertTrue(w.until(warned))
        self.assertIn(said, plain(self.panel(w, "tiktok")["body"]))
        self.assertNotIn("Only TikTok's", plain(self.panel(w, "youtube")["body"]))
        self.assertNotIn("TikTok", plain(w.view("header")["line"]))  # it was read: not a failed refresh at the foot

        def failed() -> bool:
            sync.last_errors["tiktok"] = (db.now_iso(), "TikTok couldn't be reached (is this computer online?).")
            sync._share(None)
            w.call("update_accounts")  # a read that failed outright says only that
            return "last read failed" in plain(self.panel(w, "tiktok")["body"])

        self.assertTrue(w.until(failed))
        body = plain(self.panel(w, "tiktok")["body"])
        self.assertIn("last read failed: TikTok couldn't be reached", body)
        self.assertNotIn(said, body)

    def test_stats_from_before_a_handle_changed_stay_until_you_say(self):
        """TikTok was read as one account and is now read as another: the panel says so, the dialog says what's
        there from before, and only its Forget removes it, after a copy of the stats database."""
        self.forget("youtube", "instagram", "x")
        every = self.real_platforms({"tiktok": "@mine"})
        old = db.latest_accounts()["tiktok"]["handle"]  # the handle the stats so far were read under
        before = len([p for p in db.all_posts() if p["platform"] == "tiktok"])
        with mock.patch.object(sync, "refresh", lambda *a, **k: sync.Result()):
            w = self.open(every, demo_mode=False)
            self.assertEqual(self.panel(w, "tiktok")["old_handles"], [])
            self.assertIsNone(w.call("handle_change_info", "tiktok"))
            mine = Post("mine1", "My own video", None, db.now_iso(), views=500, likes=5, comments=0, shares=0)
            soon = (analytics.now() + timedelta(minutes=1)).isoformat(timespec="seconds")  # the next read
            self.assertEqual(db.record_snapshot("tiktok", Account("@mine", 120), [mine], soon), old)
            w.call("reload")
            panel = self.panel(w, "tiktok")
            self.assertEqual(panel["old_handles"], [old])
            self.assertIn(f"Also holds stats read as {old}, from before the handle changed.", plain(panel["body"]))
            info = w.call("handle_change_info", "tiktok")
            self.assertEqual((info["key"], info["title"], info["forget"], info["keep"]),
                             ("tiktok", f"TIKTOK WAS {old.upper()} BEFORE", f"Forget {old}'s stats", "Keep them"))
            note = plain(info["note"])
            self.assertIn(f"{before} videos that @mine doesn't have", note)
            self.assertIn("after ReelForge saves a copy of the whole stats database beside it", note)
            self.assertEqual(len(db.all_posts()), before + 1)  # asking forgets nothing
            w.forget()
            w.call("forget_handle_stats", "tiktok")
            self.assertTrue(w.until(lambda: any(t["title"] == f"Forgot {old}'s TikTok stats" for t in w.sent("toast"))))
            self.assertEqual([p["post_id"] for p in db.all_posts()], ["mine1"])
            copies = [f.name for f in self.root.iterdir() if f.name.endswith("-handle")]
            self.assertEqual(len(copies), 2)  # one from before the read went in, one from before this
            self.assertTrue(any(name in " ".join(w.toasts()) for name in copies))  # ... which the pop-up names
            panel = self.panel(w, "tiktok")
            self.assertEqual(panel["old_handles"], [])
            self.assertNotIn("Also holds stats", plain(panel["body"]))
            self.assertEqual([p["post_id"] for p in w.svc.posts], ["mine1"])

    def test_the_dialog_still_asks_once_the_old_readings_are_thinned_away(self):
        """The old handle's readings of the account go with time (db.compact keeps one a day, then one a week). Its
        videos don't, so the panel and the dialog are still there, and say what is."""
        self.forget("youtube", "instagram", "x")
        every = self.real_platforms({"tiktok": "@mine"})
        old = db.latest_accounts()["tiktok"]["handle"]
        before = len(db.all_posts())
        with mock.patch.object(sync, "refresh", lambda *a, **k: sync.Result()):
            w = self.open(every, demo_mode=False)
            soon = (analytics.now() + timedelta(minutes=1)).isoformat(timespec="seconds")
            db.record_snapshot("tiktok", Account("@mine", 120), [Post("mine1", "My own video", None, db.now_iso())],
                               soon)
            with db.connect() as c:
                c.execute("DELETE FROM account_stats WHERE platform = 'tiktok' AND handle = ?", (old,))
            w.call("reload")
            self.assertEqual(self.panel(w, "tiktok")["old_handles"], [old])
            note = plain(w.call("handle_change_info", "tiktok")["note"])
            self.assertIn(f"What it read then is still here: {before} videos that @mine doesn't have.", note)
            w.forget()
            w.call("forget_handle_stats", "tiktok")
            self.assertTrue(w.until(lambda: any(t.startswith(f"Removed: {before} videos. The copy from before is")
                                                for t in w.toasts())), w.toasts())
            self.assertEqual([p["post_id"] for p in db.all_posts()], ["mine1"])
            self.assertEqual(self.panel(w, "tiktok")["old_handles"], [])

    def test_keeping_the_stats_from_before_a_handle_changed(self):
        self.forget("youtube", "instagram", "x")
        every = self.real_platforms({"tiktok": "@mine"})
        before = len(db.all_posts())
        with mock.patch.object(sync, "refresh", lambda *a, **k: sync.Result()):
            w = self.open(every, demo_mode=False)
            soon = (analytics.now() + timedelta(minutes=1)).isoformat(timespec="seconds")
            db.record_snapshot("tiktok", Account("@mine", 120), [Post("mine1", "My own video", None, db.now_iso())],
                               soon)
            w.call("reload")
            self.assertTrue(self.panel(w, "tiktok")["old_handles"])
            w.call("keep_handle_stats", "tiktok")
            self.assertEqual(self.panel(w, "tiktok")["old_handles"], [])
            self.assertIn("Kept: they count as @mine's from now on.", w.toasts())
            self.assertEqual(len(db.all_posts()), before + 1)

    def test_log_out_forgets_the_log_in_and_keeps_the_platform(self):
        """Instagram and X: ReelForge's own browser can be logged out (it never could be). The platform stays, with
        its stats; Disconnect says the log-in stays unless it's asked to take it along."""
        every = self.real_platforms({"tiktok": "@mine", "instagram": "@mine"})
        self.logged_in["instagram"] = True
        done = []

        def logout(key: str) -> None:
            done.append(key)
            self.logged_in[key] = False

        with mock.patch.object(sync, "refresh", lambda *a, **k: sync.Result()), \
                mock.patch.object(browser, "logout", logout):
            w = self.open(every, demo_mode=False)
            self.assertTrue(self.panel(w, "instagram")["logs_out"])
            self.assertFalse(self.panel(w, "tiktok")["logs_out"])  # nothing to log out of
            self.assertIsNone(w.call("logout_info", "tiktok"))
            info = w.call("logout_info", "instagram")
            self.assertEqual((info["key"], info["title"], info["button"]),
                             ("instagram", "LOG OUT OF INSTAGRAM?", "Log out"))
            note = plain(info["note"])
            self.assertIn("ReelForge's own browser is logged out of Instagram", note)
            self.assertIn("Your normal browser and your phone stay logged in.", note)
            self.assertIn("Instagram stays connected, with everything read so far.", note)
            w.call("logout", "instagram")
            self.assertTrue(w.until(lambda: any(t["title"] == "Instagram logged out" for t in w.sent("toast"))))
            self.assertEqual(done, ["instagram"])
            self.assertIn("instagram", w.svc.platforms)  # the stats it has keep it on
            panel = self.panel(w, "instagram")
            self.assertFalse(panel["logs_out"])
            self.assertIn("log in again to keep tracking", plain(panel["status"]))
            # Disconnect: the log-in stays, and the dialog says so; asked to, it logs out as well.
            self.logged_in["instagram"] = True
            w.call("follow_connections")
            info = w.call("disconnect_info", "instagram")
            self.assertTrue(info["logged_in"])
            self.assertIn("ReelForge's own browser stays logged in to Instagram", plain(info["note"]))
            self.assertFalse(w.call("disconnect_info", "tiktok")["logged_in"])
            w.call("disconnect", "instagram", True)
            self.assertTrue(w.until(lambda: done == ["instagram", "instagram"] and "instagram" not in w.svc.platforms))
            self.assertEqual(config.load_config()["instagram"]["handle"], "")
        # A log-out the browser couldn't do says so in a sentence, and the log-in stays.
        every = self.real_platforms({"x": "@mine"})
        self.logged_in["x"] = True
        with mock.patch.object(sync, "refresh", lambda *a, **k: sync.Result()), \
                mock.patch.object(browser, "logout", side_effect=OSError("profile is locked")):
            w = self.open(every, demo_mode=False)
            w.call("logout", "x")
            self.assertTrue(w.until(lambda: any(t["title"] == "X still logged in" for t in w.sent("toast"))))
            self.assertIn("ReelForge's browser window didn't work for the log-out. Try again in a moment; "
                          "data/refresh.log says what stopped it.", w.toasts())
            self.assertNotIn("profile is locked", str(w.loop.events))
        self.assertIn("error: the log-out in ReelForge's browser: OSError: profile is locked",
                      sync.log_path().read_text("utf-8"))

    def test_a_log_in_the_browser_couldnt_do_is_said_plainly(self):
        """It says so in a sentence, and the error's own words go to the log. And a second press of Log in while the
        window is open doesn't queue another window behind it."""
        every = self.real_platforms({"instagram": "@mine"})
        started, finish = threading.Event(), threading.Event()

        def login(key: str) -> None:
            started.set()
            finish.wait(10)
            raise RuntimeError("Target crashed\nCall log:\n  - navigating to login")

        with mock.patch.object(sync, "refresh", lambda *a, **k: sync.Result()), \
                mock.patch.object(browser, "login", login):
            w = self.open(every, demo_mode=False)
            w.call("login", "instagram")
            self.assertTrue(started.wait(10))
            w.forget()
            w.call("login", "instagram")  # pressed again, the window still open
            self.assertEqual(w.toasts(), ["ReelForge's browser window is already open for Instagram. Finish there, or "
                                          "close that window first."])
            finish.set()
            self.assertTrue(w.until(lambda: any(t["title"] == "Instagram not logged in" for t in w.sent("toast"))))
            self.assertIn("ReelForge's browser window didn't work for the log-in. Try again in a moment; "
                          "data/refresh.log says what stopped it.", w.toasts())
            self.assertEqual(w.svc.logging_in, "")
            self.assertNotIn("Target crashed", str(w.loop.events))
        self.assertIn("error: the log-in in ReelForge's browser: RuntimeError: Target crashed | Call log: | "
                      "- navigating to login", sync.log_path().read_text("utf-8"))

    def test_a_handle_that_cant_be_saved_says_so_plainly(self):
        every = self.real_platforms({"tiktok": "@mine"})
        denied = PermissionError(13, "Permission denied", "config.toml")
        with mock.patch.object(sync, "refresh", lambda *a, **k: sync.Result()):
            w = self.open(every, demo_mode=False)
            w.forget()
            with mock.patch.object(config, "save_handle", side_effect=denied):
                self.assertIsNone(w.call("connect", "youtube", "@new"))
                self.assertTrue(w.until(lambda: w.toasts()))
            self.assertEqual(w.toasts(), ["YouTube's handle wasn't saved. ReelForge couldn't write your settings; "
                                          "data/refresh.log says what stopped it."])
            self.assertNotIn("youtube", w.svc.platforms)
            self.assertEqual(config.load_config()["youtube"].get("handle", ""), "")
            w.forget()
            with mock.patch.object(config, "save_handle", side_effect=denied):
                w.call("disconnect", "tiktok")
                self.assertTrue(w.until(lambda: w.toasts()))
            self.assertEqual(w.toasts(), ["TikTok wasn't disconnected. ReelForge couldn't write your settings; "
                                          "data/refresh.log says what stopped it."])
            self.assertIn("tiktok", w.svc.platforms)
        self.assertIn("error: saving YouTube's handle: PermissionError: [Errno 13] Permission denied: 'config.toml'",
                      sync.log_path().read_text("utf-8"))

    def test_a_handle_saved_part_way_says_what_is_true(self):
        """The handle goes into several files in turn. When one after config.toml can't be written, the handle is
        in config.toml and the platform connects a moment later: it used to say "wasn't saved, and nothing changed.
        config.toml couldn't be written." all the same."""
        every = self.real_platforms({"tiktok": "@mine"})
        save = config.save_handle

        def part_way(key: str, handle: str) -> None:
            save(key, handle)  # config.toml has it
            raise OSError(28, "No space left on device", "AGENTS.md")

        with mock.patch.object(sync, "refresh", lambda *a, **k: sync.Result()):
            w = self.open(every, demo_mode=False)
            w.forget()
            with mock.patch.object(config, "save_handle", part_way):
                self.assertIsNone(w.call("connect", "youtube", "@new"))
                self.assertTrue(w.until(lambda: "youtube" in w.svc.platforms))
            self.assertIn("YouTube's handle is saved, but ReelForge couldn't finish updating your channel's other "
                          "files (your AI's notes among them). data/refresh.log says what stopped it.", w.toasts())
            self.assertFalse([t for t in w.toasts() if "wasn't saved" in t])
            self.assertEqual(self.panel(w, "youtube")["state"], "on")
        self.assertIn("error: saving YouTube's handle: OSError: [Errno 28] No space left on device: 'AGENTS.md'",
                      sync.log_path().read_text("utf-8"))

    def test_a_handle_in_config_toml_that_isnt_one_is_said_on_its_panel(self):
        """A link pasted into config.toml as a handle counts as none (Platform.public_handle). The platform used
        to show as not connected and nothing said why."""
        self.forget("youtube", "instagram", "x")
        every = self.real_platforms({"tiktok": "@mine"})
        every["youtube"].cfg["handle"] = "https://www.youtube.com/@mine/shorts"  # typed into config.toml by hand
        with mock.patch.object(sync, "refresh", lambda *a, **k: sync.Result()):
            w = self.open(every, demo_mode=False)
            panel = self.panel(w, "youtube")
            self.assertEqual(panel["state"], "off")
            self.assertIn("config.toml has https://www.youtube.com/@mine/shorts as your YouTube handle, which isn't "
                          "one ReelForge can use, so it counts as none. Type your @handle and press Connect",
                          plain(panel["body"]))
            self.assertNotIn("config.toml", plain(self.panel(w, "x")["body"]))  # no handle at all: nothing to say

    def test_a_typed_in_number_that_goes_down_is_asked_about_first(self):
        """Type in stats: a number lower than the one saved replaces it, so Save says so first and stores it when
        pressed again. A slip the wrong way (3,400 where 34,000 was meant) is seen before it costs anything."""
        self.forget("youtube", "instagram", "x")
        every = self.real_platforms({"tiktok": "@mine", "instagram": "@mine"})
        with mock.patch.object(sync, "refresh", lambda *a, **k: sync.Result()):
            w = self.open(every, demo_mode=False)
            eid = str(w.call("log_stats", "instagram")["episodes"][0]["id"])
            typed = lambda: [p["views"] for p in db.posts_latest() if p["platform"] == "instagram"]  # noqa: E731

            def readings() -> list[int]:
                with db.connect() as c:
                    return [r[0] for r in c.execute("SELECT views FROM post_stats WHERE platform = 'instagram' AND "
                                                    "post_id = ? ORDER BY ts, rowid", (f"manual-{eid}",))]

            self.assertIsNone(w.call("save_logged_stats", "instagram", "120", {eid: "32,000"}))
            self.assertEqual(typed(), [32_000])
            self.assertEqual(w.call("log_stats", "instagram")["last"]["instagram"]["views"], {eid: 32_000})
            asks = ("3,400 is lower than the 32,000 saved before. Press Save again if 3,400 is right: it replaces "
                    "the 32,000.")
            self.assertEqual(w.call("save_logged_stats", "instagram", "120", {eid: "3400"}), asks)
            self.assertEqual(typed(), [32_000])  # nothing is stored yet
            self.assertEqual(w.call("save_logged_stats", "instagram", "120", {eid: "3500"}),
                             asks.replace("3,400", "3,500"))  # another number: asked about again
            self.assertEqual(readings(), [32_000])
            self.assertIsNone(w.call("save_logged_stats", "instagram", "120", {eid: "3500"}))  # Save again
            self.assertEqual(typed(), [3_500])
            self.assertEqual(readings(), [3_500, 3_500])  # the 32,000 it replaces, and the new reading
            self.assertIsNone(w.call("save_logged_stats", "instagram", "125", {eid: "3,600"}))  # up: nothing to ask
            self.assertEqual(typed(), [3_600])
            # Save pressed again while the database is busy: that's said, and the next press doesn't ask again.
            self.assertIn("3,000 is lower than the 3,600", w.call("save_logged_stats", "instagram", "", {eid: "3000"}))
            with mock.patch.object(db, "record_snapshot", side_effect=sqlite3.OperationalError("database is locked")):
                self.assertEqual(w.call("save_logged_stats", "instagram", "", {eid: "3000"}),
                                 "The stats database is busy (database is locked). Try again in a moment.")
            self.assertIsNone(w.call("save_logged_stats", "instagram", "", {eid: "3000"}))
            self.assertEqual(typed(), [3_000])
            other = str(w.call("log_stats", "instagram")["episodes"][1]["id"])
            self.assertIsNone(w.call("save_logged_stats", "instagram", "", {eid: "3,600", other: "900"}))
            self.assertEqual(w.call("save_logged_stats", "instagram", "", {eid: "360", other: "90"}),
                             "2 numbers are lower than the ones saved before. Press Save again if they're right: "
                             "they replace those.")
            self.assertEqual(sorted(typed()), [900, 3_600])

    def test_another_window_connecting_shows_here(self):
        """Every ReelForge window follows a platform connected (or disconnected) in another one."""
        self.forget("x")
        every = self.real_platforms({"tiktok": "@mine", "youtube": "@mine", "instagram": "@mine"})
        self.logged_in.update(instagram=True, x=True)
        with mock.patch.object(sync, "refresh", lambda *a, **k: sync.Result()):
            w = self.open(every, demo_mode=False)
            self.assertNotIn("x", w.svc.platforms)
            config.save_handle("x", "@mine")  # as another window's Connect does
            w.svc.config_seen = None  # (the file's time may not have moved within this second)
            self.assertTrue(w.until(lambda: "x" in w.svc.platforms))
            self.assertIn("x", self.cards(w))


if __name__ == "__main__":
    unittest.main()
