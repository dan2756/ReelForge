"""Posts matched to episodes (analytics.auto_link) and the store under it (dashboard.db): links, totals, backups."""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import time
import unittest
from collections import Counter
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from dashboard import analytics, db, notify
from dashboard.platforms import PlatformError, detail

T0 =datetime(2026, 10, 23, 4, 0, tzinfo=timezone.utc)
EPISODES = r"C:\Users\me\Desktop\my channel\episodes"
# The five episodes so far: title, video slug, minutes after T0 that they went up.
REAL = {
    "kite": ("What if your kite's speed doubled every 2 seconds?", "ep02_kite_doubling", 0),
    "jump": ("What if your jump's height doubled every 2 seconds?", "ep03_jump_doubling", 420),
    "snowball": ("What if your snowball doubled in size every 2 seconds?", "ep04_snowball_doubling", 430),
    "savings": ("What if your savings doubled every second?", "ep05_savings_doubling", 1200),
    "subscribers": ("What if your subscribers doubled every second?", "ep06_subscribers_doubling", 1205),
}
# How each platform's caption reads once it's posted.
CAPTIONS = {"tiktok": "{} #fyp#whatif#space#physics#simulation", "instagram": "{} #fyp #whatif #space #physics",
            "youtube": "{} #shorts", "x": "{} https://t.co/0000000000"}
ALL_FOUR = Counter(dict.fromkeys(CAPTIONS, 1))
X_SNOWBALL = "1999999999999999999"  # the snowball video on X, captioned like the jump one; linked by hand


def at(minutes: float) -> str:
    return (T0 + timedelta(minutes=minutes)).isoformat(timespec="seconds")


def video(slug: str) -> str:
    return f"{EPISODES}\\{slug}\\final\\{slug}.mp4"


def post(post_id: str, title: str, minutes: float, views: int | None = None, likes: int | None = None):
    return SimpleNamespace(post_id=post_id, title=title, url=None, published_at=at(minutes), views=views, likes=likes,
                           comments=None, shares=None, saves=None)


def read(platform: str, posts: list, views: int | None = None, likes: int | None = None,
         minutes: float | None = None) -> None:
    """One refresh of a platform, stored the way sync.refresh stores it."""
    account = SimpleNamespace(handle="@yourchannel", followers=100, views=views, likes=likes, posts=None)
    db.record_snapshot(platform, account, posts, at(minutes) if minutes is not None else None)


def where(platform: str, post_id: str) -> tuple:
    """(episode_id, link) of a post."""
    with db.connect() as c:
        return tuple(c.execute("SELECT episode_id, link FROM posts WHERE platform = ? AND post_id = ?",
                               (platform, post_id)).fetchone())


def per_episode() -> dict[int, Counter]:
    """Episode id -> how many of its posts each platform has."""
    out = {e["id"]: Counter() for e in db.episodes()}
    for p in db.linked_posts():
        out.setdefault(p["episode_id"], Counter())[p["platform"]] += 1
    return out


def real_world(order: tuple[str, ...] = tuple(REAL)) -> dict[str, int]:
    """The five episodes, created in `order`, and their 20 posts as the first refresh stores them: none linked."""
    ids = {name: db.create_episode(REAL[name][0], video(REAL[name][1]), at(-60 + i)) for i, name in enumerate(order)}
    for k, platform in enumerate(CAPTIONS):
        posts = []
        for name, (title, _, minutes) in REAL.items():
            if (platform, name) == ("x", "snowball"):
                posts.append(post(X_SNOWBALL, CAPTIONS["x"].format(REAL["jump"][0]), 1050))
            else:
                posts.append(post(f"{platform}-{name}", CAPTIONS[platform].format(title), minutes + k))
        read(platform, posts)
    return ids


