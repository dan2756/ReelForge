"""Makes the extension's icons from ReelForge's own icon.

Run it from the ReelForge folder after the artwork changes:

    .venv/bin/python extension/tools/make_icons.py

It needs Pillow, which ReelForge's setup installs. The PNGs it writes are committed, so nobody has to run this to
load the extension.
"""
from pathlib import Path

from PIL import Image

KIT = Path(__file__).resolve().parents[2]
SOURCE = KIT / "assets" / "icon" / "reelforge.png"
OUT = KIT / "extension" / "icons"
SIZES = (16, 32, 48, 128)


def main() -> None:
    art = Image.open(SOURCE).convert("RGBA")
    OUT.mkdir(parents=True, exist_ok=True)
    for size in SIZES:
        # LANCZOS keeps the anvil's edges clean at 16 px, where the toolbar shows it.
        art.resize((size, size), Image.LANCZOS).save(OUT / f"icon{size}.png", optimize=True)
        print(f"icons/icon{size}.png")


if __name__ == "__main__":
    main()
