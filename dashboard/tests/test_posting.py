"""The check before a post (dashboard.videos.approval): every state a video can be in, on a scratch episode folder."""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest import mock

from dashboard import videos


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