class DbTest(unittest.TestCase):
    """Each test gets a fresh database in a temporary folder."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        self.addCleanup(setattr, db, "_path", db._path)
        self.dir = Path(tmp.name)
        db.init(self.dir / "stats.db")


class RealEpisodesTest(DbTest):
    def assert_in_place(self, ids: dict[str, int]) -> None:
        for name, eid in ids.items():
            for platform in CAPTIONS:
                post_id = X_SNOWBALL if (platform, name) == ("x", "snowball") else f"{platform}-{name}"
                self.assertEqual(where(platform, post_id)[0], eid, f"{platform} {name}")
        self.assertEqual(list(per_episode().values()), [ALL_FOUR] * 5)

    def test_subscribers_episode_created_first(self):
        ids = real_world(("subscribers", "savings", "snowball", "jump", "kite"))
        db.set_post_episode("x", X_SNOWBALL, ids["snowball"])
        self.assertEqual(analytics.auto_link(), 19)
        self.assert_in_place(ids)
        self.assertEqual(analytics.auto_link(), 0)

    def test_savings_posts_filed_under_subscribers_move_back(self):
        ids = real_world()
        db.set_post_episode("x", X_SNOWBALL, ids["snowball"])
        analytics.auto_link()
        for platform in CAPTIONS:  # what an older first-match version did: the titles are 84% alike
            db.link_post(platform, f"{platform}-savings", ids["subscribers"])
        self.assertEqual(per_episode()[ids["subscribers"]], ALL_FOUR + ALL_FOUR)
        self.assertEqual(analytics.auto_link(), 4)
        self.assert_in_place(ids)
        self.assertEqual(where("tiktok", "tiktok-savings"), (ids["savings"], "caption"))
        self.assertEqual(analytics.auto_link(), 0)

    def test_hand_link_never_moves(self):
        ids = real_world()
        db.set_post_episode("x", X_SNOWBALL, ids["snowball"])
        for _ in range(3):
            analytics.auto_link()
            self.assertEqual(where("x", X_SNOWBALL), (ids["snowball"], "hand"))
        self.assertEqual(where("x", "x-jump"), (ids["jump"], "caption"))

    def test_second_post_on_a_platform_does_not_double_link(self):
        ids = real_world()
        analytics.auto_link()
        # Without the hand link, the jump-captioned snowball post can't join jump: it has its X post.
        self.assertEqual(where("x", X_SNOWBALL), (None, None))
        read("tiktok", [post("tiktok-kite-again", CAPTIONS["tiktok"].format(REAL["kite"][0]), 3000)])
        self.assertEqual(analytics.auto_link(), 0)
        self.assertEqual(where("tiktok", "tiktok-kite-again"), (None, None))
        # An older version linked both; the first post keeps its place and the other one leaves.
        db.link_post("tiktok", "tiktok-kite-again", ids["kite"])
        self.assertEqual(analytics.auto_link(), 1)
        self.assertEqual(where("tiktok", "tiktok-kite"), (ids["kite"], "caption"))
        self.assertEqual(where("tiktok", "tiktok-kite-again"), (None, None))
        self.assertEqual(per_episode()[ids["kite"]], ALL_FOUR)
        self.assertEqual(analytics.auto_link(), 0)

    def test_not_an_episode_stays_unlinked(self):
        ids = real_world()
        analytics.auto_link()
        db.set_post_episode("tiktok", "tiktok-kite", None)
        for _ in range(2):
            self.assertEqual(analytics.auto_link(), 0)
            self.assertEqual(where("tiktok", "tiktok-kite"), (None, "hand"))
        self.assertNotIn("tiktok-kite", [p["post_id"] for p in db.unlinked_posts()])
        db.link_post("tiktok", "tiktok-kite", None)  # back to automatic
        self.assertIn("tiktok-kite", [p["post_id"] for p in db.unlinked_posts()])
        self.assertEqual(analytics.auto_link(), 1)
        self.assertEqual(where("tiktok", "tiktok-kite"), (ids["kite"], "caption"))

    def test_posts_linked_when_published_are_left_alone(self):
        ids = real_world()
        db.add_post("facebook", "pushed", ids["snowball"], REAL["snowball"][0], None, at(500))
        read("facebook", [post("pushed", REAL["kite"][0], 500)])  # its caption now reads like the kite video
        analytics.auto_link()
        self.assertEqual(where("facebook", "pushed"), (ids["snowball"], "publish"))

    def test_equal_scores_keep_the_episode_a_post_is_in(self):
        ids = real_world()
        analytics.auto_link()
        read("facebook", [post("fb", "What if your subscribers' savings doubled every second?", 3000)])
        self.assertEqual(analytics.auto_link(), 0)  # it names both, so neither
        self.assertEqual(where("facebook", "fb"), (None, None))
        for name in ("savings", "subscribers"):  # never moved to the other one, whichever id is higher
            db.link_post("facebook", "fb", ids[name])
            self.assertEqual(analytics.auto_link(), 0)
            self.assertEqual(where("facebook", "fb"), (ids[name], "caption"))

    def test_same_title_twice_keeps_each_post_where_it_is(self):
        title = REAL["kite"][0]
        first = db.create_episode(title, video("ep02_kite_doubling"), at(-60))
        again = db.create_episode(title, r"C:\x\completed videos\ep02 - kite\ep02_kite_doubling.mp4", at(-30))
        read("tiktok", [post("tt", title, 0)])
        read("youtube", [post("yt", title, 1)])
        db.link_post("tiktok", "tt", again)
        self.assertEqual(analytics.auto_link(), 1)
        self.assertEqual(where("tiktok", "tt"), (again, "caption"))
        self.assertEqual(where("youtube", "yt"), (first, "caption"))  # a new post takes the older one
        self.assertEqual(analytics.auto_link(), 0)


class CaptionTest(unittest.TestCase):
    """Rewordings name their episode; new videos in the same template name none (see MATCH_SCORE)."""

    def setUp(self) -> None:
        episodes = [{"id": i, "title": t, "video": video(v), "created_at": at(m)} for i, (t, v, m) in
                    enumerate(REAL.values(), 1)]
        self.matcher = analytics._Matcher(episodes)
        self.ids = dict(zip(REAL, range(1, 6)))

    def named(self, caption: str) -> list[int]:
        return self.matcher.match(analytics._key(caption), analytics._words(caption))[0]

    def test_first_line_ignores_hashtags_links_and_apostrophes(self):
        self.assertEqual(analytics._key("What if your kite's speed doubled every 2 seconds?#fyp#whatif#spa\nmore"),
                         analytics._key("what if your kites speed doubled every 2 seconds https://t.co/x"))

    def test_rewordings_name_their_episode(self):
        for caption, name in (
                ("What if your kites speed doubled every 2 secs? Wait for the end #fyp#whatif#spa", "kite"),
                ("POV: your kite’s speed doubles every two seconds", "kite"),
                ("Your jump height doubles every 2 seconds", "jump"),
                ("What if a snowball doubled in size every 2 seconds?! #fyp", "snowball"),
                ("What if your savings doubled every single second? It gets crazy", "savings"),
                ("What if your life-savings kept doubling every second?", "savings"),
                ("What if your subscriber count doubled every second?", "subscribers"),
                ("subscribers doubling", "subscribers")):
            self.assertEqual(self.named(caption), [self.ids[name]], caption)

    def test_new_videos_and_mixed_captions_name_nothing(self):
        for caption in ("What if your pizza doubled every 2 seconds?", "What if gravity doubled every second?",
                        "What if your house doubled in size every 2 seconds?",
                        "What if your bank account doubled every second?",
                        "What if your subscribers' savings doubled every second?", "doubled every 2 seconds",
                        "Follow for more", "#fyp #whatif", ""):
            self.assertEqual(self.named(caption), [], caption)


class UploadWindowTest(DbTest):
    def opened(self, platform: str, file_name: str, minutes: float) -> None:
        """The owner opened a platform's upload page for a video (as ReelForge logs it)."""
        with db.connect() as c:
            c.execute("INSERT INTO uploads (ts, platform, video, status, message) VALUES (?,?,?,?,?)",
                      (at(minutes), platform, file_name, "manual", "opened the upload page"))

    def test_empty_caption_links_by_file_name_from_another_folder(self):
        shipped = (r"C:\Users\me\Desktop\my channel\completed videos\ep05_savings_doubling - What if your "
                   r"savings doubled every second\ep05_savings_doubling.mp4")
        worth = db.create_episode(REAL["savings"][0], shipped, at(-30))
        db.create_episode(REAL["snowball"][0], video("ep04_snowball_doubling"), at(-60))
        self.opened("instagram", "ep04_snowball_doubling.mp4", -40)  # not the last one before the post
        self.opened("instagram", "ep05_savings_doubling.mp4", -10)
        self.opened("tiktok", "ep04_snowball_doubling.mp4", -5)  # another platform
        read("instagram", [post("ig", "", 0)])
        self.assertEqual(analytics.auto_link(), 1)
        self.assertEqual(where("instagram", "ig"), (worth, "upload"))
        self.assertEqual(analytics.auto_link(), 0)

    def test_window_is_six_hours_before_or_half_an_hour_after(self):
        worth = db.create_episode(REAL["savings"][0], video("ep05_savings_doubling"), at(-30))
        self.opened("instagram", "ep05_savings_doubling.mp4", 0)
        read("instagram", [post("late", "", 6 * 60 + 5), post("early", "", -20)])
        analytics.auto_link()
        self.assertEqual(where("instagram", "early"), (worth, "upload"))  # opened just after it (clock skew)
        self.assertEqual(where("instagram", "late"), (None, None))  # over 6 hours later


class TwinsTest(DbTest):
    def test_new_video_on_two_platforms_gets_one_episode(self):
        real_world()
        analytics.auto_link()
        count = len(db.episodes())
        read("tiktok", [post("tt-pizza", "What if your pizza doubled every 2 seconds? #fyp #whatif", 5000)])
        read("youtube", [post("yt-pizza", "What if your pizza doubled every 2 seconds? #shorts", 5003)])
        self.assertEqual(analytics.auto_link(), 2)
        self.assertEqual(len(db.episodes()), count + 1)
        pizza = next(e for e in db.episodes() if "pizza" in e["title"])
        self.assertEqual(pizza["title"], "What if your pizza doubled every 2 seconds?")
        self.assertEqual(where("tiktok", "tt-pizza"), (pizza["id"], "twin"))
        self.assertEqual(where("youtube", "yt-pizza"), (pizza["id"], "twin"))
        self.assertEqual(analytics.auto_link(), 0)
        # The X post four days later joins it by its caption.
        read("x", [post("x-pizza", "What if your pizza doubled every 2 seconds? https://t.co/p", 5000 + 4 * 1440)])
        self.assertEqual(analytics.auto_link(), 1)
        self.assertEqual(where("x", "x-pizza"), (pizza["id"], "caption"))
        self.assertEqual(len(db.episodes()), count + 1)

    def test_one_platform_alone_makes_no_episode(self):
        caption = "What if your pizza doubled every 2 seconds?"
        read("tiktok", [post("a", caption, 0), post("b", caption, 60)])
        self.assertEqual(analytics.auto_link(), 0)
        self.assertEqual(db.episodes(), [])

    PIZZA = "What if your pizza doubled every 2 seconds?"

    def pizza_twin(self) -> int:
        """The pizza video posted on two platforms before ReelForge knew its file: an episode with no video."""
        read("tiktok", [post("tt-pizza", f"{self.PIZZA} #fyp #whatif", 5000)])
        read("youtube", [post("yt-pizza", f"{self.PIZZA} #shorts", 5003)])
        analytics.auto_link()
        (twin,) = db.episodes()
        self.assertIsNone(twin["video"])
        return twin["id"]

    def test_starting_to_post_a_video_gives_its_twin_the_video(self):
        twin = self.pizza_twin()
        file = video("ep07_pizza_doubling")
        self.assertEqual(db.adopt_episode(self.PIZZA, file), twin)  # the caption as the Publish form has it
        self.assertEqual([(e["id"], e["video"]) for e in db.episodes()], [(twin, file)])
        self.assertEqual(db.episode_for_video("ep07_pizza_doubling.mp4")["id"], twin)
        self.assertEqual(db.adopt_episode("Reworded since", file), twin)  # by its file from then on
        self.assertEqual(where("tiktok", "tt-pizza"), (twin, "twin"))
        # Another video gets an episode of its own, as it always did.
        other = db.adopt_episode("What if your kite doubled?", video("ep08_kite"))
        self.assertNotEqual(other, twin)
        self.assertEqual(len(db.episodes()), 2)

    def test_a_twin_does_not_take_a_video_made_after_its_posts(self):
        twin = self.pizza_twin()
        went_up = (T0 + timedelta(minutes=5000)).timestamp()
        remake = self.dir / "ep09_pizza_again.mp4"  # the same caption on a video made a week after those posts
        remake.write_bytes(b"video")
        os.utime(remake, (went_up + 7 * 86400,) * 2)
        self.assertNotEqual(db.adopt_episode(self.PIZZA, str(remake)), twin)
        first = self.dir / "ep07_pizza_doubling.mp4"  # the file those posts were made from
        first.write_bytes(b"video")
        os.utime(first, (went_up - 3600,) * 2)
        self.assertEqual(db.adopt_episode(self.PIZZA, str(first)), twin)

    def test_a_new_post_goes_to_the_episode_with_the_video(self):
        twin = self.pizza_twin()
        double = db.create_episode(self.PIZZA, video("ep07_pizza_doubling"), at(5020))  # as older versions made it
        read("x", [post("x-pizza", f"{self.PIZZA} https://t.co/p", 5030)])
        self.assertEqual(analytics.auto_link(), 1)
        self.assertEqual(where("x", "x-pizza"), (double, "caption"))
        self.assertEqual(where("tiktok", "tt-pizza"), (twin, "twin"))  # a post stays where it is

    def test_double_episodes_from_older_versions_are_merged_once(self):
        twin = self.pizza_twin()
        file = video("ep07_pizza_doubling")
        double = db.create_episode(self.PIZZA, file, at(5020))  # the upload page opened 20 minutes after the posts
        kite = db.create_episode("What if your kite doubled?", video("ep08_kite"), at(5021))  # not posted yet: stays
        with db.connect() as c:
            c.execute("DELETE FROM meta WHERE key = ?", (db.TWINS_MERGED,))  # a database from an older version
        db.init(self.dir / "stats.db")
        self.assertEqual({e["id"]: e["video"] for e in db.episodes()}, {twin: file, kite: video("ep08_kite")})
        self.assertEqual(where("tiktok", "tt-pizza"), (twin, "twin"))
        backups = list(self.dir.glob("stats.db.bak-*-twins"))
        self.assertEqual(len(backups), 1)
        with closing(sqlite3.connect(backups[0])) as c:  # taken before the change
            self.assertEqual(c.execute("SELECT COUNT(*) FROM episodes").fetchone()[0], 3)
        self.assertNotIn(double, [e["id"] for e in db.episodes()])
        # Once: a double made afterwards is left for you to see, and nothing is backed up again.
        again = db.create_episode(self.PIZZA, r"C:\x\ep10_pizza.mp4", at(6000))
        db.init(self.dir / "stats.db")
        self.assertIn(again, [e["id"] for e in db.episodes()])
        self.assertEqual(list(self.dir.glob("stats.db.bak-*")), backups)

    def test_the_merge_leaves_an_episode_with_posts_of_its_own(self):
        twin = self.pizza_twin()
        other = db.create_episode(self.PIZZA, video("ep07_pizza_doubling"), at(5020))
        db.add_post("x", "x-pizza", other, self.PIZZA, None, at(5030))
        with db.connect() as c:
            c.execute("DELETE FROM meta WHERE key = ?", (db.TWINS_MERGED,))
        db.init(self.dir / "stats.db")
        self.assertEqual(sorted(e["id"] for e in db.episodes()), [twin, other])
        self.assertEqual(list(self.dir.glob("stats.db.bak-*")), [])


