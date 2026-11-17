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
PART_WAY_EXIT = 4  # --into's exit code when it stopped with some of the new version in place (scripts/install.ps1)
# Never overwritten by an update: the person's own channel, videos, tools, data and generated instructions.
KEEP = ("my-studio/", "episodes/", "completed videos/", "research/", "tools/", ".venv/", "ui/node_modules/", ".git/",
        ".claude/", "assets/sfx/mixkit/", "assets/sfx/_cache/", "dashboard/data/", "dashboard/videos/",
        "dashboard/config.toml", "CLAUDE.md", "AGENTS.md", "profiles/")
# Shipped with the kit, and yours from then on: its own first lines invite you to edit it (the reasoning effort Codex
# is asked for). An update puts one in place only where there is none.
STARTERS = (".codex/config.toml",)
# The list of sounds is the kit's and yours at once: docs/SOUND.md has you add an entry for every sound you bring. An
# update puts the kit's new list in place with what you added to yours and changed in it (_with_your_sounds).
SOUNDS = "assets/sfx/manifest.json"
# The kit's own list as the last update (or first-time setup, studio/sounds.py) put it in place: an entry of yours that
# is the same as the one there is still the kit's, so the kit's new one replaces it, and anything else is yours.
KIT_SOUNDS = "assets/sfx/.kit-manifest.json"
# Where `channel --regenerate` puts what it has to say (a channel's settings file with a mistake in it) for the update
# to pass on: the rest of what it prints is the list of files it wrote. An older version's updater runs the new
# version's command with it, so the name stays as it is.
SAID = "REELFORGE_SAID"
VERSION_FILE = "studio/__init__.py"  # names this copy's version (read_version): an update puts it in place last
# There from the first new file an update puts in place until its last: this folder is part old and part new. What a
# later attempt says goes by it, since that attempt may stop without replacing a file itself.
PART_WAY = "dashboard/data/update-part-way"
# What an attempt that stopped at a busy file had to say about your sounds, for the attempt that finishes to say again:
# by then your list is the merged one, and the later attempt finds nothing to say of it itself.
UPDATE_NOTES = "dashboard/data/update-notes.json"
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
    notes: list[str] = []
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
            notes = _copy_release(path, root, work, progress)
        except UpdateError as e:   # whatever stopped this attempt, an earlier one's new files are still in the folder
            e.partial = e.partial or (root / PART_WAY).exists()
            raise
        finally:
            shutil.rmtree(work, ignore_errors=True)
    return _finish(root, before, old, read_version(root) or rel.version, progress, log, notes)


def move_in(root: Path, progress: Progress | None = None, log: Callable[[str], None] = print) -> str:
    """Put this copy's version in place in root, an older ReelForge, the way an update does: this copy (ROOT) is a
    download the install line has just unpacked. The install line runs it for a copy the old Windows installer made,
    whose Updates tab only knows how to run a newer installer, and ReelForge has none now. Raises UpdateError."""
    progress = progress or (lambda what, done, total: None)
    if not (root / "studio" / "__init__.py").exists():
        raise UpdateError(f"There's no ReelForge in {root}; nothing was changed.")
    before = _hash(root / "requirements.txt")
    old = read_version(root)
    notes = _copy_tree(ROOT, root, progress)
    return _finish(root, before, old, read_version(root) or KIT_VERSION, progress, log, notes)


