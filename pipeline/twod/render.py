"""Render a 2D episode's frames: the episode's scene.js, drawn by pipeline/twod/runtime.js in a headless browser.

  python pipeline/twod/render.py episodes/<id> --stills 1,15,31     (build/stills/s_####.jpg, captions included)
  python pipeline/twod/render.py episodes/<id> --frames             (build/frames/f_####.jpg, resumable)
  python pipeline/twod/render.py episodes/<id> --cover-base         (build/cover_base.jpg: the cover's frame, no captions)

The browser is the one ReelForge reads stats with (studio/browsers.py): Edge on Windows, Chrome or Edge elsewhere, or
Playwright's own Chromium. Frames are numbered like Blender's (frame 1 = 0 s), so the rest of the pipeline
(compose.py, QA) treats a 2D episode like any other. Needs build/voice/timeline.json (the voice step) first.
"""
from __future__ import annotations

import argparse
import base64
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402

HERE = Path(__file__).resolve().parent
W, H = config.WIDTH, config.HEIGHT


def page_html(ep: Path, spec: dict, timeline: dict) -> str:
    """The page the browser opens: the canvas, the timeline, the runtime and the episode's scene.js."""
    data = {
        "title": spec.get("title", ""),
        "fps": spec["fps"],
        "duration": spec["duration"],
        "beats": timeline.get("beats", []),
        "words": timeline.get("words", []),
        "captions": spec.get("captions", {}),
        "base": ep.as_uri() + "/",
    }
    scene = spec.get("scene", "scene.js")
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><style>html,body{{margin:0;background:#000;overflow:hidden}}
canvas{{display:block;width:{W}px;height:{H}px}}</style></head>
<body><canvas id="c" width="{W}" height="{H}"></canvas>
<script>window.RF_DATA = {json.dumps(data, ensure_ascii=False)};</script>
<script src="{(HERE / 'runtime.js').as_uri()}"></script>
<script src="{(ep / scene).as_uri()}?v={int((ep / scene).stat().st_mtime)}"></script>
</body></html>
"""


class Renderer:
    """A headless browser with the episode's page open. Use as a context manager."""

    def __init__(self, ep: Path):
        self.ep = ep
        self.spec = config.load_spec(ep)
        tl = ep / "build" / "voice" / "timeline.json"
        if not tl.exists():
            raise SystemExit("2D: no voiceover timeline yet. Run --steps build first (it voices the script).")
        self.timeline = json.loads(tl.read_text(encoding="utf-8"))
        scene = ep / self.spec.get("scene", "scene.js")
        if not scene.exists():
            raise SystemExit(f"2D: {scene.name} not found in {ep}. Write the scene (docs/2D.md) first.")
        self.errors: list[str] = []

    def __enter__(self):
        from playwright.sync_api import sync_playwright

        sys.path.insert(0, str(config.ROOT))
        from studio import browsers

        b = browsers.find()
        if b is None:
            raise SystemExit("2D: no browser to draw with. Run setup again: it can download Chromium.")
        self._pw = sync_playwright().start()
        kw = {"headless": True, "args": ["--allow-file-access-from-files", "--disable-web-security",
                                         "--force-color-profile=srgb", "--hide-scrollbars"]}
        if b.exe:
            kw["executable_path"] = str(b.exe)
        self.browser = self._pw.chromium.launch(**kw)
        self.page = self.browser.new_page(viewport={"width": W, "height": H}, device_scale_factor=1)
        self.page.on("pageerror", lambda e: self.errors.append(str(e)))
        self.page.on("console", lambda m: self.errors.append(m.text) if m.type == "error" else None)
        html = self.ep / "build" / "2d" / "index.html"
        html.parent.mkdir(parents=True, exist_ok=True)
        html.write_text(page_html(self.ep, self.spec, self.timeline), encoding="utf-8")
        self.page.goto(html.as_uri())
        t0 = time.time()
        while True:
            state = self.page.evaluate("[window.__rf_ready === true, window.__rf_error || '']")
            if state[0]:
                break
            if state[1] or self.errors or time.time() - t0 > 60:
                self.close()
                raise SystemExit("2D: the scene didn't load:\n  " + (state[1] or "\n  ".join(self.errors) or
                                                                     "timed out after 60 s"))
            time.sleep(0.05)
        return self

    def frame(self, t: float, captions: bool = True) -> bytes:
        try:
            url = self.page.evaluate("([t, c]) => window.__rf_frame(t, {captions: c})", [t, captions])
        except Exception as e:     # the scene threw: show where
            raise SystemExit(f"2D: scene.js failed drawing {t:.2f} s:\n  {e}") from None
        return base64.b64decode(url.split(",", 1)[1])

    def close(self):
        try:
            self.browser.close()
        finally:
            self._pw.stop()

    def __exit__(self, *exc):
        self.close()


def stills(ep: Path, secs: list[float]) -> list[Path]:
    out = ep / "build" / "stills"
    out.mkdir(parents=True, exist_ok=True)
    written = []
    with Renderer(ep) as r:
        fps = r.spec["fps"]
        for s in secs:
            n = int(round(s * fps)) + 1
            p = out / f"s_{n:04d}.jpg"
            p.write_bytes(r.frame((n - 1) / fps))
            written.append(p)
    print(f"  wrote {len(written)} stills to {out}")
    return written


def frames(ep: Path) -> int:
    """Every frame up to render_until (the whole video, unless the spec says otherwise). Skips frames already
    there, so a stopped render carries on; delete build/frames after changing the scene or the script."""
    out = ep / "build" / "frames"
    import hashlib
    spec = config.load_spec(ep)
    scene = ep / spec.get("scene", "scene.js")
    tl = ep / "build" / "voice" / "timeline.json"
    drawn = json.dumps({k: spec.get(k) for k in ("title", "captions", "duration", "render_until", "fps", "scene")},
                       sort_keys=True).encode()      # what the picture depends on (the sound cues don't count)
    stamp = hashlib.sha1(drawn + b"".join(p.read_bytes() for p in (scene, tl, HERE / "runtime.js")
                                          if p.exists())).hexdigest()
    mark = out / "stamp.txt"
    if out.exists() and (not mark.exists() or mark.read_text(encoding="utf-8").strip() != stamp):
        old = list(out.glob("f_*.jpg"))
        for f in old:          # the scene, the script or the spec changed: the frames drawn before are out of date
            f.unlink()
        if old:
            print(f"  the scene, script or spec changed since the last render: drawing all {len(old)} frames again")
    out.mkdir(parents=True, exist_ok=True)
    mark.write_text(stamp + "\n", encoding="utf-8")
    with Renderer(ep) as r:
        spec, fps = r.spec, r.spec["fps"]
        n = int(round(spec.get("render_until", spec["duration"]) * fps))
        todo = [i for i in range(n) if not (out / f"f_{i + 1:04d}.jpg").exists()]
        t0, done = time.time(), 0
        for i in todo:
            tmp = out / f"f_{i + 1:04d}.tmp"
            tmp.write_bytes(r.frame(i / fps))
            tmp.replace(out / f"f_{i + 1:04d}.jpg")
            done += 1
            if done % 150 == 0:
                rate = done / max(time.time() - t0, 1e-6)
                print(f"  frame {i + 1}/{n} ({rate:.0f} frames/s)", flush=True)
    print(f"  {n} frames in {out} ({len(todo)} drawn now, in {time.time() - t0:.0f} s)")
    return n


def cover_base(ep: Path) -> Path:
    """The cover's frame without the captions (the cover draws its own words over it)."""
    with Renderer(ep) as r:
        t = float(r.spec.get("cover", {}).get("time", 1.0))
        p = ep / "build" / "cover_base.jpg"
        p.write_bytes(r.frame(t, captions=False))
    print(f"  wrote {p}")
    return p


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("episode", type=Path)
    ap.add_argument("--stills", default="")
    ap.add_argument("--frames", action="store_true")
    ap.add_argument("--cover-base", action="store_true")
    a = ap.parse_args()
    ep = a.episode.resolve()
    if a.stills:
        stills(ep, [float(s) for s in a.stills.split(",") if s.strip()])
    if a.frames:
        frames(ep)
    if a.cover_base:
        cover_base(ep)


if __name__ == "__main__":
    main()