OLD_SCHEMA = """
CREATE TABLE episodes (id INTEGER PRIMARY KEY, title TEXT NOT NULL, video TEXT, created_at TEXT NOT NULL);
CREATE TABLE posts (platform TEXT NOT NULL, post_id TEXT NOT NULL, episode_id INTEGER REFERENCES episodes(id),
                    title TEXT, url TEXT, published_at TEXT, PRIMARY KEY (platform, post_id));
CREATE TABLE post_stats (platform TEXT NOT NULL, post_id TEXT NOT NULL, ts TEXT NOT NULL,
                         views INTEGER, likes INTEGER, comments INTEGER, shares INTEGER, saves INTEGER);
CREATE TABLE account_stats (platform TEXT NOT NULL, ts TEXT NOT NULL, handle TEXT,
                            followers INTEGER, views INTEGER, likes INTEGER, posts INTEGER);
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE uploads (id INTEGER PRIMARY KEY, ts TEXT NOT NULL, platform TEXT NOT NULL, video TEXT, status TEXT,
                      post_id TEXT, url TEXT, message TEXT);
"""


class MigrationTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        self.addCleanup(setattr, db, "_path", db._path)
        self.dir = Path(tmp.name)

    def test_old_database_is_backed_up_once_and_its_links_marked(self):
        path = self.dir / "stats.db"
        with closing(sqlite3.connect(path)) as c:
            c.execute("PRAGMA journal_mode=WAL")
            c.executescript(OLD_SCHEMA)
            ids = {name: c.execute("INSERT INTO episodes (title, video, created_at) VALUES (?,?,?)",
                                   (title, video(slug), at(minutes - 5))).lastrowid
                   for name, (title, slug, minutes) in REAL.items()}
            for platform in CAPTIONS:
                for name, (title, _, minutes) in REAL.items():
                    post_id, caption = f"{platform}-{name}", CAPTIONS[platform].format(title)
                    if (platform, name) == ("x", "snowball"):
                        post_id, caption = X_SNOWBALL, CAPTIONS["x"].format(REAL["jump"][0])
                    c.execute("INSERT INTO posts VALUES (?,?,?,?,?,?)",
                              (platform, post_id, ids[name], caption, None, at(minutes)))
                    c.execute("INSERT INTO post_stats VALUES (?,?,?,?,?,?,?,?)",
                              (platform, post_id, at(minutes + 60), 100, 5, 1, 0, None))
            c.execute("INSERT INTO posts VALUES ('tiktok', 'other', NULL, 'Follow for more', NULL, ?)", (at(9),))
            c.commit()
        db.init(path)
        backups = list(self.dir.glob("stats.db.bak-*-schema"))
        self.assertEqual(len(backups), 1)
        with closing(sqlite3.connect(backups[0])) as c:  # taken before the change
            self.assertNotIn("link", [r[1] for r in c.execute("PRAGMA table_info(posts)")])
        with db.connect() as c:
            rows = {r["post_id"]: r for r in c.execute("SELECT * FROM posts")}
        self.assertEqual(rows[X_SNOWBALL]["link"], "hand")
        self.assertEqual(Counter(r["link"] for r in rows.values()), Counter({"caption": 19, "hand": 1, None: 1}))
        self.assertEqual((rows["tiktok-kite"]["views"], rows["tiktok-kite"]["last_ts"]), (100, at(60)))
        self.assertEqual(analytics.auto_link(), 0)
        db.init(path)
        self.assertEqual(list(self.dir.glob("stats.db.bak-*")), backups)
        with closing(sqlite3.connect(backups[0])) as c:  # and not rewritten
            self.assertNotIn("link", [r[1] for r in c.execute("PRAGMA table_info(posts)")])

    def test_new_database_needs_no_backup(self):
        db.init(self.dir / "stats.db")
        db.init(self.dir / "stats.db")
        self.assertEqual([f.name for f in self.dir.iterdir() if ".bak" in f.name], [])


