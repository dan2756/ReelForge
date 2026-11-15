"""Posting with the browser extension as the window shows and starts it (dashboard.posting_tab): the posting and
extension views, the calls behind the buttons, and the job states on the video list, the POSTED line and RECENT
UPLOADS. On a scratch channel; "the extension" is the test calling the ledger the way the local server will, and the
page that hands it a batch is a stand-in (the server opens the real one)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest import mock

from dashboard import db, desktop, extension_api, posting, posting_tab
from dashboard.platforms import Post
from dashboard.tests.test_posting import approve
from dashboard.tests.test_publish import CAPTION, TAGS, PublishTest, Reader
from dashboard.tests.window import Window, plain
from dashboard.view import MUTED, cell_len

CHROME = "install-chrome"
NAMES = {"tiktok": "TikTok", "youtube": "YouTube", "instagram": "Instagram", "x": "X"}
TIKTOK_URL = "https://www.tiktok.com/@me/video/7300000000000000001"


class PostingTest(PublishTest):
    """Your own window (not the demo) on a channel with one video and four platforms connected."""

    def setUp(self) -> None:
        super().setUp()
        self.store = self.root / "extension.json"
        patch = mock.patch.object(extension_api, "store_path", lambda: self.store)
        patch.start()
        self.addCleanup(patch.stop)
        self.pages: list[str] = []  # the batches whose page "opened"
        self.page_opens = True

    def extension(self, seen_minutes_ago: float = 0, **entry) -> None:
        """The extension, connected in one browser and last heard from this long ago."""
        seen = (datetime.now(timezone.utc) - timedelta(minutes=seen_minutes_ago)).isoformat(timespec="seconds")
        self.store.write_text(json.dumps({"app_id": "0000", "extension_ids": [], "paired": [
            {"install": CHROME, "name": "Chrome on this PC", "version": "0.1.0", "token_sha256": "ab" * 32,
             "paired_at": seen, "last_seen": seen, **entry}]}), encoding="utf-8")

    def window(self, connect: bool = True, **options) -> Window:
        if connect and not self.store.exists():
            self.extension()
        w = super().window({key: Reader(key, name) for key, name in NAMES.items()}, **options)
        w.svc.demo = False
        # What the local server will do: open the page that hands the batch to the extension.
        w.svc.open_post_page = lambda batch: self.pages.append(batch) or self.page_opens
        w.forget()
        w.svc.update_posting(fresh=True)
        return w

    def rows(self, w: Window) -> dict[str, dict]:
        return {r["key"]: r for r in w.view("posting")["rows"]}

    def post(self, w: Window, *platforms: str, **kw) -> dict:
        return w.call("post_with_extension", list(platforms or ("tiktok",)), CAPTION, TAGS,
                      kw.pop("stop_before", True), kw.pop("force", None))

    def work(self, job_id: str, *states: str, **last) -> None:
        """The extension takes the job and reports these states (what it says with the last one: last)."""
        posting.claim(job_id, CHROME)
        for i, state in enumerate(states):
            posting.advance(job_id, state, install=CHROME, **(last if i == len(states) - 1 else {}))

    def flags(self, w: Window) -> dict[str, str]:
        """Each platform's two letters on the picked video's line of the list -> their style."""
        line = w.view("files")["items"][0]["lines"][1]
        # (the line's own grey is under every piece of it: a flag's colour, written after it, is the one drawn)
        return {seg[0]: seg[1].removeprefix(f"{MUTED} ") for seg in line if seg[0] in ("TT", "YT", "IG", "X")}


class View(PostingTest):
    def test_a_video_nobody_has_posted(self):
        w = self.window()
        w.call("pick_file", str(self.video))  # picked already: nothing new
        w.svc.update_posting()
        view = w.view("posting")
        self.assertEqual(view["video"], {"path": str(self.video), "name": "ep01.mp4"})
        self.assertEqual((view["can_post"], view["why"]), (True, ""))
        self.assertEqual(view["review"]["state"], "none")
        self.assertIn("Not checked by ReelForge", plain(view["review"]["line"]))
        self.assertEqual(view["extension"]["state"], "on")
        self.assertEqual([(r["key"], r["name"], r["state"], r["busy"], r["url"], r["job"], r["actions"])
                          for r in view["rows"]],
                         [(key, name, "none", False, None, None, []) for key, name in NAMES.items()])
        self.assertEqual(plain(view["rows"][0]["line"]), "○ not posted")

    def test_every_action_a_row_can_offer_has_the_words_for_its_button(self):
        w = self.window()
        labels = w.view("posting")["labels"]
        self.assertEqual({a for acts in posting_tab.ACTIONS.values() for a in acts}, set(labels))
        self.assertEqual((labels["mark_posted"], labels["not_posted"], labels["retry"]),
                         ("It's posted", "It didn't post", "Try again"))

    def test_an_upload_page_opened_by_hand_shows_on_its_row_until_the_post_is_spotted(self):
        w = self.window()
        w.call("open_upload", "tiktok", CAPTION, TAGS)
        rows = self.rows(w)
        self.assertEqual(plain(rows["tiktok"]["line"]), "○ not posted  upload page opened 0m ago, not spotted yet")
        self.assertEqual((rows["tiktok"]["state"], rows["tiktok"]["actions"]), ("none", []))  # nothing to wait for
        self.assertEqual(plain(rows["youtube"]["line"]), "○ not posted")
        other = self.root / "videos" / "ep02.mp4"  # another video's row says nothing of it
        other.write_bytes(b"second")
        w.call("pick_file", str(other))
        self.assertEqual(plain(self.rows(w)["tiktok"]["line"]), "○ not posted")

    def test_picking_a_video_sends_its_view(self):
        other = self.root / "videos" / "ep02.mp4"
        other.write_bytes(b"second")
        w = self.window()
        w.call("pick_file", str(other))
        self.assertEqual(w.view("posting")["video"]["name"], "ep02.mp4")

    def test_no_video_no_post(self):
        self.video.unlink()
        w = self.window()
        view = w.view("posting")
        self.assertEqual((view["video"], view["rows"], view["can_post"]), (None, [], False))
        self.assertEqual(view["why"], "Pick a video on the left first.")

    def test_without_the_extension_post_cannot_be_pressed(self):
        w = self.window(connect=False)
        view = w.view("posting")
        self.assertEqual((view["extension"]["state"], view["can_post"], view["why"]),
                         ("off", False, "Connect the browser extension first."))
        self.assertIsNone(self.post(w))
        self.assertEqual(w.toasts(), ["Connect the browser extension first."])
        self.assertEqual(posting.recent(), [])

    def test_setup_says_when_only_the_extension_is_in_the_way(self):
        """The window's Post button then explains how to set the extension up, instead of being greyed out."""
        w = self.window(connect=False)
        self.assertTrue(w.view("posting")["setup"])
        self.extension()
        w.svc.update_posting()
        view = w.view("posting")
        self.assertEqual((view["can_post"], view["setup"]), (True, False))  # nothing is in the way
        self.store.unlink()
        self.video.unlink()  # something else is: setting the extension up wouldn't help
        w.svc.update_posting()
        view = w.view("posting")
        self.assertEqual((view["can_post"], view["setup"]), (False, False))

    def test_the_demo_never_posts(self):
        self.extension()
        w = PublishTest.window(self)  # the tests' plain window is the demo
        view = w.view("posting")
        self.assertEqual((view["can_post"], view["why"], view["review"]["state"]),
                         (False, "The demo doesn't post anything.", "demo"))
        self.assertEqual(view["extension"]["state"], "off")
        self.assertIsNone(w.call("post_with_extension", ["tiktok"], CAPTION, TAGS, True, None))
        self.assertFalse(w.call("post_info", CAPTION, TAGS)["can_post"])
        w.call("post_action", "tiktok", "post_again")
        self.assertEqual(posting.recent(), [])
        self.assertEqual(self.pages, [])

    def test_a_pipeline_video_shows_its_review_and_needs_it(self):
        ep = self.root / "episodes" / "ep02_duck"
        (ep / "final").mkdir(parents=True)
        (ep / "episode.json").write_text(json.dumps({"id": "ep02_duck", "title": CAPTION}), encoding="utf-8")
        video = ep / "final" / "ep02_duck.mp4"
        video.write_bytes(b"rendered")
        approve(ep, video, review=None)
        w = self.window()
        w.call("pick_file", str(video))
        view = w.view("posting")
        self.assertEqual((view["review"]["state"], view["can_post"]), ("unreviewed", False))
        self.assertIn("no review yet", view["why"])
        self.assertTrue(plain(view["review"]["line"]).startswith("! QA passed, but there is no review yet"))
        made = self.post(w, "tiktok", "youtube")
        self.assertEqual((made["jobs"], [b["reason"] for b in made["blocked"]]), ([], ["review", "review"]))
        self.assertEqual([t["title"] for t in w.sent("toast")], ["TikTok, YouTube: not started"])  # said once
        self.assertEqual(posting.recent(), [])
        # The buttons that open an upload page never ask.
        w.call("open_upload", "tiktok", CAPTION, TAGS)
        self.assertEqual(self.opened, [desktop.UPLOAD_PAGES["tiktok"]])
        # Reviewed since: the view says so when the tab is opened again, and Post can be pressed.
        approve(ep, video)
        w.call("tab_opened", "publish")
        view = w.view("posting")
        self.assertEqual((view["review"]["state"], view["can_post"]), ("approved", True))
        self.assertTrue(plain(view["review"]["line"]).startswith("✓ QA passed and the review says APPROVED"))


