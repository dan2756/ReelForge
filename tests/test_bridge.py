"""The loop under the window's Python side (studio/bridge.py): its workers, and what quitting waits for."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

from studio.bridge import Loop

ROOT = Path(__file__).resolve().parent.parent

# A service whose read is still under way when the refresh timer starts the next worker in the same group, which
# finds it busy and is over at once (dashboard.service.run_refresh does this every few minutes).
SLOW_READ = """
import sys, time
bridge = sys.modules["__main__"]

class Reader(bridge.Service):
    def start(self, options):
        def read():
            time.sleep(0.8)
            with open(options["saved"], "w") as f:
                f.write("saved")
        self.loop.run_worker(read, group="refresh", exclusive=True)
        self.loop.run_worker(lambda: None, group="refresh", exclusive=True)
        return True

bridge.make_service = lambda name, loop: Reader(loop)
"""


class Workers(unittest.TestCase):
    def test_a_newer_worker_in_a_group_does_not_hide_one_still_running(self):
        loop = Loop(out=None)
        go, over = threading.Event(), threading.Event()
        first = loop.run_worker(go.wait, group="refresh", exclusive=True)
        second = loop.run_worker(over.set, group="refresh", exclusive=True)
        self.assertTrue(over.wait(5))
        second.thread.join(5)
        self.assertEqual(loop.live(("refresh",)), [first])  # the one that finished is gone, the read is still there
        self.assertTrue(first.is_cancelled)  # exclusive: told to stop waiting, as before
        self.assertEqual(loop.live(("login",)), [])
        go.set()
        first.thread.join(5)
        self.assertEqual(loop.live(), [])

    def test_quitting_waits_for_a_read_that_a_newer_worker_overtook(self):
        with tempfile.TemporaryDirectory() as tmp:
            preload, saved = Path(tmp) / "preload.py", Path(tmp) / "saved.txt"
            preload.write_text(SLOW_READ, encoding="utf-8")
            env = dict(os.environ, PYTHONUTF8="1", REELFORGE_LOG="", REELFORGE_BRIDGE_PRELOAD=str(preload))
            lines = [{"id": 0, "m": "start", "a": ["dashboard", {"saved": str(saved)}]},
                     {"id": 1, "m": "stop", "a": []}]
            res = subprocess.run([sys.executable, "-m", "studio.bridge"], cwd=ROOT, env=env, capture_output=True,
                                 text=True, encoding="utf-8", timeout=60,
                                 input="".join(json.dumps(m) + "\n" for m in lines))
            replies = {m["id"]: m for m in map(json.loads, res.stdout.splitlines()) if "id" in m}
            self.assertIs(replies[1]["r"], True)  # the window is told a read is still finishing
            self.assertEqual(res.returncode, 0)
            self.assertTrue(saved.exists(), "the Python side exited before the read had saved what it read")


if __name__ == "__main__":
    unittest.main()