class SnapshotTest(DbTest):
    def account(self, platform: str = "tiktok") -> tuple:
        a = db.latest_accounts()[platform]
        return a["views"], a["likes"]

    def test_totals_count_every_known_post(self):
        read("tiktok", [post("a", "A", 0, views=100, likes=10), post("b", "B", 1, views=50, likes=5)], minutes=60)
        self.assertEqual(self.account(), (150, 15))
        read("tiktok", [post("a", "A", 0, views=120, likes=12)], minutes=120)  # b fell off the first page
        self.assertEqual(self.account(), (170, 17))
        read("tiktok", [post("a", "A", 0, views=130, likes=13)], views=1000, minutes=180)  # its own total wins
        self.assertEqual(self.account(), (1000, 18))

    def test_a_read_without_posts_keeps_the_last_totals(self):
        read("tiktok", [post("a", "A", 0, views=100, likes=10)], minutes=60)
        read("tiktok", [], minutes=120)  # the profile loaded, the video list didn't (a captcha)
        self.assertEqual(db.latest_accounts()["tiktok"]["ts"], at(120))
        self.assertEqual(self.account(), (100, 10))

    def test_missing_or_lower_counts_never_replace_known_ones(self):
        read("youtube", [post("v", "V", 0, views=2514, likes=40)], minutes=60)
        read("youtube", [post("v", "V", 0, views=2497, likes=None)], minutes=120)  # RSS counts lag and dip
        latest = next(p for p in db.posts_latest() if p["post_id"] == "v")
        self.assertEqual((latest["views"], latest["likes"], latest["ts"]), (2514, 40, at(120)))
        with db.connect() as c:
            rows = c.execute("SELECT views, likes FROM post_stats WHERE post_id = 'v' ORDER BY ts").fetchall()
        self.assertEqual([tuple(r) for r in rows], [(2514, 40), (2497, None)])  # snapshots stay as read
        read("youtube", [post("v", "V", 0, views=2600, likes=41)], minutes=180)
        latest = next(p for p in db.posts_latest() if p["post_id"] == "v")
        self.assertEqual((latest["views"], latest["likes"]), (2600, 41))

    def test_a_read_without_the_follower_count_keeps_the_last_one(self):
        def youtube(followers, posts, minutes):
            account = SimpleNamespace(handle="@yourchannel", followers=followers, views=None, likes=None, posts=posts)
            db.record_snapshot("youtube", account, [post("v", "V", 0, views=10)], at(minutes))

        youtube(None, None, 30)  # a new channel: YouTube doesn't show the count yet
        self.assertIsNone(db.latest_accounts()["youtube"]["followers"])
        youtube(400, 12, 60)
        youtube(None, None, 120)  # the page came without its subscriber line
        latest = db.latest_accounts()["youtube"]
        self.assertEqual((latest["ts"], latest["followers"], latest["posts"]), (at(120), 400, 12))
        youtube(390, 12, 180)  # a count that really went down is stored as read
        self.assertEqual(db.latest_accounts()["youtube"]["followers"], 390)

    def test_typed_in_and_read_numbers_are_not_added_together(self):
        eid = db.create_episode("E", None, at(0))
        db.add_post("x", f"manual-{eid}", eid, "E", None, at(0))
        read("x", [post(f"manual-{eid}", "E", 0, views=500)], minutes=60)
        self.assertEqual(self.account("x")[0], 500)
        read("x", [post("1234", "E", 0, views=40)], minutes=120)  # X connected: its own posts from now on
        self.assertEqual(self.account("x")[0], 40)


class TypedInTest(DbTest):
    """Numbers typed in for a platform ReelForge can't read yet (a manual-<episode> post each: the Accounts tab's Type
    in stats)."""

    def typed(self, platform: str, numbers: dict[int, int], minutes: float) -> None:
        """Save typed-in views per episode, the way the dialog's Save stores them."""
        titles = {e["id"]: e["title"] for e in db.episodes()}
        for eid in numbers:
            db.add_post(platform, f"manual-{eid}", eid, titles[eid], None, at(0))
        read(platform, [post(f"manual-{eid}", titles[eid], 0, views=n) for eid, n in numbers.items()], minutes=minutes)

    def posts(self, platform: str) -> list[str]:
        with db.connect() as c:
            return [r[0] for r in c.execute("SELECT post_id FROM posts WHERE platform = ? ORDER BY post_id",
                                            (platform,))]

    def test_the_real_post_takes_the_place_of_typed_in_numbers(self):
        kite = db.create_episode(REAL["kite"][0], video(REAL["kite"][1]), at(-60))
        jump = db.create_episode(REAL["jump"][0], video(REAL["jump"][1]), at(-50))
        self.typed("instagram", {kite: 1200, jump: 300}, 60)
        # Logged in now: Instagram is read, and its newest post is the kite video.
        read("instagram", [post("ig777", CAPTIONS["instagram"].format(REAL["kite"][0]), 5, views=1350)], minutes=120)
        self.assertEqual(analytics.auto_link(), 1)
        self.assertEqual(where("instagram", "ig777"), (kite, "caption"))
        # The kite video's typed number is the real post's first reading now, and its stand-in is gone; the jump
        # video's post wasn't read, so its typed number stays where it is.
        self.assertEqual(self.posts("instagram"), ["ig777", f"manual-{jump}"])
        self.assertEqual(self.readings("instagram", f"manual-{kite}"), [])
        self.assertEqual(self.readings("instagram", "ig777"), [1200, 1350])
        self.assertEqual(self.readings("instagram", f"manual-{jump}"), [300])
        self.assertEqual(analytics.auto_link(), 0)
        self.assertEqual(self.posts("instagram"), ["ig777", f"manual-{jump}"])
        self.assertEqual(self.readings("instagram", "ig777"), [1200, 1350])

    def test_typed_in_numbers_stay_while_no_post_is_read(self):
        kite = db.create_episode(REAL["kite"][0], video(REAL["kite"][1]), at(-60))
        self.typed("x", {kite: 500}, 60)
        read("tiktok", [post("tt", CAPTIONS["tiktok"].format(REAL["kite"][0]), 5, views=90)], minutes=120)
        analytics.auto_link()
        self.assertEqual(where("x", f"manual-{kite}"), (kite, "publish"))  # another platform's post doesn't replace it
        self.assertEqual(where("tiktok", "tt"), (kite, "caption"))

    def test_numbers_typed_in_after_the_log_in_ran_out_stay_until_the_post_is_read_again(self):
        """X was read for a while, then logged ReelForge's browser out: the numbers typed in since are the newer
        ones. They used to vanish at the next refresh of any platform, because the episode had a post on X."""
        kite = db.create_episode(REAL["kite"][0], video(REAL["kite"][1]), at(-60))
        read("x", [post("x1", CAPTIONS["x"].format(REAL["kite"][0]), 5, views=9000)], minutes=30)
        analytics.auto_link()
        self.assertEqual(where("x", "x1"), (kite, "caption"))
        self.typed("x", {kite: 12000}, 24 * 60)  # logged out since: typed in the next day
        read("tiktok", [post("tt", CAPTIONS["tiktok"].format(REAL["kite"][0]), 6, views=50)], minutes=24 * 60 + 30)
        analytics.auto_link()
        self.assertEqual(self.posts("x"), [f"manual-{kite}", "x1"])
        self.assertEqual(db.latest_accounts()["x"]["views"], 12000)
        self.assertEqual(self.readings("x", f"manual-{kite}"), [12000])
        # Logged in again: the post itself is read, with newer numbers than the typed ones.
        read("x", [post("x1", CAPTIONS["x"].format(REAL["kite"][0]), 5, views=12500)], minutes=48 * 60)
        analytics.auto_link()
        self.assertEqual(self.posts("x"), ["x1"])

    def test_typed_in_numbers_become_the_real_posts_history(self):
        """Weeks of numbers typed in while X was logged out used to be deleted, readings and all, the moment X was
        read again: the post's history then jumped from its last read before the log-out to today. They are its
        readings for those weeks now, as typed, between what was read before and after."""
        kite = db.create_episode(REAL["kite"][0], video(REAL["kite"][1]), at(-60))
        jump = db.create_episode(REAL["jump"][0], video(REAL["jump"][1]), at(-50))
        week = 7 * 24 * 60
        read("x", [post("x1", CAPTIONS["x"].format(REAL["kite"][0]), 5, views=9000),
                   post("x2", CAPTIONS["x"].format(REAL["jump"][0]), 425, views=700)], minutes=600)
        analytics.auto_link()
        for n, views in enumerate((10_000, 11_000, 12_000), start=1):  # logged out since: typed in once a week
            self.typed("x", {kite: views, jump: 800}, n * week)
            analytics.auto_link()  # (what the other platforms' refreshes run meanwhile)
        # Logged in again. X's page shows the kite post only: the jump one has left it.
        read("x", [post("x1", CAPTIONS["x"].format(REAL["kite"][0]), 5, views=12_500)], minutes=4 * week)
        analytics.auto_link()
        self.assertEqual(self.posts("x"), [f"manual-{jump}", "x1", "x2"])  # jump's wait until its post is read
        self.assertEqual(self.readings("x", "x1"), [9000, 10_000, 11_000, 12_000, 12_500])
        self.assertEqual([v for _, v in db.post_series(at(-1000))["x", "x1"]], [9000, 10_000, 11_000, 12_000, 12_500])
        self.assertEqual(self.readings("x", f"manual-{jump}"), [800, 800, 800])
        self.assertEqual(next(p["views"] for p in db.posts_latest() if p["post_id"] == "x1"), 12_500)  # as read
        self.assertEqual(where("x", "x1"), (kite, "caption"))
        with db.connect() as c:  # nothing typed in was dropped on the way
            self.assertEqual(c.execute("SELECT COUNT(*) FROM post_stats WHERE platform = 'x'").fetchone()[0], 9)

    def test_a_typed_in_number_can_be_put_right(self):
        """34000 typed where 3400 was meant: the video, the account and their charts all come down to 3400."""
        kite = db.create_episode(REAL["kite"][0], video(REAL["kite"][1]), at(-60))
        jump = db.create_episode(REAL["jump"][0], video(REAL["jump"][1]), at(-50))
        self.typed("x", {kite: 3000, jump: 100}, 60)
        self.typed("x", {kite: 34000, jump: 100}, 120)
        self.assertEqual(db.latest_accounts()["x"]["views"], 34100)
        self.typed("x", {kite: 3400, jump: 100}, 180)
        latest = {p["post_id"]: p["views"] for p in db.posts_latest()}
        self.assertEqual(latest, {f"manual-{kite}": 3400, f"manual-{jump}": 100})
        self.assertEqual(db.latest_accounts()["x"]["views"], 3500)
        self.assertEqual([v for _, v in db.post_series(at(-1000))["x", f"manual-{kite}"]], [3000, 3400, 3400])
        self.assertEqual([r["views"] for r in db.account_series("x", "")], [3100, 3500, 3500])

    def readings(self, platform: str, post_id: str) -> list[int]:
        """A post's readings as they're stored (charts hold a count at its highest: db.post_series)."""
        with db.connect() as c:
            return [r[0] for r in c.execute("SELECT views FROM post_stats WHERE platform = ? AND post_id = ? "
                                            "ORDER BY ts, rowid", (platform, post_id))]

    def test_a_slip_saved_again_before_it_was_noticed_is_put_right_everywhere_it_was_saved(self):
        """Every Save stores each video's number again, so a slip is in every reading since it was typed."""
        kite = db.create_episode(REAL["kite"][0], video(REAL["kite"][1]), at(-60))
        jump = db.create_episode(REAL["jump"][0], video(REAL["jump"][1]), at(-50))
        self.typed("x", {kite: 3000, jump: 100}, 60)
        self.typed("x", {kite: 34000, jump: 100}, 120)  # the slip
        self.typed("x", {kite: 34000, jump: 150}, 150)  # ... saved again with the other video's new number
        self.typed("x", {kite: 3400, jump: 150}, 180)
        self.assertEqual(self.readings("x", f"manual-{kite}"), [3000, 3400, 3400, 3400])
        self.assertEqual(self.readings("x", f"manual-{jump}"), [100, 100, 150, 150])
        self.assertEqual([r["views"] for r in db.account_series("x", "")], [3100, 3500, 3550, 3550])

    def test_a_number_typed_too_low_by_mistake_leaves_the_history_alone(self):
        """3400 typed where 34000 was meant, on a video with weeks of typed-in numbers: every earlier reading above
        3400 used to come down to it, the video's and the account's, and stayed down once the right number was in."""
        kite = db.create_episode(REAL["kite"][0], video(REAL["kite"][1]), at(-60))
        jump = db.create_episode(REAL["jump"][0], video(REAL["jump"][1]), at(-50))
        week = 7 * 24 * 60
        for n, views in enumerate((10_000, 20_000, 30_000, 32_000), start=1):  # typed in once a week
            self.typed("instagram", {kite: views, jump: 900}, n * week)
        self.typed("instagram", {kite: 3_400, jump: 900}, 5 * week)  # the slip: one digit short
        self.typed("instagram", {kite: 34_000, jump: 900}, 5 * week + 5)  # put right five minutes later
        # Only the newest number came down with the slip. What came before it still holds the charts up.
        self.assertEqual(self.readings("instagram", f"manual-{kite}"), [10_000, 20_000, 30_000, 3_400, 3_400, 34_000])
        self.assertEqual([v for _, v in db.post_series(at(-1000))["instagram", f"manual-{kite}"]],
                         [10_000, 20_000, 30_000, 30_000, 30_000, 34_000])
        self.assertEqual([r["views"] for r in db.account_series("instagram", "")],
                         [10_900, 20_900, 30_900, 4_300, 4_300, 34_900])
        self.assertEqual(db.latest_accounts()["instagram"]["views"], 34_900)

    def test_putting_a_typed_in_number_right_leaves_read_numbers_alone(self):
        kite = db.create_episode(REAL["kite"][0], video(REAL["kite"][1]), at(-60))
        read("x", [post("1234", "something else", 0, views=9000)], minutes=30)  # read while X was logged in
        self.typed("x", {kite: 500}, 60)
        self.typed("x", {kite: 50}, 120)
        # (the total saved with the slip was the typed-in numbers' own: it comes down with it; the read one stays)
        self.assertEqual([r["views"] for r in db.account_series("x", "")], [9000, 50, 50])
        self.assertEqual(next(p["views"] for p in db.posts_latest() if p["post_id"] == "1234"), 9000)


