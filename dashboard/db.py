"""SQLite store: stat snapshots over time, posts, episodes, the upload log and your notifications.

Platforms mostly report lifetime totals, so every refresh writes a snapshot row. The charts
are built from those snapshots; the longer ReelForge runs, the more history it has.
"""

from __future__ import annotations

import json
import re
import sqlite3
from contextlib import closing, contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path, PureWindowsPath

SCHEMA = """
CREATE TABLE IF NOT EXISTS episodes (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    video TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS posts (
    platform TEXT NOT NULL,
    post_id TEXT NOT NULL,
    episode_id INTEGER REFERENCES episodes(id),
    title TEXT,
    url TEXT,
    published_at TEXT,
    last_ts TEXT, views INTEGER, likes INTEGER, comments INTEGER, shares INTEGER, saves INTEGER,
    link TEXT,
    PRIMARY KEY (platform, post_id)
);
CREATE TABLE IF NOT EXISTS post_stats (
    platform TEXT NOT NULL,
    post_id TEXT NOT NULL,
    ts TEXT NOT NULL,
    views INTEGER, likes INTEGER, comments INTEGER, shares INTEGER, saves INTEGER
);
CREATE INDEX IF NOT EXISTS post_stats_by_post ON post_stats (platform, post_id, ts);
CREATE TABLE IF NOT EXISTS account_stats (
    platform TEXT NOT NULL,
    ts TEXT NOT NULL,
    handle TEXT,
    followers INTEGER, views INTEGER, likes INTEGER, posts INTEGER
);
CREATE INDEX IF NOT EXISTS account_stats_by_platform ON account_stats (platform, ts);
CREATE INDEX IF NOT EXISTS posts_by_published ON posts (published_at);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE INDEX IF NOT EXISTS post_stats_by_ts ON post_stats (ts);
CREATE TABLE IF NOT EXISTS uploads (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL,
    platform TEXT NOT NULL,
    video TEXT,
    status TEXT,
    post_id TEXT,
    url TEXT,
    message TEXT
);
CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL,
    kind TEXT NOT NULL,
    key TEXT UNIQUE,
    title TEXT NOT NULL,
    body TEXT,
    tab TEXT,
    read INTEGER NOT NULL DEFAULT 0,
    deleted INTEGER NOT NULL DEFAULT 0
);
"""

_path: Path | None = None
_demo = False  # the store is the demo's made-up one: nothing about it is kept (no backups, no log)


LATEST_COLUMNS = ("last_ts", "views", "likes", "comments", "shares", "saves")
# How a post got its episode (posts.link): you chose it, its caption matched, it went up just after you opened that
# video's upload page, posts on two platforms matched each other, or ReelForge posted it (or you typed in its numbers).
# NULL: not linked. auto_link re-checks every link except hand and publish ones.
LINKS = ("hand", "caption", "upload", "twin", "publish")
TYPED = "manual-"  # a post id that starts with this holds numbers you typed in for an episode: manual-<episode id>
BACKUPS_KEPT = 7


def init(path: Path, demo: bool = False) -> None:
    global _path, _demo
    path.parent.mkdir(parents=True, exist_ok=True)
    _path, _demo = path, demo
    with connect() as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.executescript(SCHEMA)
        have = {r["name"] for r in db.execute("PRAGMA table_info(posts)")}
        notes = {r["name"] for r in db.execute("PRAGMA table_info(notifications)")}
    if not have.issuperset((*LATEST_COLUMNS, "link")) or "deleted" not in notes:
        _migrate()


def _migrate() -> None:
    """Add the columns an older database lacks, after saving a copy of it (see backup)."""
    from .analytics import caption_matches  # imported here because analytics imports this module

    with connect() as db:
        db.execute("BEGIN IMMEDIATE")  # a second ReelForge starting at the same moment waits here, then finds it done
        have = {r["name"] for r in db.execute("PRAGMA table_info(posts)")}
        notes = {r["name"] for r in db.execute("PRAGMA table_info(notifications)")}
        missing = [c for c in LATEST_COLUMNS if c not in have]
        if not missing and "link" in have and "deleted" in notes:
            return
        backup("schema")
        # Each post keeps its latest numbers too, so listing posts never scans the history.
        for c in missing:
            db.execute(f"ALTER TABLE posts ADD COLUMN {c} {'TEXT' if c == 'last_ts' else 'INTEGER'}")
        if missing:
            db.execute("""UPDATE posts SET (last_ts, views, likes, comments, shares, saves) = (
                            SELECT ts, views, likes, comments, shares, saves FROM post_stats s
                            WHERE s.platform = posts.platform AND s.post_id = posts.post_id
                            ORDER BY ts DESC LIMIT 1)""")
        if "link" not in have:
            db.execute("ALTER TABLE posts ADD COLUMN link TEXT")
            # Older links weren't marked. One the caption matcher agrees with was a caption match; any other was
            # made by hand (like an X post captioned as one video that is really another).
            linked = db.execute("SELECT * FROM posts WHERE episode_id IS NOT NULL").fetchall()
            named = caption_matches(linked, db.execute("SELECT * FROM episodes").fetchall())
            db.executemany("UPDATE posts SET link = ? WHERE platform = ? AND post_id = ?",
                           [("caption" if p["episode_id"] in named[p["platform"], p["post_id"]] else "hand",
                             p["platform"], p["post_id"]) for p in linked])
        if "deleted" not in notes:  # the × on a notification hides it (see notify.delete)
            db.execute("ALTER TABLE notifications ADD COLUMN deleted INTEGER NOT NULL DEFAULT 0")