def _finish(root: Path, requirements_before: str, old: str, new: str, progress: Progress,
            log: Callable[[str], None], notes: list[str] = ()) -> str:
    """Once the new files are in: the Python packages, the steps after them (_after), and the note the restarted
    window reads. Packages that didn't install are said last, when everything else is done. The window's packages
    are in then and your AI's notes are the new version's, so opening ReelForge again, which repairs the packages,
    is all that's left. (It used to stop before the notes, and nothing wrote them again after the repair.) notes:
    what else the update has to say (your sounds, and what _after says), at the end of the summary and in the note the
    window reads."""
    installed = _packages(root, requirements_before, progress)
    notes = [*notes, *_after(root, requirements_before, progress, log)]
    _write_marker(root, old, new, notes)
    _drop(root / UPDATE_NOTES)  # said now, in the summary and the note: an earlier attempt's need keeping no longer
    if not installed:
        how = ("Close ReelForge and open it again: setup starts and installs them." if _stamp(root).exists() else
               f"Install them with: {_venv_python(root)} -m pip install -r requirements.txt")   # no .venv to repair
        raise UpdateError("The new version's files are in place, but its Python packages didn't install (are you "
                          f"online?). {how}", partial=True)
    return " ".join([f"Updated ReelForge from {old} to {new}.", *notes])


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


def _copy_release(archive: Path, root: Path, work: Path, progress: Progress) -> list[str]:
    progress("Unpacking", 0, 0)
    out = work / "unpacked"
    try:
        if archive.name.endswith(".zip"):
            with zipfile.ZipFile(archive) as z:
                z.extractall(out)  # ZipFile drops absolute paths and ".." parts
                runnable(z, out)
        else:
            _untar(archive, out)
    except (zipfile.BadZipFile, tarfile.TarError, OSError) as e:
        raise UpdateError(f"The download couldn't be unpacked ({e}); try again.") from e
    tops = [p for p in out.iterdir() if p.is_dir()]
    return _copy_tree(tops[0] if len(tops) == 1 and not any(p.is_file() for p in out.iterdir()) else out, root,
                      progress)


def _json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError, RecursionError):
        return None


TABLES = {"sounds": ("sound", "sounds"), "_licenses": ("licence note", "licence notes")}


def _and(words: list[str]) -> str:
    return words[0] if len(words) == 1 else ", ".join(words[:-1]) + " and " + words[-1]


def _named(items: list[tuple[str, str]]) -> str:
    """('sounds', 'whoosh'), ('sounds', 'pop'), ('_licenses', 'pixabay') -> sounds "whoosh" and "pop", and licence
    note "pixabay"."""
    groups = []
    for kind, (one, many) in (*TABLES.items(), ("", ("entry", "entries"))):
        names = [f'"{name}"' for k, name in items if k == kind]
        if names:
            groups.append(f"{one if len(names) == 1 else many} {_and(names)}")
    return groups[0] if len(groups) == 1 else ", ".join(groups[:-1]) + ", and " + groups[-1]


