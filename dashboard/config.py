"""Paths, config.toml and the local login store (which platforms you've logged in to in ReelForge's browser).

Each profile (each channel, see studio/profiles.py) has its own settings and data: HOME is the profile's dashboard
folder, with its config.toml (CONFIG_PATH) and data/ (DATA_DIR: DB_PATH, the stats, and TOKENS_PATH, its log-ins).
They're the profile's this process works on, looked up each time; the first profile's are this folder's own, as
they always were."""

from __future__ import annotations

import codecs
import json
import os
import re
import shutil
import threading
import tomllib
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent     # the dashboard's code, and the first profile's settings, data/, videos/

PLATFORM_KEYS = ("tiktok", "youtube", "instagram", "x")

_tokens_lock = threading.Lock()


def profile():
    """The profile this process works on (studio/profiles.py), or None for a dashboard run outside a ReelForge
    folder."""
    try:
        from studio import profiles
    except ImportError:
        return None
    return profiles.current()


def _set(name: str) -> Path | None:
    """A path set on this module by hand (the tests point them at scratch folders)."""
    return globals().get(name)


def home() -> Path:
    """The profile's dashboard folder: its config.toml and data/, and where relative paths in config.toml start."""
    if _set("HOME"):
        return _set("HOME")
    p = profile()
    return APP_DIR if p is None or p.main else p.dashboard


def config_path() -> Path:
    return _set("CONFIG_PATH") or home() / "config.toml"


def data_dir() -> Path:
    return _set("DATA_DIR") or home() / "data"


def db_path() -> Path:
    return _set("DB_PATH") or data_dir() / "stats.db"


def tokens_path() -> Path:
    return _set("TOKENS_PATH") or data_dir() / "tokens.json"


_PATHS = {"HOME": home, "CONFIG_PATH": config_path, "DATA_DIR": data_dir, "DB_PATH": db_path,
          "TOKENS_PATH": tokens_path}


def __getattr__(name: str):
    if name in _PATHS:
        return _PATHS[name]()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


class ConfigError(Exception):
    """config.toml can't be read; the message says where."""


def read_text(path: Path) -> str:
    """config.toml's text, however a Windows tool saved it: plain UTF-8, UTF-8 with a byte-order mark (older Notepad,
    PowerShell's Set-Content -Encoding UTF8) or UTF-16 with one (PowerShell's > and Out-File). One of the last two is
    saved again as plain UTF-8, the same text, so everything else that reads the file (first-time setup, the
    profile menu, an older ReelForge) can read it too."""
    raw = path.read_bytes()
    marked = "utf-8-sig" if raw.startswith(codecs.BOM_UTF8) else \
        "utf-16" if raw.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)) else None
    try:
        text = raw.decode(marked or "utf-8")
    except UnicodeDecodeError as e:
        raise ConfigError(f"{path.name} isn't saved as UTF-8 text, so ReelForge can't read it. Open it in a text "
                          "editor and save it again as UTF-8 (in Notepad: Save as, then Encoding: UTF-8).") from e
    if marked:
        try:
            tmp = path.with_suffix(".utf8.tmp")
            tmp.write_bytes(text.encode("utf-8"))
            tmp.replace(path)
        except OSError:
            pass  # a file ReelForge can't write is still read
    return text


def load_config() -> dict:
    path = config_path()
    if not path.exists():
        example = APP_DIR / "config.example.toml"
        # Its folder is made only when there is an example to put in it. With none, the ReelForge folder was moved or
        # removed after this process started (the hourly stats read, caught as it starts), and making the folder
        # would put a stub of the old one back. The copy then fails as it did before, with nothing made.
        if example.is_file():
            path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(example, path)
    text = read_text(path)
    try:
        cfg = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        # A Windows path in double quotes ("C:\Users\you") is the usual one: \U starts a character code in TOML.
        at, lines = re.search(r"at line (\d+)", str(e)), text.splitlines()
        line = lines[int(at.group(1)) - 1] if at and 0 < int(at.group(1)) <= len(lines) else ""
        hint = (' Write a Windows folder with forward slashes, like "C:/Users/you/Videos".'
                if "\\" in line.split("#")[0] else "")
        raise ConfigError(f"{path.name} has a typo ({e}).{hint} Fix it in a text editor, or delete the file "
                          "and ReelForge makes a fresh one from config.example.toml.") from e
    for key in PLATFORM_KEYS:
        if not isinstance(cfg.get(key), dict):
            cfg[key] = {}
    # Two values that aren't of the kind asked for are read the way ReelForge 2.2.1 read them, which had no check
    # here: a file it opened with must not stop the window, and the hourly read with it, after an update.
    if isinstance(cfg.get("refresh_minutes"), bool):
        cfg["refresh_minutes"] = int(cfg["refresh_minutes"])  # false: only when you press Refresh. true: 1 minute
    tags = cfg.get("default_tags")
    if isinstance(tags, list) and all(isinstance(t, str) for t in tags):  # (the form studio.toml has them in)
        cfg["default_tags"] = " ".join("#" + t.lstrip("#") for t in tags if t.strip("# "))
    # Any other value of the wrong kind is said here, by name, instead of going wrong somewhere in the window later.
    minutes = cfg.get("refresh_minutes", 30)
    try:
        float(minutes)
    except (TypeError, ValueError):
        raise ConfigError(f"refresh_minutes in {path.name} should be a number of minutes, like refresh_minutes = 30 "
                          "(0: only when you press Refresh).") from None
    cfg["refresh_minutes"] = refresh_minutes(cfg)
    for key, example in TEXT_SETTINGS.items():
        if key in cfg and not isinstance(cfg[key], str):
            raise ConfigError(f'{key} in {path.name} should be text in quotes, like {key} = "{example}".')
    return cfg