def backup(why: str) -> Path:
    """Copy the database next to itself as stats.db.bak-<YYYYmmdd-HHMMSS>-<why>, consistent even while it's in use.
    A copy is never written over another: a second one in the same second gets a number before its why."""
    stamp = f"{_path.name}.bak-{datetime.now():%Y%m%d-%H%M%S}"
    target, n = _path.with_name(f"{stamp}-{why}"), 1
    while target.exists():
        target, n = _path.with_name(f"{stamp}-{n + 1}-{why}"), n + 1
    with closing(sqlite3.connect(_path, timeout=10)) as src, closing(sqlite3.connect(target)) as dst:
        src.backup(dst)
    return target


@contextmanager
def connect():
    # Short-lived connections so refresh and upload threads never share one.
    db = sqlite3.connect(_path, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA synchronous=NORMAL")
    try:
        with db:
            yield db
    finally:
        db.close()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def to_iso(value) -> str:
    """Normalise a datetime, unix timestamp or ISO string to a UTC ISO string."""
    if isinstance(value, (int, float)):
        dt = datetime.fromtimestamp(value, timezone.utc)
    elif isinstance(value, str):
        # Meta sends +0000 offsets; fromisoformat wants +00:00.
        dt = datetime.fromisoformat(re.sub(r"([+-]\d\d)(\d\d)$", r"\1:\2", value.replace("Z", "+00:00")))
    else:
        dt = value
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts)


def record_snapshot(platform: str, account, posts: list, ts: str | None = None,
                    conn: sqlite3.Connection | None = None) -> str | None:
    """Store one refresh: the account totals plus every post's current numbers.

    A read that misses numbers never wipes known ones: a post keeps its last known counts and its views never go down
    (RSS counts lag and dip), while post_stats keeps what was read. Without the platform's own totals, the account gets
    the sum over every post ReelForge knows there (one page load only shows the newest), or its last totals if no posts
    came. Numbers you typed in are taken as typed, so a slip can be put right (see _correct_typed).

    Returns the @handle the platform's stats were read under before, when this read is under another one and shows
    that to be another account (see _handle_change): the two are stored together from here on, so a copy of the
    store is saved first, and handle_change, forget_old_handle and keep_old_handle are how you say which it is.
    """
    ts = ts or now_iso()
    if conn is not None:
        _record(conn, platform, account, posts, ts)  # the demo writes its history in one go
        return None
    with connect() as db:
        change = _handle_change(db, platform, account.handle, posts, ts)
    if change and change["before"]:
        backup("handle")
    with connect() as db:  # what the read showed about the handle is stored with the read, or not at all
        if change:
            _note_change(db, platform, account.handle, change)
        _record(db, platform, account, posts, ts)
    return change["before"] if change else None


HANDLES = "handles"  # the meta key of the handle changes that aren't settled yet (see _changes)


def _handle(text) -> str:
    """A handle as kept with a reading, for comparing two: no @, lower case ("" for none)."""
    return str(text or "").strip().lstrip("@").lower()


def _newest_handle(db: sqlite3.Connection, platform: str) -> str | None:
    """The handle of the platform's newest reading that has one: the one it's read under now."""
    row = db.execute("""SELECT handle FROM account_stats WHERE platform = ? AND handle IS NOT NULL AND handle != ''
                        ORDER BY ts DESC, rowid DESC LIMIT 1""", (platform,)).fetchone()
    return row[0] if row else None


def _under(db: sqlite3.Connection, platform: str, handles) -> list[sqlite3.Row]:
    """The platform's account readings (rowid, ts) saved under any of these handles, however they were written."""
    want = {_handle(h) for h in handles}
    return [r for r in db.execute("SELECT rowid, ts, handle FROM account_stats WHERE platform = ? ORDER BY ts, rowid",
                                  (platform,)) if _handle(r["handle"]) in want]


def _changes(db: sqlite3.Connection) -> dict:
    """Each platform's handle changes that aren't settled (meta HANDLES):

      platform -> {"at": when it was first read under the handle it has now,
                   "old": [{"handle": an earlier handle, "posts": the posts it left behind, "first", "last": when it
                            was read, "sure": a read under the handle it has now showed it to be another account},
                           ...]}, the handle it had last first

    A handle's posts are written down when the change is seen, because nothing else says for good which handle a
    post was read under: readings only carry their time, and old ones are thinned out (compact). Worked out later
    from the times, an account's own post could be taken for the other account's, and a change that was never
    answered was lost sight of once the old handle's readings were thinned away."""
    found = get_meta(HANDLES, None, db)
    return found if isinstance(found, dict) else {}


