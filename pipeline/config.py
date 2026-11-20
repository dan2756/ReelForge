"""Paths, tools and the channel's rules for the post-production scripts (run with the kit's .venv Python).

The rules (video length, loudness, caption format, platforms) come from my-studio/studio.toml, which onboarding
writes; without it the defaults in studio/settings.py apply. They're the rules of the channel the episode belongs to:
an episode in another profile's folder (profiles/<name>/episodes/<id>, see studio/profiles.py) follows that
channel's settings, and ships to its own completed videos/.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PIPELINE = ROOT / "pipeline"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from studio import profiles as _profiles  # noqa: E402


def _episode_profile():
    """The profile of the episode this script was given (every pipeline script takes the episode's folder), or None."""
    for arg in sys.argv[1:]:
        if arg.startswith("-"):
            continue
        try:
            if (Path(arg) / "episode.json").is_file():
                return _profiles.of(arg)
        except OSError:
            continue
    return None


_here = _episode_profile()
if _here is not None:
    _profiles.use(_here)  # the scripts this one starts (the mixers, compose, QA) follow it too

from studio import settings as _settings  # noqa: E402
from studio import tools as _tools  # noqa: E402

PROFILE = _profiles.current()
SETTINGS = _settings.load(PROFILE.settings)
EPISODES = PROFILE.episodes
COMPLETED = PROFILE.completed   # one subfolder per finished video: mp4 + cover + captions
CHANNEL_NAME = SETTINGS["channel"]["name"]
PLATFORMS = SETTINGS["platforms"]["enabled"]

WIDTH, HEIGHT = 1080, 1920
SAMPLE_RATE = 48000
MIN_SECONDS = float(SETTINGS["video"]["min_seconds"])
MAX_SECONDS = float(SETTINGS["video"]["max_seconds"])

# Master loudness (integrated LUFS). post/loudness.py masters every mix to it and qa.py enforces it.
# Sound-effects-only videos feel louder than their number: -15 LUFS was reported as far too loud on this
# pipeline's videos and -25.5 LUFS fixed it, so that is the default ("quiet" in onboarding).
TARGET_LUFS = float(SETTINGS["sound"]["target_lufs"])
MAX_LUFS = TARGET_LUFS + 2.0                    # integrated: FAIL above this
MAX_SHORT_TERM_LUFS = TARGET_LUFS + 8.0         # loudest 3 s window (the climax): FAIL above this
MAX_TRUE_PEAK = min(-1.0, TARGET_LUFS + 19.5)   # dBTP: FAIL above this (-6 dBTP at the -25.5 default)

# Caption rules (checked by the caption step and by qa.py)
MAX_HASHTAGS = SETTINGS["captions"]["max_hashtags"]
TIKTOK_FIRST_TAG = SETTINGS["captions"]["tiktok_first_tag"]
HASHTAGS_ON_X = bool(SETTINGS["captions"]["hashtags_on_x"])
TITLE_MAX_CHARS = int(SETTINGS["captions"]["title_max_chars"])


def _missing(name, hint):
    raise FileNotFoundError(f"{name} not found. {hint}")


BLENDER = _tools.find_blender() or ""
FFMPEG = _tools.find_ffmpeg() or ""


def require_blender():
    if not BLENDER:
        _missing("Blender 4.5", "Run setup again (it can download it), or set STUDIO_BLENDER to blender's path.")
    return BLENDER


def require_ffmpeg():
    if not FFMPEG:
        _missing("ffmpeg", "Run setup again (it can download it), or set STUDIO_FFMPEG to ffmpeg's path.")
    return FFMPEG


# Typography: Georgia (serif) and Consolas (mono) on Windows; the closest fonts elsewhere (studio/tools.py).
FONT_SERIF, FONT_SERIF_ITALIC, FONT_SERIF_BOLD, FONT_MONO, FONT_MONO_BOLD = _tools.find_fonts()


# ---------------------------------------------------------------- the episode spec

def engine(spec):
    """The engine: "2d" (code-drawn scenes with a voiceover and captions) or "blender" (3D scenes): the spec's "engine"."""
    return "2d" if str(spec.get("engine", "blender")).lower() in ("2d", "twod", "canvas") else "blender"


def load_spec(ep_dir):
    """episode.json, with a 2D episode's "duration": "auto" filled from its voiceover (build/voice/timeline.json:
    the voice ends, plus a short tail) and render_until defaulting to the whole video. None while not voiced yet."""
    import json

    ep_dir = Path(ep_dir)
    spec = json.loads((ep_dir / "episode.json").read_text(encoding="utf-8"))
    if engine(spec) == "2d":
        tl = ep_dir / "build" / "voice" / "timeline.json"
        if spec.get("duration") in (None, "auto"):
            spec["duration"] = None
            if tl.exists():
                spec["duration"] = float(json.loads(tl.read_text(encoding="utf-8"))["duration"])
        if spec.get("duration") is not None:
            spec.setdefault("render_until", spec["duration"])
            dur = spec["duration"]
            beats = {}
            if tl.exists():
                beats = {b["id"]: b for b in json.loads(tl.read_text(encoding="utf-8"))["beats"] if b.get("id")}

            def when(v, what):
                """A time: seconds, a beat's id (when it starts; "id:end" for when it ends), or negative seconds from
                the end of the video."""
                if isinstance(v, str):
                    bid, _, edge = v.partition(":")
                    if bid not in beats:
                        raise SystemExit(f"{what}: no beat with \"id\": \"{bid}\" in the script")
                    return beats[bid]["end" if edge == "end" else "start"]
                return dur + v if v < 0 else v

            audio = spec.get("audio") or {}
            for h in audio.pop("cue_hits", []):         # sound hits on the script's beats: {"at": "d10", ...}
                h = dict(h)
                h["at"] = round(when(h["at"], "audio.cue_hits") + float(h.pop("offset", 0.0)), 3)
                audio.setdefault("hits", []).append(h)
            for key in ("cta", "title_card"):
                if spec.get(key):
                    spec[key] = {**spec[key], **{k: when(spec[key][k], key) for k in ("start", "end")
                                                 if k in spec[key]}}
            if spec.get("cover") and "time" in spec["cover"]:
                spec["cover"] = {**spec["cover"], "time": when(spec["cover"]["time"], "cover")}
    return spec