class Question(PostingTest):
    """post_info: what the question before a post shows."""

    def test_each_platforms_exact_text_and_nothing_in_the_way(self):
        w = self.window()
        info = w.call("post_info", CAPTION, TAGS)
        self.assertEqual((info["can_post"], info["why"], info["stop_before"]), (True, "", True))
        self.assertEqual(info["video"]["name"], "ep01.mp4")
        self.assertEqual(info["review"]["state"], "none")
        self.assertIn("not yet on the live sites", info["note"])
        # What happens next, in one sentence: you press Post. With "stop before posting" off, where the extension
        # may press it, and never on X.
        self.assertEqual(info["how"], "ReelForge opens each upload page in your browser, attaches the video and fills "
                                      "in the text. You check it and press Post.")
        self.assertIn("always on X, you check it and press Post", info["how_press"])
        by = {p["key"]: p for p in info["platforms"]}
        self.assertEqual(list(by), list(NAMES))
        self.assertEqual(by["youtube"]["text"], {"title": f"{CAPTION} #shorts", "description": TAGS})
        self.assertEqual(by["youtube"]["fields"], [{"label": "Title", "text": f"{CAPTION} #shorts"},
                                                   {"label": "Description", "text": TAGS}])
        self.assertEqual(by["x"]["fields"], [{"label": "Post", "text": CAPTION}])
        for p in info["platforms"]:
            self.assertEqual((p["blocked"], p["again"], p["press"], p["review"], p["handle"]),
                             (None, False, False, "none", "@me"))
        self.assertEqual(posting.recent(), [])  # asking makes nothing

    def test_what_is_in_the_way_is_said_per_platform(self):
        w = self.window()
        (job,) = self.post(w, "tiktok")["jobs"]
        self.work(job["id"], "ready", "posted", url=TIKTOK_URL)
        (job,) = self.post(w, "youtube")["jobs"]
        info = w.call("post_info", CAPTION, TAGS)
        by = {p["key"]: p for p in info["platforms"]}
        self.assertEqual((by["tiktok"]["blocked"]["reason"], by["tiktok"]["blocked"]["url"], by["tiktok"]["again"]),
                         ("posted", TIKTOK_URL, True))
        self.assertEqual((by["youtube"]["blocked"]["reason"], by["youtube"]["again"]), ("in_progress", False))
        self.assertIsNone(by["instagram"]["blocked"])
        self.assertTrue(info["can_post"])
        self.assertTrue(by["instagram"]["press"] is False)
        # Press Post for me: on offer where a post made with the extension is already up, never on X.
        other = self.root / "videos" / "ep02.mp4"
        other.write_bytes(b"second")
        w.call("pick_file", str(other))
        by = {p["key"]: p for p in w.call("post_info", "The second video", TAGS)["platforms"]}
        self.assertEqual({k: p["press"] for k, p in by.items()},
                         {"tiktok": True, "youtube": False, "instagram": False, "x": False})

    def test_an_empty_caption_or_no_extension_says_why(self):
        w = self.window()
        info = w.call("post_info", "", TAGS)
        self.assertEqual((info["can_post"], info["why"]), (False, "Write a caption first."))
        self.store.unlink()
        info = w.call("post_info", CAPTION, TAGS)
        self.assertEqual((info["can_post"], info["why"]), (False, "Connect the browser extension first."))
        self.assertEqual(info["extension"]["state"], "off")


