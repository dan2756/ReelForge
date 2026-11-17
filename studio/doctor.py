"""Checks that everything the pipeline needs is in place, and says how to fix whatever isn't.

  python -m studio doctor                  quick checks (a few seconds)
  python -m studio doctor --render-test    also builds the example scene in Blender and renders one frame
"""

from __future__ import annotations

import importlib
import json
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib
from pathlib import Path

from . import BLENDER_VERSION, KIT_NAME, KIT_VERSION
from . import paths as P
from . import settings as S
from . import sounds, tools

OK, WARN, FAIL = "ok", "warn", "FAIL"
MARK = {OK: "  ok  ", WARN: " warn ", FAIL: " FAIL "}


class Report:
    def __init__(self, quiet: bool = False):
        self.rows: list[tuple[str, str, str]] = []
        self.quiet = quiet

    def add(self, status: str, what: str, detail: str = ""):
        self.rows.append((status, what, detail))
        if not self.quiet:
            print(f"[{MARK[status]}] {what}" + (f": {detail}" if detail else ""), flush=True)

    @property
    def failed(self) -> bool:
        return any(s == FAIL for s, _, _ in self.rows)


def run(render_test: bool = False, json_output: bool = False) -> int:
    if json_output:
        r = checks(render_test, quiet=True)
        print(json.dumps({
            "version": KIT_VERSION,
            "root": str(P.ROOT),
            "failed": r.failed,
            "checks": [{"status": status, "what": what, "detail": detail} for status, what, detail in r.rows],
        }, indent=2))
        return 1 if r.failed else 0
    print(f"{KIT_NAME} {KIT_VERSION}: checking {P.ROOT}\n")
    r = checks(render_test)
    print()
    if r.failed:
        print("Some checks FAILED. Fix those first (the line says how), then run this again.")
        return 1
    print("All set." if not any(s == WARN for s, _, _ in r.rows) else "Ready, with the warnings above.")
    return 0


def window_check(r: Report, fix: str) -> None:
    """ReelForge's window: Bun runs it, and its packages (OpenTUI) are installed (studio/ui.py)."""
    from . import ui

    bun = ui.bun_path()
    version = ""
    if bun:
        try:
            version = subprocess.run([str(bun), "--version"], capture_output=True, text=True, timeout=30,
                                     stdin=subprocess.DEVNULL).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            version = ""
    if not version:
        r.add(FAIL, "Window", f"Bun isn't installed: {fix} (it downloads Bun {ui.BUN_VERSION})")
        return
    if not ui.packages_ready():
        r.add(FAIL, "Window", f"Bun {version}, but the window's packages aren't installed: {fix}")
        return
    try:
        core = json.loads((ui.UI / "node_modules" / "@opentui" / "core" / "package.json").read_text("utf-8"))
        r.add(OK, "Window", f"Bun {version}, OpenTUI {core['version']}")
    except (OSError, ValueError, KeyError):
        r.add(FAIL, "Window", f"Bun {version}, but OpenTUI isn't installed: {fix}")


def extension_check(r: Report) -> None:
    """The browser extension that fills in upload pages (docs/EXTENSION.md): its folder is here with the id
    ReelForge lets in, and the file of connected browsers can be read. It is optional, so the worst this row ever
    is, is a warning."""
    try:
        from dashboard import extension_api

        fine, said = extension_api.check()
        r.add(OK if fine else WARN, "Browser extension", said)
    except Exception as e:  # the window's own packages are missing, say: the rows above have that
        r.add(WARN, "Browser extension", f"couldn't be checked ({type(e).__name__}: {e})")


def youtube_api_check(r: Report) -> None:
    """Posting to YouTube through its own API (docs/YOUTUBE-API.md): which channels have given ReelForge the client
    file of their Google Cloud project. The row says that and nothing from the file: not a value, and not the words
    of an error that might carry one. It is optional, so no client anywhere is a pass, and the worst this row ever
    is, is a warning."""
    try:
        from dashboard.platforms import youtube_api

        from . import profiles

        every = profiles.every()
        have, without, unread = [], [], []
        for p in every:
            path = p.data / "youtube_api.json"   # each channel's own (youtube_api.store_path)
            try:
                store = json.loads(path.read_text("utf-8"))
                if not isinstance(store, dict):
                    raise ValueError
            except FileNotFoundError:
                without.append(p.name)
                continue
            except (OSError, ValueError):
                unread.append(p)
                continue
            client = all(youtube_api._usable(store.get(key)) for key in ("client_id", "client_secret"))
            (have if client else without).append(p.name)
        if unread:
            # With several channels the command says whose file it means (-p): left to itself it works on the
            # first channel, and would give that one a client it never asked for.
            files = ", ".join(P.rel(p.data / "youtube_api.json") for p in unread)
            again = " and ".join(f"reelforge{'' if len(every) == 1 else f' -p {p.flag}'} youtube-api client FILE"
                                 for p in unread)
            r.add(WARN, "YouTube API", f"{files} can't be read, so ReelForge has no client there. Give it the client "
                                       f"file again: {again}")
        elif not have:
            r.add(OK, "YouTube API", "not set up (optional: docs/YOUTUBE-API.md)")
        else:
            whose = "" if len(every) == 1 else f" for {', '.join(have)}"
            rest = f" (not set up for {', '.join(without)})" if without else ""
            # (the guide beside it: it is where the window's side of this is said, as far as it has got)
            r.add(OK, "YouTube API", f"client on file{whose}{rest}. You connect in ReelForge's window each time it "
                                     "opens (docs/YOUTUBE-API.md)")
    except Exception as e:  # the window's own packages are missing, say: the rows above have that
        r.add(WARN, "YouTube API", f"couldn't be checked ({type(e).__name__})")