class HandleTest(DbTest):
    """A platform read under another @handle than its stats so far (db.record_snapshot): a renamed account carries
    on; what looks like another account is stored too, after a copy of the store, until you say which it is
    (db.forget_old_handle, db.keep_old_handle). Nothing is forgotten before that."""

    def read(self, handle: str, posts: list, followers: int, minutes: float, platform: str = "tiktok") -> str | None:
        account = SimpleNamespace(handle=handle, followers=followers, views=None, likes=None, posts=None)
        return db.record_snapshot(platform, account, posts, at(minutes))

    def copies(self) -> list[Path]:
        return sorted(self.dir.glob("stats.db.bak-*-handle"))

    def stored(self, platform: str = "tiktok") -> list[str]:
        return [p["post_id"] for p in db.all_posts() if p["platform"] == platform]

    def typo_then_mine(self) -> None:
        """Connected as @mrbest by mistake (an account that exists), then as @me."""
        theirs = [post(f"big{i}", f"Their video {i}", i, views=2_000_000, likes=1000) for i in range(3)]
        self.assertIsNone(self.read("@mrbest", theirs, 900_000, 60))
        self.assertIsNone(self.read("@mrbest", theirs, 900_010, 90))
        self.mine = [post("mine1", "My video", 5, views=500, likes=5), post("mine2", "My other video", 6, views=300)]

    def test_the_same_handle_is_nothing_new(self):
        mine = [post("a", "A", 0, views=10)]
        self.assertIsNone(self.read("@me", mine, 100, 60))
        self.assertIsNone(self.read("@Me", mine, 101, 120))  # however it's capitalised
        self.assertEqual((db.old_handles("tiktok"), db.handle_change("tiktok"), self.copies()), ([], None, []))

    def test_a_renamed_account_carries_on(self):
        mine = [post("a", "A", 0, views=10), post("b", "B", 1, views=20)]
        self.read("@oldname", mine, 100, 60)
        self.assertIsNone(self.read("@newname", [*mine, post("c", "C", 100, views=1)], 105, 120))  # the same posts
        self.assertEqual((db.old_handles("tiktok"), self.copies()), ([], []))
        self.assertEqual(self.stored(), ["a", "b", "c"])
        with db.connect() as c:  # its earlier readings are under the new name
            self.assertEqual({r[0] for r in c.execute("SELECT handle FROM account_stats")}, {"@newname"})

    def test_another_account_is_copied_first_and_nothing_is_forgotten_unasked(self):
        self.typo_then_mine()
        self.assertEqual(self.read("@me", self.mine, 120, 120), "@mrbest")
        (copy,) = self.copies()
        with closing(sqlite3.connect(copy)) as c:  # taken before @me's numbers went in
            self.assertEqual({r[0] for r in c.execute("SELECT handle FROM account_stats")}, {"@mrbest"})
            self.assertEqual(c.execute("SELECT COUNT(*) FROM posts").fetchone()[0], 3)
        self.assertEqual(self.stored(), ["big0", "big1", "big2", "mine1", "mine2"])  # both, as before the fix
        self.assertEqual(db.latest_accounts()["tiktok"]["views"], 6_000_800)
        self.assertEqual(db.old_handles("tiktok"), ["@mrbest"])
        found = db.handle_change("tiktok")
        self.assertEqual((found["now"], found["old"], found["posts"], len(found["rows"])),
                         ("@me", ["@mrbest"], ["big0", "big1", "big2"], 2))
        self.assertIsNone(self.read("@me", self.mine, 121, 150))  # said once: the next read is just a read
        self.assertEqual(len(self.copies()), 1)

    def test_forgetting_the_other_account_leaves_this_one_whole(self):
        self.typo_then_mine()
        eid = db.create_episode("Their video 0", None, at(0))  # as a twin of theirs would have made
        own = db.create_episode("My video", video("ep01_mine"), at(0))
        self.read("@me", self.mine, 120, 120)
        self.read("@me", self.mine, 121, 150)
        db.link_post("tiktok", "big0", eid, "twin")
        db.link_post("tiktok", "mine1", own)
        read("youtube", [post("yt", "Their video 0", 1, views=7)], minutes=120)
        db.set_meta("milestones", {"_seeded": 1, "followers:tiktok": 500_000, "views:total": 1_000_000,
                                   "video:tiktok|big0": 1_000_000, "video:tiktok|mine1": 0, "followers:youtube": 100})
        db.set_meta("badge_tiers", {"_seeded": 1, "seen:tiktok": 1, "seen:youtube": 1, "video:tiktok|big0:views": 5,
                                    "video:tiktok|mine1:views": 1})
        for key in ("milestone:video:tiktok|big0:1000000", "milestone:followers:tiktok:500000",
                    "milestone:video:tiktok|mine1:100", "milestone:followers:youtube:100"):
            notify.add("milestone", key, key=key)

        gone = db.forget_old_handle("tiktok")
        self.assertEqual((gone["old"], gone["now"], gone["posts"], gone["readings"], gone["episodes"]),
                         (["@mrbest"], "@me", 3, 2, 1))
        self.assertIn(gone["backup"], self.copies())
        with closing(sqlite3.connect(gone["backup"])) as c:  # everything is still in the copy
            self.assertEqual(c.execute("SELECT COUNT(*) FROM posts WHERE platform = 'tiktok'").fetchone()[0], 5)
        self.assertEqual(self.stored(), ["mine1", "mine2"])
        self.assertEqual([(r["followers"], r["views"], r["likes"]) for r in db.account_series("tiktok", "")],
                         [(120, 800, 5), (121, 800, 5)])  # without the 6,000,000 views and 3,000 likes of theirs
        with db.connect() as c:
            self.assertEqual({r[0] for r in c.execute("SELECT handle FROM account_stats WHERE platform = 'tiktok'")},
                             {"@me"})
            self.assertEqual(c.execute("SELECT COUNT(*) FROM post_stats WHERE post_id LIKE 'big%'").fetchone()[0], 0)
            self.assertEqual({r[0] for r in c.execute("SELECT key FROM notifications")},
                             {"milestone:video:tiktok|mine1:100", "milestone:followers:youtube:100"})
        self.assertEqual([e["id"] for e in db.episodes()], [own])  # the episode made from their video went too
        self.assertEqual(self.stored("youtube"), ["yt"])  # another platform's post is left alone (and unlinked)
        self.assertEqual(db.get_meta("milestones"), {"_seeded": 1, "video:tiktok|mine1": 0, "followers:youtube": 100})
        self.assertEqual(db.get_meta("badge_tiers"), {"_seeded": 1, "seen:youtube": 1, "video:tiktok|mine1:views": 1})
        self.assertEqual((db.old_handles("tiktok"), db.forget_old_handle("tiktok")), ([], None))  # nothing left to

    def test_keeping_them_makes_them_this_accounts(self):
        self.typo_then_mine()
        self.read("@me", self.mine, 120, 120)
        self.assertTrue(db.keep_old_handle("tiktok"))
        self.assertEqual((db.old_handles("tiktok"), db.handle_change("tiktok")), ([], None))
        self.assertEqual(len(self.stored()), 5)
        with db.connect() as c:  # the readings under the old handle are this account's now
            self.assertEqual({r[0] for r in c.execute("SELECT handle FROM account_stats")}, {"@me"})
        self.assertFalse(db.keep_old_handle("tiktok"))
        self.assertIsNone(self.read("@me", self.mine, 121, 150))  # settled: nothing more is asked
        self.assertEqual(db.old_handles("tiktok"), [])

    def test_back_to_the_right_handle_after_a_wrong_one(self):
        """@me for a while, @mrbest by mistake, @me again: only what was read as @mrbest can be forgotten."""
        mine = [post("mine1", "My video", 5, views=500)]
        self.read("@me", mine, 100, 30)
        theirs = [post("big0", "Their video", 0, views=2_000_000)]
        self.assertEqual(self.read("@mrbest", theirs, 900_000, 60), "@me")
        self.assertEqual(self.read("@me", mine, 101, 90), "@mrbest")  # not taken for a rename back
        self.assertEqual(len(self.copies()), 2)
        self.assertEqual(db.handle_change("tiktok")["posts"], ["big0"])
        db.forget_old_handle("tiktok")
        self.assertEqual(self.stored(), ["mine1"])
        self.assertEqual([r["followers"] for r in db.account_series("tiktok", "")], [100, 101])

    def test_numbers_you_typed_in_are_never_forgotten(self):
        eid = db.create_episode("My video", video("ep01_mine"), at(0))
        db.add_post("x", f"manual-{eid}", eid, "My video", None, at(0))
        self.read("@oldme", [post("old1", "Their video", 0, views=9)], 10, 30, platform="x")
        self.read("@oldme", [post(f"manual-{eid}", "My video", 0, views=40)], 10, 60, platform="x")
        self.assertEqual(self.read("@me", [post("x1", "Another video", 5, views=7)], 12, 120, platform="x"), "@oldme")
        self.assertEqual(db.handle_change("x")["posts"], ["old1"])
        db.forget_old_handle("x")
        self.assertEqual(self.stored("x"), [f"manual-{eid}", "x1"])

    def test_typed_in_numbers_under_a_corrected_handle_ask_nothing(self):
        """Instagram, never logged in, its numbers typed in: putting its handle right is no other account. (It
        used to copy the store and say the stats hold both accounts.)"""
        eid = db.create_episode("My video", video("ep01_mine"), at(0))
        db.add_post("instagram", f"manual-{eid}", eid, "My video", None, at(0))
        typed = lambda n: [post(f"manual-{eid}", "My video", 0, views=n)]  # noqa: E731
        self.read("@my.name", typed(40), 10, 60, platform="instagram")
        self.assertIsNone(self.read("@my_name", typed(50), 11, 120, platform="instagram"))
        self.assertEqual((db.old_handles("instagram"), db.handle_change("instagram"), self.copies()), ([], None, []))
        # Logged in later, and read: still nothing of the old handle's to tell apart.
        self.assertIsNone(self.read("@my_name", [post("ig1", "My video", 0, views=60)], 12, 180, platform="instagram"))
        self.assertEqual((db.old_handles("instagram"), self.copies()), ([], []))

    def test_a_read_with_nothing_to_compare_decides_nothing(self):
        """A renamed account whose first read under the new name brings no videos (TikTok's totals alone): it used
        to be taken for another account there and then, with a copy and a notification, and stay that way."""
        mine = [post("a", "A", 0, views=10), post("b", "B", 1, views=20)]
        self.read("@oldname", mine, 100, 60)
        self.read("@oldname", mine, 100, 90)
        self.assertIsNone(self.read("@newname", [], 100, 120))  # only the totals: can't say
        self.assertEqual((db.old_handles("tiktok"), db.handle_change("tiktok"), self.copies()), ([], None, []))
        self.assertIsNone(self.read("@newname", [], 100, 130))
        self.assertIsNone(self.read("@newname", mine, 101, 150))  # the same videos: a new name
        self.assertEqual((db.old_handles("tiktok"), db.handle_change("tiktok"), self.copies()), ([], None, []))
        self.assertIsNone(db.get_meta(db.HANDLES, {}).get("tiktok"))  # nothing left to look out for
        with db.connect() as c:
            self.assertEqual({r[0] for r in c.execute("SELECT handle FROM account_stats")}, {"@newname"})

    def test_another_account_is_said_by_the_first_read_that_can_tell(self):
        self.typo_then_mine()
        self.assertIsNone(self.read("@me", [], 120, 120))  # only the totals
        self.assertEqual((db.old_handles("tiktok"), self.copies()), ([], []))
        self.assertEqual(self.read("@me", self.mine, 120, 150), "@mrbest")  # videos, none of them @mrbest's
        self.assertEqual((db.old_handles("tiktok"), len(self.copies())), (["@mrbest"], 1))
        self.assertEqual(db.handle_change("tiktok")["posts"], ["big0", "big1", "big2"])
        db.forget_old_handle("tiktok")
        self.assertEqual(self.stored(), ["mine1", "mine2"])
        # The reading from before the videos came carried @mrbest's total on: that went too.
        self.assertEqual([(r["followers"], r["views"]) for r in db.account_series("tiktok", "")],
                         [(120, 0), (120, 800)])

    def test_a_new_name_found_out_later_takes_the_question_back(self):
        """A read that showed none of the old handle's videos (a page of new ones only), then one that does: it
        was a new name after all."""
        mine = [post("a", "A", 0, views=10), post("b", "B", 1, views=20)]
        self.read("@oldname", mine, 100, 60)
        self.assertEqual(self.read("@newname", [post("c", "C", 100, views=1)], 100, 120), "@oldname")
        notify.handle_changed("tiktok", "TikTok", "@oldname", "@newname")
        self.assertEqual(db.old_handles("tiktok"), ["@oldname"])
        self.assertIsNone(self.read("@newname", [*mine, post("c", "C", 100, views=2)], 100, 150))
        self.assertEqual((db.old_handles("tiktok"), db.handle_change("tiktok")), ([], None))
        self.assertEqual([n for n in notify.recent() if (n["key"] or "").startswith("handle:")], [])
        with db.connect() as c:
            self.assertEqual({r[0] for r in c.execute("SELECT handle FROM account_stats")}, {"@newname"})
        self.assertEqual(self.stored(), ["a", "b", "c"])

    def test_handles_mixed_in_the_store_from_before_are_left_alone(self):
        """A store that already holds readings under two handles (a rename from months ago, before ReelForge
        looked at handles at all): nothing says whose posts were whose, so nothing is asked and nothing copied."""
        with db.connect() as c:
            c.executemany("INSERT INTO account_stats (platform, ts, handle, followers, views) VALUES (?,?,?,?,?)",
                          [("youtube", at(60 * i), "@oldname" if i < 5 else "@newname", 100 + i, 100)
                           for i in range(10)])
            c.execute("""INSERT INTO posts (platform, post_id, title, published_at, last_ts, views)
                         VALUES ('youtube', 'v1', 'V', ?, ?, 100)""", (at(0), at(540)))
        self.assertEqual(db.old_handles("youtube"), [])
        self.assertIsNone(self.read("@newname", [post("v1", "V", 0, views=101)], 110, 600, platform="youtube"))
        self.assertEqual((db.old_handles("youtube"), db.handle_change("youtube"), self.copies()), ([], None, []))
        self.assertEqual(db.forget_old_handle("youtube"), None)
        self.assertEqual(self.stored("youtube"), ["v1"])

    def long_ago(self, platform: str, reads: list[tuple[datetime, str, list[str]]]) -> None:
        """Reads at real times in the past (compact goes by today's date): (when, handle, the ids of the posts,
        all uploaded long before)."""
        iso = lambda t: t.isoformat(timespec="seconds")  # noqa: E731
        up = iso(reads[0][0] - timedelta(days=300))
        for when, handle, ids in reads:
            account = SimpleNamespace(handle=handle, followers=100, views=None, likes=None, posts=None)
            db.record_snapshot(platform, account, [SimpleNamespace(
                post_id=i, title=i, url=None, published_at=up, views=1000, likes=1, comments=None, shares=None,
                saves=None) for i in ids], iso(when))

    def test_thinned_history_never_costs_this_account_a_post(self):
        """@wrong for a week, the handle put right on a Wednesday, and one of @me's own posts read for two more
        days only (it left its page). Thinned to one reading a week, the times said that post was last read while
        the handle was still @wrong, and Forget removed it with the other account's."""
        now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
        monday = (now - timedelta(days=now.weekday(), weeks=13)).replace(hour=10)
        wrong, mine = ["W1", "W2"], ["M1", "M2"]
        self.long_ago("x", [(monday - timedelta(days=4), "@wrong", wrong), (monday, "@wrong", wrong),
                            (monday + timedelta(days=1), "@wrong", wrong),
                            (monday + timedelta(days=2), "@me", [*mine, "M_left"]),
                            (monday + timedelta(days=3), "@me", [*mine, "M_left"]),
                            *((monday + timedelta(days=5 + 7 * week), "@me", mine) for week in range(13))])
        self.assertEqual(db.handle_change("x")["posts"], wrong)
        self.assertGreater(db.compact(min_hours=0), 0)  # thinned: M_left's last reading has none of @me's with it
        found = db.handle_change("x")
        self.assertEqual((found["old"], found["posts"]), (["@wrong"], wrong))
        self.assertEqual(db.forget_old_handle("x")["posts"], 2)
        self.assertEqual(self.stored("x"), ["M1", "M2", "M_left"])
        with db.connect() as c:
            self.assertEqual({r[0] for r in c.execute("SELECT handle FROM account_stats")}, {"@me"})

    def test_the_question_outlives_the_old_handles_readings(self):
        """A mistyped handle put right the same day, and nothing said for a month: thinning leaves that day one
        reading of the account, the right handle's. The old one was then lost sight of, with its videos still in
        the totals and no way left to take them out."""
        now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
        day = (now - timedelta(days=30)).replace(hour=9)
        self.long_ago("tiktok", [(day, "@mrbest", ["big1", "big2"]),
                                 (day + timedelta(minutes=30), "@mrbest", ["big1", "big2"]),
                                 (day + timedelta(hours=1), "@me", ["mine1"]),
                                 (day + timedelta(hours=2), "@me", ["mine1"]), (now, "@me", ["mine1"])])
        db.compact(min_hours=0)
        with db.connect() as c:
            self.assertEqual({r[0] for r in c.execute("SELECT handle FROM account_stats")}, {"@me"})
        self.assertEqual(db.old_handles("tiktok"), ["@mrbest"])
        found = db.handle_change("tiktok")
        self.assertEqual((found["posts"], found["rows"]), (["big1", "big2"], []))
        self.assertEqual(db.parse(found["first"]), day)  # when it was read is still known, for the dialog
        self.assertEqual(db.latest_accounts()["tiktok"]["views"], 3000)
        gone = db.forget_old_handle("tiktok")
        self.assertEqual((gone["posts"], gone["readings"]), (2, 0))
        self.assertEqual(self.stored(), ["mine1"])
        self.assertEqual({r["views"] for r in db.account_series("tiktok", "")}, {1000})
        self.assertEqual(db.old_handles("tiktok"), [])

    def test_no_copy_no_read(self):
        """The copy that comes first can't be written (a full disk): the read isn't stored, the reason is said in
        a sentence and kept in the log, and the next read tries again. It used to be put down to a busy database
        at every read from then on."""
        self.typo_then_mine()
        full = sqlite3.OperationalError("database or disk is full")

        class Closing:  # a connection whose copy fails part-way
            def __init__(self, conn):
                self.conn = conn

            def backup(self, target):
                target.conn.execute("CREATE TABLE half (written INTEGER)")
                raise full

            def close(self):
                self.conn.close()

        with mock.patch.object(db, "closing", lambda conn: closing(Closing(conn))):
            with self.assertRaises(PlatformError) as caught:
                self.read("@me", self.mine, 120, 120)
        self.assertEqual(str(caught.exception), "ReelForge couldn't save a copy of your stats before storing @me's "
                         "with them, so this read wasn't stored. Check that the disk has room. ReelForge tries again "
                         "at the next refresh.")
        self.assertIn("(from OperationalError: database or disk is full)", detail(caught.exception))
        self.assertEqual(self.copies(), [])  # half a copy isn't left behind
        self.assertEqual((self.stored(), db.old_handles("tiktok")), (["big0", "big1", "big2"], []))
        self.assertEqual(self.read("@me", self.mine, 120, 150), "@mrbest")  # room again: copied, then stored
        self.assertEqual(len(self.copies()), 1)

    def test_a_refresh_says_so_once_and_the_note_leads_to_accounts(self):
        from dashboard import sync
        from dashboard.platforms import Account, Platform, Post

        class Mine(Platform):
            key, name, public_stats = "tiktok", "TikTok", True

            def fetch(self):
                return Account("@me", 120), [Post("mine1", "My video", None, at(5), views=500)]

        self.typo_then_mine()
        for state in (sync._attempted, sync.last_errors, sync.last_warnings):
            state.clear()
            self.addCleanup(state.clear)
        refresh = lambda: sync.refresh({"tiktok": Mine({"handle": "@me"})}, {"refresh_minutes": 30},  # noqa: E731
                                       manual=True, force=True)
        # While the copy can't be written, the refresh says so, each time, and nothing of @me's is stored.
        with mock.patch.object(db, "backup", side_effect=sqlite3.OperationalError("database or disk is full")):
            for _ in range(2):
                result = refresh()
                self.assertEqual((result.done, result.failed), ([], {"TikTok": "said"}))
                self.assertEqual(result.errors, [
                    "TikTok: ReelForge couldn't save a copy of your stats before storing @me's with them, so this "
                    "read wasn't stored. Check that the disk has room. ReelForge tries again at the next refresh."])
                self.assertIn("(from OperationalError: database or disk is full)", result.details[0])
        self.assertEqual((self.stored(), notify.recent()), (["big0", "big1", "big2"], []))
        for _ in range(2):
            self.assertEqual(refresh().errors, [])
        notes = [n for n in notify.recent() if (n["key"] or "").startswith("handle:")]
        self.assertEqual([(n["key"], n["title"]) for n in notes], [("handle:tiktok:me", "TikTok is now read as @me")])
        self.assertIn("read as @mrbest", notes[0]["body"])
        self.assertEqual(notify.target(notes[0]), notify.Target("accounts", account="tiktok"))
        self.assertEqual(len(self.copies()), 1)


