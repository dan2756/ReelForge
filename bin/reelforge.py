"""The reelforge command's entry point. bin/reelforge.cmd (Windows), bin/reelforge (macOS/Linux), ReelForge.bat
and the shortcuts all run this with ReelForge's own Python (.venv). `reelforge help` lists what it can do.

First it checks that ReelForge's Python packages are all there. A setup that stopped part way (its window closed
while it was making .venv, say) leaves some out: then it exits with code 3, and the launchers run setup to repair
.venv instead of ReelForge crashing on its first import. The same when the packages are an older version's: an update
that copied the new files and stopped before installing what they need."""

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPAIR = 3  # the exit code that asks the launchers to run setup
# requirements.txt's packages by the names Python imports them as (tests/test_studio.py keeps this in step with
# requirements.txt)
PACKAGES = ("numpy", "PIL", "jinja2", "httpx", "playwright", "certifi", "truststore")


def missing() -> list[str]:
    """The packages this Python can't find."""
    return [name for name in PACKAGES if importlib.util.find_spec(name) is None]


def outdated(root: Path = ROOT, prefix: str = sys.prefix) -> bool:
    """requirements.txt isn't the one .venv's packages were installed from. Setup and updates keep a copy of the file
    they installed from in .venv/installed.txt, so the two differ only after an update put the new version's files in
    place and stopped before installing its packages. Asked of ReelForge's own .venv only: another Python running
    this (a git copy someone works on) looks after its own packages, and a .venv without that copy has nothing to
    compare."""
    try:
        if Path(prefix).resolve() != (root / ".venv").resolve():
            return False
        return (root / ".venv" / "installed.txt").read_bytes() != (root / "requirements.txt").read_bytes()
    except OSError:
        return False


if __name__ == "__main__":
    if gone := missing():
        print(f"ReelForge's Python packages aren't all installed ({', '.join(gone)} missing): the last setup "
              "probably stopped part way.")
        sys.exit(REPAIR)
    if outdated():
        print("ReelForge's Python packages are an older version's: the last update stopped before it installed the "
              "new ones.")
        sys.exit(REPAIR)
    sys.path.insert(0, str(ROOT))
    from studio.__main__ import main

    sys.exit(main())
