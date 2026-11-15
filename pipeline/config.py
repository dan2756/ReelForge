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


def caption_problems(spec):
    """What in episode.json breaks the channel's caption rules, as sentences ([] when nothing does). The caption step
    stops on these, and the lint asks them before the render."""
    tags = _settings.tag_list(spec.get("hashtags", []))
    n, first = MAX_HASHTAGS, TIKTOK_FIRST_TAG
    hook = " ".join(spec["title"].split())
    problems = []
    if len(tags) > n:
        problems.append(f"{len(tags)} hashtags in episode.json, the channel's maximum is {n}")
    elif (n > 0 and "tiktok" in PLATFORMS and first and
            first.lower() not in (t.lower() for t in tags) and len(tags) >= n):
        problems.append(f"TikTok needs #{first} first and max {n} hashtags; drop one hashtag from episode.json")
    if "#" in hook:
        problems.append("put hashtags in episode.json 'hashtags', not in the title")
    if len(hook) > 280:
        problems.append(f"the title is {len(hook)} characters, X allows 280")
    return problems


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


def _faces(fonts):
    """(serif, italic, bold, mono, mono bold) with any face this machine doesn't have replaced by the regular one
    beside it. Debian's fonts-dejavu-core has the serif without its italic, for one; find_fonts then answers with a
    path from another system, and the story lines would stop the video step instead of being drawn upright."""
    serif, italic, bold, mono, mono_bold = fonts
    return (serif, italic if Path(italic).is_file() else serif, bold if Path(bold).is_file() else serif,
            mono, mono_bold if Path(mono_bold).is_file() else mono)


# Typography: Georgia (serif) and Consolas (mono) on Windows; the closest fonts elsewhere (studio/tools.py).
FONT_SERIF, FONT_SERIF_ITALIC, FONT_SERIF_BOLD, FONT_MONO, FONT_MONO_BOLD = _faces(_tools.find_fonts())