def checks(render_test: bool = False, quiet: bool = False) -> Report:
    """Every check, as a Report (printed line by line unless quiet)."""
    r = Report(quiet)
    fix = "run setup again (reelforge setup, or setup.bat)" if P.WINDOWS else "run ./setup.sh again"

    v = sys.version_info
    r.add(OK if v >= (3, 11) else FAIL, "Python", f"{v.major}.{v.minor}.{v.micro}" +
          ("" if v >= (3, 11) else " (needs 3.11 or newer)"))
    missing = []
    for mod in ("numpy", "PIL", "jinja2", "httpx", "playwright"):
        try:
            importlib.import_module(mod)
        except ImportError:
            missing.append(mod)
    r.add(FAIL if missing else OK, "Python packages", f"missing {', '.join(missing)}: {fix}" if missing else
          "numpy, Pillow, Jinja2, httpx, Playwright")
    window_check(r, fix)

    blender = tools.find_blender()
    if not blender:
        r.add(FAIL, "Blender", f"not found: {fix} (it can download Blender {BLENDER_VERSION}), or set STUDIO_BLENDER")
    else:
        ver = tools.blender_version(blender)
        series = ".".join(BLENDER_VERSION.split(".")[:2])
        if not ver:
            r.add(FAIL, "Blender", f"{blender} won't start")
        elif ver.startswith(series + ".") or ver == series:
            r.add(OK, "Blender", f"{ver} ({P.rel(Path(blender))})")
        else:
            r.add(WARN, "Blender", f"{ver} found; the pipeline is built on {series} LTS and may not work on other "
                                   f"versions. Setup can put {BLENDER_VERSION} in this folder.")

    ffmpeg = tools.find_ffmpeg()
    if not ffmpeg:
        r.add(FAIL, "ffmpeg", f"not found: {fix}, or set STUDIO_FFMPEG")
    else:
        try:
            out = subprocess.run([ffmpeg, "-version"], capture_output=True, text=True, timeout=30).stdout
            ver = out.split("\n", 1)[0].replace("ffmpeg version ", "").split(" ")[0]
            has_probe = tools.has_ffprobe(ffmpeg)
            r.add(OK if has_probe else FAIL, "ffmpeg", f"{ver} ({P.rel(Path(ffmpeg))})" +
                  ("" if has_probe else f"; ffprobe is missing next to it: {fix}"))
        except (OSError, subprocess.SubprocessError) as e:
            r.add(FAIL, "ffmpeg", f"won't run: {e}")

    fonts = tools.find_fonts()
    absent = [str(f) for f in fonts if not Path(f).is_file()]
    hint = "" if P.WINDOWS or P.MAC else f" ({tools.font_package()})"
    r.add(FAIL if absent else OK, "Fonts", ("missing: " + ", ".join(absent) + hint) if absent else
          ", ".join(sorted({Path(f).stem for f in fonts})))

    from . import browsers

    b = browsers.find()
    r.add(OK if b else WARN, "Stats browser", b.describe() if b else
          f"no Chrome, Edge or Chromium for the TikTok, Instagram and X stats: {fix} (it downloads Chromium)")

    try:
        gone = sounds.missing()
        mix = [n for n, m in gone if m["source"] == "mixkit"]
        other = [n for n, m in gone if m["source"] != "mixkit"]
        total = len(sounds.manifest()["sounds"])
        if other:
            r.add(FAIL, "Sounds", f"{len(other)} kit sounds are missing ({', '.join(other[:3])}...): re-download the kit")
        elif mix:
            r.add(FAIL, "Sounds", f"{len(mix)} Mixkit sounds not downloaded yet: {fix} (or python -m studio sounds)")
        else:
            r.add(OK, "Sounds", f"all {total} in place")
    except (OSError, ValueError) as e:
        r.add(FAIL, "Sounds", f"can't read assets/sfx/manifest.json: {e}")

    if tools.path_too_long():
        r.add(WARN, "Folder path", f"{len(str(P.ROOT))} characters long; Windows can't load some of Blender's files "
                                   "from paths that deep. Move this folder somewhere shorter, e.g. C:\\Studio")
    free = P.free_gb()
    r.add(OK if free >= 10 else WARN, "Disk space", f"{free:.0f} GB free" + ("" if free >= 10 else
          " (keep 10 GB free: a render needs about 1 GB per video until it is shipped)"))

    if S.onboarded():
        try:
            s = S.load()
            r.add(OK, "First-time setup", f"done for {s['channel']['name']} (my-studio/studio.toml)")
        except S.SettingsError as e:     # a hand edit with a typo in it: every command that reads it stops there
            r.add(FAIL, "Settings", str(e))
        for name in ("CLAUDE.md", "AGENTS.md"):
            f = P.ROOT / name
            gen = f.exists() and "generated by" in f.read_text(encoding="utf-8", errors="replace")[:400]
            r.add(OK if gen else WARN, name, "written by first-time setup" if gen else
                  "not generated yet: run reelforge channel")
    else:
        r.add(WARN, "First-time setup", "not done yet: run reelforge channel (or channel" +
              (".bat)" if P.WINDOWS else ".sh)"))

    def window_settings(cfg: Path) -> bool:
        """A profile's config.toml reads (or isn't there yet). One with a typo stops ReelForge's window from opening
        on that profile, so it's a failed check that names the file."""
        try:
            if cfg.exists():
                tomllib.loads(cfg.read_text(encoding="utf-8"))
        except (tomllib.TOMLDecodeError, UnicodeDecodeError) as e:
            r.add(FAIL, "Settings", f"{cfg} has a typo ({e}): ReelForge can't open until it's fixed in a text editor")
            return False
        return True

    dash_cfg = P.DASHBOARD / "config.toml"
    if P.DASHBOARD.exists() and window_settings(dash_cfg):
        r.add(OK if dash_cfg.exists() else WARN, "Dashboard", "configured" if dash_cfg.exists() else
              "not configured yet (first-time setup writes dashboard/config.toml)")
    from . import profiles

    more = profiles.extra()
    for p in more:
        try:
            S.load(p.settings)
        except S.SettingsError as e:
            r.add(FAIL, "Settings", str(e))
        window_settings(p.config)
    if more:  # the other channels, each set up in its own folder (studio/profiles.py)
        unset = [p.name for p in more if not p.onboarded]
        r.add(WARN if unset else OK, "Profiles", f"{len(more) + 1} channels: " +
              ", ".join(p.name for p in profiles.every()) +
              (f" ({', '.join(unset)} not set up yet: its Settings in the profile menu)" if unset else ""))

    extension_check(r)
    youtube_api_check(r)

    if render_test and not r.failed:
        render_check(r)
    return r


