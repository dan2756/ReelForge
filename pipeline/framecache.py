"""Which scene the pictures in build/frames and build/stills were rendered from.

Blender skips a frame that is already on disk, so after the scene changes an old frame looks exactly like a new one
and ends up in the video. The build step therefore writes a stamp for the scene it built (build/scene.json): the
episode.json keys the scene code read, that code, and the Blender that ran it. The render step copies the stamp next to
the frames it renders (build/frames/.scene), the stills step next to its stills, and the render and the video step
compare the two instead of trusting whatever is in the folder.

Also here: finding the frames a stopped render left empty or cut off, the frames still missing, and turning
"12-15.5,31-33" (seconds) into frame numbers.

Standard library only: build_scene.py imports this inside Blender's own Python.
"""
import hashlib
import json
import os
import re
from pathlib import Path

SCENE = "scene.json"    # in build/: the stamp of the scene that build/scene.blend holds
STAMP = ".scene"        # in build/frames and build/stills: the stamp of the scene those pictures show


class Recorder(dict):
    """The episode's spec, remembering which top-level keys were read from it.

    The scene code gets this instead of the plain dict, so the stamp covers exactly what the picture depends on: a
    new story line or sound cue leaves the frames valid, a change to anything the world or the effect reads does not.
    Whatever walks the whole spec (a loop, a copy) counts as having read all of it."""

    def __init__(self, spec):
        super().__init__(spec)
        self.read = set()

    def __getitem__(self, key):
        self.read.add(key)
        return super().__getitem__(key)

    def get(self, key, default=None):
        self.read.add(key)
        return super().get(key, default)

    def __contains__(self, key):
        self.read.add(key)
        return super().__contains__(key)

    def setdefault(self, key, default=None):
        self.read.add(key)
        return super().setdefault(key, default)

    def pop(self, key, *default):
        self.read.add(key)
        return super().pop(key, *default)

    def _all(self):
        self.read.update(super().keys())

    def __iter__(self):
        self._all()
        return super().__iter__()

    def keys(self):
        self._all()
        return super().keys()

    def values(self):
        self._all()
        return super().values()

    def items(self):
        self._all()
        return super().items()

    def copy(self):
        self._all()
        return dict(super().items())


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _spec_digests(spec, keys):
    """{key: digest of its value, or None when episode.json has no such key} for the keys the scene read."""
    return {k: _digest(dict.__getitem__(spec, k)) if dict.__contains__(spec, k) else None for k in sorted(keys)}


def _code_digests(root, names):
    out = {}
    for name in sorted(names):
        try:
            out[name] = hashlib.sha256((Path(root) / name).read_bytes()).hexdigest()
        except OSError:
            out[name] = None
    return out


def scene_stamp(spec, keys, root, sources, blender, engine=""):
    """The stamp of a scene just built: `keys` of `spec` (what the scene code read), the `sources` (code files under
    `root`, pipeline/blender) that built it, Blender's version and the render engine. Its "fingerprint" is one hash
    of all of that; the parts are kept so a later mismatch can say what changed."""
    root = Path(root)
    names = {Path(s).resolve().relative_to(root.resolve()).as_posix() for s in sources}
    stamp = {"spec": _spec_digests(spec, keys), "code": _code_digests(root, names),
             "blender": str(blender), "engine": str(engine)}
    stamp["fingerprint"] = _digest(stamp)
    return stamp


def read_stamp(path):
    """The stamp in a file (build/scene.json, build/frames/.scene), or None if there is none to read."""
    try:
        stamp = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return stamp if isinstance(stamp, dict) and stamp.get("fingerprint") else None


def write_stamp(path, stamp):
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(stamp, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def changed(old, new):
    """What differs between two stamps, in words: ['episode.json "effect"', 'effects/grow.py', ...]."""
    out = []
    for key in sorted(set(old.get("spec", {})) | set(new.get("spec", {}))):
        if old.get("spec", {}).get(key) != new.get("spec", {}).get(key):
            out.append(f'episode.json "{key}"')
    for name in sorted(set(old.get("code", {})) | set(new.get("code", {}))):
        if old.get("code", {}).get(name) != new.get("code", {}).get(name):
            out.append(f"pipeline/blender/{name}")
    if old.get("blender") != new.get("blender"):
        out.append(f"Blender ({old.get('blender')} then, {new.get('blender')} now)")
    if old.get("engine") != new.get("engine"):
        out.append(f"the renderer ({old.get('engine')} then, {new.get('engine')} now)")
    return out


def outdated(stamp, spec, root):
    """What changed on disk since the scene with this stamp was built: the episode.json keys it read and the code
    that built it. An empty list means build/scene.blend is still the scene episode.json describes."""
    now = dict(stamp, spec=_spec_digests(spec, stamp.get("spec", {})),
               code=_code_digests(root, stamp.get("code", {})))
    return changed(stamp, now)


def frames_state(build, folder="frames", pattern="f_*.jpg"):
    """Where the pictures in build/<folder> stand against the scene in build/scene.blend: (state, what changed).

    none       no pictures there
    match      rendered from this scene
    changed    rendered from an older scene (the second value says what changed since)
    unstamped  no record of the scene they show, and the scene has been built since stamps existed: can't tell
    unbuilt    they have a stamp but the scene has none: the last build did not finish
    untracked  neither has a stamp (built by a ReelForge from before stamps): nothing to compare
    """
    build = Path(build)
    if not any((build / folder).glob(pattern)):
        return "none", []
    scene, theirs = read_stamp(build / SCENE), read_stamp(build / folder / STAMP)
    if scene is None:
        return ("unbuilt" if theirs else "untracked"), []
    if theirs is None:
        return "unstamped", []
    if theirs["fingerprint"] == scene["fingerprint"]:
        return "match", []
    return "changed", changed(theirs, scene)


def why(state, what):
    """The sentence half that follows "the frames in build/frames ..." for a state that can't be trusted."""
    if state == "changed":
        return "were rendered from an older scene (changed since: " + (", ".join(what) or "the scene") + ")"
    return "have no record of the scene they were rendered from, and the scene has been built again since"


# ---------------------------------------------------------------- the files

def number(path):
    """The frame number in f_0412.jpg or s_0631.jpg, or None."""
    m = re.fullmatch(r"[a-z]_(\d+)", Path(path).stem)
    return int(m.group(1)) if m else None


def _whole(path):
    """A JPEG that was written to its end. A render that was killed leaves the frame it was on empty (Blender makes
    the file first) or cut off; picture data never holds the end marker FF D9, so its last bytes tell."""
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - 16))
            return size > 4 and b"\xff\xd9" in fh.read()
    except OSError:
        return False