def _merge_sounds(theirs: dict, mine: dict, was: dict | None, keep_unknown: bool,
                  on_disk: Callable[[str, object], bool] = lambda name, entry: False):
    """The new list with what is yours in mine, and which of yours by a name the new list also has were kept and which
    replaced, which of the kit's own the new list no longer has were taken out, and which of those stay all the same
    (a sound whose file is here, a licence note a sound left comes from: still the kit's, which the record of its list
    keeps, see _with_your_sounds). was: the kit's list as it was (None: no record of it), which tells your entries
    from the kit's own: one that is the same as there is the kit's, and its new one goes in, or none, where the new
    list no longer has it. With no record, keep_unknown says which goes in; it also keeps the kit's own that it took
    out, still named as taken out (nowhere to keep a copy of your list first). on_disk: whether a sound's file is on
    this computer (a sound the kit took out stays while it is)."""
    out, kept, replaced, dropped, stayed = dict(theirs), [], [], [], []
    for key, value in mine.items():
        if key in TABLES and not (isinstance(value, dict) and isinstance(out.get(key), dict)):
            if value != out.get(key):
                replaced.append(("", key))                   # not a list of entries, which every mix would stop on
            continue
        kind = key if key in TABLES else ""
        table = dict(out[key]) if kind else out
        before = (was.get(key) if kind else was) if was is not None else None
        before = before if isinstance(before, dict) else ({} if was is not None else None)
        for name, entry in (value.items() if kind else [(key, value)]):
            if name not in table:
                if before is not None and name in before and before[name] == entry:
                    # The kit's own, which it has taken out of its list since: a sound withdrawn from Mixkit, say,
                    # that setup would go on trying to download. While its file is here, nothing is downloaded and
                    # a cue sheet of yours that uses it goes on mixing it, so it stays; otherwise it goes, and is
                    # named (a licence note too, unless a sound left uses it: below), unless your list can't be
                    # copied first.
                    if kind == "sounds" and on_disk(name, entry):
                        table[name] = entry
                        stayed.append((kind, name))
                    elif kind:
                        dropped.append((kind, name))
                        if keep_unknown:
                            table[name] = entry
                    continue
                table[name] = entry                          # yours, or one the kit took out that you had changed
            elif table[name] == entry or (before is not None and name in before and before[name] == entry):
                continue                                     # the kit's own, as it put it there: its new one goes in
            elif before is not None or keep_unknown:
                table[name] = entry                          # yours, by a name the new list has too: yours stays
                kept.append((kind, name))
            else:
                replaced.append((kind, name))
        if kind:
            out[key] = table
    # A licence note the kit took out stays while a sound left in the list comes from that source (sound["source"]):
    # one kept for its file, or one of yours. It is where that sound's terms are written down; it goes with the last.
    sounds = out.get("sounds")
    used = {e.get("source") for e in sounds.values() if isinstance(e, dict) and isinstance(e.get("source"), str)} \
        if isinstance(sounds, dict) else set()
    for item in [(kind, name) for kind, name in dropped if kind == "_licenses" and name in used]:
        dropped.remove(item)
        stayed.append(item)
        out["_licenses"] = {**out["_licenses"], item[1]: mine["_licenses"][item[1]]}
    return out, kept, replaced, dropped, stayed


def _backup_sounds(yours: Path, root: Path) -> str:
    """A copy of your list of sounds in backups/, before an update puts entries of the new one in its place: the
    path to say, from the ReelForge folder."""
    folder = root / "backups"
    folder.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y-%m-%d-%H%M%S")
    dest, n = folder / f"sounds-before-update-{stamp}.json", 1
    while dest.exists():
        n += 1
        dest = folder / f"sounds-before-update-{stamp}-{n}.json"
    shutil.copy2(yours, dest)
    return f"backups/{dest.name}"


def _read_list(path: Path) -> tuple[object, str]:
    """A list of sounds as it reads, or None and why it doesn't read (in words for the update's summary)."""
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as e:
        return None, f"{e.msg} at line {e.lineno}, column {e.colno}"
    except (OSError, ValueError, RecursionError) as e:
        return None, str(e) or type(e).__name__
    return (data, "") if isinstance(data, dict) else (None, "it isn't a list of sounds like the kit's")


