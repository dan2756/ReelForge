"""Your studio's settings: my-studio/studio.toml (written by onboarding) laid over the defaults below.

The pipeline (captions, QA, loudness, video length) and the dashboard both read these, so one interview configures
everything. Edit studio.toml by hand if you like, or run onboarding again. Each profile (each channel) has its own:
these are the profile's this process works on (see studio/profiles.py).
"""

from __future__ import annotations

import copy
import json
import re
import tomllib
from pathlib import Path

from . import paths

PLATFORMS = ("tiktok", "youtube", "instagram", "x")
PLATFORM_NAMES = {"tiktok": "TikTok", "youtube": "YouTube Shorts", "instagram": "Instagram Reels", "x": "X"}
RECOMMENDED = ("tiktok", "youtube", "instagram")  # the three big short-video apps: onboarding ticks these to start
AIS = ("claude", "codex")
AI_NAMES = {"claude": "Claude Code", "codex": "Codex"}

# Loudness choices offered by onboarding (integrated LUFS). Sound-effects-only videos with heavy booms feel much
# louder than their LUFS number suggests: -15 LUFS ("normal" for music and speech) was reported as far too loud on
# exactly this pipeline's videos, and -25.5 LUFS was what fixed it.
LOUDNESS = {
    "quiet": (-25.5, "Quiet: -25.5 LUFS, tested on this kit's sound-effects-only videos; easy on the ears"),
    "medium": (-20.0, "Medium: -20 LUFS"),
    "loud": (-15.0, "Loud: -15 LUFS, about as loud as most uploads (booms will hit hard)"),
    "voice": (-16.0, "Voice: -16 LUFS, the usual level for videos with a voiceover (recommended for 2D)"),
}
ENGINES = {
    "2d": "2D animation with a voiceover and captions   · recommended",
    "blender": "3D scenes made in Blender (no voiceover)",
}

DEFAULTS: dict = {
    "studio": {"kit_version": "", "created": "", "updated": ""},
    "channel": {
        "name": "My Channel",
        "creator": "",                 # what the AI calls you
        "about": "",
        "audience": "",
        "tone": [],
        "language": "English",
    },
    "format": {
        "template": "doubling",
        "series": "Doubling",
        "premise": "",
        "twist": "",
        "custom": "",
        "references": [],
        "subjects": [],
        "avoid": "",
        "episode_label": "Ep. {n}",
    },
    "video": {
        "fps": 30,
        "min_seconds": 61.0,           # TikTok's Creator Rewards only pays for videos over 1 minute
        "max_seconds": 75.0,
        "hud": True,                   # a big on-screen counter
        "loop": True,                  # end on the first frame so the video loops seamlessly
        "cta": "FOLLOW FOR MORE",
        "style": "cinematic",
        "style_notes": "",
        "engine": "blender",           # "2d": code-drawn scenes + voiceover + captions; "blender": 3D scenes. Channels
                                       # set up before 3.0 have no engine and keep making 3D videos
        "voice": "af_heart",           # the voice of 2D videos (studio/voice.py)
        "voiceover": False,            # 3D videos tell the story in on-screen lines, no narration; 2D videos are voiced
    },
    "sound": {"loudness": "quiet", "target_lufs": -25.5},
    "captions": {
        "max_hashtags": 5,
        "tiktok_first_tag": "fyp",     # "" for none
        "hashtags_on_x": False,
        "title_max_chars": 80,
        "default_tags": ["fyp", "whatif", "simulation", "physics", "space"],
    },
    "platforms": {
        "enabled": list(PLATFORMS),
        "handles": {p: "" for p in PLATFORMS},
        "background_stats": True,      # an hourly job reads your stats while ReelForge is closed
    },
    "schedule": {"per_day": 2, "per_week": 14, "post_times": "8-11 am and 5-8 pm", "timezone": ""},
    "ai": {"assistant": ["claude"]},  # the AIs that make the videos: Claude Code, Codex or both
}


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in over.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def fix_it(path: Path) -> str:
    """How to put right a channel's studio.toml with a mistake in it: by hand, then its notes for your AI again from
    it, or the file again from the channel's answers. Both commands name the channel (-p: Profile.flag, 1 for the
    first, whose name can't be read from the file meanwhile, profiles/<folder> for another, which no other channel
    answers to), so the other channels' settings and notes are left as they are, and the second keeps a copy of the
    file first (generate.write_all)."""
    from . import profiles

    who = profiles.of(path).flag
    return (f"Fix it in a text editor, then run reelforge -p {who} channel --regenerate --preserve-settings so your "
            "AI's notes follow it. Or write it again from your answers, keeping a copy of it as it is, with: "
            f"reelforge -p {who} channel --regenerate")