class Posting(PostingTest):
    def test_post_makes_the_jobs_and_opens_the_page_for_the_batch(self):
        w = self.window()
        made = self.post(w, "tiktok", "youtube")
        self.assertEqual(self.pages, [made["batch"]])
        self.assertEqual((made["opened"], made["blocked"], [j["platform"] for j in made["jobs"]]),
                         (True, [], ["tiktok", "youtube"]))
        jobs = posting.in_batch(made["batch"])
        self.assertEqual([(j["state"], j["press"], json.loads(j["text"])) for j in jobs],
                         [("queued", 0, {"caption": f"{CAPTION} #fyp {TAGS}"}),
                          ("queued", 0, {"title": f"{CAPTION} #shorts", "description": TAGS})])
        rows = self.rows(w)
        self.assertEqual((rows["tiktok"]["state"], rows["tiktok"]["busy"], rows["tiktok"]["job"],
                          rows["tiktok"]["actions"]), ("queued", True, jobs[0]["id"], ["cancel"]))
        self.assertEqual(plain(rows["tiktok"]["line"]), "● waiting for the extension to pick it up")
        self.assertEqual(rows["instagram"]["state"], "none")
        self.assertTrue(w.view("posting")["can_post"])  # Instagram and X are still free
        # The same on the rest of the tab: the list's flags, the POSTED line, RECENT UPLOADS.
        flags = self.flags(w)
        self.assertEqual((flags["TT"], flags["YT"], flags["IG"]),
                         (f"bold {posting_tab.AMBER}", f"bold {posting_tab.AMBER}", "#3a4049"))
        posted = w.view("posted")
        self.assertIn("● TikTok queued", plain(posted["line"]))
        self.assertEqual((posted["done"]["tiktok"], posted["states"]["tiktok"], posted["states"]["x"]),
                         (False, "queued", "none"))
        notes = [plain(r["cells"][4]) for r in w.view("uploads")["rows"]]
        self.assertEqual(notes, ["waiting for the extension to pick it up"] * 2)

    def test_stop_before_posting_off_is_kept_on_the_job_and_never_for_x(self):
        w = self.window()
        made = self.post(w, "tiktok", "x", stop_before=False)
        self.assertEqual({j["platform"]: j["press"] for j in posting.in_batch(made["batch"])}, {"tiktok": 1, "x": 0})

    def test_a_page_that_does_not_open_starts_nothing(self):
        self.page_opens = False
        w = self.window()
        made = self.post(w, "tiktok", "youtube")
        self.assertEqual((made["batch"], made["jobs"], made["opened"]), (None, [], False))
        self.assertEqual([(j["state"], j["detail"]) for j in posting.recent()],
                         [("cancelled", "the posting page didn't open")] * 2)
        self.assertEqual(db.uploads(), [])  # no page opened: nothing for a later post to be filed under
        toast = w.sent("toast")[-1]
        self.assertEqual((toast["title"], toast["severity"]), ("Nothing was posted", "warning"))
        self.assertIn("by hand", toast["body"])
        self.assertEqual(self.rows(w)["tiktok"]["actions"], ["retry"])
        self.page_opens = True  # nothing stands in the way of the next try
        self.assertEqual(len(self.post(w, "tiktok", "youtube")["jobs"]), 2)

    def test_until_the_local_server_is_there_the_page_cannot_open(self):
        w = self.window()
        del w.svc.open_post_page  # the window's own, before the server takes it over
        made = self.post(w)
        self.assertEqual((made["opened"], made["jobs"]), (False, []))
        self.assertIn("couldn't open the posting page", w.toasts()[-1])
        self.assertEqual([j["state"] for j in posting.recent()], ["cancelled"])

    def test_a_second_press_for_the_same_video_and_platform_is_refused(self):
        w = self.window()
        (job,) = self.post(w)["jobs"]
        again = self.post(w, "tiktok", "youtube")
        self.assertEqual([j["platform"] for j in again["jobs"]], ["youtube"])
        self.assertEqual([(b["platform"], b["reason"], b["job"]) for b in again["blocked"]],
                         [("tiktok", "in_progress", job["id"])])
        toast = next(t for t in w.sent("toast") if t["title"] == "TikTok: not started")
        self.assertIn("already under way", toast["body"])
        self.assertIsNone(self.post(w, "vimeo"))  # nothing it knows was ticked
        self.assertIn("Tick a platform", w.toasts()[-1])

    def test_the_extension_at_work_shows_step_by_step_without_a_press_here(self):
        w = self.window()
        (job,) = self.post(w)["jobs"]
        w.forget()
        posting.claim(job["id"], CHROME)  # from the local server's thread, or another window: not through this one
        posting.advance(job["id"], "uploading", install=CHROME, step="upload", progress=0.6)
        self.assertTrue(w.until(lambda: w.view("posting") and self.rows(w)["tiktok"]["state"] == "uploading"))
        row = self.rows(w)["tiktok"]
        self.assertEqual((plain(row["line"]), row["busy"], row["actions"]), ("● uploading 60%", True, ["cancel"]))
        posting.advance(job["id"], "uploading", install=CHROME, step="upload", progress=0.9)  # no change of state
        self.assertTrue(w.until(lambda: plain(self.rows(w)["tiktok"]["line"]) == "● uploading 90%"))
        posting.advance(job["id"], "filling", install=CHROME, step="fill_caption")
        self.assertTrue(w.until(lambda: plain(self.rows(w)["tiktok"]["line"]) == "● filling in the text: the caption"))
        posting.advance(job["id"], "ready", install=CHROME, step="ready")
        self.assertTrue(w.until(lambda: self.rows(w)["tiktok"]["state"] == "ready"))
        row = self.rows(w)["tiktok"]
        self.assertEqual((plain(row["line"]), row["busy"], row["actions"]),
                         ("● ready: check it in the browser, then press Post there", False, ["mark_posted", "cancel"]))
        self.assertTrue(w.until(lambda: "ready for you to press Post" in plain(w.view("posted")["line"])))

    def test_posted_with_its_id_shows_everywhere_before_the_next_stats_read(self):
        w = self.window()
        (job,) = self.post(w)["jobs"]
        self.work(job["id"], "ready", "posted", post_id="7300000000000000001", url=TIKTOK_URL)
        self.assertTrue(w.until(lambda: w.view("posted")["done"]["tiktok"]))
        row = self.rows(w)["tiktok"]
        self.assertEqual((row["state"], row["busy"], row["url"], row["actions"]),
                         ("posted", False, TIKTOK_URL, ["open_post", "post_again"]))
        self.assertTrue(plain(row["line"]).startswith("✓ posted"))
        self.assertTrue(w.until(lambda: plain(w.view("files")["items"][0]["lines"][0]) == "✓ ep01.mp4"))
        self.assertEqual(self.flags(w)["TT"], "bold #ffffff")  # the platform's own colour
        w.call("post_action", job["id"], "open_post")
        self.assertEqual(self.opened, [TIKTOK_URL])

    def test_posted_without_an_id_waits_for_the_stats_and_says_so(self):
        w = self.window()
        (job,) = self.post(w)["jobs"]
        self.work(job["id"], "ready", "posted")
        self.assertTrue(w.until(lambda: self.rows(w)["tiktok"]["state"] == "posted"))
        row = self.rows(w)["tiktok"]
        self.assertIn("not in your stats yet", plain(row["line"]))
        self.assertEqual((row["url"], row["actions"]), (None, ["post_again"]))  # no link to open yet
        self.assertTrue(w.until(lambda: w.view("posted")["done"]["tiktok"]))
        self.assertIn("✓ TikTok posted, not in your stats yet", plain(w.view("posted")["line"]))
        self.assertTrue(w.until(lambda: "posted with the extension · not in your stats yet" in
                                [plain(r["cells"][4]) for r in w.view("uploads")["rows"]]))

    def test_a_failure_says_where_and_why_and_can_be_tried_again(self):
        w = self.window()
        (job,) = self.post(w)["jobs"]
        self.work(job["id"], "opening", "failed", step="find_input", error_code="hook_not_found")
        self.assertTrue(w.until(lambda: self.rows(w)["tiktok"]["state"] == "failed"))
        row = self.rows(w)["tiktok"]
        self.assertTrue(plain(row["line"]).startswith(
            "× failed at finding the upload box: TikTok changed its page, so the extension lost its way"))
        self.assertEqual((row["busy"], row["actions"]), (False, ["retry"]))
        self.assertTrue(w.until(lambda: self.flags(w)["TT"] == f"bold {posting_tab.BAD}"))
        self.assertIn("× TikTok failed", plain(w.view("posted")["line"]))
        self.assertTrue(w.view("posting")["can_post"])
        w.call("post_action", job["id"], "retry")
        (again,) = [j for j in posting.recent() if j["state"] == "queued"]
        self.assertEqual((again["text"], self.pages[-1]), (job_text(job), again["batch"]))
        self.assertEqual(self.rows(w)["tiktok"]["job"], again["id"])
        # Not without the extension, though: a retry is a post like any other.
        posting.cancel(again["id"])
        self.store.unlink()
        w.call("post_action", again["id"], "retry")
        self.assertEqual(w.toasts()[-1], "Connect the browser extension first.")
        self.assertEqual([j["state"] for j in posting.recent()], ["failed", "cancelled"])

    def test_the_extensions_own_words_for_a_failure_are_used(self):
        w = self.window()
        (job,) = self.post(w)["jobs"]
        self.work(job["id"], "uploading", "failed", step="upload", error_code="platform_error",
                  error_message="TikTok says: you've reached today's upload limit.")
        self.assertTrue(w.until(lambda: self.rows(w)["tiktok"]["state"] == "failed"))
        self.assertIn("× failed at uploading: TikTok says: you've reached today's upload limit.",
                      plain(self.rows(w)["tiktok"]["line"]))

    def test_cancel(self):
        w = self.window()
        (job,) = self.post(w)["jobs"]
        w.call("post_action", job["id"], "cancel")
        self.assertEqual(posting.get(job["id"])["state"], "cancelled")
        row = self.rows(w)["tiktok"]
        self.assertEqual((row["state"], row["actions"]), ("cancelled", ["retry"]))
        self.assertIn("○ cancelled (you cancelled it)", plain(row["line"]))
        self.assertEqual(self.flags(w)["TT"], "#3a4049")
        w.call("post_action", job["id"], "cancel")  # pressed twice: nothing more happens
        w.call("post_action", job["id"], "mark_posted")
        self.assertEqual(posting.get(job["id"])["state"], "cancelled")
        self.assertIn("cancelled can't become posted", w.toasts()[-1])

    def test_a_job_reelforge_lost_track_of_takes_your_word(self):
        w = self.window()
        made = self.post(w, "tiktok", "youtube")
        for job in made["jobs"]:
            self.work(job["id"], "ready", "unknown")
        self.assertTrue(w.until(lambda: self.rows(w)["youtube"]["state"] == "unknown"))
        row = self.rows(w)["tiktok"]
        self.assertEqual((row["busy"], row["actions"]), (False, ["mark_posted", "not_posted"]))
        self.assertTrue(plain(row["line"]).startswith("? did it post?"))
        self.assertEqual(self.flags(w)["TT"], f"bold {posting_tab.AMBER}")
        w.call("post_action", made["jobs"][0]["id"], "mark_posted")
        w.call("post_action", made["jobs"][1]["id"], "not_posted")
        rows = self.rows(w)
        self.assertEqual((rows["tiktok"]["state"], rows["youtube"]["state"]), ("posted", "cancelled"))
        self.assertEqual(w.view("posted")["done"], {"tiktok": True, "youtube": False, "instagram": False, "x": False})

    def test_only_unknown_rows_left_says_what_to_do(self):
        w = self.window()
        w.svc.platforms = {"tiktok": w.svc.platforms["tiktok"]}  # a channel that is only on TikTok
        (job,) = self.post(w)["jobs"]
        self.assertEqual(w.view("posting")["why"], "A post is already under way.")
        self.work(job["id"], "ready", "unknown")
        self.assertTrue(w.until(lambda: self.rows(w)["tiktok"]["state"] == "unknown"))
        self.assertIn("Say first whether the last try posted", w.view("posting")["why"])
        w.call("post_action", job["id"], "mark_posted")
        view = w.view("posting")
        self.assertEqual(view["can_post"], False)
        self.assertIn("Already posted everywhere", view["why"])

    def test_a_job_quiet_for_minutes_says_so(self):
        w = self.window()
        (job,) = self.post(w)["jobs"]
        self.work(job["id"], "uploading")
        then = (datetime.now(timezone.utc) - timedelta(minutes=4)).isoformat(timespec="seconds")
        with db.connect() as c:
            c.execute("UPDATE post_jobs SET updated_at = ? WHERE id = ?", (then, job["id"]))
        w.svc.update_posting()
        self.assertEqual(plain(self.rows(w)["tiktok"]["line"]), "● uploading  no news for 4m")

    def test_the_clock_runs_in_the_window(self):
        w = self.window()
        (job,) = self.post(w)["jobs"]
        then = (datetime.now(timezone.utc) - timedelta(minutes=11)).isoformat(timespec="seconds")
        with db.connect() as c:
            c.execute("UPDATE post_jobs SET created_at = ? WHERE id = ?", (then, job["id"]))
        w.svc.mind_the_jobs()  # every half minute (tick), and at start
        row = self.rows(w)["tiktok"]
        self.assertEqual((row["state"], row["actions"]), ("expired", ["retry"]))
        self.assertIn("the extension didn't pick it up", plain(row["line"]))