def _with_your_sounds(new: Path, yours: Path, root: Path) -> tuple[bytes | None, list[str], bool, object]:
    """The kit's new list of sounds (new) with what is yours in the one in place (yours), and what to say about it.
    Yours: a sound, a licence note or any other key the new list lacks, and one it has by the same name that you
    added or changed, which the kit's list as it was (KIT_SOUNDS) tells from the kit's own. Yours stays and is
    named: a cue sheet of yours that uses it goes on mixing your sound, and nothing is lost. With no record of the
    kit's list as it was (the first update that keeps one), the new list's entry goes in, after a copy of your list
    is kept in backups/, and the entries it replaced are named. The kit's own that the new list no longer has go too,
    after a copy, and are named, except a sound whose file is on this computer and a licence note that a sound left in
    the list comes from. None for the list: there is nothing of yours to carry over, or yours can't be read and a copy
    of it is in backups/, so the new file goes in as it is. Then whether yours stays as it is instead (it can't be
    read, and no copy of it could be kept). Last, the record of the kit's list to keep for the next update
    (_note_kit_sounds): None for the new list as it is, else the new list with the kit's own that stay in yours, or
    False for none this time."""
    theirs, was = _json(new), _json(root / KIT_SOUNDS)
    if not isinstance(theirs, dict):
        return None, [], False, None
    mine, why = _read_list(yours)
    if mine is None:
        # A comma after the last entry you added, say: every mix stops on it already, and the new list in its place
        # would take what you added with it, without a word. So a copy goes to backups/ first, and with nowhere to
        # keep one yours stays as it is.
        try:
            backup = _backup_sounds(yours, root)
        except OSError:
            return None, [f"Your {SOUNDS} can't be read ({why}), and ReelForge couldn't keep a copy of it in backups/, "
                          "so it's left as it was, and the new version's sounds aren't in it yet (the next update "
                          "brings them). Put the mistake right in a text editor: every mix stops on it until then."], \
                True, None
        return None, [f"Your {SOUNDS} couldn't be read ({why}), so the new version's list took its place. Your list "
                      f"as it was is in {backup}: copy your own entries from there into the new one."], False, None
    was = was if isinstance(was, dict) else None
    sfx = yours.parent

    def on_disk(name: str, entry) -> bool:
        """Whether a sound's file is here, as setup and the mixes look for it (studio/sounds.py missing())."""
        file = entry.get("file") if isinstance(entry, dict) else None
        try:
            return bool(isinstance(file, str) and file and (sfx / file).is_file()) or \
                (sfx / "_cache" / f"{name}.wav").is_file()
        except (OSError, ValueError):
            return False

    out, kept, replaced, dropped, stayed = _merge_sounds(theirs, mine, was, keep_unknown=False, on_disk=on_disk)
    notes, backup, record, held_out = [], "", None, []
    if replaced or dropped:
        try:
            backup = _backup_sounds(yours, root)
        except OSError:  # nowhere to keep a copy: they stay as they are, which may be the kit's old ones
            out, held, replaced, held_out, stayed = _merge_sounds(theirs, mine, was, keep_unknown=True,
                                                                  on_disk=on_disk)
            unsure = [k for k in held if k not in kept]
            dropped = []
            if unsure:
                one = len(unsure) == 1
                notes.append(f"Your {SOUNDS} keeps what it had for {_named(unsure)}, though the new version has its "
                             f"own by {'that name' if one else 'those names'}: ReelForge couldn't keep a copy of your "
                             "list in backups/ before replacing any.")
                # Not told from yours this time, so no record of the kit's list goes in either: kept now, the
                # entries held here would be the same as nothing in it and count as yours for good. The next
                # update asks again, and keeps a copy of your list then.
                record = False
            if held_out:
                one = len(held_out) == 1
                notes.append(f"Your {SOUNDS} keeps {_named(held_out)}, which the new version no longer has: ReelForge "
                             f"couldn't keep a copy of your list in backups/ before taking {'it' if one else 'them'} "
                             "out.")
    if record is None and (stayed or held_out):
        # The kit's own that stay in yours though the new list no longer has them (a sound whose file is here, the
        # licence note of a sound left, one held for want of a copy) are still the kit's in the record of its list:
        # the next update looks at each again, takes it out once nothing keeps it (with a copy of your list then),
        # and puts the kit's new one in if it brings that name back. Left out of the record, they'd be the same as
        # nothing in it, and count as yours for good: setup would go on trying to download a sound the kit withdrew.
        record = {k: dict(v) if isinstance(v, dict) else v for k, v in theirs.items()}
        for kind, name in [*stayed, *held_out]:
            record[kind] = {**(record.get(kind) if isinstance(record.get(kind), dict) else {}), name: mine[kind][name]}
    if kept:
        one = len(kept) == 1
        notes.append(f"Your own {_named(kept)} in {SOUNDS} {'is' if one else 'are'} kept: the new version has "
                     f"{'one' if one else 'some'} by {'that name' if one else 'those names'} too, which "
                     f"{'is' if one else 'are'} left out. To have both, give yours another name, in your cue sheets "
                     "too.")
    if replaced:
        one = len(replaced) == 1
        notes.append(f"The new version's own {_named(replaced)} took the place of what your {SOUNDS} had by "
                     f"{'that name' if one else 'those names'}. " +
                     (f"If any of it was yours, your list as it was is in {backup}." if backup else
                      "ReelForge couldn't keep a copy of your list in backups/ first."))
    if dropped:
        one = len(dropped) == 1
        # A cue sheet names sounds, never a licence note: a note that went needs nothing more said of it.
        gone = sum(kind == "sounds" for kind, _ in dropped)
        uses = ("it" if one else "them") if gone == len(dropped) else "that sound" if gone == 1 else "those sounds"
        needs = f": a cue sheet of yours that uses {uses} needs another" if gone else ""
        notes.append(f"The new version no longer has {_named(dropped)}, so {'it was' if one else 'they were'} taken "
                     f"out of your {SOUNDS} too{needs}. Your list as it was is in {backup}.")
    text = None if out == theirs else json.dumps(out, indent=2, ensure_ascii=False).encode("utf-8") + b"\n"
    return text, notes, False, record


