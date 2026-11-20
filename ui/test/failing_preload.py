"""For the tests of a call that fails (ui/test/errors.test.tsx): the demo's window and the first-run screens as
scripts/shots_preload.py sets them up (nothing on this computer changes, no browser opens), with the calls named in
REELFORGE_TEST_FAIL (commas between them) raising, the way a bug or a locked stats database would. Run inside the
window's Python side before it starts (REELFORGE_BRIDGE_PRELOAD)."""

import os
import runpy
from pathlib import Path

from dashboard.service import Dashboard
from studio.bridge import rpc
from studio.setup_service import Setup

runpy.run_path(str(Path(__file__).resolve().parent.parent.parent / "scripts" / "shots_preload.py"), run_name="preload")


def broken(self, *args, **kwargs):
    raise RuntimeError("boom")


for name in filter(None, os.environ.get("REELFORGE_TEST_FAIL", "").split(",")):
    for service in (Dashboard, Setup):
        if hasattr(service, name):
            setattr(service, name, rpc(broken))