def _keep_changes(db: sqlite3.Connection, platform: str, record: dict | None) -> None:
    every = _changes(db)
    if record and record["old"]:
        every[platform] = record
    else:
        every.pop(platform, None)
    db.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (HANDLES, json.dumps(every)))


def _handle_change(db: sqlite3.Connection, platform: str, handle: str, posts: list, ts: str) -> dict | None:
    """Before a read under `handle` is stored: what it changes about the platform's handles (None: nothing).

    A handle other than the newest reading's is a new name for the same account, or another account (a handle you
    mistyped that exists, or a switch to another one). The posts say which. One of the old handle's posts, read
    under the new one too: the same account, and its readings under the old name go under the new one. A read with
    posts, none of them the old handle's: another account, whose numbers are about to be stored with the ones so
    far, so the store is copied first (backup "handle") and you're asked which it is. A read with nothing to compare
    (TikTok's totals alone, numbers you typed in) can't say, so the old handle's posts are only written down. Every
    later read under the new handle is compared with what's written down, until one settles it either way.

    Returns {"record": what's kept about the platform from here on (see _changes; None: nothing),
             "relabel": the old handles whose readings go under `handle`,
             "before": the old handle this read showed to be another account, or None,
             "untold": whether an account you were told was another one turned out to be this one}."""
    new = _handle(handle)
    last = _newest_handle(db, platform) if new else None
    if not last:
        return None
    was = _changes(db).get(platform)
    if _handle(last) == new and not was:
        return None  # the usual read
    read = {p.post_id for p in posts if not p.post_id.startswith(TYPED)}
    old = [dict(e) for e in was["old"]] if was else []
    at = was["at"] if was else ts
    if _handle(last) != new:
        # What's stored and isn't an earlier handle's was read under the one that's going. Numbers you typed in are
        # yours whatever the handle was, so they're no part of this: a platform with only those has nothing to ask.
        stored = {r[0] for r in db.execute(
            "SELECT post_id FROM posts WHERE platform = ? AND post_id NOT LIKE 'manual-%'", (platform,))}
        theirs = stored - {post_id for e in old for post_id in e["posts"]}
        old = [e for e in old if _handle(e["handle"]) != new]  # back to an earlier handle: its posts are this one's
        if theirs:
            times = [r["ts"] for r in _under(db, platform, [last])]
            old.insert(0, {"handle": last, "posts": sorted(theirs), "first": times[0], "last": times[-1],
                           "sure": False})
        at = ts
    relabel, kept, before, untold = [], [], None, False
    for e in old:
        if read & set(e["posts"]):  # one of its posts, under this handle now: the same account, renamed
            relabel.append(e["handle"])
            untold = untold or e["sure"]
            continue
        if read and not e["sure"]:
            e["sure"], before = True, e["handle"]
        kept.append(e)
    record = {"at": at, "old": kept} if kept else None
    if record == was and not relabel:
        return None
    return {"record": record, "relabel": relabel, "before": before,
            "untold": untold and not any(e["sure"] for e in kept)}


def _note_change(db: sqlite3.Connection, platform: str, handle: str, change: dict) -> None:
    """Store what _handle_change worked out."""
    if change["relabel"]:
        db.executemany("UPDATE account_stats SET handle = ? WHERE rowid = ?",
                       [(handle, r["rowid"]) for r in _under(db, platform, change["relabel"])])
    if change["untold"]:  # the notification said the stats hold two accounts: they never did
        db.execute("DELETE FROM notifications WHERE key LIKE ?", (f"handle:{platform}:%",))
    _keep_changes(db, platform, change["record"])


def old_handles(platform: str) -> list[str]:
    """The other @handles this platform's stats were read under, besides the one it's read under now: none, unless
    the handle changed to what a read showed to be another account (_handle_change) and you haven't said which it
    is yet. That's kept in the store (_changes), so the question stays however old the readings get."""
    with connect() as db:
        return [e["handle"] for e in (_changes(db).get(platform) or {}).get("old", []) if e["sure"]]


def _from_before(db: sqlite3.Connection, platform: str) -> dict | None:
    """What a platform's stats hold from before its handle changed to another account's (None: nothing): the handle
    now and the old ones, the readings still saved under the old ones (account_stats rowids), when the old ones
    were read (first, last) and when the one now was first (at), and the old ones' posts: those written down when
    the change was seen (_changes), never one that has been read since."""
    record = _changes(db).get(platform) or {}
    old = [e for e in record.get("old", []) if e["sure"]]
    if not old:
        return None
    rows = _under(db, platform, [e["handle"] for e in old])
    theirs = {post_id for e in old for post_id in e["posts"]}
    posts = sorted(r[0] for r in db.execute(
        "SELECT post_id FROM posts WHERE platform = ? AND (last_ts IS NULL OR last_ts < ?)", (platform, record["at"]))
                   if r[0] in theirs)
    return {"now": _newest_handle(db, platform), "old": [e["handle"] for e in old],
            "rows": [r["rowid"] for r in rows], "first": min(e["first"] for e in old),
            "last": max(e["last"] for e in old), "at": record["at"], "posts": posts}