def _note_kit_sounds(new: Path, root: Path, record=None) -> None:
    """The kit's list as it is put in place (or record, see _with_your_sounds; False: none this time), for the next
    update to tell your entries from the kit's own. Not kept (a full disk): the next update keeps a copy of yours in
    backups/ before it replaces any."""
    if record is False:
        return
    try:
        data = new.read_bytes() if record is None else \
            json.dumps(record, indent=2, ensure_ascii=False).encode("utf-8") + b"\n"
        record = root / KIT_SOUNDS
        if not record.exists() or record.read_bytes() != data:
            record.parent.mkdir(parents=True, exist_ok=True)
            tmp = record.with_name(record.name + ".rf-new")
            tmp.write_bytes(data)
            os.replace(tmp, record)
    except OSError:
        pass


def _copy_tree(src: Path, root: Path, progress: Progress) -> list[str]:
    """Copy a new version's files (src) over the copy in root, leaving everything that's the person's own alone.
    The file that names the version goes last, and only when every other file is in: until then this copy still
    counts as the version it was, so the Updates tab offers the update again instead of calling a folder that is part
    old and part new "the newest version". Returns what the update's summary says besides (_with_your_sounds)."""
    if not (src / VERSION_FILE).exists():
        raise UpdateError("The download doesn't look like ReelForge; nothing was changed.")
    files = sorted((p for p in src.rglob("*") if p.is_file()), key=lambda p: p == src / VERSION_FILE)
    busy, changed, part_way, notes = [], 0, root / PART_WAY, []
    earlier = _json(root / UPDATE_NOTES)       # an earlier attempt's, which stopped at a busy file
    earlier = [line for line in earlier if isinstance(line, str) and line] if isinstance(earlier, list) else []
    for i, f in enumerate(files, 1):
        rel = f.relative_to(src).as_posix()
        progress("Copying the new files", i, len(files))
        dest = root / rel
        # (KIT_SOUNDS is written from the new list once that is in, never copied: a copy that has been set up has one)
        if rel == KIT_SOUNDS or kept(rel) or (rel in STARTERS and dest.exists()) or (rel == VERSION_FILE and busy):
            continue
        tmp = dest.with_name(dest.name + ".rf-new")
        try:
            merged, said, as_is, record = (_with_your_sounds(f, dest, root) if rel == SOUNDS and dest.exists() else
                                           (None, [], False, None))
            if as_is:   # yours, which can't be read, and no copy of it could be kept: it stays, and the kit's list
                _drop(tmp)          # as it was stays the record of it
                notes += said
                continue
            if dest.exists() and (dest.read_bytes() == merged if merged is not None else
                                  dest.stat().st_size == f.stat().st_size and dest.read_bytes() == f.read_bytes()):
                if os.name != "nt" and os.access(f, os.X_OK) and not os.access(dest, os.X_OK):
                    os.chmod(dest, 0o755)   # an earlier update left it unrunnable
                _drop(tmp)                  # what an earlier update that stopped at this file left beside it
                if rel == SOUNDS:
                    _note_kit_sounds(f, root, record)
                    notes += said
                continue  # unchanged (this also leaves a running launcher alone)
            dest.parent.mkdir(parents=True, exist_ok=True)
            if merged is not None:
                tmp.write_bytes(merged)
            else:
                shutil.copy2(f, tmp)
            if os.name != "nt" and os.access(f, os.X_OK):
                os.chmod(tmp, 0o755)
            # Windows won't replace a file someone has open. Most let go within a moment, so wait for the first
            # one; once a file has stayed busy the update can't finish this time, and the rest aren't waited for.
            _replace(tmp, dest, wait=0 if busy else BUSY_WAIT)
            if rel == SOUNDS:   # (only once the list is in: a record of the new one beside your old one would take
                _note_kit_sounds(f, root, record)   # the old kit's entries in it for yours at the next attempt)
                notes += said
            changed += 1
            if changed == 1:
                _note_part_way(part_way)
        except OSError:
            busy.append(rel)
            _drop(tmp)
    notes = list(dict.fromkeys([*earlier, *notes]))     # each once: a later attempt may say the same again
    if busy:
        # New files an earlier attempt put in count too: updating again, as the message asks, can stop at the same
        # busy file without replacing anything, and "Nothing was changed" would then be said of a mixed folder.
        partial = bool(changed) or part_way.exists()
        names = f"{', '.join(busy[:3])}{'…' if len(busy) > 3 else ''}"
        _keep_notes(root, notes)    # said now, and again by the attempt that finishes
        raise UpdateError(
            f"{len(busy)} of the new files couldn't be put in place ({names}): another program has them open, or the "
            "disk is full. " + ("The other new files are in, and this copy still counts as the version you had. "
                                if partial else "Nothing was changed. ") +
            "Close other ReelForge windows, then update again to finish." + "".join(f" {n}" for n in notes),
            partial=partial)
    _drop(part_way)
    return notes