def yes_no(value) -> bool | None:
    """A yes or no in any form a person types it (true, "yes", on, 1, False, "no", off, 0: the words the questions
    take), else None. A no in quotes is a word to TOML, and any word is a yes to bool(), so a hand edit of
    `background_stats = "false"` turned the hourly read of your stats on."""
    if isinstance(value, bool):
        return value
    if not isinstance(value, (str, int)):
        return None
    v = str(value).strip().strip("\"'").strip().lower()
    return True if v in ("y", "yes", "true", "1", "on") else False if v in ("n", "no", "false", "0", "off") else None


class SettingsError(ValueError):
    """A studio.toml that can't be used. The message names the file, says what's wrong in it and how to put it right,
    so whoever catches it can show it as it is."""

    def __init__(self, path: Path, reason: str):
        super().__init__(f"{path} can't be read: {reason}. {fix_it(path)}")
        self.path, self.reason = path, reason


def stats_key(key) -> bool:
    """A key that is background_stats once case, and any -, _, spaces or tabs in it, are set aside (Background-Stats,
    "background stats", backgroundstats, _background_stats, say), or its singular, background_stat: how a person may
    write the setting for the hourly read of their stats."""
    return isinstance(key, str) and re.sub(r"[-_ \t]", "", key.casefold()) in ("backgroundstats", "backgroundstat")


def _stray_stats(data, table: tuple[str, ...] = ()) -> tuple[tuple[str, ...], str] | None:
    """The first background_stats key (stats_key) in a parsed studio.toml anywhere but under [platforms] as
    background_stats: (the table it's in, the key as written), else None."""
    items = data.items() if isinstance(data, dict) else enumerate(data) if isinstance(data, list) else ()
    for key, value in items:
        here = table if isinstance(data, list) else (*table, str(key))
        if isinstance(data, dict) and stats_key(key) and (table, key) != (("platforms",), "background_stats"):
            return table, key
        found = _stray_stats(value, here)
        if found:
            return found
    return None


def load(path: Path | None = None) -> dict:
    """Settings as a plain dict: the defaults with studio.toml laid over them (missing file = defaults). The file
    invites hand edits, so one with a typo in it raises SettingsError, which says where."""
    path = path or paths.SETTINGS
    text = ""
    if path.exists():
        try:    # utf-8-sig: Notepad on older Windows saves a hand edit with a byte-order mark, which TOML refuses
            text = path.read_text(encoding="utf-8-sig")
        except UnicodeDecodeError as e:
            raise SettingsError(path, str(e)) from None
    return parse(text, path)


def parse(text: str, path: Path | None = None) -> dict:
    """load for a studio.toml's text (path: the file it's for, which a SettingsError names)."""
    path = path or paths.SETTINGS
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise SettingsError(path, str(e)) from None
    # The hourly read uses your log-ins, so a background_stats line it wouldn't go by (added at the end of the file,
    # where it's under the last table, or spelled Background-Stats) is a mistake that says where it belongs, never left
    # out without a word while the yes under [platforms], or the default yes, turns the read on. Meanwhile
    # generate.background_stats goes by every such line, as it does with any mistake in the file.
    stray = _stray_stats(data)
    if stray:
        table, key = stray
        spelled = "" if key == "background_stats" else ", written background_stats"
        if table == ("platforms",):
            raise SettingsError(path, f"{key} under [platforms] should be written background_stats")
        where = f"under [{'.'.join(table)}]" if table else "at the top of the file"
        raise SettingsError(path, f"{key} {where} belongs under [platforms]{spelled}")
    s = _merge(DEFAULTS, data)
    # `captions = 3` parses, and would stop whoever reads a key of it, somewhere far from this file
    for name in (*DEFAULTS, "platforms.handles"):
        table = s
        for part in name.split("."):
            table = table[part]
        if not isinstance(table, dict):
            raise SettingsError(path, f"[{name}] should be a table of settings, not one value")
    if not isinstance(s["platforms"]["enabled"], list):
        raise SettingsError(path, 'enabled under [platforms] should be a list, like ["tiktok", "youtube"]')
    s["platforms"]["enabled"] = [p for p in s["platforms"]["enabled"] if p in PLATFORMS] or list(PLATFORMS)
    # the hourly read uses your log-ins: a yes or no typed as a word is read as meant, anything else is a mistake that
    # says where (and generate.background_stats then goes by the line as written, where only a yes is a yes)
    stats = yes_no(s["platforms"]["background_stats"])
    if stats is None:
        raise SettingsError(path, "background_stats under [platforms] should be true or false")
    s["platforms"]["background_stats"] = stats
    try:
        s["captions"]["max_hashtags"] = max(0, int(s["captions"]["max_hashtags"]))
    except (TypeError, ValueError):
        raise SettingsError(path, "max_hashtags under [captions] should be a whole number") from None
    s["captions"]["tiktok_first_tag"] = str(s["captions"]["tiktok_first_tag"]).lstrip("#").strip()
    s["ai"]["assistant"] = assistants(s["ai"]["assistant"])
    return s