def _settled(db: sqlite3.Connection, platform: str) -> None:
    """You've said which it is (keep_old_handle, forget_old_handle): the question is closed."""
    record = _changes(db).get(platform) or {}
    _keep_changes(db, platform, {**record, "old": [e for e in record.get("old", []) if not e["sure"]]})


def handle_change(platform: str) -> dict | None:
    """What forget_old_handle would forget, to ask about first (see _from_before); None when there's nothing."""
    with connect() as db:
        return _from_before(db, platform)


def keep_old_handle(platform: str) -> bool:
    """The stats from before the platform's handle changed are this account's too (you renamed it): the readings
    under the old handle are put under the one it has now, and its posts stay. Returns whether there was anything
    to keep."""
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        found = _from_before(db, platform)
        if not found:
            return False
        db.executemany("UPDATE account_stats SET handle = ? WHERE rowid = ?",
                       [(found["now"], r) for r in found["rows"]])
        _settled(db, platform)
        return True


def forget_old_handle(platform: str) -> dict | None:
    """Forget what a platform's stats hold from before its handle changed, because that was another account: the
    readings under the old handle, its posts (those written down when the change was seen, see _changes) with their
    readings, the notifications about them and the episodes ReelForge made up from them (twins without a video).
    The whole store is copied first (backup "handle"), and nothing else is touched: this account's own readings and
    posts stay, and so does every other platform. This account's totals since then lose what they counted of the
    other's posts, and the round numbers and badges the platform has passed start again from where this account is.
    Returns what went, with the copy's path ("backup"), or None when there was nothing to forget."""
    from . import notify  # imported here because notify imports this module

    if not handle_change(platform):
        return None
    copy = backup("handle")
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        found = _from_before(db, platform)  # looked up again, now that nothing else can write
        if not found:
            return None
        posts = found["posts"]
        mine = [(platform, pid) for pid in posts]
        episodes = {r[0] for ref in mine for r in db.execute(
            "SELECT episode_id FROM posts WHERE platform = ? AND post_id = ? AND episode_id IS NOT NULL", ref)}
        # Where the platform's total is ReelForge's own sum over its posts (_record), this account's readings since
        # then counted the other account's posts too: they come down by exactly those posts' numbers.
        newest = db.execute("""SELECT views, likes FROM account_stats WHERE platform = ?
                               ORDER BY ts DESC, rowid DESC LIMIT 1""", (platform,)).fetchone()
        for field in ("views", "likes"):
            every = db.execute(f"SELECT SUM({field}) FROM posts WHERE platform = ? AND post_id NOT LIKE 'manual-%'",
                               (platform,)).fetchone()[0]
            theirs = sum(r[0] or 0 for ref in mine for r in db.execute(
                f"SELECT {field} FROM posts WHERE platform = ? AND post_id = ?", ref))
            if theirs and newest[field] == every:
                db.execute(f"UPDATE account_stats SET {field} = MAX({field} - ?, 0) WHERE platform = ? AND ts >= ?",
                           (theirs, platform, found["at"]))
        db.executemany("DELETE FROM account_stats WHERE rowid = ?", [(r,) for r in found["rows"]])
        for table in ("post_stats", "posts"):
            db.executemany(f"DELETE FROM {table} WHERE platform = ? AND post_id = ?", mine)
        emptied = [e for e in episodes if db.execute(
            """DELETE FROM episodes WHERE id = ? AND video IS NULL
               AND NOT EXISTS (SELECT 1 FROM posts WHERE episode_id = episodes.id)""", (e,)).rowcount]
        refs = {f"{platform}|{pid}" for pid in posts}
        about = (f"milestone:followers:{platform}:", f"badge:day:{platform}:", f"handle:{platform}:")
        gone = [n["id"] for n in map(dict, db.execute("SELECT id, kind, key, title FROM notifications"))
                if notify.target(n).post in refs or (n["key"] or "").startswith(about)]
        db.executemany("DELETE FROM notifications WHERE id = ?", [(i,) for i in gone])
        # What the platform had passed was the other account's doing: these marks are set afresh, quietly, by the
        # next read (notify.check_milestones, badges.notify_new).
        for key, stale in (("milestones", lambda k: k in (f"followers:{platform}", "views:total")
                            or k.removeprefix("video:") in refs),
                           ("badge_tiers", lambda k: k == f"seen:{platform}" or k.startswith(f"day:{platform}:")
                            or k.removeprefix("video:").rsplit(":", 1)[0] in refs)):
            marks = get_meta(key, None, db)
            if isinstance(marks, dict):
                db.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                           (key, json.dumps({k: v for k, v in marks.items() if not stale(k)})))
        _settled(db, platform)
    return {"backup": copy, "now": found["now"], "old": found["old"], "readings": len(found["rows"]),
            "posts": len(posts), "episodes": len(emptied)}