def _keep_notes(root: Path, notes: list[str]) -> None:
    """What this attempt had to say, for the one that finishes (UPDATE_NOTES). Nowhere to keep it: it was said once,
    in the message the attempt stopped with."""
    path = root / UPDATE_NOTES
    try:
        if notes:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(notes, ensure_ascii=False), encoding="utf-8")
        else:
            path.unlink(missing_ok=True)
    except OSError:
        pass


def _note_part_way(part_way: Path) -> None:
    try:
        part_way.parent.mkdir(parents=True, exist_ok=True)
        part_way.write_text("An update has put some of the new version's files in this folder and not yet all of "
                            "them. Update again to finish (ReelForge's Updates tab).\n", encoding="utf-8")
    except OSError:
        pass


# The two helpers below come from studio/paths.py and studio/tools.py, and the updater has to work beside those
# files as an older version had them: that version's own update, stopped part way, can leave this file new and
# them old, and this file is then what finishes the update.

def _replace(src: Path, dest: Path, wait: float) -> None:
    """paths.replace, which waits for a file another program has open. Beside a paths.py from before it had one, a
    plain replace."""
    if hasattr(paths, "replace"):
        paths.replace(src, dest, wait=wait)
    else:
        os.replace(src, dest)


def _untar(archive: Path, out: Path) -> None:
    """tools.untar: nothing lands outside the folder, on any Python. Beside a tools.py from before it had one, the
    way that version unpacked, with Python's own check."""
    if hasattr(tools, "untar"):
        tools.untar(archive, out)
    else:
        with tarfile.open(archive) as t:
            t.extractall(out, filter="data")


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