def render_check(r: Report) -> None:
    """Build the example scene and render one frame, in a temporary episode folder that is deleted afterwards."""
    example = next((P.EXAMPLES / n / "episode.json" for n in ("demo_doubling_duck", "demo_disaster_pov")
                    if (P.EXAMPLES / n / "episode.json").exists()), None)
    if example is None:
        r.add(WARN, "Render test", "no example scene found in examples/")
        return
    tmp = Path(tempfile.mkdtemp(prefix="render_test_", dir=P.ROOT / "episodes"))
    try:
        spec = json.loads(example.read_text(encoding="utf-8"))
        spec["id"] = tmp.name
        (tmp / "episode.json").write_text(json.dumps(spec), encoding="utf-8")
        t0 = time.time()
        print("        building the example scene in Blender (about a minute)...",
              file=sys.stderr if r.quiet else sys.stdout, flush=True)
        cmd = [sys.executable, str(P.ROOT / "pipeline" / "make_episode.py"), str(tmp), "--steps", "build,stills",
               "--stills", "21"]
        res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        still = next(iter(sorted((tmp / "build" / "stills").glob("s_*.jpg"))), None) if res.returncode == 0 else None
        if still and still.stat().st_size > 10_000:
            keep = P.ROOT / "my-studio" / "render-test.jpg"
            keep.parent.mkdir(exist_ok=True)
            shutil.copy2(still, keep)
            r.add(OK, "Render test", f"built and rendered a frame in {time.time() - t0:.0f} s "
                                     f"(my-studio/render-test.jpg)")
        else:
            log = tmp / "build" / "build.log"
            built = log.read_text(encoding="utf-8", errors="replace") if log.exists() else ""
            if res.returncode and "[build_scene] saved" in built:
                # Blender did its part. What stopped is in the command's own output: the check of episode.json
                # (its FAIL rows), the still, or the text drawn over it (a failed step prints the end of its log).
                said = (res.stdout or "") + (res.stderr or "")
                rows = [line for line in said.splitlines() if "[FAIL]" in line]
                r.add(FAIL, "Render test", "Blender built the example, then a later step stopped:\n" +
                      ("\n".join(rows) or said[-1500:]))
            else:
                r.add(FAIL, "Render test", "Blender couldn't build or render the example:\n" +
                      (built or res.stdout)[-600:])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