def _record(db: sqlite3.Connection, platform: str, account, posts: list, ts: str) -> None:
    db.executemany(
        """INSERT INTO posts (platform, post_id, title, url, published_at,
                              last_ts, views, likes, comments, shares, saves)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT (platform, post_id) DO UPDATE SET
             title = COALESCE(NULLIF(excluded.title, ''), posts.title),
             url = COALESCE(excluded.url, posts.url),
             published_at = excluded.published_at,
             last_ts = excluded.last_ts,
             views = CASE WHEN posts.post_id LIKE 'manual-%' THEN COALESCE(excluded.views, posts.views)
                          ELSE COALESCE(MAX(excluded.views, posts.views), excluded.views, posts.views) END,
             likes = COALESCE(excluded.likes, posts.likes), comments = COALESCE(excluded.comments, posts.comments),
             shares = COALESCE(excluded.shares, posts.shares), saves = COALESCE(excluded.saves, posts.saves)""",
        [(platform, p.post_id, p.title, p.url, to_iso(p.published_at),
          ts, p.views, p.likes, p.comments, p.shares, p.saves) for p in posts],
    )
    db.executemany(
        """INSERT INTO post_stats (platform, post_id, ts, views, likes, comments, shares, saves)
           VALUES (?,?,?,?,?,?,?,?)""",
        [(platform, p.post_id, ts, p.views, p.likes, p.comments, p.shares, p.saves) for p in posts],
    )
    # Typed-in numbers (manual-<episode> posts) and the platform's own posts are never added together.
    typed = bool(posts) and all(p.post_id.startswith("manual-") for p in posts)
    totals = []
    for field, given in (("views", account.views), ("likes", account.likes)):
        if given is None and posts:
            given = db.execute(
                f"SELECT SUM({field}) FROM posts WHERE platform = ? AND (post_id LIKE 'manual-%') = ?",
                (platform, typed)).fetchone()[0]
        if given is None:
            given = (db.execute(
                f"SELECT {field} FROM account_stats WHERE platform = ? AND ts < ? ORDER BY ts DESC LIMIT 1",
                (platform, ts)).fetchone() or [None])[0]
        totals.append(given)
    if typed:
        _correct_typed(db, platform, posts, totals[0])
    # A read that couldn't see the followers (YouTube's page without its subscriber line) or the post count keeps
    # the last one seen: stored as unknown, the platform's card would go blank and every total would drop by it.
    counts = []
    for field, given in (("followers", account.followers), ("posts", account.posts)):
        if given is None:
            given = (db.execute(
                f"""SELECT {field} FROM account_stats WHERE platform = ? AND ts < ? AND {field} IS NOT NULL
                    ORDER BY ts DESC LIMIT 1""", (platform, ts)).fetchone() or [None])[0]
        counts.append(given)
    db.execute(
        "INSERT INTO account_stats (platform, ts, handle, followers, views, likes, posts) VALUES (?,?,?,?,?,?,?)",
        (platform, ts, account.handle, counts[0], *totals, counts[1]),
    )


def _correct_typed(db: sqlite3.Connection, platform: str, posts: list, total: int | None) -> None:
    """A typed-in number put right downwards (34000 that should have been 3400): the earlier readings that were
    higher come down to it, the video's and, on a platform with only typed-in numbers, the account's, so no chart
    holds on to the slip (charts keep a count at its highest reading)."""
    db.executemany("UPDATE post_stats SET views = ? WHERE platform = ? AND post_id = ? AND views > ?",
                   [(p.views, platform, p.post_id, p.views) for p in posts if p.views is not None])
    read_too = db.execute("SELECT 1 FROM posts WHERE platform = ? AND post_id NOT LIKE 'manual-%' LIMIT 1",
                          (platform,)).fetchone()
    if total is not None and not read_too:
        db.execute("UPDATE account_stats SET views = ? WHERE platform = ? AND views > ?", (total, platform, total))


def latest_accounts() -> dict[str, sqlite3.Row]:
    with connect() as db:
        rows = db.execute(
            """SELECT * FROM account_stats a
               WHERE ts = (SELECT MAX(ts) FROM account_stats WHERE platform = a.platform)"""
        ).fetchall()
    return {r["platform"]: r for r in rows}


def account_series(platform: str, since: str) -> list[sqlite3.Row]:
    with connect() as db:
        return db.execute(
            "SELECT ts, followers, views, likes FROM account_stats WHERE platform = ? AND ts >= ? ORDER BY ts",
            (platform, since),
        ).fetchall()


def posts_latest() -> list[dict]:
    """Every known post with its latest numbers (NULL until its first refresh) and how it got its episode (link)."""
    with connect() as db:
        rows = db.execute(
            """SELECT p.platform, p.post_id, p.episode_id, p.link, p.title, p.url, p.published_at, p.last_ts AS ts,
                      p.views, p.likes, p.comments, p.shares, p.saves, e.title AS episode
               FROM posts p LEFT JOIN episodes e ON e.id = p.episode_id"""
        ).fetchall()
    return [dict(r) for r in rows]