def _packages(root: Path, requirements_before: str, progress: Progress) -> bool:
    """Install the new version's Python packages when they aren't the ones installed. False when pip stopped: the
    next start then finds them out of step (bin/reelforge.py) and setup repairs them."""
    if not _needs_packages(root, requirements_before):
        return True
    progress("Installing new Python packages", 0, 0)
    res = subprocess.run([_venv_python(root), "-m", "pip", "install", "--disable-pip-version-check", "--quiet", "-r",
                          str(root / "requirements.txt")], capture_output=True, text=True, cwd=root)
    if res.returncode == 0 and _stamp(root).parent.exists():
        shutil.copy2(root / "requirements.txt", _stamp(root))
    return res.returncode == 0


def _after(root: Path, requirements_before: str, progress: Progress, log: Callable[[str], None]) -> list[str]:
    """New Python packages if needed, the window's (Bun and OpenTUI), then agent instructions refreshed without
    replacing settings or accounts from older onboarding answers. Returns what refreshing them had to say: a channel
    whose studio.toml has a mistake in it gets notes that go by its answers instead, and you're told which file."""
    py = _venv_python(root)
    said: list[str] = []
    # The Python packages are _packages' step now, which _finish runs just before this one and reports after it.
    progress("Getting the window's packages", 0, 0)
    res = subprocess.run([py, "-m", "studio.ui", "install"], capture_output=True, text=True, cwd=root,
                         env={**os.environ, "PYTHONUTF8": "1"}, stdin=subprocess.DEVNULL)
    if res.returncode != 0:
        log("Updated, but the window's packages didn't install; ReelForge tries again when it opens.")
    if (root / "my-studio" / "studio.toml").exists() or any((root / "profiles").glob("*/my-studio/studio.toml")):
        progress("Rewriting CLAUDE.md and AGENTS.md", 0, 0)
        # every profile's, not just this one's
        env = {k: v for k, v in os.environ.items() if k not in ("REELFORGE_PROFILE", SAID)}
        # Where it says what it has to say. Every new file is in place by now, so a temporary folder that can't be
        # written to, or a file there that can't be removed again (a scanner holding it on Windows), must not fail
        # the update: the notes are still written, and only what they had to say goes unsaid.
        out: Path | None = None
        try:
            fd, name = tempfile.mkstemp(prefix="reelforge-said-", suffix=".json")
            out = Path(name)
            os.close(fd)
        except OSError:
            pass
        try:
            res = subprocess.run([py, "-m", "studio", "channel", "--regenerate", "--preserve-settings"],
                                 capture_output=True, text=True, cwd=root,
                                 env={**env, "PYTHONUTF8": "1", **({SAID: str(out)} if out else {})})
            if res.returncode != 0:
                log("Updated, but rewriting CLAUDE.md / AGENTS.md failed; run: "
                    "reelforge channel --regenerate --preserve-settings")
            lines = _json(out) if out else None    # nothing there (a version that doesn't write it): nothing to say
            said = [line for line in lines if isinstance(line, str) and line] if isinstance(lines, list) else []
        finally:
            if out:
                _drop(out)
    return said


def _marker(root: Path) -> Path:
    return root / "dashboard" / "data" / "updated.json"


def _write_marker(root: Path, old: str, new: str, notes: list[str] = ()) -> None:
    try:
        _marker(root).parent.mkdir(parents=True, exist_ok=True)
        _marker(root).write_text(json.dumps({"from": old, "to": new, "ts": time.time(), "notes": list(notes)}),
                                 encoding="utf-8")
    except OSError:
        pass


def take_marker(root: Path = ROOT) -> dict | None:
    """What the last update did ({from, to, notes}), once: the restarted dashboard says "Updated to ..."."""
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
        # The install line goes by this: only after a 1 does it say that ReelForge still works as it was.
        return PART_WAY_EXIT if e.partial else 1
    except Exception as e:   # anywhere in the update, so some of the new files may be in place already
        print(f"\nThe update didn't finish: {type(e).__name__}: {e}")
        return PART_WAY_EXIT
    print(f"\n{summary}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
