"""A release waits for the tests of the very commit it's made from (.github/workflows): the Release workflow runs the
Test workflow itself and publishes only after it. 2.2.0 and 2.2.1 were published while their own test runs were
failing, because a tag started the two workflows side by side.

The workflows are read as text (the kit has no YAML reader); they aren't part of a download, so there's nothing to
check in an installed copy."""

import re
import unittest
from pathlib import Path

WORKFLOWS = Path(__file__).resolve().parent.parent / ".github" / "workflows"


def jobs(text: str) -> dict[str, str]:
    """A workflow's jobs by id: each one's own lines."""
    body = text[text.index("\njobs:\n") + len("\njobs:\n"):]
    starts = list(re.finditer(r"^  ([A-Za-z0-9_-]+):\s*$", body, re.M))
    return {m[1]: body[m.end():(starts[i + 1].start() if i + 1 < len(starts) else len(body))]
            for i, m in enumerate(starts)}


def key(job: str, name: str) -> str:
    """A job's own setting (its four-space keys, not a step's)."""
    found = re.search(rf"^    {name}:[ \t]*(.*)$", job, re.M)
    return found[1].strip() if found else ""


@unittest.skipUnless(WORKFLOWS.is_dir(), "the workflows aren't part of a download")
class ReleaseGate(unittest.TestCase):
    def setUp(self):
        self.test = (WORKFLOWS / "test.yml").read_text(encoding="utf-8")
        self.release = jobs((WORKFLOWS / "release.yml").read_text(encoding="utf-8"))

    def test_the_test_workflow_can_be_run_by_the_release(self):
        triggers = self.test[self.test.index("\non:\n"):self.test.index("\njobs:\n")]
        self.assertRegex(triggers, r"(?m)^  workflow_call:\s*$")
        self.assertRegex(triggers, r"(?m)^  pull_request:\s*$")                 # and still by a pull request,
        self.assertRegex(triggers, r'(?m)^  push:\s*\n    branches: \["\*\*"\]')  # and a push to any branch

    def test_publishing_needs_every_test_of_this_commit(self):
        gate = next((name for name, job in self.release.items()
                     if key(job, "uses") == "./.github/workflows/test.yml"), None)
        self.assertIsNotNone(gate, "no job in release.yml runs the Test workflow")
        publish = self.release["release"]
        self.assertIn("gh release create", publish)
        needs = re.findall(r"[A-Za-z0-9_-]+", key(publish, "needs"))
        self.assertIn(gate, needs)
        self.assertIn("packages", needs)
        # Nothing lets the release run when the tests didn't: no always(), and the tests run whenever it would
        # (a job that needs a skipped job is skipped too, so it could not be the tests that are left out).
        self.assertEqual(key(publish, "if"), "startsWith(github.ref, 'refs/tags/v')")
        self.assertEqual(key(self.release[gate], "if"), key(publish, "if"))
        for job in (publish, self.release[gate]):
            self.assertNotIn("continue-on-error", job.split("    steps:")[0])

    def test_only_the_built_downloads_are_published(self):
        # the release uploads everything in dist/, and the jobs of the Test workflow may keep files of their own
        publish = self.release["release"]
        step = publish[publish.index("actions/download-artifact"):].split("\n      - ", 1)[0]
        self.assertRegex(step, r"(?m)^          name: packages\b")


if __name__ == "__main__":
    unittest.main()
