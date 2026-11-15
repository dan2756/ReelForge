"""A release waits for the tests of the very commit it's made from (.github/workflows): the Release workflow runs the
Test workflow itself, and the Extension workflow (the browser extension's own tests), and publishes only after
both. 2.2.0 and 2.2.1 were published while their own test runs were failing, because a tag started the two
workflows side by side. The gate is only as good as the workflows it runs, so they are held to failing when their
tests do.

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


def under(job: str, name: str) -> list[str]:
    """The lines under one of a job's own settings (its six-space lines), without their comments."""
    found = re.search(rf"^    {name}:[ \t]*\n((?:      .*\n)*)", job, re.M)
    return [line.split("#")[0].strip() for line in found[1].splitlines()] if found else []


CALLED = ("test.yml", "extension.yml")   # the workflows a release runs and waits for


@unittest.skipUnless(WORKFLOWS.is_dir(), "the workflows aren't part of a download")
class ReleaseGate(unittest.TestCase):
    def setUp(self):
        self.called = {name: (WORKFLOWS / name).read_text(encoding="utf-8") for name in CALLED}
        self.release = jobs((WORKFLOWS / "release.yml").read_text(encoding="utf-8"))

    def gate(self, workflow: str) -> str:
        """The job of release.yml that runs a workflow."""
        found = next((name for name, job in self.release.items()
                      if key(job, "uses") == f"./.github/workflows/{workflow}"), None)
        self.assertIsNotNone(found, f"no job in release.yml runs {workflow}")
        return found

    def test_the_test_workflows_can_be_run_by_the_release(self):
        for name, text in self.called.items():
            with self.subTest(workflow=name):
                triggers = text[text.index("\non:\n"):text.index("\njobs:\n")]
                self.assertRegex(triggers, r"(?m)^  workflow_call:\s*$")
                self.assertRegex(triggers, r"(?m)^  pull_request:\s*$")                 # and still by a pull request,
                self.assertRegex(triggers, r'(?m)^  push:\s*\n    branches: \["\*\*"\]')  # a push to any branch,
                self.assertRegex(triggers, r"(?m)^  workflow_dispatch:\s*$")            # and by hand

    def test_publishing_needs_every_test_of_this_commit(self):
        publish = self.release["release"]
        self.assertIn("gh release create", publish)
        needs = re.findall(r"[A-Za-z0-9_-]+", key(publish, "needs"))
        self.assertIn("packages", needs)
        # Nothing lets the release run when the tests didn't: no always(), and the tests run whenever it would
        # (a job that needs a skipped job is skipped too, so it could not be the tests that are left out).
        self.assertEqual(key(publish, "if"), "startsWith(github.ref, 'refs/tags/v')")
        self.assertNotIn("continue-on-error", publish.split("    steps:")[0])
        for workflow in CALLED:
            with self.subTest(workflow=workflow):
                gate = self.gate(workflow)
                self.assertIn(gate, needs)
                self.assertEqual(key(self.release[gate], "if"), key(publish, "if"))
                self.assertNotIn("continue-on-error", self.release[gate])

    def test_a_called_workflow_fails_when_its_tests_do(self):
        # The release needs the job that calls a workflow, and that job is only as red as the workflow lets itself
        # be. So no job of either may carry on after an error. (A step of test.yml may: the test render on GitHub's
        # Intel Macs, which have no graphics card to render with.)
        for workflow, text in self.called.items():
            for name, job in jobs(text).items():
                with self.subTest(workflow=workflow, job=name):
                    self.assertEqual(key(job, "continue-on-error"), "")
        # The extension's workflow is one job of plain steps, so more can be held: nothing in it carries on after
        # an error, its job is never skipped, and both of its suites run as written, with nothing after the command
        # that could swallow a failure.
        extension = self.called["extension.yml"]
        self.assertNotIn("continue-on-error", extension)
        (job,) = jobs(extension).values()
        self.assertEqual(key(job, "if"), "")
        steps = job.split("\n      - ")
        for suite in ("--stand-in", "--real"):
            with self.subTest(suite=suite):
                run = f"node extension/tests/run.mjs {suite}"
                (step,) = [s for s in steps if run in s]
                self.assertRegex(step, rf"(?m)^        run: {re.escape(run)}$")
                self.assertNotRegex(step, r"(?m)^        if:")

    def test_the_tests_a_release_runs_get_a_token_that_can_only_read(self):
        # A called workflow's jobs get the token of the job that calls them, and the release's own can write (it
        # publishes). The tests run the commit's code, so the jobs that call them hand on read access to the
        # checkout and nothing else. A called workflow that asked for more would stop the release before it starts.
        for workflow in CALLED:
            with self.subTest(workflow=workflow):
                self.assertEqual(under(self.release[self.gate(workflow)], "permissions"), ["contents: read"])
                self.assertNotRegex(self.called[workflow], r"(?m)^\s+[a-z-]+:\s*write\b")
        # ... while publishing still can: the workflow's own setting, which the release job goes by
        release = (WORKFLOWS / "release.yml").read_text(encoding="utf-8")
        self.assertRegex(release[:release.index("\njobs:\n")], r"(?m)^permissions:\n(?:  .*\n)*?  contents: write$")
        self.assertEqual(under(self.release["release"], "permissions"), [])

    def test_only_the_built_downloads_are_published(self):
        # the release uploads everything in dist/, and the jobs of the Test workflow may keep files of their own
        publish = self.release["release"]
        step = publish[publish.index("actions/download-artifact"):].split("\n      - ", 1)[0]
        self.assertRegex(step, r"(?m)^          name: packages\b")


if __name__ == "__main__":
    unittest.main()