def job_text(job: dict) -> str:
    return posting.get(job["id"])["text"]


class Spotted(PostingTest):
    """What the stats read finds: posts made by hand, and the ones the extension helped with."""

    def test_a_post_found_by_this_windows_read_settles_the_job_and_gets_its_numbers(self):
        youtube = Reader("youtube", "YouTube")
        self.extension()
        w = PublishTest.window(self, {"tiktok": Reader("tiktok", "TikTok"), "youtube": youtube})
        w.svc.demo = False
        w.svc.open_post_page = lambda batch: True
        (job,) = self.post(w, "youtube")["jobs"]
        self.work(job["id"], "ready", "unknown")
        youtube.posts = [Post("dQw4w9WgXcQ", f"{CAPTION} #shorts", "https://youtu.be/dQw4w9WgXcQ", db.now_iso(),
                              views=42)]
        w.call("refresh", True)
        self.assertTrue(w.until(lambda: self.rows(w)["youtube"]["state"] == "posted"))
        row = self.rows(w)["youtube"]
        self.assertEqual((row["url"], row["actions"]), ("https://youtu.be/dQw4w9WgXcQ", ["open_post", "post_again"]))
        self.assertTrue(plain(row["line"]).startswith("✓ posted  42 views"))
        done = posting.get(job["id"])
        self.assertEqual((done["post_id"], done["detail"]), ("dQw4w9WgXcQ", "found by a stats read"))
        self.assertTrue(w.until(lambda: "live · 42 views" in plain(w.view("uploads")["rows"][0]["cells"][4])))

    def test_a_post_made_by_hand_is_spotted_and_can_be_opened_or_posted_again(self):
        episode = db.create_episode(CAPTION, str(self.video))
        db.add_post("instagram", "3500000000000000001", episode, CAPTION, "https://www.instagram.com/reel/abc/")
        w = self.window()
        w.svc.reload()
        w.call("form_changed", CAPTION, TAGS)
        row = self.rows(w)["instagram"]
        self.assertEqual((row["state"], row["job"], row["url"], row["actions"]),
                         ("spotted", None, "https://www.instagram.com/reel/abc/", ["open_post", "post_again"]))
        self.assertTrue(plain(row["line"]).startswith("✓ posted"))
        w.call("post_action", "instagram", "open_post")  # a row without a job goes by its platform
        self.assertEqual(self.opened, ["https://www.instagram.com/reel/abc/"])
        # Posting to it from the question is refused unless you say again...
        made = self.post(w, "instagram")
        self.assertEqual([b["reason"] for b in made["blocked"]], ["spotted"])
        self.assertIn("Already posted", w.toasts()[-1])
        # ...and Post again on its row starts one with the form's text, stopping before Post.
        w.call("post_action", "instagram", "post_again")
        (job,) = [j for j in posting.recent() if j["platform"] == "instagram"]
        self.assertEqual((job["state"], job["press"], json.loads(job["text"])),
                         ("queued", 0, {"caption": f"{CAPTION} {TAGS}"}))
        self.assertEqual(self.rows(w)["instagram"]["state"], "queued")
        self.assertEqual(self.pages, [job["batch"]])

    def test_post_again_through_the_question_sets_the_old_job_aside(self):
        w = self.window()
        (first,) = self.post(w)["jobs"]
        self.work(first["id"], "ready", "posted", url=TIKTOK_URL)
        self.assertEqual(self.post(w)["jobs"], [])
        (second,) = self.post(w, force=["tiktok"])["jobs"]
        self.assertEqual(posting.get(first["id"])["superseded"], 1)
        self.assertEqual(self.rows(w)["tiktok"]["job"], second["id"])


