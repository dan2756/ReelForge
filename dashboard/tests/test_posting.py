"""The posting ledger (dashboard.posting): making jobs and what refuses one, every move a job can and can't make,
claiming, the one press of Post, what the clock and a stats read decide, and the video's identity. Then the check
before a post (dashboard.videos.approval): every state a video can be in. Nothing here needs a window, a browser or
the extension: "the extension" is whoever calls claim and advance."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
import tempfile
import threading
import time
import types
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from dashboard import analytics, db, posting, videos
from dashboard.config import PLATFORM_KEYS
from dashboard.platforms import Account, Post
from dashboard.posting import Refused

CAPTION, TAGS = "What if gravity doubled every second?", "#whatif #physics #gravity"
CHROME, EDGE = "install-chrome", "install-edge"  # two browsers with the extension
STEPS = ("opening", "uploading", "filling", "ready")


class LedgerTest(unittest.TestCase):
    """A channel with one finished video that didn't come from the pipeline (so nothing has to approve it)."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.addCleanup(setattr, db, "_path", db._path)
        db.init(self.root / "stats.db")
        (self.root / "videos").mkdir()
        self.video = self.root / "videos" / "ep01.mp4"
        self.video.write_bytes(b"the first cut")
        self.cfg = {"videos_dir": str(self.root / "videos"), "episodes_dir": str(self.root / "episodes"),
                    "completed_dir": str(self.root / "completed"), **{key: {} for key in PLATFORM_KEYS}}

    def batch(self, *platforms: str, video: Path | None = None, **kw) -> dict:
        return posting.create_batch(video or self.video, list(platforms or ("tiktok",)), CAPTION, TAGS, self.cfg,
                                    **kw)

    def job(self, platform: str = "tiktok", **kw) -> dict:
        (job,) = self.batch(platform, **kw)["jobs"]
        return job

    def walk(self, job: dict, *states: str, install: str = CHROME) -> dict:
        """The extension takes a job and reports these states, one after the other."""
        out = posting.claim(job["id"], install)
        for state in states:
            out = posting.advance(job["id"], state, install=install)
        return out

    def state(self, job: dict) -> str:
        return posting.get(job["id"])["state"]

    def refused(self, code: str, fn, *args, **kw) -> Refused:
        with self.assertRaises(Refused) as caught:
            fn(*args, **kw)
        self.assertEqual(caught.exception.code, code)
        return caught.exception

    def age(self, job: dict, minutes: float, column: str = "updated_at") -> None:
        """As if the job had last been heard of (or made: created_at) this long ago."""
        then = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat(timespec="seconds")
        with db.connect() as c:
            c.execute(f"UPDATE post_jobs SET {column} = ? WHERE id = ?", (then, job["id"]))

    def events(self, job: dict) -> list[tuple]:
        with db.connect() as c:
            return [tuple(r) for r in c.execute("SELECT state, step, detail FROM post_events WHERE job = ? ORDER BY id",
                                                (job["id"],))]


