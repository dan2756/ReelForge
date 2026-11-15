"""ReelForge updates: is there a newer version on GitHub, and putting it in place in this folder.

  python -m studio.update             is there a newer version?
  python -m studio.update --apply     download it and install it into this folder
  python -m studio.update --into DIR  put this copy's version in place in DIR, an older ReelForge, the way an update
                                      does (the install line runs it for a copy the old Windows installer made)

How the new version goes in depends on how this copy got here:
  git        a git clone: git pull (only when you haven't changed the kit's own files yourself)
  zip        anything else (the install line, a download, the old Windows installer): the new version's download is
             unpacked and copied over this folder

Your own things are never touched: my-studio/, episodes/, completed videos/, research/, tools/, .venv/, the window's
packages (ui/node_modules: the update installs what the new version's window needs), the downloaded sounds, the
dashboard's data, settings and videos, your other channels' profiles (profiles/), and CLAUDE.md / AGENTS.md
(the update refreshes those from each profile's saved settings, so they get the new version's instructions). After an update the dashboard exits with code 75,
which tells the reelforge launcher to start it again.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import KIT_VERSION, net, paths, tools

ROOT = Path(__file__).resolve().parent.parent
REPO = "dan2756/ReelForge"
API = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES = f"https://github.com/{REPO}/releases"
RESTART = 75  # the exit code that asks the reelforge launcher to start ReelForge again
# Never overwritten by an update: the person's own channel, videos, tools, data and generated instructions.
KEEP = ("my-studio/", "episodes/", "completed videos/", "research/", "tools/", ".venv/", "ui/node_modules/", ".git/",
        ".claude/", "assets/sfx/mixkit/", "assets/sfx/_cache/", "dashboard/data/", "dashboard/videos/",
        "dashboard/config.toml", "CLAUDE.md", "AGENTS.md", "profiles/")
# Shipped with the kit, and yours from then on: its own first lines invite you to edit it (the reasoning effort Codex
# is asked for). An update puts one in place only where there is none.
STARTERS = (".codex/config.toml",)
VERSION_FILE = "studio/__init__.py"  # names this copy's version (read_version): an update puts it in place last
# There from the first new file an update puts in place until its last: this folder is part old and part new. What a
# later attempt says goes by it, since that attempt may stop without replacing a file itself.
PART_WAY = "dashboard/data/update-part-way"
BUSY_WAIT = 10.0  # seconds an update waits for a file another program has open (a virus scanner, another window)

Progress = Callable[[str, int, int], None]  # (what's happening, done, total); total 0 = no idea how long


@dataclass
class Release:
    version: str                      # "1.0.1"
    tag: str                          # "v1.0.1"
    notes: str = ""                   # the release notes, Markdown
    url: str = RELEASES
    published: str = ""
    assets: dict[str, tuple[str, int]] = field(default_factory=dict)  # file name -> (download url, size)


class UpdateError(Exception):
    """Something stopped the update; the message says what to do. partial: some of the new version is in this folder
    already (the copy or the steps after it stopped part way), so "the version you have keeps working" would not be
    true and the message says how to finish instead. Without it, nothing in this folder has changed."""

    def __init__(self, message: str, partial: bool = False):
        super().__init__(message)
        self.partial = partial


def version_tuple(v: str) -> tuple[int, ...]:
    """'v1.2' -> (1, 2, 0); anything after the numbers (like -beta) is ignored."""
    nums = [int(x) for x in re.findall(r"\d+", (v or "").lstrip("vV").split("-")[0].split("+")[0])[:3]]
    return tuple(nums + [0] * (3 - len(nums)))


def is_newer(candidate: str, than: str = KIT_VERSION) -> bool:
    return version_tuple(candidate) > version_tuple(than)


def install_kind(root: Path = ROOT) -> str:
    """'git' or 'zip': how this copy got here, which decides how it's updated. A copy the old Windows installer made
    (unins000.exe in it) updates the zip way: ReelForge has no installer now."""
    if (root / ".git").exists():
        return "git"
    return "zip"


def _get(url: str, timeout: float = 20, accept: str = "application/vnd.github+json"):
    req = urllib.request.Request(url, headers={"User-Agent": net.AGENT, "Accept": accept})
    return urllib.request.urlopen(req, timeout=timeout, context=net.ssl_context())


def latest(timeout: float = 20) -> Release:
    """The newest published release (GitHub leaves out drafts and pre-releases here). Raises UpdateError."""
    try:
        with _get(API, timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise UpdateError("No ReelForge release is published yet.") from e
        if e.code in (403, 429):
            raise UpdateError("GitHub asked ReelForge to slow down; it checks again later.") from e
        raise UpdateError(f"GitHub answered {e.code}; ReelForge checks again later.") from e
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        raise UpdateError("Couldn't reach GitHub (offline?); ReelForge checks again later.") from e
    except ValueError as e:
        raise UpdateError("GitHub's answer didn't make sense; ReelForge checks again later.") from e
    tag = str(data.get("tag_name") or "")
    if not tag or data.get("draft") or data.get("prerelease"):
        raise UpdateError("No ReelForge release is published yet.")
    assets = {a["name"]: (a["browser_download_url"], int(a.get("size") or 0))
              for a in data.get("assets") or [] if a.get("name") and a.get("browser_download_url")}
    return Release(version=tag.lstrip("vV"), tag=tag, notes=str(data.get("body") or "").strip(),
                   url=str(data.get("html_url") or RELEASES), published=str(data.get("published_at") or ""),
                   assets=assets)


def check(timeout: float = 20) -> dict:
    """What the Updates tab shows: never raises. Keys: current, latest, newer, notes, url, published, assets,
    checked (ISO time), error (a calm one-line message, or "")."""
    out = {"current": KIT_VERSION, "latest": "", "newer": False, "notes": "", "url": RELEASES, "published": "",
           "assets": {}, "error": "", "checked": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    try:
        rel = latest(timeout)
    except UpdateError as e:
        out["error"] = str(e)
        return out
    except Exception as e:  # anything unexpected is still just "try later"
        out["error"] = f"The update check failed ({type(e).__name__}); ReelForge checks again later."
        return out
    out.update(latest=rel.version, newer=is_newer(rel.version), notes=rel.notes, url=rel.url,
               published=rel.published, assets={k: list(v) for k, v in rel.assets.items()})
    return out


def release_from(info: dict) -> Release:
    """A Release back from check()'s dict (the dashboard keeps that dict between checks)."""
    return Release(version=info["latest"], tag="v" + info["latest"], notes=info.get("notes", ""),
                   url=info.get("url", RELEASES), published=info.get("published", ""),
                   assets={k: (v[0], int(v[1])) for k, v in (info.get("assets") or {}).items()})


