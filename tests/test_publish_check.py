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

    def test_a_line_that_ends_in_a_not_before_the_verdict_is_no_rejection(self):
        # A list's last line with no full stop, then the verdict on a line of its own, as the guides ask for it.
        # Read across the line break that was "NOT CUT OFF APPROVED", and an approved video could not ship.
        self.assertTrue(self.verdict("- Captions: readable, not cut off\nAPPROVED"))
        self.assertTrue(self.verdict("- Loop: seamless, does not jump\n\nAPPROVED"))
        self.assertTrue(self.verdict("- Not too fast\nAPPROVED"))
        self.assertTrue(self.verdict("Facts: all sourced, not invented\nVerdict APPROVED"))
        self.assertFalse(self.verdict("Looked at every shot.\nNOT YET APPROVED"))   # on one line it is a rejection
        self.assertFalse(self.verdict("Sound: fine.\nNOT\tYET  APPROVED"))
        self.assertFalse(self.verdict("## Verdict\nNOT\u00a0APPROVED"))            # with a no-break space between
        self.assertFalse(self.verdict("NOT\u202fYET\u3000APPROVED"))

    def test_a_rejection_wrapped_onto_two_lines_is_still_a_rejection(self):
        # A review wrapped at 120 columns breaks "NOT APPROVED" between its words about one time in ten. Read line by
        # line that was an approval: the APPROVED that starts the next line was the verdict, and the cut shipped.
        self.assertFalse(self.verdict("The rewind is 2.6 s, over the 2.2 s the format allows, so this cut is NOT\n"
                                      "APPROVED. Fix both and render again."))
        self.assertFalse(self.verdict("Two captions sit over the wrong shot. NOT YET\nAPPROVED: fix them first."))
        self.assertFalse(self.verdict("Two captions sit over the wrong shot. This cut is NOT\nYET APPROVED."))
        self.assertFalse(self.verdict("The loop jumps at the rewind, so this cut has NOT BEEN\nAPPROVED."))
        self.assertFalse(self.verdict("The loop jumps at the rewind, so this cut has NOT YET BEEN\n  APPROVED."))
        self.assertFalse(self.verdict("The loop jumps at the rewind, so this cut has NOT YET\nBEEN APPROVED."))
        self.assertFalse(self.verdict("The counter can't be read over the clouds: this cut should NOT BE\nAPPROVED."))
        self.assertFalse(self.verdict("The counter can't be read over the clouds: this cut is NOT TO\nBE APPROVED."))
        self.assertFalse(self.verdict("so this cut is NOT   \n    APPROVED"))               # spaces round the break
        # ... and after a word no statement ends on, with one more word after the break at most
        self.assertFalse(self.verdict("The title covers the counter, so this cut is NOT FULLY\nAPPROVED yet."))
        self.assertFalse(self.verdict("The title covers the counter, so this cut is NOT YET\nFULLY APPROVED."))
        self.assertFalse(self.verdict("Looked at it again, and as it stands this is NOT an\nAPPROVED cut."))
        self.assertTrue(self.verdict("- Is the floor level? Not quite level, and it reads fine\nAPPROVED"))
        self.assertFalse(self.verdict("APPROVED\n\nSecond look: a caption is cut off at 41 s, so this one is not\n"
                                      "approved after all."))
        with tempfile.TemporaryDirectory() as tmp:      # ... and with the line ends Windows writes
            path = Path(tmp) / "review.md"
            path.write_bytes(b"The rewind is too long, so this cut is NOT\r\nAPPROVED. Fix it and render again.\r\n")
            self.assertFalse(review_approved(path))
            path.write_bytes(b"- Captions: readable, not cut off\r\nAPPROVED\r\n")
            self.assertTrue(review_approved(path))
            # CR CR LF: text that had CRLF already, written out as text on Windows. Read as text it has an empty
            # line after every line, and the two halves of the rejection were two statements.
            path.write_bytes(b"The rewind is too long, so this cut is NOT\r\r\nAPPROVED. Fix it and render again.\r\r\n")
            self.assertFalse(review_approved(path))
            path.write_bytes(b"- Captions: readable, not cut off\r\r\nAPPROVED\r\r\n")
            self.assertTrue(review_approved(path))
            path.write_bytes(b"- Glitch before the reversal: there is not\r\r\n\r\r\nAPPROVED\r\r\n")   # an empty line
            self.assertTrue(review_approved(path))

    def test_no_longer_approved_is_a_rejection(self):
        # in capitals it was an approval: the word APPROVED, and no "not" before it
        self.assertFalse(self.verdict("NO LONGER APPROVED: the loop jumps at the rewind."))
        self.assertFalse(self.verdict("APPROVED\n\nSecond look: the loop jumps at the rewind, so this cut is no longer "
                                      "approved."))
        self.assertFalse(self.verdict("APPROVED\n\nSecond look: the loop jumps at the rewind, so this cut is no longer\n"
                                      "approved."))
        self.assertFalse(self.verdict("Second look: the caption at 12 s is cut off on the right. NO\nLONGER APPROVED."))
        self.assertTrue(self.verdict("- Clipping at 31 s: no longer there\nAPPROVED"))

    def test_an_empty_line_ends_the_statement(self):
        # a new paragraph is a new statement, whatever the one before it ends in
        self.assertTrue(self.verdict("- Glitch before the reversal: there is not\n\nAPPROVED"))
        self.assertTrue(self.verdict("- Posted anywhere: not yet\n\nAPPROVED"))
        # Straight under a line that ends in "not" or "not yet" it reads as that sentence going on. That is the safe
        # way round: the other way, a cut that was turned down ships.
        self.assertFalse(self.verdict("- Posted anywhere: not yet\nAPPROVED"))

    def test_every_kind_of_line_break_ends_a_line(self):
        for name, br in (("vertical tab", chr(0x0b)), ("form feed", chr(0x0c)), ("next line", chr(0x85)),
                         ("line separator", chr(0x2028)), ("paragraph separator", chr(0x2029))):
            with self.subTest(between=name):
                self.assertTrue(self.verdict(f"- Captions: readable, not cut off{br}APPROVED"))
                self.assertFalse(self.verdict(f"so this cut is NOT{br}APPROVED"))

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