def last_read(platform: str) -> dict[str, str]:
    """When each of a platform's posts was last read: post id -> time."""
    with connect() as db:
        return {r[0]: r[1] for r in db.execute(
            "SELECT post_id, last_ts FROM posts WHERE platform = ? AND last_ts IS NOT NULL", (platform,))}


def platforms_with_posts_since(since: str) -> set[str]:
    with connect() as db:
        return {r[0] for r in db.execute("SELECT DISTINCT platform FROM posts WHERE published_at >= ?", (since,))}


def stored_platforms() -> set[str]:
    """The platforms ReelForge has any stats for (read, or typed in): see Platform.connected."""
    with connect() as db:
        return {r[0] for r in db.execute("SELECT DISTINCT platform FROM account_stats")}


def get_meta(key: str, default=None, conn: sqlite3.Connection | None = None):
    if conn is None:
        with connect() as db:
            return get_meta(key, default, db)
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    if row is None:
        return default
    try:
        return json.loads(row["value"])
    except ValueError:  # older versions stored compacted_at as a bare string
        return row["value"]


def set_meta(key: str, value) -> None:
    with connect() as db:
        db.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, json.dumps(value)))


def bump() -> int:
    """Count a change every ReelForge window shows (new stats, an episode, an upload, a hidden video): a window that
    sees the count go up reloads (app.ReelForge.keep_in_step). Returns the new count."""
    with connect() as db:
        db.execute("""INSERT INTO meta (key, value) VALUES ('changes', '1')
                      ON CONFLICT (key) DO UPDATE SET value = CAST(value AS INTEGER) + 1""")
        return int(db.execute("SELECT value FROM meta WHERE key = 'changes'").fetchone()[0])


def changes(conn: sqlite3.Connection | None = None) -> int:
    """How many changes bump has counted."""
    count = get_meta("changes", 0, conn)
    return count if isinstance(count, int) else 0


def post_series(since: str) -> dict[tuple[str, str], list[tuple[str, int]]]:
    """(platform, post_id) -> [(ts, views), ...], oldest first, for posts published since `since`.

    Older posts don't need their curves: scores and typical curves come from recent videos. A reading
    without a view count is skipped, and a count that dips (YouTube's feed lags its pages) is held at its
    highest, so curves never step down.
    """
    with connect() as db:
        return _curves(db.execute(
            """SELECT s.platform, s.post_id, s.ts, s.views FROM post_stats s
               JOIN posts p ON p.platform = s.platform AND p.post_id = s.post_id
               WHERE p.published_at >= ? AND s.views IS NOT NULL
               ORDER BY s.platform, s.post_id, s.ts""", (since,)))


def early_series() -> dict[tuple[str, str], list[tuple[str, int]]]:
    """Like post_series, but every post's readings from its first FIRST_HOURS hours only, however old the post."""
    with connect() as db:
        return _curves(db.execute(
            f"""SELECT s.platform, s.post_id, s.ts, s.views FROM post_stats s
                JOIN posts p ON p.platform = s.platform AND p.post_id = s.post_id
                WHERE s.views IS NOT NULL AND s.ts < {_EARLY_END}
                ORDER BY s.platform, s.post_id, s.ts"""))


def _curves(rows) -> dict[tuple[str, str], list[tuple[str, int]]]:
    """(platform, post_id, ts, views) rows, oldest first, as each post's readings with every dip held at its highest."""
    out: dict[tuple[str, str], list[tuple[str, int]]] = {}
    for platform, post_id, ts, views in rows:
        pts = out.setdefault((platform, post_id), [])
        pts.append((ts, max(views, pts[-1][1]) if pts else views))
    return out


# Old snapshots get thinned out, keeping the last one in each time bucket: full detail for 2 days,
# then one per 3 hours, one per day after 2 weeks, one per week after 2 months. A post's first
# FIRST_HOURS hours are kept in full: its views 1, 6, 12 and 24 hours in are measured from them
# (analytics.views_after takes readings up to 3 hours either side).
FIRST_HOURS = 27
_EARLY_END = f"strftime('%Y-%m-%dT%H:%M:%S+00:00', p.published_at, '+{FIRST_HOURS} hours')"  # as stored: UTC ISO
_BUCKETS = (
    (2, "substr(ts, 1, 10) || '.' || (CAST(substr(ts, 12, 2) AS INTEGER) / 3)"),
    (14, "substr(ts, 1, 10)"),
    (60, "strftime('%Y-%W', substr(ts, 1, 19))"),
)