def asset_name(version: str, *, platform: str = sys.platform) -> str:
    if platform == "win32":
        return f"ReelForge-{version}-Windows.zip"
    if platform == "darwin":
        return f"ReelForge-{version}-macOS.zip"
    return f"ReelForge-{version}-Linux.tar.gz"


# ---------------------------------------------------------------- applying an update

def apply(rel: Release, progress: Progress | None = None, log: Callable[[str], None] = print,
          root: Path = ROOT) -> str:
    """Put `rel` in place in this folder. Returns a one-line summary; raises UpdateError. Each file is replaced in
    one step, and the folder only counts as the new version once every file is in (_copy_tree), so an update that
    stopped part way is offered again and finishes from where it stopped."""
    progress = progress or (lambda what, done, total: None)
    kind = install_kind(root)
    before = _hash(root / "requirements.txt")
    old = KIT_VERSION
    if kind == "git":
        _git_pull(root, progress)
    else:
        name = asset_name(rel.version)
        if name not in rel.assets:
            raise UpdateError(f"The {rel.version} release has no {name} to download. Get it from {rel.url}")
        work = Path(tempfile.mkdtemp(prefix="reelforge-update-"))
        try:
            url, size = rel.assets[name]
            path = _download(url, work / name, size, progress)
            _verify(rel, name, path, progress)
            _copy_release(path, root, work, progress)
        except UpdateError as e:   # whatever stopped this attempt, an earlier one's new files are still in the folder
            e.partial = e.partial or (root / PART_WAY).exists()
            raise
        finally:
            shutil.rmtree(work, ignore_errors=True)
    new = read_version(root) or rel.version
    _after(root, before, progress, log)
    _write_marker(root, old, new)
    return f"Updated ReelForge from {old} to {new}."


def move_in(root: Path, progress: Progress | None = None, log: Callable[[str], None] = print) -> str:
    """Put this copy's version in place in root, an older ReelForge, the way an update does: this copy (ROOT) is a
    download the install line has just unpacked. The install line runs it for a copy the old Windows installer made,
    whose Updates tab only knows how to run a newer installer, and ReelForge has none now. Raises UpdateError."""
    progress = progress or (lambda what, done, total: None)
    if not (root / "studio" / "__init__.py").exists():
        raise UpdateError(f"There's no ReelForge in {root}; nothing was changed.")
    before = _hash(root / "requirements.txt")
    old = read_version(root)
    _copy_tree(ROOT, root, progress)
    new = read_version(root) or KIT_VERSION
    _after(root, before, progress, log)
    _write_marker(root, old, new)
    return f"Updated ReelForge from {old} to {new}."