class ExtensionView(PostingTest):
    def view(self, w: Window) -> dict:
        w.svc.update_extension()
        return w.view("extension")

    def test_not_connected(self):
        w = self.window(connect=False)
        view = self.view(w)
        self.assertEqual((view["state"], view["installs"], view["port"], view["hint_dismissed"]),
                         ("off", [], None, False))
        self.assertEqual(plain(view["line"]), "○ Browser extension not connected")
        self.assertTrue(view["folder"].endswith("extension"))
        self.assertIsInstance(view["folder_there"], bool)
        self.assertEqual(view["protocol"], 1)
        said = " ".join(plain(line) for line in view["lines"])
        self.assertIn("You check it and press Post", said)
        self.assertIn("not yet on the live sites", said)  # never more than the tests have shown
        # The Publish tab's one line about it (a narrow window keeps the part ranked 0), and the steps to set it up.
        self.assertEqual([(plain(text), rank) for text, rank in view["hint"]],
                         [("Optional. ", 1), ("It can fill in the upload pages for you.", 0),
                          (" You check each one and press Post.", 2)])
        self.assertEqual(len(view["steps"]), 4)
        self.assertIn("chrome://extensions", view["steps"][0])
        self.assertIn("Load unpacked", view["steps"][2])
        self.assertIn("Connect a browser", view["steps"][3])
        w.call("dismiss_extension_hint")
        self.assertTrue(w.view("extension")["hint_dismissed"])
        self.assertTrue(db.get_meta(posting_tab.HINT))

    def test_a_window_that_is_not_listening_says_why_on_its_own(self):
        """problem: what the window shows beside Post, since no post can be handed over from this window."""
        w = self.window()
        w.svc.extension_problem = "ReelForge isn't listening for the extension here (it is switched off)."
        view = self.view(w)
        self.assertEqual(view["problem"], w.svc.extension_problem)
        self.assertIn(view["problem"], [plain(line) for line in view["lines"]])
        w.svc.extension_problem = ""
        self.assertEqual(self.view(w)["problem"], "")

    def test_connected_and_heard_from_just_now(self):
        self.extension(seen_minutes_ago=0.2)
        w = self.window()
        w.svc.extension_port = 47821  # as the local server sets it once it listens
        view = self.view(w)
        self.assertEqual((view["state"], view["port"]), ("on", 47821))
        self.assertTrue(plain(view["line"]).startswith("● Browser extension connected  Chrome on this PC · v0.1.0"))
        (install,) = view["installs"]
        self.assertEqual((install["install"], install["name"], install["version"], install["on"]),
                         (CHROME, "Chrome on this PC", "0.1.0", True))
        self.assertNotIn("token_sha256", install)  # nothing of the secret goes to the window

    def test_connected_but_quiet_is_waiting_and_post_still_works(self):
        self.extension(seen_minutes_ago=30)
        w = self.window()
        view = self.view(w)
        self.assertEqual((view["state"], view["installs"][0]["on"], view["installs"][0]["seen"]),
                         ("waiting", False, "30m ago"))
        self.assertIn("it wakes up when you post", plain(view["line"]))
        self.assertTrue(w.view("posting")["can_post"])

    def test_an_extension_of_another_version_is_old_and_post_waits_for_the_update(self):
        self.extension(protocol=0)
        w = self.window()
        older = ("The browser extension is older than this ReelForge. Press Reload on its card on your browser's "
                 "extensions page.")
        self.assertEqual(self.view(w)["state"], "old")
        self.assertEqual((w.view("posting")["can_post"], w.view("posting")["why"]), (False, older))
        self.extension(protocol=2)  # the other way round: the sentence says which side to update
        view = self.view(w)
        self.assertEqual(view["state"], "old")
        self.assertIn("This ReelForge is older than the browser extension", plain(view["line"]))
        w.svc.update_posting()
        self.assertEqual(w.view("posting")["why"],
                         "This ReelForge is older than the browser extension. Update ReelForge on its Updates tab.")

    def test_one_browser_that_is_behind_does_not_stop_another(self):
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.store.write_text(json.dumps({"paired": [
            {"install": CHROME, "name": "Chrome", "last_seen": now},
            {"install": "install-edge", "name": "Edge", "last_seen": now, "protocol": 0}]}), encoding="utf-8")
        w = self.window()
        view = self.view(w)
        self.assertEqual((view["state"], [i["old"] for i in view["installs"]]), ("on", [False, True]))
        self.assertTrue(w.view("posting")["can_post"])

    def test_what_a_browser_was_seen_signed_in_to_is_passed_on_as_the_extension_said_it(self):
        self.extension(accounts={"tiktok": {"signed_in": True, "handle": "@me"}})
        w = self.window()
        (install,) = self.view(w)["installs"]
        self.assertEqual(install["accounts"], {"tiktok": {"signed_in": True, "handle": "@me"}})
        self.assertEqual(install["signed"], "signed in there: TikTok @me")
        self.assertIn("apart from the accounts connected on the Accounts tab", w.view("extension")["signed_note"])

    def test_what_a_browser_is_signed_in_to_reads_as_a_line(self):
        names = {"tiktok": "TikTok", "youtube": "YouTube", "instagram": "Instagram", "x": "X"}
        said = posting_tab.signed_in({"tiktok": {"signed_in": True, "handle": "@me"},
                                      "youtube": {"signed_in": True, "handle": None},
                                      "instagram": {"signed_in": False, "handle": None},
                                      "x": {"signed_in": None, "handle": None}, "junk": "x"}, names)
        self.assertEqual(said, "signed in there: TikTok @me · YouTube yes · Instagram no")  # X: not looked at yet
        self.assertIn("It hasn't said yet", posting_tab.signed_in({}, names))
        self.assertIn("It hasn't said yet", posting_tab.signed_in({"x": "junk"}, names))

    def test_a_browser_that_connects_while_the_window_is_open_shows_within_a_second(self):
        w = self.window(connect=False)
        self.assertEqual(w.view("posting")["extension"]["state"], "off")
        w.forget()
        self.extension()
        self.assertTrue(w.until(lambda: w.view("extension") and w.view("extension")["state"] == "on", 4))
        self.assertEqual(w.view("posting")["extension"]["state"], "on")
        self.assertTrue(w.view("posting")["can_post"])

    def test_a_file_that_cannot_be_read_is_not_connected(self):
        self.store.write_text("{not json", encoding="utf-8")
        w = self.window(connect=False)
        self.assertEqual(self.view(w)["state"], "off")
        self.store.write_text(json.dumps({"paired": [{"install": CHROME}, "junk"]}), encoding="utf-8")
        view = self.view(w)
        self.assertEqual((view["state"], view["installs"][0]["seen"]), ("waiting", "never"))