def compact(min_hours: float = 20) -> int:
    """Thin out old snapshots and save the daily backup (at most once every `min_hours`). Returns rows removed."""
    now = datetime.now(timezone.utc)
    last = get_meta("compacted_at")
    if last and now - parse(last) < timedelta(hours=min_hours):
        return 0
    removed = 0
    with connect() as db:
        for days, bucket in _BUCKETS:
            cut = (now - timedelta(days=days)).isoformat(timespec="seconds")
            removed += db.execute(
                f"""DELETE FROM post_stats WHERE ts < ? AND rowid NOT IN (
                      SELECT MAX(rowid) FROM post_stats WHERE ts < ? GROUP BY platform, post_id, {bucket})
                    AND NOT EXISTS (SELECT 1 FROM posts p WHERE p.platform = post_stats.platform
                                    AND p.post_id = post_stats.post_id AND post_stats.ts < {_EARLY_END})""",
                (cut, cut)).rowcount
            account_cut = (now - timedelta(days=days + 12)).isoformat(timespec="seconds")
            removed += db.execute(
                f"""DELETE FROM account_stats WHERE ts < ? AND rowid NOT IN (
                      SELECT MAX(rowid) FROM account_stats WHERE ts < ? GROUP BY platform, {bucket})""",
                (account_cut, account_cut)).rowcount
        # Read notifications beyond the newest 300 go too (notify.tidy does the same).
        db.execute("""DELETE FROM notifications WHERE read = 1 AND id NOT IN (
                        SELECT id FROM notifications ORDER BY id DESC LIMIT 300)""")
        db.execute("PRAGMA optimize")
    set_meta("compacted_at", now.isoformat(timespec="seconds"))
    daily_backup()
    return removed


def daily_backup(keep: int = BACKUPS_KEPT) -> Path | None:
    """Save data/backups/stats-YYYYMMDD.db unless today's is there, and keep the newest `keep`.

    Never raises: a failed backup only means there is none today. Returns today's backup, or None. How it went is
    kept for the Settings tab (last_backup). The demo's made-up stats aren't backed up: its copies would sit among
    the real ones.
    """
    if _demo:
        return None
    try:
        folder = _path.parent / "backups"
        folder.mkdir(exist_ok=True)
        target = folder / f"{_path.stem}-{datetime.now():%Y%m%d}.db"
        if not target.exists():
            part = target.with_suffix(".part")  # renamed once complete, so a half-written file never counts
            part.unlink(missing_ok=True)
            with connect() as db:
                db.execute("VACUUM INTO ?", (str(part),))
            part.replace(target)
        ours = sorted(f for f in folder.iterdir() if re.fullmatch(rf"{re.escape(_path.stem)}-\d{{8}}\.db", f.name))
        for old in ours[:-keep]:
            old.unlink()
    except Exception as e:
        _note_backup(e)
        return None
    _note_backup(None)
    return target


def _note_backup(error: Exception | None) -> None:
    """Keep how the daily backup went (meta "backup"): {"ok": when the last one worked}, and after one that didn't
    also {"failed": when, "why": in a few words}. A backup that fails every day (a full disk, a folder ReelForge
    can't write to) would otherwise go unnoticed until the day it's needed."""
    try:
        was = get_meta("backup")
        note = {"ok": was.get("ok") if isinstance(was, dict) else None}
        if error is None:
            note = {"ok": now_iso()}
        else:
            why = error.strerror if isinstance(error, OSError) and error.strerror else \
                str(error) if isinstance(error, sqlite3.Error) else type(error).__name__
            note.update(failed=now_iso(), why=why)
        set_meta("backup", note)
    except Exception:
        pass  # the store itself is the trouble: nothing to keep it in


def last_backup() -> dict:
    """How the daily backup last went (see _note_backup); {} before the first one."""
    note = get_meta("backup")
    return note if isinstance(note, dict) else {}


def episodes() -> list[sqlite3.Row]:
    with connect() as db:
        return db.execute(
            """SELECT e.*, COALESCE(MIN(p.published_at), e.created_at) AS first_post
               FROM episodes e LEFT JOIN posts p ON p.episode_id = e.id
               GROUP BY e.id ORDER BY first_post DESC"""
        ).fetchall()


def video_file(path: str) -> PureWindowsPath:
    """A saved video path, split on / and on \\ alike: a database brought over from a Windows PC to a Mac or Linux
    keeps its backslashes, which those systems don't split on (and the file names still match up)."""
    return PureWindowsPath(path)


def episode_for_video(name: str) -> sqlite3.Row | None:
    """The newest episode made from a video with this file name, whichever folder the file was in then."""
    name = video_file(name).name.lower()
    with connect() as db:
        rows = db.execute("SELECT * FROM episodes WHERE video IS NOT NULL ORDER BY id DESC").fetchall()
    return next((e for e in rows if video_file(e["video"]).name.lower() == name), None)


def create_episode(title: str, video: str | None = None, created_at: str | None = None) -> int:
    with connect() as db:
        cur = db.execute(
            "INSERT INTO episodes (title, video, created_at) VALUES (?,?,?)",
            (title, video, created_at or now_iso()),
        )
        return cur.lastrowid