class CompactTest(DbTest):
    def test_compacted_at_written_bare_by_older_versions_still_reads(self):
        with db.connect() as c:
            c.execute("INSERT OR REPLACE INTO meta VALUES ('compacted_at', ?)", (db.now_iso(),))
        self.assertEqual(db.compact(), 0)  # it ran less than 20 hours ago
        self.assertIsInstance(db.get_meta("compacted_at"), str)
        two_days_ago = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat(timespec="seconds")
        with db.connect() as c:
            c.execute("INSERT OR REPLACE INTO meta VALUES ('compacted_at', ?)", (two_days_ago,))
        db.compact()
        with db.connect() as c:
            stored = c.execute("SELECT value FROM meta WHERE key = 'compacted_at'").fetchone()[0]
        self.assertGreater(db.parse(json.loads(stored)), db.parse(two_days_ago))

    def test_daily_backup_keeps_the_newest_seven(self):
        folder = self.dir / "backups"
        folder.mkdir()
        for day in range(1, 10):
            (folder / f"stats-202601{day:02d}.db").write_bytes(b"older")
        (folder / "notes.txt").write_text("not a backup")
        db.compact(min_hours=0)
        today = folder / f"stats-{datetime.now():%Y%m%d}.db"
        with closing(sqlite3.connect(today)) as c:
            self.assertIn("posts", [r[0] for r in c.execute("SELECT name FROM sqlite_master")])
        kept = sorted(f.name for f in folder.glob("stats-*.db"))
        self.assertEqual(kept, [f"stats-202601{day:02d}.db" for day in range(4, 10)] + [today.name])
        self.assertTrue((folder / "notes.txt").exists())
        stamp = today.stat().st_mtime_ns
        db.compact(min_hours=0)  # once a day: today's stays as it is
        self.assertEqual(today.stat().st_mtime_ns, stamp)

    def test_failed_backup_never_stops_compaction(self):
        (self.dir / "backups").write_text("a file where the folder should be")
        db.compact(min_hours=0)
        self.assertIsNotNone(db.get_meta("compacted_at"))
        note = db.last_backup()  # ... and it's kept that it failed, and why, for the Settings tab
        self.assertTrue(note["failed"])
        self.assertIsNone(note["ok"])
        self.assertIn(note["why"], ("File exists", "Cannot create a file when that file already exists"))