# Settings that are text: each with what one looks like, for the message when it's something else.
TEXT_SETTINGS = {"videos_dir": "videos", "episodes_dir": "../episodes", "completed_dir": "../completed videos",
                 "default_tags": "#cooking #recipe"}


def refresh_minutes(cfg: dict) -> float:
    """How often an open window re-reads the stats, in minutes (0: only when you press Refresh): refresh_minutes,
    whatever kind of value the settings hold. One that isn't a number counts as the usual 30 (load_config says so
    when it reads the file; settings made some other way end up here)."""
    try:
        minutes = float(cfg.get("refresh_minutes", 30))
    except (TypeError, ValueError):
        return 30.0
    return minutes if 0 < minutes < float("inf") else 0.0


def save_handle(platform: str, handle: str) -> None:
    """Connect a platform ("@name") or disconnect it (""): its handle in config.toml, the rest of the file as it was.
    Your studio's settings follow (studio.generate.set_account), so your AI's notes cover the platforms you're
    connected to and running onboarding again keeps them."""
    load_config()  # config.toml from the example, if there isn't one yet
    from studio import dashboard_config, generate

    path = config_path()
    if path.resolve() == dashboard_config.config_toml().resolve():  # this profile's own
        generate.set_account(platform, handle)
    else:  # a config.toml somewhere else (the tests): only its handle
        dashboard_config.set_handle(platform, handle, path)


def resolve(path: str) -> Path:
    """Resolve a config path relative to the profile's dashboard folder (for the first profile, this folder)."""
    p = Path(path).expanduser()
    return p if p.is_absolute() else (home() / p).resolve()


def videos_dir(cfg: dict) -> Path:
    """Where you keep finished videos to upload (any .mp4 / .mov / .m4v in it is listed)."""
    return resolve(cfg.get("videos_dir", "videos"))


def episodes_dir(cfg: dict) -> Path:
    """The render pipeline's episodes folder, if you use it; ReelForge lists each episode's final/<id>.mp4."""
    return resolve(cfg.get("episodes_dir", "../episodes"))


def completed_dir(cfg: dict) -> Path:
    """Where the pipeline delivers finished cuts, one folder per video; ReelForge lists <folder>/*/<video>.mp4."""
    return resolve(cfg.get("completed_dir", "../completed videos"))


def _read_tokens() -> dict:
    path = tokens_path()
    if path.exists():
        try:
            data = json.loads(path.read_text("utf-8"))
        except (ValueError, OSError):
            return {}  # unreadable: treat as not logged in rather than crash
        return data if isinstance(data, dict) else {}
    return {}


def get_token(platform: str) -> dict | None:
    with _tokens_lock:
        return _read_tokens().get(platform)


def set_token(platform: str, data: dict | None) -> None:
    """Store (or with None, forget) an entry, such as "browser:instagram" once you've logged in to Instagram.

    Every ReelForge window and the hourly job keep their log-ins in this one file, so they take turns writing it
    (a lock file beside it, as for the stats reads): two of them reading it at the same moment would each save
    without the other's change. Each also writes through a temporary file of its own."""
    from .sync import ReadLock  # here, not at the top: sync loads the platforms, which load this module

    path = tokens_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with _tokens_lock:
        turn = ReadLock(path.with_suffix(".lock"))
        turn.acquire(wait=5)  # still taken after that: written anyway, as it always was
        try:
            tokens = _read_tokens()
            if data is None:
                tokens.pop(platform, None)
            else:
                tokens[platform] = data
            tmp = path.with_name(f"{path.stem}.{os.getpid()}.tmp")
            tmp.write_text(json.dumps(tokens, indent=2), "utf-8")
            tmp.replace(path)
        finally:
            turn.release()