def add_post(platform: str, post_id: str, episode_id: int | None, title: str, url: str | None,
             published_at: str | None = None) -> None:
    """A post ReelForge made itself (pushed, or manual-<episode id> for typed-in numbers), so its episode is known."""
    link = "publish" if episode_id is not None else None
    with connect() as db:
        db.execute(
            """INSERT INTO posts (platform, post_id, episode_id, title, url, published_at, link) VALUES (?,?,?,?,?,?,?)
               ON CONFLICT (platform, post_id) DO UPDATE SET
                 episode_id = COALESCE(excluded.episode_id, posts.episode_id),
                 link = COALESCE(excluded.link, posts.link)""",
            (platform, post_id, episode_id, title, url, published_at or now_iso(), link),
        )


def retire_typed() -> int:
    """Drop the numbers you typed in for an episode on a platform (a manual-<episode> post and its readings) once
    that episode's real post there has been read since you typed them: ReelForge reads the platform itself now, so
    the typed ones would only sit beside it with numbers that never move. Typed numbers stay for an episode whose
    post hasn't been found, and for one whose post was last read before you typed them (the platform's log-in ran
    out, and typing is how its numbers are kept up since). Returns how many went."""
    with connect() as db:
        gone = db.execute(
            """SELECT platform, post_id FROM posts t WHERE post_id LIKE 'manual-%' AND episode_id IS NOT NULL
               AND EXISTS (SELECT 1 FROM posts r WHERE r.platform = t.platform AND r.episode_id = t.episode_id
                           AND r.post_id NOT LIKE 'manual-%' AND r.last_ts > COALESCE(t.last_ts, ''))""").fetchall()
        for table in ("post_stats", "posts"):
            db.executemany(f"DELETE FROM {table} WHERE platform = ? AND post_id = ?", [tuple(r) for r in gone])
    return len(gone)


def link_post(platform: str, post_id: str, episode_id: int | None, how: str | None = "caption") -> None:
    """File a post under an episode, noting how (see LINKS). None for either unlinks it, for auto_link to decide."""
    if episode_id is None or how is None:
        episode_id = how = None
    elif how not in LINKS:
        raise ValueError(f"unknown link {how!r}")
    with connect() as db:
        db.execute("UPDATE posts SET episode_id = ?, link = ? WHERE platform = ? AND post_id = ?",
                   (episode_id, how, platform, post_id))


def set_post_episode(platform: str, post_id: str, episode_id: int | None) -> None:
    """Your choice of episode for a post; None means it isn't an episode. auto_link never changes either."""
    with connect() as db:
        db.execute("UPDATE posts SET episode_id = ?, link = 'hand' WHERE platform = ? AND post_id = ?",
                   (episode_id, platform, post_id))


def relink(changes: list[tuple], new_episodes: dict[int, tuple[str, str]]) -> int:
    """Apply what auto_link worked out, in one go. Returns how many posts changed episode.

    changes: (platform, post_id, episode_id, link as read, episode_id, link to set). A post that changed since it was
    read (you linked it by hand meanwhile) is left alone. A negative episode_id is a key of new_episodes: the
    (title, created_at) of an episode to create.
    """
    if not changes:
        return 0
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        now = {(r[0], r[1]): (r[2], r[3]) for r in db.execute("SELECT platform, post_id, episode_id, link FROM posts")}
        todo = [c for c in changes if now.get((c[0], c[1])) == (c[2], c[3])]
        ids = {k: db.execute("INSERT INTO episodes (title, created_at) VALUES (?,?)", v).lastrowid
               for k, v in new_episodes.items() if any(c[4] == k for c in todo)}
        db.executemany("UPDATE posts SET episode_id = ?, link = ? WHERE platform = ? AND post_id = ?",
                       [(ids.get(c[4], c[4]), c[5], c[0], c[1]) for c in todo])
    return sum(c[2] != c[4] for c in todo)


def all_posts() -> list[sqlite3.Row]:
    with connect() as db:
        return db.execute("SELECT * FROM posts ORDER BY published_at, platform, post_id").fetchall()


def linked_posts() -> list[sqlite3.Row]:
    with connect() as db:
        return db.execute("SELECT * FROM posts WHERE episode_id IS NOT NULL").fetchall()


def unlinked_posts() -> list[sqlite3.Row]:
    """Posts with no episode, except those you marked as not an episode."""
    with connect() as db:
        return db.execute(
            "SELECT * FROM posts WHERE episode_id IS NULL AND link IS NOT 'hand' ORDER BY published_at"
        ).fetchall()


def log_upload(platform: str, video: str, status: str, post_id: str | None = None,
               url: str | None = None, message: str = "") -> None:
    with connect() as db:
        db.execute(
            "INSERT INTO uploads (ts, platform, video, status, post_id, url, message) VALUES (?,?,?,?,?,?,?)",
            (now_iso(), platform, video, status, post_id, url, message),
        )


def uploads(limit: int | None = 30) -> list[sqlite3.Row]:
    """The upload log, newest first; limit=None gives all of it."""
    limit = -1 if limit is None else limit
    with connect() as db:
        return db.execute("SELECT * FROM uploads ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