class ApiTest(DbTest):
    def test_link_post_notes_how_and_none_unlinks(self):
        eid = db.create_episode("E", None, at(0))
        read("tiktok", [post("p", "E", 0)])
        db.link_post("tiktok", "p", eid, "upload")
        self.assertEqual(where("tiktok", "p"), (eid, "upload"))
        db.link_post("tiktok", "p", eid, None)
        self.assertEqual(where("tiktok", "p"), (None, None))
        db.link_post("tiktok", "p", eid)
        self.assertEqual(where("tiktok", "p"), (eid, "caption"))
        db.link_post("tiktok", "p", None)
        self.assertEqual(where("tiktok", "p"), (None, None))
        with self.assertRaises(ValueError):
            db.link_post("tiktok", "p", eid, "manual")

    def test_add_post_marks_publish_and_posts_latest_shows_it(self):
        eid = db.create_episode("E", None, at(0))
        db.add_post("tiktok", "pushed", eid, "E", None)
        db.add_post("tiktok", "pushed", None, "E", None)  # no episode given: the link stays
        self.assertEqual(where("tiktok", "pushed"), (eid, "publish"))
        self.assertEqual([p["link"] for p in db.posts_latest()], ["publish"])

    def test_episode_for_video_goes_by_file_name_newest_first(self):
        db.create_episode("E", video("ep05_savings_doubling"))
        moved = db.create_episode("E", r"C:\x\completed videos\ep05 - E\EP05_Savings_Doubling.mp4")
        self.assertEqual(db.episode_for_video("ep05_savings_doubling.mp4")["id"], moved)
        self.assertEqual(db.episode_for_video(video("ep05_savings_doubling"))["id"], moved)
        self.assertIsNone(db.episode_for_video("ep06_subscribers_doubling.mp4"))


