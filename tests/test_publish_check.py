"""The publish check's reading of build/qa/review.md."""

import tempfile
import unittest
from pathlib import Path

import os

from studio.publish_check import review_approved, review_state


class ReviewVerdict(unittest.TestCase):
    def verdict(self, text: str) -> bool:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "review.md"
            path.write_text(text, encoding="utf-8")
            return review_approved(path)

    def test_the_later_verdict_wins(self):
        self.assertTrue(self.verdict("APPROVED"))
        self.assertFalse(self.verdict("REJECTED: the duck clips the frame"))
        self.assertTrue(self.verdict("First pass REJECTED: clipping.\nFixed the mix.\nAPPROVED"))
        self.assertFalse(self.verdict("APPROVED\n\nActually REJECTED: the loop jumps"))

    def test_a_word_that_only_contains_approved_is_not_an_approval(self):
        self.assertFalse(self.verdict("DISAPPROVED"))
        self.assertFalse(self.verdict("UNAPPROVED"))
        self.assertFalse(self.verdict("NOT APPROVED"))
        self.assertFalse(self.verdict("The picture is fine but this is not approved."))
        self.assertFalse(self.verdict("APPROVED at first glance.\nNOT APPROVED: the loop jumps."))
        self.assertTrue(self.verdict("NOT APPROVED on the first pass.\nFixed.\nAPPROVED"))

    def test_a_rejection_that_mentions_approved_afterwards_stays_a_rejection(self):
        self.assertFalse(self.verdict("## Verdict\nREJECTED\n\n## What to fix before this is approved\nThe loop."))
        self.assertFalse(self.verdict("**REJECTED**\n\nThe shots approved before stay as they were."))
        self.assertFalse(self.verdict("Rejected: once this is fixed it will be approved"))
        self.assertFalse(self.verdict("REJECTED.\nNOT YET APPROVED: two lines sit over the wrong shot."))
        self.assertFalse(self.verdict("This cut is pre-approved by nobody. PRE-APPROVED is not a verdict."))

    def test_prose_after_an_approval_does_not_undo_it(self):
        self.assertTrue(self.verdict("**APPROVED**\n\nSo the shots approved before stay as they were."))
        self.assertTrue(self.verdict("Verdict: APPROVED.\nNothing else to fix."))

    def test_a_fingerprint_has_to_be_in_the_review(self):
        digest = "ab" * 32
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "review.md"
            path.write_text("Looked at every shot.\nAPPROVED", encoding="utf-8")
            self.assertTrue(review_approved(path))
            self.assertFalse(review_approved(path, digest))
            path.write_text(f"Review-SHA256: {'cd' * 32}\nReview-SHA256: {digest}\nAPPROVED", encoding="utf-8")
            self.assertTrue(review_approved(path, digest))
            self.assertFalse(review_approved(path, "cd" * 32))

    def test_a_missing_review_is_not_approved(self):
        self.assertFalse(review_approved(Path("/no/such/review.md")))


class ReviewState(unittest.TestCase):
    def episode(self, tmp: str, review: str | None, video_after_review: bool = False) -> Path:
        ep = Path(tmp) / "ep01_duck"
        (ep / "final").mkdir(parents=True)
        (ep / "build" / "qa").mkdir(parents=True)
        video = ep / "final" / "ep01_duck.mp4"
        video.write_bytes(b"video")
        os.utime(video, (1_000_000, 1_000_000))
        if review is not None:
            path = ep / "build" / "qa" / "review.md"
            path.write_text(review, encoding="utf-8")
            os.utime(path, (1_000_600, 1_000_600))
        if video_after_review:
            os.utime(video, (1_001_200, 1_001_200))
        return ep

    def test_each_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(review_state(self.episode(tmp, None)), "unreviewed")
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(review_state(self.episode(tmp, "Still looking at the stills.")), "unreviewed")
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(review_state(self.episode(tmp, "REJECTED: the loop jumps")), "rejected")
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(review_state(self.episode(tmp, "APPROVED")), "approved")

    def test_a_review_of_an_earlier_cut_is_stale(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(review_state(self.episode(tmp, "APPROVED", video_after_review=True)), "stale")