class List(PostingTest):
    def test_the_flags_always_fit_beside_what_is_said_about_the_file(self):
        folder = self.root / "videos"
        long = folder / "a video with a very long name that goes on and on.mp4"
        long.write_bytes(b"x" * 2_000_000)
        w = self.window()
        w.call("scan_files")
        for item in w.view("files")["items"]:
            line = plain(item["lines"][1])
            self.assertLessEqual(cell_len(line), 40, line)
            self.assertTrue(line.endswith("TT YT IG X"), line)
            self.assertIn("ago", line)
        # With fewer platforms there is room to say where the file is from, too.
        self.cfg["videos_dir"] = str(folder)
        one = PublishTest.window(self, {"tiktok": Reader("tiktok", "TikTok")})
        line = plain(one.view("files")["items"][0]["lines"][1])
        self.assertTrue(line.startswith("   videos · "), line)
        self.assertTrue(line.endswith("  TT"), line)

    def test_a_pipeline_video_keeps_its_source_when_it_fits(self):
        from dashboard.service import _file_info

        self.assertEqual(_file_info("pipeline", "108.0 MB", "12h ago", 30), "pipeline · 108.0 MB · 12h ago")
        self.assertEqual(_file_info("pipeline", "108.0 MB", "12h ago", 25), "108.0 MB · 12h ago")
        self.assertEqual(_file_info("ep32 - What if hail dou", "108.0 MB", "12h ago", 34),
                         "ep32 - What… · 108.0 MB · 12h ago")
        self.assertEqual(_file_info("pipeline", "108.0 MB", "12h ago", 10), "12h ago")
        self.assertEqual(_file_info("pipeline", "108.0 MB", "12h ago", 3), "")