@unittest.skipIf(os.environ.get("REELFORGE_SKIP_TIMING"), "REELFORGE_SKIP_TIMING is set")
class YearOfPostsTest(DbTest):
    """A year at two videos a day: 730 episodes and 2,920 posts, some reworded or without a caption."""

    def test_a_year_is_matched_quickly(self):
        nouns = ("car phone house dog cat pizza balloon elevator tree river bike boat plane train rocket bridge tower "
                 "city island cake coffee laptop guitar shoe hat lamp clock garden pool robot drone ship wallet bank "
                 "planet moon star cloud volcano castle").split()
        things = ("speed size weight price battery height temperature value volume age length power signal "
                  "brightness noise gravity storage memory wifi").split()
        pairs = [(n, t) for t in things for n in nouns][:730]
        episodes, posts, uploads = [], {p: [] for p in CAPTIONS}, []
        for i, (noun, thing) in enumerate(pairs):
            every = "second" if i % 4 == 0 else f"{2 + i % 3} seconds"
            title, slug = f"What if your {noun}'s {thing} doubled every {every}?", f"ep{i:03d}_{noun}_{thing}"
            start = 720 * i  # two videos a day
            episodes.append((i + 1, title, video(slug), at(start)))
            for k, platform in enumerate(CAPTIONS):
                caption = CAPTIONS[platform].format(title)
                if platform == "tiktok" and i % 10 == 3:
                    caption = f"Your {noun}'s {thing} doubles every {every} #fyp"
                if platform == "instagram" and i % 10 == 7:
                    caption = ""
                posts[platform].append(post(f"{platform}-{i}", caption, start + 10 + 5 * k, views=100 * k, likes=k))
                uploads.append((at(start + 5 + 5 * k), platform, f"{slug}.mp4", "manual"))
        with db.connect() as c:
            c.executemany("INSERT INTO episodes (id, title, video, created_at) VALUES (?,?,?,?)", episodes)
            c.executemany("INSERT INTO uploads (ts, platform, video, status) VALUES (?,?,?,?)", uploads)
        for platform, items in posts.items():
            read(platform, items)

        started = time.perf_counter()
        first = analytics.auto_link()
        first_s = time.perf_counter() - started
        refreshes = []
        for _ in range(3):  # the fastest of three, so a busy moment on the machine doesn't count against ReelForge
            started = time.perf_counter()
            again = analytics.auto_link()
            refreshes.append(time.perf_counter() - started)
            self.assertEqual(again, 0)
        again_s = min(refreshes)
        sys.stderr.write(f"\n  a year of posts: first run {first_s:.3f}s ({first} linked), "
                         f"then {again_s:.3f}s per refresh ... ")
        self.assertEqual(first, 2920)
        self.assertEqual(list(per_episode().values()), [ALL_FOUR] * 730)
        # GitHub's shared test machines (CI is set there) run this 5-7 times slower than a PC does
        self.assertLess(again_s, 1.5 if os.environ.get("CI") else 0.5)


if __name__ == "__main__":
    unittest.main()
