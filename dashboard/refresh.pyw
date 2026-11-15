"""Read every platform's stats once, save them, and exit, without opening a window.

This is what ReelForge's hourly background job runs (reelforge stats on), so your charts keep filling in and the
bell has news for you while ReelForge is closed. Each run adds a line to data/refresh.log, and so does a run that
couldn't start: under pythonw there is no terminal to say it in.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent  # the ReelForge folder: the dashboard and studio packages live here
sys.path.insert(0, str(ROOT))

try:
    from dashboard.__main__ import main  # noqa: E402

    main(["refresh"])
except BaseException as stopped:  # main logs what goes wrong in a read; this is what stops it before or outside one
    code = stopped.code if isinstance(stopped, SystemExit) else None
    if not isinstance(stopped, SystemExit) or code not in (None, 0, 1, 2):
        why = ("ReelForge's Python packages are missing: run setup again" if code == 3
               else f"{type(stopped).__name__}: {stopped}".splitlines()[0])
        try:
            from datetime import datetime

            folder = ROOT / "dashboard" / "data"
            folder.mkdir(parents=True, exist_ok=True)
            with open(folder / "refresh.log", "a", encoding="utf-8") as log:
                log.write(f"{datetime.now():%Y-%m-%d %H:%M}  error: the stats read didn't run: {why}\n")
        except OSError:
            pass
