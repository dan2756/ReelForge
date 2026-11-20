"""Read every platform's stats once, save them, and exit, without opening a window.

This is what ReelForge's hourly background job runs (reelforge stats on), so your charts keep filling in and the
bell has news for you while ReelForge is closed. Each run adds a line to data/refresh.log, and so does a run that
couldn't start: under pythonw there is no terminal to say it in.

A run whose folder was moved or removed while it was starting (reelforge move, an uninstall) stops without a word,
and makes nothing where the folder was.
"""
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve()  # this script, by the name it was started under
ROOT = HERE.parent.parent  # the ReelForge folder: the dashboard and studio packages live here
sys.path.insert(0, str(ROOT))
# This run leaves Python's bytecode files to ReelForge's window and setup, which write them anyway. Python makes every
# folder missing on the way to one it writes, so a run whose folder was renamed as it started put the old folder back
# that way too. The runs it starts, one for each profile, do the same.
sys.dont_write_bytecode = True
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

try:
    from dashboard.__main__ import main  # noqa: E402

    main(["refresh"])
except BaseException as stopped:  # main logs what goes wrong in a read; this is what stops it before or outside one
    code = stopped.code if isinstance(stopped, SystemExit) else None
    if not isinstance(stopped, SystemExit) or code not in (None, 0, 1, 2):
        why = ("ReelForge's Python packages are missing: run setup again" if code == 3
               else f"{type(stopped).__name__}: {stopped}".splitlines()[0])
        # Only while this script is still where it was started. Once the folder has been renamed or removed, what
        # stopped the run is that it's gone, and a log written here would put a stub of it back. The data folder is
        # made without its parents for the same reason: the folder above it is this script's own.
        if HERE.is_file():
            try:
                from datetime import datetime

                folder = ROOT / "dashboard" / "data"
                folder.mkdir(exist_ok=True)
                with open(folder / "refresh.log", "a", encoding="utf-8") as log:
                    log.write(f"{datetime.now():%Y-%m-%d %H:%M}  error: the stats read didn't run: {why}\n")
            except OSError:
                pass