def sweep_empty(folder, pattern="f_*.jpg"):
    """Delete the empty and cut-off pictures in a folder and return their paths. Blender skips any frame that is on
    disk, so one of these would never be rendered again."""
    gone = []
    for f in sorted(Path(folder).glob(pattern)):
        if not _whole(f):
            try:
                f.unlink()
                gone.append(f)
            except OSError:
                pass        # still being written by a render that is running: leave it to that render
    return gone


def present(folder, pattern="f_*.jpg"):
    """The numbers of the pictures on disk that have something in them."""
    out = set()
    for f in Path(folder).glob(pattern):
        n = number(f)
        try:
            if n is not None and f.stat().st_size > 0:
                out.add(n)
        except OSError:
            pass
    return out


def ranges(numbers):
    """[(first, last), ...] for a set of frame numbers."""
    out = []
    for n in sorted(numbers):
        if out and n == out[-1][1] + 1:
            out[-1] = (out[-1][0], n)
        else:
            out.append((n, n))
    return out


def missing(folder, total):
    """The frames 1..total that aren't on disk, as [(first, last), ...]."""
    return ranges(set(range(1, total + 1)) - present(folder))


def count(spans):
    return sum(b - a + 1 for a, b in spans)


def frames_for(text, fps, total):
    """Frame ranges for seconds like "12-15.5,31-33" (a single number is that second's one frame), the way the
    stills step numbers them: the frame shown at second s is round(s * fps) + 1. Raises ValueError with a sentence
    for anything that isn't inside the render."""
    spans = []
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            a, b = (float(x) for x in part.split("-", 1)) if "-" in part else (float(part), float(part))
        except ValueError:
            raise ValueError(f"{part!r} isn't a range of seconds: write them like 12-15.5,31-33") from None
        if b < a:
            raise ValueError(f"{part!r} ends before it starts")
        first, last = int(round(a * fps)) + 1, int(round(b * fps)) + 1
        if first > total:
            raise ValueError(f"{part!r} starts after the last rendered frame ({(total - 1) / fps:g} s)")
        spans.append((first, min(last, total)))
    if not spans:
        raise ValueError("no seconds given: write them like 12-15.5,31-33")
    return ranges({n for a, b in spans for n in range(a, b + 1)})


def delete(folder, spans):
    """Delete the frames in these ranges; returns how many files went."""
    gone = 0
    for f in Path(folder).glob("f_*.jpg"):
        n = number(f)
        if n is not None and any(a <= n <= b for a, b in spans):
            f.unlink()
            gone += 1
    return gone


def describe(spans, fps, most=6):
    """'frames 361-466 (12.0-15.5 s), 931 (31.0 s)' for a list of ranges; long lists are cut short."""
    parts = []
    for a, b in spans[:most]:
        if a == b:
            parts.append(f"{a} ({(a - 1) / fps:.1f} s)")
        else:
            parts.append(f"{a}-{b} ({(a - 1) / fps:.1f}-{(b - 1) / fps:.1f} s)")
    more = f" and {len(spans) - most} more ranges" if len(spans) > most else ""
    return ("frame " if count(spans) == 1 else "frames ") + ", ".join(parts) + more


def progress(done, total, made, seconds):
    """The render's progress line, from the files on disk: `made` of the `done` frames appeared in the last
    `seconds`."""
    if made <= 0:
        return f"frames {done}/{total}, the first one is still rendering"
    each = seconds / made
    left = (total - done) * each
    eta = f"about {left / 60:.0f} min left" if left >= 90 else f"about {max(left, 0):.0f} s left"
    return f"frames {done}/{total}, {each:.1f} s a frame, {eta}"