def assistants(value) -> list[str]:
    """The AIs that make a channel's videos, from studio.toml's [ai] assistant: a list of claude and codex, one or both,
    in that order. Versions before 2.2.0 wrote one word, "claude", "codex" or "both"; anything else is Claude Code."""
    words = [value] if isinstance(value, str) else list(value) if isinstance(value, (list, tuple)) else []
    return [a for a in AIS if a in words or "both" in words] or ["claude"]


def ai_names(ais, joiner: str = " or ") -> str:
    """'Claude Code', 'Codex' or 'Claude Code or Codex' (joiner between the two)."""
    return joiner.join(AI_NAMES[a] for a in assistants(ais))


def onboarded(path: Path | None = None) -> bool:
    return (path or paths.SETTINGS).exists()


# ---------------------------------------------------------------- caption rules (shared by the pipeline and the dashboard)

_HASHTAG = re.compile(r"(?<![\w&])#\w+")


def tag_list(tags) -> list[str]:
    """'#a b #A #c' or ['a', '#b'] -> ['a', 'b', 'c']: hashtags without '#', in order, repeats dropped."""
    words = tags if isinstance(tags, (list, tuple)) else str(tags).replace(",", " ").split()
    out, seen = [], set()
    for w in words:
        t = str(w).strip().lstrip("#")
        if t and t.lower() not in seen:
            seen.add(t.lower())
            out.append(t)
    return out


def platform_tags(platform: str, tags, s: dict) -> list[str]:
    """The hashtags a platform gets: at most max_hashtags; TikTok's first one forced; none on X unless allowed."""
    c = s["captions"]
    n = c["max_hashtags"]
    if platform == "x" and not c["hashtags_on_x"]:
        return []
    out = tag_list(tags)
    first = c["tiktok_first_tag"]
    if platform == "tiktok" and first:
        out = [first] + [t for t in out if t.lower() != first.lower()]
    return out[:n]


def strip_hashtags(text: str) -> str:
    return " ".join(_HASHTAG.sub(" ", text).split())


def post_texts(title: str, tags, s: dict) -> dict[str, str]:
    """Every enabled platform's post, in the format the channel uses:
    TikTok and Instagram: the hook line + hashtags. YouTube: the hook is the title, the hashtags the description
    (written as 'title, blank line, hashtags'). X: the hook (+ hashtags only if allowed)."""
    hook = strip_hashtags(" ".join(title.split()))
    tags = tag_list([*_HASHTAG.findall(title), *tag_list(tags)])
    out = {}
    for p in s["platforms"]["enabled"]:
        line = " ".join("#" + t for t in platform_tags(p, tags, s))
        if p == "youtube":
            out[p] = f"{hook}\n\n{line}\n" if line else f"{hook}\n"
        else:
            out[p] = f"{hook} {line}".strip() + "\n"
    return out


# ---------------------------------------------------------------- writing studio.toml (no extra dependency)

def _toml_value(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(_toml_value(x) for x in v) + "]"
    return json.dumps(str(v), ensure_ascii=False)   # a JSON string is a valid TOML basic string


def dump_toml(data: dict, comments: dict | None = None) -> str:
    """dict -> TOML text. Nested dicts become [tables]; `comments` maps 'table' or 'table.key' to a comment."""
    comments = comments or {}
    lines: list[str] = []

    def table(prefix: str, d: dict):
        scalars = {k: v for k, v in d.items() if not isinstance(v, dict)}
        subs = {k: v for k, v in d.items() if isinstance(v, dict)}
        if prefix and scalars:
            if lines:
                lines.append("")
            if prefix in comments:
                lines.extend("# " + c for c in comments[prefix].splitlines())
            lines.append(f"[{prefix}]")
        for k, v in scalars.items():
            note = comments.get(f"{prefix}.{k}" if prefix else k)
            lines.append(f"{k} = {_toml_value(v)}" + (f"   # {note}" if note else ""))
        for k, v in subs.items():
            table(f"{prefix}.{k}" if prefix else k, v)

    table("", data)
    return "\n".join(lines) + "\n"


def engine(s: dict) -> str:
    """A channel's video type: "2d" or "blender"."""
    return "2d" if str(s.get("video", {}).get("engine", "blender")).lower() == "2d" else "blender"


def engines_in_use() -> set[str]:
    """The video types the channels in this folder make. Before any channel is set up: 2D, the default for new ones.
    A channel whose studio.toml has a mistake in it counts by its answers, as its notes are written meanwhile; if
    those can't be read either its type isn't known, and it asks for neither the voice nor Blender. (It was left out,
    so a folder whose one channel makes 3D videos counted as 2D: every start opened setup, asking for the voice.)"""
    from . import profiles

    out, unknown = set(), False
    for p in profiles.every():
        if p.onboarded:
            try:
                out.add(engine(load(p.settings)))
            except (OSError, ValueError):
                from . import generate      # (it imports this module)

                try:
                    answered = generate._answered(p)
                except Exception:           # answers that read as JSON and are no answers: its type isn't known
                    answered = None
                if answered:
                    out.add(engine(answered))
                else:
                    unknown = True
    return out or (set() if unknown else {"2d"})