def read_version(root: Path = ROOT) -> str:
    try:
        text = (root / "studio" / "__init__.py").read_text(encoding="utf-8")
    except OSError:
        return ""
    m = re.search(r'KIT_VERSION = "([^"]+)"', text)
    return m.group(1) if m else ""


def _hash(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return ""


def _download(url: str, dest: Path, size: int, progress: Progress) -> Path:
    try:
        with _get(url, 120, accept="application/octet-stream") as r, dest.open("wb") as f:
            total = int(r.headers.get("Content-Length") or size or 0)
            done = 0
            while chunk := r.read(1 << 18):
                f.write(chunk)
                done += len(chunk)
                progress("Downloading", done, total)
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        raise UpdateError(f"The download stopped ({e}). Check your connection and try again.") from e
    return dest


def _verify(rel: Release, name: str, path: Path, progress: Progress) -> None:
    """Check the file against the release's SHA256SUMS.txt. Every release has one (the install lines insist on it
    too), so a release without it is not installed."""
    if "SHA256SUMS.txt" not in rel.assets:
        raise UpdateError(f"The {rel.version} release has no SHA256SUMS.txt to check its download against, so it "
                          f"wasn't installed. See {rel.url}")
    progress("Checking the download", 0, 0)
    try:
        with _get(rel.assets["SHA256SUMS.txt"][0], 30, accept="application/octet-stream") as r:
            sums = r.read().decode("utf-8", "replace")
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        raise UpdateError(f"Couldn't fetch the release's checksums ({e}); try again.") from e
    want = next((ln.split()[0].lower() for ln in sums.splitlines()
                 if ln.strip() and ln.split()[-1].lstrip("*") == name), None)
    if not want:
        raise UpdateError(f"The release's checksums don't list {name}; download it yourself from {rel.url}")
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    if h.hexdigest().lower() != want:
        raise UpdateError("The download doesn't match the release's checksum (it was damaged on the way); "
                          "try again.")


def kept(rel: str) -> bool:
    """Whether a path (relative to the kit folder, with forward slashes) is the person's own and stays as it is."""
    return any(rel == k.rstrip("/") or rel.startswith(k) for k in KEEP)


def runnable(z: zipfile.ZipFile, out: Path) -> None:
    """ZipFile unpacks every file without its permissions, so the macOS download's scripts (bin/reelforge, setup.sh,
    the .command files) would stop being runnable after an update. Give back the ones the zip marks executable."""
    if os.name == "nt":
        return
    for info in z.infolist():
        target = out / info.filename
        if not info.is_dir() and (info.external_attr >> 16) & 0o111 and target.is_file():
            os.chmod(target, 0o755)


def _copy_release(archive: Path, root: Path, work: Path, progress: Progress) -> None:
    progress("Unpacking", 0, 0)
    out = work / "unpacked"
    try:
        if archive.name.endswith(".zip"):
            with zipfile.ZipFile(archive) as z:
                z.extractall(out)  # ZipFile drops absolute paths and ".." parts
                runnable(z, out)
        else:
            tools.untar(archive, out)   # nothing lands outside the folder, on any Python (see studio/tools.py)
    except (zipfile.BadZipFile, tarfile.TarError, OSError) as e:
        raise UpdateError(f"The download couldn't be unpacked ({e}); try again.") from e
    tops = [p for p in out.iterdir() if p.is_dir()]
    _copy_tree(tops[0] if len(tops) == 1 and not any(p.is_file() for p in out.iterdir()) else out, root, progress)


def _copy_tree(src: Path, root: Path, progress: Progress) -> None:
    """Copy a new version's files (src) over the copy in root, leaving everything that's the person's own alone.
    The file that names the version goes last, and only when every other file is in: until then this copy still
    counts as the version it was, so the Updates tab offers the update again instead of calling a folder that is part
    old and part new "the newest version"."""
    if not (src / VERSION_FILE).exists():
        raise UpdateError("The download doesn't look like ReelForge; nothing was changed.")
    files = sorted((p for p in src.rglob("*") if p.is_file()), key=lambda p: p == src / VERSION_FILE)
    busy, changed, part_way = [], 0, root / PART_WAY
    for i, f in enumerate(files, 1):
        rel = f.relative_to(src).as_posix()
        progress("Copying the new files", i, len(files))
        dest = root / rel
        if kept(rel) or (rel in STARTERS and dest.exists()) or (rel == VERSION_FILE and busy):
            continue
        tmp = dest.with_name(dest.name + ".rf-new")
        try:
            if dest.exists() and dest.stat().st_size == f.stat().st_size and dest.read_bytes() == f.read_bytes():
                if os.name != "nt" and os.access(f, os.X_OK) and not os.access(dest, os.X_OK):
                    os.chmod(dest, 0o755)   # an earlier update left it unrunnable
                _drop(tmp)                  # what an earlier update that stopped at this file left beside it
                continue  # unchanged (this also leaves a running launcher alone)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, tmp)
            if os.name != "nt" and os.access(f, os.X_OK):
                os.chmod(tmp, 0o755)
            # Windows won't replace a file someone has open. Most let go within a moment, so wait for the first
            # one; once a file has stayed busy the update can't finish this time, and the rest aren't waited for.
            paths.replace(tmp, dest, wait=0 if busy else BUSY_WAIT)
            changed += 1
            if changed == 1:
                _note_part_way(part_way)
        except OSError:
            busy.append(rel)
            _drop(tmp)
    if busy:
        # New files an earlier attempt put in count too: updating again, as the message asks, can stop at the same
        # busy file without replacing anything, and "Nothing was changed" would then be said of a mixed folder.
        partial = bool(changed) or part_way.exists()
        names = f"{', '.join(busy[:3])}{'…' if len(busy) > 3 else ''}"
        raise UpdateError(
            f"{len(busy)} of the new files couldn't be put in place ({names}): another program has them open, or the "
            "disk is full. " + ("The other new files are in, and this copy still counts as the version you had. "
                                if partial else "Nothing was changed. ") +
            "Close other ReelForge windows, then update again to finish.", partial=partial)
    _drop(part_way)


def _note_part_way(part_way: Path) -> None:
    try:
        part_way.parent.mkdir(parents=True, exist_ok=True)
        part_way.write_text("An update has put some of the new version's files in this folder and not yet all of "
                            "them. Update again to finish (ReelForge's Updates tab).\n", encoding="utf-8")
    except OSError:
        pass


def _drop(tmp: Path) -> None:
    try:
        tmp.unlink(missing_ok=True)
    except OSError:
        pass


def _git_pull(root: Path, progress: Progress) -> None:
    progress("Pulling with git", 0, 0)
    git = shutil.which("git")
    if not git:
        raise UpdateError("This copy is a git clone, but git isn't installed: run git pull yourself.")
    diff = subprocess.run([git, "-C", str(root), "diff", "HEAD", "--name-only", "-z"],
                          capture_output=True, text=True, encoding="utf-8")
    changed = [p for p in diff.stdout.split("\0") if p]
    # Onboarding rewrites CLAUDE.md and AGENTS.md, which the kit ships as placeholders: they're set aside for the
    # pull and put back after it. Any other file you changed stops the update.
    yours = {p: (root / p).read_bytes() for p in changed if (kept(p) or p in STARTERS) and (root / p).is_file()}
    if diff.returncode != 0 or any(p not in yours for p in changed):
        raise UpdateError("You've changed files in this copy (git status lists them). Commit or stash them, "
                          "then update again.")
    if yours:
        subprocess.run([git, "-C", str(root), "checkout", "HEAD", "--", *yours], capture_output=True, text=True)
    try:
        res = subprocess.run([git, "-C", str(root), "pull", "--ff-only"], capture_output=True, text=True)
    finally:
        for p, data in yours.items():
            (root / p).write_bytes(data)
    if res.returncode != 0:
        msg = (res.stderr or res.stdout).strip().splitlines()
        raise UpdateError(f"git pull didn't work ({msg[-1] if msg else 'no message'}).")


def _stamp(root: Path) -> Path:
    """The copy of requirements.txt that .venv's packages were last installed from (setup and updates keep it)."""
    return root / ".venv" / "installed.txt"


def _needs_packages(root: Path, requirements_before: str) -> bool:
    """Whether the Python packages have to be installed: requirements.txt isn't the one .venv was installed from.
    "Did this update change the file?" isn't the question: an update that stopped after copying the new
    requirements.txt finds no change when it's run again, and used to skip the install for good. A copy with no
    .venv of its own (a git copy run with another Python) has nothing to compare with, and falls back to that."""
    if _stamp(root).exists():
        return _hash(_stamp(root)) != _hash(root / "requirements.txt")
    return _hash(root / "requirements.txt") != requirements_before


def _venv_python(root: Path) -> str:
    for p in (root / ".venv" / "Scripts" / "python.exe", root / ".venv" / "bin" / "python"):
        if p.exists():
            return str(p)
    return sys.executable


def _after(root: Path, requirements_before: str, progress: Progress, log: Callable[[str], None]) -> None:
    """New Python packages if needed, the window's (Bun and OpenTUI), then agent instructions refreshed without
    replacing settings or accounts from older onboarding answers."""
    py = _venv_python(root)
    installed = True
    if _needs_packages(root, requirements_before):
        progress("Installing new Python packages", 0, 0)
        res = subprocess.run([py, "-m", "pip", "install", "--disable-pip-version-check", "--quiet", "-r",
                              str(root / "requirements.txt")], capture_output=True, text=True, cwd=root)
        installed = res.returncode == 0
        if installed and _stamp(root).parent.exists():
            shutil.copy2(root / "requirements.txt", _stamp(root))
    progress("Getting the window's packages", 0, 0)
    res = subprocess.run([py, "-m", "studio.ui", "install"], capture_output=True, text=True, cwd=root,
                         env={**os.environ, "PYTHONUTF8": "1"}, stdin=subprocess.DEVNULL)
    if res.returncode != 0:
        log("Updated, but the window's packages didn't install; ReelForge tries again when it opens.")
    if not installed:   # said once the window's packages are in; the next start finds the Python ones out of step
        how = ("Close ReelForge and open it again: setup starts and installs them." if _stamp(root).exists() else
               f"Install them with: {py} -m pip install -r requirements.txt")   # no .venv of its own to repair
        raise UpdateError("The new version's files are in place, but its Python packages didn't install (are you "
                          f"online?). {how}", partial=True)
    if (root / "my-studio" / "studio.toml").exists() or any((root / "profiles").glob("*/my-studio/studio.toml")):
        progress("Rewriting CLAUDE.md and AGENTS.md", 0, 0)
        env = {k: v for k, v in os.environ.items() if k != "REELFORGE_PROFILE"}  # every profile's, not just this one's
        res = subprocess.run([py, "-m", "studio", "channel", "--regenerate", "--preserve-settings"], capture_output=True, text=True,
                             cwd=root, env={**env, "PYTHONUTF8": "1"})
        if res.returncode != 0:
            log("Updated, but rewriting CLAUDE.md / AGENTS.md failed; run: "
                "reelforge channel --regenerate --preserve-settings")


def _marker(root: Path) -> Path:
    return root / "dashboard" / "data" / "updated.json"


def _write_marker(root: Path, old: str, new: str) -> None:
    try:
        _marker(root).parent.mkdir(parents=True, exist_ok=True)
        _marker(root).write_text(json.dumps({"from": old, "to": new, "ts": time.time()}), encoding="utf-8")
    except OSError:
        pass


def take_marker(root: Path = ROOT) -> dict | None:
    """What the last update did ({from, to}), once: the restarted dashboard says "Updated to ..."."""
    try:
        data = json.loads(_marker(root).read_text(encoding="utf-8"))
        _marker(root).unlink()
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--into" in argv:
        return _move_in_main(argv[argv.index("--into") + 1:][:1])
    info = check()
    if info["error"]:
        print(info["error"])
        return 1
    if not info["newer"]:
        print(f"ReelForge {info['current']} is the newest version.")
        return 0
    print(f"ReelForge {info['latest']} is out (you have {info['current']}). {info['url']}")
    if "--apply" not in argv:
        print("Run: python -m studio.update --apply   (or press Update and restart on the Updates tab)")
        return 0
    try:
        print(apply(release_from(info), _show))
    except UpdateError as e:
        print(f"\nThe update didn't finish: {e}")
        return 1
    print("Start ReelForge again to use it.")
    return 0


def _show(what: str, done: int, total: int) -> None:
    pct = f" {100 * done / total:5.1f}%" if total else ""
    print(f"\r  {what}{pct}   ", end="", flush=True)


def _move_in_main(where: list[str]) -> int:
    if not where:
        print("Say which folder: python -m studio.update --into DIR")
        return 2
    try:
        summary = move_in(Path(where[0]), _show)
    except UpdateError as e:
        print(f"\nThe update didn't finish: {e}")
        return 1
    print(f"\n{summary}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
