"""For the test of leaving the first-run screens while they work (ui/test/setup.test.tsx): what
scripts/shots_preload.py sets up, with the health check that ends the downloads screen held back until the file named
in REELFORGE_TEST_HOLD exists (20 seconds at most), so the test can press keys while the screen is still working. Run
inside the window's Python side before it starts (REELFORGE_BRIDGE_PRELOAD)."""

import os
import runpy
import time
from pathlib import Path

from studio import doctor

runpy.run_path(str(Path(__file__).resolve().parent.parent.parent / "scripts" / "shots_preload.py"), run_name="preload")
checks = doctor.checks


def held(*args, **kwargs):
    end = time.monotonic() + 20
    while not os.path.exists(os.environ["REELFORGE_TEST_HOLD"]) and time.monotonic() < end:
        time.sleep(0.05)
    return checks(*args, **kwargs)


doctor.checks = held