class Making(LedgerTest):
    def test_one_press_of_post_makes_a_job_for_each_platform(self):
        before = db.changes()
        made = self.batch("tiktok", "youtube")
        self.assertRegex(made["batch"], r"^pb_[0-9a-f]{16}$")
        self.assertEqual(made["blocked"], [])
        self.assertEqual([j["platform"] for j in made["jobs"]], ["tiktok", "youtube"])
        (episode,) = db.episodes()
        self.assertEqual((episode["title"], episode["video"]), (CAPTION, str(self.video)))
        for job in made["jobs"]:
            self.assertRegex(job["id"], r"^pj_[0-9a-f]{16}$")
            self.assertEqual((job["batch"], job["state"], job["via"], job["press"], job["superseded"]),
                             (made["batch"], "queued", "extension", 0, 0))
            self.assertEqual((job["video"], job["video_path"], job["video_size"]),
                             ("ep01.mp4", str(self.video), len(b"the first cut")))
            self.assertEqual(job["video_sha256"], hashlib.sha256(b"the first cut").hexdigest())
            self.assertEqual((job["episode_id"], job["review"]), (episode["id"], "none"))
            self.assertEqual(self.events(job), [("queued", None, None)])
        # The upload log has them too, so a post that goes up next is filed under this video and RECENT UPLOADS
        # lists them.
        self.assertEqual([(u["platform"], u["video"], u["status"], u["message"], u["ts"]) for u in db.uploads()],
                         [(j["platform"], "ep01.mp4", "extension", "posting with the extension", j["created_at"])
                          for j in reversed(made["jobs"])])
        self.assertEqual(db.changes(), before + 1)  # every open window redraws
        self.assertEqual(posting.in_batch(made["batch"]), made["jobs"])
        self.assertEqual(posting.active_batches(), [made["batch"]])

    def test_the_text_is_taken_once_in_each_platforms_shape(self):
        made = self.batch(*PLATFORM_KEYS)
        text = {j["platform"]: json.loads(j["text"]) for j in made["jobs"]}
        self.assertEqual(text, {
            "tiktok": {"caption": f"{CAPTION} #fyp {TAGS}"},
            "youtube": {"title": f"{CAPTION} #shorts", "description": TAGS},
            "instagram": {"caption": f"{CAPTION} {TAGS}"},
            "x": {"text": CAPTION},
        })

    def test_a_job_under_way_blocks_another_and_force_does_not_help(self):
        job = self.job()
        for state in ("queued", "claimed", *STEPS):
            if state == "claimed":
                posting.claim(job["id"], CHROME)
            elif state != "queued":
                posting.advance(job["id"], state, install=CHROME)
            for force in ((), ("tiktok",)):
                again = self.batch("tiktok", "youtube", force=force)
                (block,) = [b for b in again["blocked"] if b["platform"] == "tiktok"]
                self.assertEqual((block["reason"], block["job"], block["state"]), ("in_progress", job["id"], state))
                self.assertIn("Cancel it first", block["message"])
                # The platform that was free got its job the first time round, and is blocked from then on too.
                self.assertEqual(len(again["jobs"]) + len(again["blocked"]), 2)
        self.assertEqual(posting.get(job["id"])["superseded"], 0)

    def test_a_posted_video_is_posted_again_only_when_you_say_so(self):
        job = self.walk(self.job(), *STEPS)
        posting.advance(job["id"], "posted", install=CHROME, url="https://www.tiktok.com/@me/video/7300000000000000001")
        again = self.batch()
        self.assertEqual((again["batch"], again["jobs"]), (None, []))
        (block,) = again["blocked"]
        self.assertEqual((block["reason"], block["job"], block["url"]),
                         ("posted", job["id"], "https://www.tiktok.com/@me/video/7300000000000000001"))
        forced = self.batch(force=["tiktok"])
        self.assertEqual([j["state"] for j in forced["jobs"]], ["queued"])
        self.assertEqual(posting.get(job["id"])["superseded"], 1)
        self.assertEqual(posting.get(job["id"])["state"], "posted")  # set aside, not rewritten
        self.assertEqual(self.events(job)[-1], ("posted", None, "set aside: posted again"))

    def test_an_unknown_job_blocks_until_it_is_settled_or_you_post_again(self):
        job = self.walk(self.job(), *STEPS, "unknown")
        (block,) = self.batch()["blocked"]
        self.assertEqual((block["reason"], block["job"]), ("unknown", job["id"]))
        self.assertEqual(len(self.batch(force=["tiktok"])["jobs"]), 1)
        self.assertEqual(posting.get(job["id"])["superseded"], 1)

    def test_a_job_that_ended_without_a_post_leaves_the_way_free(self):
        for end in ("failed", "cancelled", "expired"):
            job = self.job()
            if end == "expired":
                self.age(job, 11, "created_at")
                self.assertEqual(posting.expire_stale(), 1)
            else:
                posting.advance(job["id"], end, by="app", error_code="timeout" if end == "failed" else None)
            self.assertEqual(self.state(job), end)
        self.assertEqual(self.job()["state"], "queued")

    def test_a_post_the_stats_read_already_found_blocks_too(self):
        episode = db.create_episode(CAPTION, str(self.video))
        db.add_post("youtube", "abcdefghijk", episode, CAPTION, "https://youtu.be/abcdefghijk")
        made = self.batch("tiktok", "youtube")
        self.assertEqual([j["platform"] for j in made["jobs"]], ["tiktok"])
        (block,) = made["blocked"]
        self.assertEqual((block["platform"], block["reason"], block["url"]),
                         ("youtube", "spotted", "https://youtu.be/abcdefghijk"))
        self.assertIn("Already posted", block["message"])
        self.assertEqual([j["platform"] for j in self.batch("youtube", force=("youtube",))["jobs"]], ["youtube"])

    def test_a_video_is_its_bytes_not_its_name(self):
        job = self.job()
        renamed = self.root / "videos" / "final cut.mp4"
        renamed.write_bytes(self.video.read_bytes())
        (block,) = self.batch(video=renamed)["blocked"]
        self.assertEqual((block["reason"], block["job"]), ("in_progress", job["id"]))
        self.video.write_bytes(b"a second cut, under the same name")
        remade = self.job()
        self.assertNotEqual(remade["video_sha256"], job["video_sha256"])
        self.assertTrue(posting.unchanged(remade))
        self.assertFalse(posting.unchanged(posting.get(job["id"])))  # its file was replaced

    def test_two_presses_at_the_same_moment_make_one_job(self):
        go, results = threading.Barrier(2), []

        def press() -> None:
            go.wait(5)
            results.append(self.batch())

        threads = [threading.Thread(target=press) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(20)
        self.assertEqual(sorted(len(r["jobs"]) for r in results), [0, 1])
        (lost,) = [r for r in results if not r["jobs"]]
        self.assertEqual(lost["blocked"][0]["reason"], "in_progress")
        self.assertEqual(len(db.episodes()), 1)
        # And the index itself refuses a second live row, whatever the code above it does.
        (job,) = [r for r in results if r["jobs"]][0]["jobs"]
        twin = {**job, "id": "pj_" + "0" * 16, "state": "unknown"}
        with self.assertRaises(sqlite3.IntegrityError), db.connect() as c:
            c.execute(f"INSERT INTO post_jobs ({', '.join(twin)}) VALUES ({','.join('?' * len(twin))})",
                      list(twin.values()))
        for free in ("failed", "superseded"):  # ...and only a live one
            with db.connect() as c:
                row = {**twin, "state": "failed"} if free == "failed" else {**twin, "id": "pj_" + "1" * 16,
                                                                           "superseded": 1}
                c.execute(f"INSERT INTO post_jobs ({', '.join(row)}) VALUES ({','.join('?' * len(row))})",
                          list(row.values()))

    def test_the_episode_is_the_one_the_video_has_or_a_twin_of_its_posts(self):
        twin = db.create_episode(CAPTION, None)  # posts spotted before anything was started here
        db.add_post("x", "1900000000000000001", None, CAPTION, "https://x.com/me/status/1900000000000000001")
        db.link_post("x", "1900000000000000001", twin, "twin")
        made = self.batch("tiktok", "x")
        self.assertEqual([(e["id"], e["video"]) for e in db.episodes()], [(twin, str(self.video))])
        self.assertEqual([(j["platform"], j["episode_id"]) for j in made["jobs"]], [("tiktok", twin)])
        self.assertEqual(made["blocked"][0]["reason"], "spotted")  # the twin's post is this video's

    def test_what_stops_a_batch_before_anything_is_made(self):
        cases = [
            (dict(video=self.root / "videos" / "gone.mp4"), "missing"),
            (dict(demo=True), "review"),
        ]
        for kw, reason in cases:
            made = self.batch("tiktok", "youtube", **kw)
            self.assertEqual((made["batch"], made["jobs"]), (None, []))
            self.assertEqual([(b["platform"], b["reason"]) for b in made["blocked"]],
                             [("tiktok", reason), ("youtube", reason)])
        empty = posting.create_batch(self.video, ["tiktok"], "#fyp #whatif", TAGS, self.cfg)
        self.assertEqual([b["reason"] for b in empty["blocked"]], ["empty"])
        self.assertEqual((db.episodes(), db.uploads(), posting.recent()), ([], [], []))

    def test_x_takes_280_characters_and_is_never_pressed_for_you(self):
        with mock.patch.object(videos, "X_TAGS", True):
            long = posting.create_batch(self.video, ["x", "tiktok"], "What if " + "very " * 55 + "long?", TAGS,
                                        self.cfg, press=True)
        self.assertEqual([(b["platform"], b["reason"]) for b in long["blocked"]], [("x", "too_long")])
        self.assertEqual([(j["platform"], j["press"]) for j in long["jobs"]], [("tiktok", 1)])
        posting.cancel(long["jobs"][0]["id"])
        made = self.batch("x", "tiktok", press=True)
        self.assertEqual({j["platform"]: j["press"] for j in made["jobs"]}, {"x": 0, "tiktok": 1})
        self.assertEqual(self.batch("vimeo")["blocked"][0]["reason"], "unknown_platform")

    def test_a_pipeline_video_needs_its_approval(self):
        ep = self.root / "episodes" / "ep02_duck"
        (ep / "final").mkdir(parents=True)
        (ep / "episode.json").write_text(json.dumps({"id": "ep02_duck", "title": CAPTION}), encoding="utf-8")
        video = ep / "final" / "ep02_duck.mp4"
        video.write_bytes(b"rendered")
        made = self.batch("tiktok", "youtube", video=video)
        self.assertEqual([b["reason"] for b in made["blocked"]], ["review", "review"])
        self.assertIn("QA hasn't checked this video yet", made["blocked"][0]["message"])
        self.assertEqual(posting.recent(), [])
        approve(ep, video)
        self.assertEqual([j["review"] for j in self.batch("tiktok", video=video)["jobs"]], ["approved"])

    def test_options_and_the_route_are_kept_per_platform(self):
        made = self.batch("youtube", "tiktok", via={"youtube": "api"}, options={"youtube": {"privacy": "private"}})
        by = {j["platform"]: j for j in made["jobs"]}
        self.assertEqual((by["youtube"]["via"], json.loads(by["youtube"]["options"])), ("api", {"privacy": "private"}))
        self.assertEqual((by["tiktok"]["via"], by["tiktok"]["options"]), ("extension", "{}"))
        self.assertEqual({u["platform"]: (u["status"], u["message"]) for u in db.uploads()},
                         {"youtube": ("api", "posting with its API"),
                          "tiktok": ("extension", "posting with the extension")})

    def test_trying_again_uses_the_text_the_failed_job_had(self):
        job = self.walk(self.job(press=True), "opening")
        self.refused("bad_transition", posting.retry, job["id"], self.cfg)  # still under way
        posting.advance(job["id"], "failed", install=CHROME, error_code="hook_not_found", step="find_input")
        (again,) = posting.retry(job["id"], self.cfg)["jobs"]
        self.assertNotEqual(again["id"], job["id"])
        self.assertEqual((again["text"], again["press"], again["state"], again["episode_id"]),
                         (job["text"], 1, "queued", job["episode_id"]))
        self.assertEqual(len(db.episodes()), 1)


class Moving(LedgerTest):
    def test_the_whole_way_from_queued_to_posted(self):
        job = self.job()
        before = db.changes()
        posting.claim(job["id"], CHROME)
        for state, step in (("opening", "open_tab"), ("uploading", "attach"), ("filling", "fill_caption"),
                            ("ready", "ready")):
            now = posting.advance(job["id"], state, install=CHROME, step=step)
            self.assertEqual((now["state"], now["step"]), (state, step))
        done = posting.advance(job["id"], "posted", install=CHROME, post_id="7300000000000000001",
                               url="https://www.tiktok.com/@me/video/7300000000000000001")
        self.assertEqual((done["state"], done["post_id"]), ("posted", "7300000000000000001"))
        self.assertEqual([e[0] for e in self.events(job)],
                         ["queued", "claimed", "opening", "uploading", "filling", "ready", "posted"])
        self.assertEqual(db.changes(), before + 6)  # each change of state
        self.assertEqual(posting.active_batches(), [])

    def test_steps_can_be_skipped_forwards_and_nothing_goes_back(self):
        order = posting.ORDER
        for i, old in enumerate(order):
            for j, new in enumerate(order):
                expected = j >= i if old != "posted" else new == "posted"
                self.assertEqual(posting.allowed(old, new, "app"), expected, f"{old} -> {new}")
        job = self.walk(self.job(), "uploading")  # claimed straight to uploading
        self.assertEqual(job["state"], "uploading")
        for back in ("opening", "uploading"):
            if back != job["state"]:
                self.refused("bad_transition", posting.advance, job["id"], back, install=CHROME)
        posting.advance(job["id"], "ready", install=CHROME)
        self.refused("bad_transition", posting.advance, job["id"], "filling", install=CHROME)
        self.assertEqual(self.state(job), "ready")

    def test_any_job_under_way_can_fail_or_be_cancelled_and_then_it_is_over(self):
        for old in posting.IN_FLIGHT:
            for end in ("failed", "cancelled"):
                for by in ("extension", "app"):
                    self.assertTrue(posting.allowed(old, end, by), f"{old} -> {end} by {by}")
            self.assertTrue(posting.allowed(old, "cancelled", "person"))
        for over in ("posted", "failed", "cancelled", "expired"):
            for new in (*posting.LIVE, *posting.FREE):
                for by in ("extension", "app", "person", "reconcile"):
                    self.assertEqual(posting.allowed(over, new, by), new == over and (
                        by in ("app",) or (by == "extension" and new in posting.EXTENSION_STATES)
                        or (by == "person" and new in ("posted", "cancelled")) or (by == "reconcile" and
                                                                                    new == "posted")),
                        f"{over} -> {new} by {by}")
        job = self.walk(self.job(), "opening")
        failed = posting.advance(job["id"], "failed", install=CHROME, step="find_input", error_code="hook_not_found",
                                 error_message="TikTok changed its page.")
        self.assertEqual((failed["state"], failed["step"], failed["error_code"], failed["error_message"]),
                         ("failed", "find_input", "hook_not_found", "TikTok changed its page."))
        for new in ("uploading", "posted", "cancelled", "unknown"):
            self.refused("bad_transition", posting.advance, job["id"], new, install=CHROME)
        self.assertEqual(posting.advance(job["id"], "failed", install=CHROME), failed)  # said twice: nothing changes

    def test_only_queued_expires_and_only_ready_or_posting_goes_unknown(self):
        for old in (*posting.LIVE, *posting.FREE):
            self.assertEqual(posting.allowed(old, "expired", "app"), old in ("queued", "expired"), old)
            self.assertEqual(posting.allowed(old, "unknown", "app"), old in ("ready", "posting", "unknown"), old)
            self.assertEqual(posting.allowed(old, "unknown", "extension"), old in ("ready", "posting", "unknown"), old)

    def test_the_extension_reports_states_but_never_grants_itself_one(self):
        for new in ("queued", "claimed", "posting", "expired"):
            for old in posting.IN_FLIGHT:
                self.assertFalse(posting.allowed(old, new, "extension"), f"{old} -> {new}")
        job = self.walk(self.job(), *STEPS)
        self.refused("bad_transition", posting.advance, job["id"], "posting", install=CHROME)

    def test_an_unknown_job_is_settled_by_you_or_a_stats_read_and_nobody_else(self):
        for new in (*posting.LIVE, *posting.FREE):
            if new != "unknown":
                self.assertFalse(posting.allowed("unknown", new, "extension"), new)
                self.assertFalse(posting.allowed("unknown", new, "app"), new)
            self.assertEqual(posting.allowed("unknown", new, "person"), new in ("posted", "cancelled"), new)
            self.assertEqual(posting.allowed("unknown", new, "reconcile"), new == "posted", new)
        job = self.walk(self.job(), *STEPS, "unknown")
        for new in ("posted", "failed", "cancelled", "ready"):
            self.refused("bad_transition", posting.advance, job["id"], new, install=CHROME)
        self.assertEqual(self.state(job), "unknown")
        self.assertEqual(posting.resolve(job["id"], True)["state"], "posted")
        other = self.walk(self.job("youtube"), *STEPS, "unknown")
        said = posting.resolve(other["id"], False)
        self.assertEqual((said["state"], said["detail"]), ("cancelled", "you said it didn't post"))
        self.assertEqual(self.job("youtube")["state"], "queued")  # free for another try

    def test_its_posted_is_your_word_once_the_post_was_ready_to_go(self):
        for old in posting.IN_FLIGHT:
            self.assertEqual(posting.allowed(old, "posted", "person"), old in ("ready", "posting"), old)
            self.assertEqual(posting.allowed(old, "posted", "reconcile"), old in ("ready", "posting"), old)
        job = self.walk(self.job(), "uploading")
        self.refused("bad_transition", posting.resolve, job["id"], True)
        self.assertEqual(posting.cancel(job["id"])["state"], "cancelled")

    def test_a_state_said_again_updates_the_step_and_the_progress(self):
        job = self.walk(self.job(), "uploading")
        signature, changes, logged = posting.signature(), db.changes(), len(self.events(job))
        for percent in (0.2, 0.6):
            now = posting.advance(job["id"], "uploading", install=CHROME, step="upload", progress=percent)
        self.assertEqual((now["state"], now["step"], now["progress"]), ("uploading", "upload", 0.6))
        self.assertEqual(db.changes(), changes)  # not a change of state: no window reloads its numbers
        self.assertEqual(posting.signature(), signature + 2)  # ...but each one shows (the window watches this)
        self.assertEqual(self.events(job)[logged:], [("uploading", "upload", None)])  # logged once, not per percent
        moved = posting.advance(job["id"], "filling", install=CHROME)
        self.assertEqual((moved["step"], moved["progress"], moved["detail"]), (None, None, None))

    def test_no_such_job(self):
        self.refused("not_found", posting.advance, "pj_none", "opening")
        self.refused("not_found", posting.claim, "pj_none", CHROME)
        self.refused("not_found", posting.grant_press, "pj_none", CHROME)
        self.assertIsNone(posting.get("pj_none"))


class Claiming(LedgerTest):
    def test_the_first_browser_to_ask_gets_the_job(self):
        job = self.job()
        mine = posting.claim(job["id"], CHROME)
        self.assertEqual((mine["state"], mine["claimed_by"]), ("claimed", CHROME))
        self.assertEqual(self.refused("already_claimed", posting.claim, job["id"], EDGE).job["claimed_by"], CHROME)
        self.assertEqual(posting.claim(job["id"], CHROME), mine)  # its own answer again, should the first get lost
        self.refused("not_yours", posting.advance, job["id"], "opening", install=EDGE)
        posting.advance(job["id"], "opening", install=CHROME)
        self.refused("already_claimed", posting.claim, job["id"], CHROME)  # it has started

    def test_two_browsers_asking_at_once(self):
        job = self.job()
        go, won, lost = threading.Barrier(2), [], []

        def ask(install: str) -> None:
            go.wait(5)
            try:
                won.append(posting.claim(job["id"], install)["claimed_by"])
            except Refused as e:
                lost.append(e.code)

        threads = [threading.Thread(target=ask, args=(who,)) for who in (CHROME, EDGE)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(20)
        self.assertEqual((len(won), lost), (1, ["already_claimed"]))
        self.assertEqual(posting.get(job["id"])["claimed_by"], won[0])

    def test_a_job_nobody_reported_on_cannot_be_moved_by_a_browser(self):
        job = self.job()
        self.refused("not_yours", posting.advance, job["id"], "opening", install=CHROME)  # not claimed yet

    def test_what_cannot_be_claimed(self):
        over = self.job()
        posting.cancel(over["id"])
        self.refused("not_claimable", posting.claim, over["id"], CHROME)
        late = self.job()
        self.age(late, 11, "created_at")  # the clock hasn't run yet, but ten minutes are ten minutes
        self.assertIn("waited too long", self.refused("not_claimable", posting.claim, late["id"], CHROME).message)
        self.assertEqual(self.state(late), "expired")
        api = self.job("youtube", via={"youtube": "api"})
        self.refused("not_claimable", posting.claim, api["id"], CHROME)
        self.assertEqual(self.state(api), "queued")


class Pressing(LedgerTest):
    """Press Post for me: only when you asked for it on this job, never on X, and only on a platform where a post
    made with the extension has already gone up."""

    def earlier_post(self, platform: str = "tiktok") -> None:
        first = posting.create_batch(self.video, [platform], "The one before", TAGS, self.cfg)
        self.assertEqual(first["blocked"], [])
        self.walk(first["jobs"][0], *STEPS, "posted")
        self.video.write_bytes(self.video.read_bytes() + b"+")  # the next video

    def test_post_is_pressed_exactly_once(self):
        self.earlier_post()
        job = self.walk(self.job(press=True), *STEPS)
        pressed = posting.grant_press(job["id"], CHROME)
        self.assertEqual((pressed["state"], pressed["step"]), ("posting", "press"))
        self.refused("already_pressed", posting.grant_press, job["id"], CHROME)
        posting.advance(job["id"], "posted", install=CHROME)
        self.refused("already_pressed", posting.grant_press, job["id"], CHROME)

    def test_two_asks_at_once_get_one_grant(self):
        self.earlier_post()
        job = self.walk(self.job(press=True), *STEPS)
        go, answers = threading.Barrier(2), []

        def ask() -> None:
            go.wait(5)
            try:
                answers.append(posting.grant_press(job["id"], CHROME)["state"])
            except Refused as e:
                answers.append(e.code)

        threads = [threading.Thread(target=ask) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(20)
        self.assertEqual(sorted(answers), ["already_pressed", "posting"])

    def test_not_before_everything_is_filled_in(self):
        self.earlier_post()
        job = self.walk(self.job(press=True), "uploading")
        self.refused("not_ready", posting.grant_press, job["id"], CHROME)
        self.assertEqual(self.state(job), "uploading")

    def test_not_unless_you_switched_stop_before_posting_off(self):
        self.earlier_post()
        job = self.walk(self.job(), *STEPS)
        self.refused("press_not_allowed", posting.grant_press, job["id"], CHROME)
        self.assertEqual(self.state(job), "ready")

    def test_not_on_a_platform_without_a_post_you_pressed_yourself(self):
        self.assertFalse(posting.press_unlocked("tiktok"))
        job = self.walk(self.job(press=True), *STEPS)
        self.refused("press_not_allowed", posting.grant_press, job["id"], CHROME)
        posting.advance(job["id"], "posted", install=CHROME)  # pressed by you
        self.assertTrue(posting.press_unlocked("tiktok"))
        self.assertFalse(posting.press_unlocked("youtube"))  # each platform earns it by itself

    def test_never_on_x(self):
        self.earlier_post("x")
        self.assertFalse(posting.press_unlocked("x"))
        job = self.walk(self.job("x", press=True), *STEPS)
        with db.connect() as c:  # even if a row said so
            c.execute("UPDATE post_jobs SET press = 1 WHERE id = ?", (job["id"],))
        self.refused("press_not_allowed", posting.grant_press, job["id"], CHROME)

    def test_only_the_browser_that_has_the_job(self):
        self.earlier_post()
        job = self.walk(self.job(press=True), *STEPS)
        self.refused("not_yours", posting.grant_press, job["id"], EDGE)
        self.assertEqual(self.state(job), "ready")


class Clock(LedgerTest):
    def test_a_job_nobody_picks_up_expires_after_ten_minutes(self):
        fresh, old = self.job("tiktok"), self.job("youtube")
        self.age(fresh, 9, "created_at")
        self.age(old, 10, "created_at")
        before = db.changes()
        self.assertEqual(posting.expire_stale(), 1)
        self.assertEqual((self.state(fresh), self.state(old)), ("queued", "expired"))
        self.assertEqual(posting.get(old["id"])["detail"], "not picked up in time")
        self.assertEqual(db.changes(), before + 1)
        self.assertEqual(posting.expire_stale(), 0)

    def test_ready_and_silent_for_45_minutes_is_unknown(self):
        quiet, alive = self.walk(self.job("tiktok"), *STEPS), self.walk(self.job("youtube"), *STEPS)
        self.age(quiet, 46)
        self.age(alive, 46)
        posting.advance(alive["id"], "ready", install=CHROME)  # the extension is still watching the tab
        self.assertEqual(posting.expire_stale(), 1)
        self.assertEqual((self.state(quiet), self.state(alive)), ("unknown", "ready"))
        self.age(alive, 44)
        self.assertEqual(posting.expire_stale(), 0)

    def test_posting_and_silent_for_15_minutes_is_unknown(self):
        first = self.job(press=True)
        self.walk(first, *STEPS, "posted")
        self.video.write_bytes(b"the next video")
        job = self.walk(self.job(press=True), *STEPS)
        posting.grant_press(job["id"], CHROME)
        self.age(job, 14)
        self.assertEqual(posting.expire_stale(), 0)
        self.age(job, 15)
        self.assertEqual(posting.expire_stale(), 1)
        unknown = posting.get(job["id"])
        self.assertEqual((unknown["state"], unknown["detail"]), ("unknown", "no confirmation after Post was pressed"))

    def test_the_clock_leaves_every_other_state_alone(self):
        job = self.walk(self.job(), "uploading")
        self.age(job, 600)
        self.age(job, 600, "created_at")
        self.assertEqual(posting.expire_stale(), 0)
        self.assertEqual(self.state(job), "uploading")
        self.assertEqual(posting.expire_stale(datetime.now(timezone.utc) + timedelta(days=1)), 0)


class Reconciling(LedgerTest):
    """A stats read is the second witness: it files a new post under the job's episode."""

    def spot(self, job: dict, post_id: str = "7300000000000000001", minutes: float = 3, platform: str | None = None,
             episode: int | None = None) -> str:
        """The stats read finds a post that went up this long after the job was made, and files it."""
        at = (db.parse(job["created_at"]) + timedelta(minutes=minutes)).isoformat(timespec="seconds")
        platform = platform or job["platform"]
        url = f"https://example.invalid/{platform}/{post_id}"
        db.add_post(platform, post_id, None, CAPTION, url, at)
        db.link_post(platform, post_id, episode or job["episode_id"], "caption")
        return url

    def test_a_post_found_under_the_episode_settles_a_job_that_was_waiting(self):
        for i, waiting in enumerate(("ready", "unknown")):
            video = self.root / "videos" / f"video{i}.mp4"
            video.write_bytes(f"video {i}".encode())
            job = self.walk(posting.create_batch(video, ["tiktok"], f"Video number {i}", TAGS, self.cfg)["jobs"][0],
                            *STEPS, *(["unknown"] if waiting == "unknown" else []))
            url = self.spot(job, f"73000000000000000{i}")
            self.assertEqual(posting.reconcile(), 1)
            done = posting.get(job["id"])
            self.assertEqual((done["state"], done["post_id"], done["url"], done["detail"]),
                             ("posted", f"73000000000000000{i}", url, "found by a stats read"))
            self.assertEqual(posting.reconcile(), 0)
        with db.connect() as c:  # the reader's link stays as it made it
            self.assertEqual({r[0] for r in c.execute("SELECT link FROM posts")}, {"caption"})

    def test_a_job_being_posted_is_settled_too(self):
        self.walk(self.job(press=True), *STEPS, "posted")
        self.video.write_bytes(b"the next video")
        job = self.walk(self.job(press=True), *STEPS)
        posting.grant_press(job["id"], CHROME)
        self.spot(job, episode=job["episode_id"])
        self.assertEqual(posting.reconcile(), 1)
        self.assertEqual(self.state(job), "posted")

    def test_what_does_not_settle_a_job(self):
        job = self.walk(self.job(), *STEPS)
        self.spot(job, "7300000000000000001", minutes=-30)             # up before the job was made
        self.spot(job, "abcdefghijk", platform="youtube")               # another platform
        other = db.create_episode("Another video", "other.mp4")
        self.spot(job, "7300000000000000002", episode=other)            # another video's post
        db.add_post("tiktok", f"manual-{job['episode_id']}", job["episode_id"], CAPTION, None)  # typed-in numbers
        self.assertEqual(posting.reconcile(), 0)
        self.assertEqual(self.state(job), "ready")
        early = self.walk(self.job("instagram"), "uploading")            # not waiting on a post yet
        self.spot(early, "3500000000000000001")
        self.assertEqual(posting.reconcile(), 0)
        self.assertEqual(self.state(early), "uploading")

    def test_a_post_with_another_caption_is_still_this_videos_by_when_it_went_up(self):
        # You changed the caption on the platform's page before pressing Post: the caption matcher has nothing to go
        # on, but the job was started for this video on that platform a few minutes before.
        job = self.walk(self.job("instagram"), *STEPS)
        went_up = (db.parse(job["created_at"]) + timedelta(minutes=4)).isoformat(timespec="seconds")
        db.record_snapshot("instagram", Account("@me", 100),
                           [Post("3500000000000000001", "New day, new video", "https://example.invalid/reel", went_up)])
        self.assertEqual(analytics.auto_link(), 1)
        with db.connect() as c:
            self.assertEqual(tuple(c.execute("SELECT episode_id, link FROM posts").fetchone()),
                             (job["episode_id"], "upload"))
        self.assertEqual(posting.reconcile(), 1)
        self.assertEqual(posting.get(job["id"])["url"], "https://example.invalid/reel")

    def test_a_clock_two_minutes_out_still_counts(self):
        job = self.walk(self.job(), *STEPS)
        self.spot(job, minutes=-1.5)
        self.assertEqual(posting.reconcile(), 1)

    def test_the_post_you_posted_again_over_does_not_settle_the_new_job(self):
        first = self.walk(self.job(), *STEPS)
        self.spot(first)
        self.assertEqual(posting.reconcile(), 1)
        again = self.walk(self.job(force=["tiktok"]), *STEPS, "unknown")
        self.assertEqual(posting.reconcile(), 0)  # the first post is still the first job's
        self.assertEqual(self.state(again), "unknown")
        self.spot(again, "7300000000000000009", minutes=4)
        self.assertEqual(posting.reconcile(), 1)
        self.assertEqual(posting.get(again["id"])["post_id"], "7300000000000000009")


class Posted(LedgerTest):
    def test_a_post_whose_id_the_extension_read_goes_into_the_stats(self):
        job = self.walk(self.job("youtube"), *STEPS)
        posting.advance(job["id"], "posted", install=CHROME, post_id="dQw4w9WgXcQ", url="https://youtu.be/dQw4w9WgXcQ")
        (post,) = db.posts_latest()
        self.assertEqual((post["platform"], post["post_id"], post["episode_id"], post["link"], post["url"]),
                         ("youtube", "dQw4w9WgXcQ", job["episode_id"], "publish", "https://youtu.be/dQw4w9WgXcQ"))
        self.assertEqual(post["title"], f"{CAPTION} #shorts")
        posting.advance(job["id"], "posted", install=CHROME, post_id="dQw4w9WgXcQ")  # said twice
        self.assertEqual(len(db.posts_latest()), 1)

    def test_an_id_the_stats_reader_would_not_use_is_left_out(self):
        job = self.walk(self.job("instagram"), *STEPS)
        done = posting.advance(job["id"], "posted", install=CHROME, post_id="DBx3kQwPabc",  # a shortcode, not the pk
                               url="https://www.instagram.com/reel/DBx3kQwPabc/")
        self.assertEqual((done["state"], done["post_id"], done["url"]),
                         ("posted", None, "https://www.instagram.com/reel/DBx3kQwPabc/"))
        self.assertEqual(db.posts_latest(), [])  # the reader files it, under its own id

    def test_the_id_can_follow_the_news_that_it_posted(self):
        job = self.walk(self.job(), *STEPS, "posted")
        self.assertEqual(db.posts_latest(), [])
        late = posting.advance(job["id"], "posted", install=CHROME, post_id="7300000000000000001")
        self.assertEqual(late["post_id"], "7300000000000000001")
        self.assertEqual(len(db.posts_latest()), 1)

    def test_a_job_as_the_extension_gets_it(self):
        job = self.walk(self.job(press=True), "uploading")
        posting.advance(job["id"], "uploading", install=CHROME, step="upload", progress=0.5, detail="half way")
        got = posting.job_json(posting.get(job["id"]), {"tiktok": "@renderedwrongtv"})
        self.assertEqual(got, {
            "id": job["id"], "batch": job["batch"], "platform": "tiktok", "via": "extension", "state": "uploading",
            "step": "upload", "progress": 0.5, "detail": "half way", "error_code": None, "error_message": None,
            "post_id": None, "url": None, "press": True, "review": "none",
            "video": {"name": "ep01.mp4", "size": 13, "sha256": hashlib.sha256(b"the first cut").hexdigest()},
            "text": {"caption": f"{CAPTION} #fyp {TAGS}"},
            "upload_url": "https://www.tiktok.com/tiktokstudio/upload", "handle": "@renderedwrongtv",
            "updated_at": got["updated_at"],
        })
        self.assertEqual(posting.job_json(posting.get(job["id"]))["handle"], "")
        json.dumps(got)  # nothing in it that can't go over the wire


class Standing(LedgerTest):
    """Where each video stands on each platform, for the window's list."""

    def test_the_live_job_else_the_newest_that_ended_without_a_post(self):
        failed = self.walk(self.job(), "opening")
        posting.advance(failed["id"], "failed", install=CHROME, error_code="timeout")
        self.assertEqual(posting.for_files([self.video])[self.video]["tiktok"]["id"], failed["id"])
        cancelled = self.job()
        posting.cancel(cancelled["id"])
        self.assertEqual(posting.for_files([self.video])[self.video]["tiktok"]["id"], cancelled["id"])
        live = self.job()
        self.assertEqual(posting.for_files([self.video])[self.video]["tiktok"]["id"], live["id"])
        self.walk(live, *STEPS, "posted")
        again = self.job(force=["tiktok"])
        self.assertEqual(posting.for_files([self.video])[self.video]["tiktok"]["id"], again["id"])  # not the old one

    def test_a_file_with_the_same_name_and_other_bytes_has_no_jobs(self):
        self.job()
        other = self.root / "elsewhere" / "ep01.mp4"
        other.parent.mkdir()
        other.write_bytes(b"another video altogether")
        untouched = self.root / "videos" / "ep02.mp4"
        untouched.write_bytes(b"never posted")
        with mock.patch.object(posting, "digest", wraps=posting.digest) as hashed:
            standing = posting.for_files([self.video, other, untouched])
        self.assertEqual(list(standing[self.video]), ["tiktok"])
        self.assertEqual(standing[other], {})
        self.assertNotIn(untouched, standing)
        self.assertNotIn(mock.call(untouched), hashed.mock_calls)  # a file without jobs is never read


class Digest(LedgerTest):
    def test_a_video_is_hashed_once_and_again_only_when_it_changes(self):
        sha = hashlib.sha256(b"the first cut").hexdigest()
        with mock.patch.object(Path, "open", autospec=True, side_effect=Path.open) as opened:
            def reads() -> int:
                """How many times the video was opened to be read through."""
                return sum(1 for call in opened.call_args_list if call.args[1:] == ("rb",))

            self.assertEqual(posting.digest(self.video), sha)
            self.assertEqual(posting.digest(self.video), sha)
            self.assertEqual(reads(), 1)
            # Another cut of the same length, a moment later: the time alone gives it away.
            st = self.video.stat()
            self.video.write_bytes(b"the 2nd cut..")
            os.utime(self.video, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))
            self.assertEqual(posting.digest(self.video), hashlib.sha256(b"the 2nd cut..").hexdigest())
            self.assertEqual(reads(), 2)
            self.video.write_bytes(b"and a longer third cut")
            self.assertEqual(posting.digest(self.video), hashlib.sha256(b"and a longer third cut").hexdigest())
            self.assertEqual(posting.digest(self.video), hashlib.sha256(b"and a longer third cut").hexdigest())
            self.assertEqual(reads(), 3)

    def test_it_is_kept_in_the_database_by_path_size_and_time(self):
        posting.digest(self.video)
        st = self.video.stat()
        self.assertEqual(db.get_meta(posting.DIGESTS), {
            str(self.video): [st.st_size, st.st_mtime_ns, hashlib.sha256(b"the first cut").hexdigest()]})
        other = self.root / "videos" / "ep02.mp4"
        other.write_bytes(b"another")
        posting.digest(other)
        self.video.unlink()
        other.write_bytes(b"another, recut")
        posting.digest(other)
        self.assertEqual(list(db.get_meta(posting.DIGESTS)), [str(other)])  # a video that is gone drops out

    def test_a_missing_video_is_an_error_for_the_caller(self):
        with self.assertRaises(OSError):
            posting.digest(self.root / "videos" / "gone.mp4")


def approve(ep: Path, video: Path, review: str | None = "Looked at every sheet.\nAPPROVED", passed: bool = True,
            **qa) -> None:
    """The pipeline's QA report for this exact file, and a review written after it."""
    st = video.stat()
    (ep / "build" / "qa").mkdir(parents=True, exist_ok=True)
    report = {"episode": ep.name, "pass": passed, "video": video.name,
              "fingerprint": f"{st.st_size}:{int(st.st_mtime)}", "checks": [], **qa}
    (ep / "build" / "qa" / "qa.json").write_text(json.dumps(report), encoding="utf-8")
    if review is not None:
        path = ep / "build" / "qa" / "review.md"
        path.write_text(review, encoding="utf-8")
        os.utime(path, (st.st_mtime + 60, st.st_mtime + 60))


class Approval(unittest.TestCase):
    """videos.approval: where a video stands before it is posted."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.cfg = {"videos_dir": str(self.root / "videos"), "episodes_dir": str(self.root / "episodes"),
                    "completed_dir": str(self.root / "completed")}
        self.ep = self.root / "episodes" / "ep01_duck"
        (self.ep / "final").mkdir(parents=True)
        self.spec = {"id": "ep01_duck", "title": "What if a duck doubled?"}
        (self.ep / "episode.json").write_text(json.dumps(self.spec), encoding="utf-8")
        self.video = self.ep / "final" / "ep01_duck.mp4"
        self.video.write_bytes(b"rendered")
        os.utime(self.video, (time.time() - 3600,) * 2)

    def state(self, video: Path | None = None) -> str:
        got = videos.approval(video or self.video, self.cfg)
        self.assertEqual(sorted(got), ["line", "state"])
        self.assertTrue(got["line"].endswith("."), got["line"])
        self.line = got["line"]
        return got["state"]

    def test_a_video_from_outside_the_pipeline_has_nothing_to_check(self):
        loose = self.root / "videos" / "holiday.mp4"
        loose.parent.mkdir()
        loose.write_bytes(b"x")
        self.assertEqual(self.state(loose), "none")
        self.assertIn("Not checked by ReelForge", self.line)
        self.assertIn("none", videos.POSTABLE)

    def test_qa_passed_and_the_review_approved_this_cut(self):
        approve(self.ep, self.video)
        self.assertEqual(self.state(), "approved")
        self.assertIn("approved", videos.POSTABLE)

    def test_the_copy_in_completed_videos_stands_as_the_file_it_copies(self):
        approve(self.ep, self.video)
        copy = self.root / "completed" / "ep01_duck - What if a duck doubled" / "ep01_duck.mp4"
        copy.parent.mkdir(parents=True)
        copy.write_bytes(self.video.read_bytes())
        st = self.video.stat()
        os.utime(copy, (st.st_atime, st.st_mtime))  # delivered with its time, as the pipeline does
        self.assertEqual(self.state(copy), "approved")
        os.utime(copy, None)  # another file under that name
        self.assertEqual(self.state(copy), "stale")

    def test_no_review_yet(self):
        approve(self.ep, self.video, review=None)
        self.assertEqual(self.state(), "unreviewed")
        approve(self.ep, self.video, review="Still looking at the stills.")
        self.assertEqual(self.state(), "unreviewed")

    def test_a_rejection(self):
        approve(self.ep, self.video, review="REJECTED: the loop jumps.\n\nWhat to fix before this is approved: it.")
        self.assertEqual(self.state(), "rejected")

    def test_a_review_of_an_earlier_cut_is_stale(self):
        approve(self.ep, self.video)
        self.video.write_bytes(b"rendered again")  # a new cut, QA run on it, the old review still there
        approve(self.ep, self.video, review=None)
        self.assertEqual(self.state(), "stale")
        self.assertIn("earlier cut", self.line)

    def test_qa_of_an_earlier_cut_is_stale(self):
        approve(self.ep, self.video)
        self.video.write_bytes(b"rendered again, longer")
        review = self.ep / "build" / "qa" / "review.md"
        os.utime(review, None)  # even a review written since doesn't help: QA never saw this file
        self.assertEqual(self.state(), "stale")
        self.assertIn("qa step", self.line)

    def test_qa_that_failed_never_ran_or_cannot_be_read(self):
        self.assertEqual(self.state(), "qa_failed")
        self.assertIn("hasn't checked", self.line)
        approve(self.ep, self.video, passed=False, checks=[{"check": "duration", "status": "PASS"},
                                                           {"check": "loudness", "status": "FAIL"}])
        self.assertEqual(self.state(), "qa_failed")
        self.assertIn("(loudness)", self.line)
        report = self.ep / "build" / "qa" / "qa.json"
        for broken in ('{"pass": tru', "[1, 2]", "", '"pass"'):
            report.write_text(broken, encoding="utf-8")
            self.assertEqual(self.state(), "qa_failed", broken)
            self.assertIn("can't be read", self.line)
        report.write_bytes(b"\xff\xfe\x00broken")
        self.assertEqual(self.state(), "qa_failed")

    def test_a_demo_is_never_postable(self):
        (self.ep / "episode.json").write_text(json.dumps({**self.spec, "demo": True}), encoding="utf-8")
        approve(self.ep, self.video)
        self.assertEqual(self.state(), "demo")
        for state in ("stale", "rejected", "unreviewed", "qa_failed", "demo"):
            self.assertNotIn(state, videos.POSTABLE)

    def test_a_broken_episode_file_is_not_an_exception(self):
        approve(self.ep, self.video)
        (self.ep / "episode.json").write_text("{not json", encoding="utf-8")
        self.assertEqual(self.state(), "approved")  # QA and the review still say what they say

    def receipts(self, snapshot: dict) -> types.ModuleType:
        """studio.review as a ReelForge with content-bound reviews has it: QA stores what it checked, and the review
        has to carry that receipt's fingerprint."""
        module = types.ModuleType("studio.review")
        module.snapshot = lambda ep, spec=None, settings=None: snapshot
        module.fingerprint = lambda receipt: hashlib.sha256(json.dumps(receipt, sort_keys=True).encode()).hexdigest()
        module.qa_matches = lambda report, current: bool(report.get("pass")) and \
            report.get("review_snapshot") == current and report.get("review_fingerprint") == module.fingerprint(current)
        import studio

        patch = mock.patch.dict(sys.modules, {"studio.review": module})
        patch.start()
        self.addCleanup(patch.stop)
        had = getattr(studio, "review", None)
        studio.review = module
        self.addCleanup(lambda: setattr(studio, "review", had) if had else delattr(studio, "review"))
        return module

    def test_receipts_are_used_when_this_reelforge_has_them(self):
        checked = {"schema": 1, "outputs": {"ep01_duck.mp4": "aa"}, "inputs": {}}
        receipts = self.receipts(checked)
        digest = receipts.fingerprint(checked)
        approve(self.ep, self.video, review=f"Review-SHA256: {digest}\nAPPROVED", review_snapshot=checked,
                review_fingerprint=digest)
        self.assertEqual(self.state(), "approved")
        # The files' times don't matter any more: the receipt says what was reviewed.
        os.utime(self.video, None)
        self.assertEqual(self.state(), "approved")
        # An approval without this cut's receipt reviewed something else.
        approve(self.ep, self.video, review="APPROVED", review_snapshot=checked, review_fingerprint=digest)
        self.assertEqual(self.state(), "stale")
        self.assertIn("Review-SHA256", self.line)
        # Anything the receipt covers changing after QA: stale, whatever the review says.
        receipts.snapshot = lambda ep, spec=None, settings=None: {**checked, "outputs": {"ep01_duck.mp4": "bb"}}
        approve(self.ep, self.video, review=f"Review-SHA256: {digest}\nAPPROVED", review_snapshot=checked,
                review_fingerprint=digest)
        self.assertEqual(self.state(), "stale")
        # A report from before receipts has none: one fresh QA and review.
        receipts.snapshot = lambda ep, spec=None, settings=None: checked
        approve(self.ep, self.video)
        self.assertEqual(self.state(), "stale")
        approve(self.ep, self.video, review="REJECTED", review_snapshot=checked, review_fingerprint=digest)
        self.assertEqual(self.state(), "rejected")

    def test_receipts_that_cannot_be_taken_are_a_state_with_the_reason(self):
        receipts = self.receipts({})

        def broken(ep, spec=None, settings=None):
            raise KeyError("id")

        receipts.snapshot = broken
        approve(self.ep, self.video)
        self.assertEqual(self.state(), "stale")
        self.assertIn("KeyError", self.line)
