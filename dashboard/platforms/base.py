"""What every platform implements, plus the shared stats and post-text types."""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

import httpx


class PlatformError(Exception):
    """A failure with a message fit to show in the UI. needs_login: the site logged ReelForge's browser out, so
    reading it again takes a new log-in on the Accounts tab."""

    def __init__(self, message: str = "", needs_login: bool = False):
        super().__init__(message)
        self.needs_login = needs_login


AGAIN = "ReelForge tries again at the next refresh."
# What a failed read is put down to when it isn't a PlatformError (which says it itself): the kind, then what you're
# told, the platform's name first. The error's own words go to data/refresh.log (detail), never on screen.
KINDS = {
    "slow": "{name} took too long to answer. " + AGAIN,
    "offline": "{name} couldn't be reached (is this computer online?). " + AGAIN,
    "busy": "{name}'s numbers couldn't be saved: the stats database was busy. " + AGAIN,
    "browser": "{name} wasn't read: ReelForge's browser stopped part-way. " + AGAIN,
    "odd": "{name} wasn't read: its page wasn't what ReelForge expected. " + AGAIN,
}


def kind_of(error: BaseException) -> str:
    """Which of KINDS an error is ("said" for a PlatformError, whose message is already fit to show)."""
    from_browser = type(error).__module__.startswith("playwright")  # Playwright's errors, without needing it here
    if isinstance(error, PlatformError) and str(error).strip():
        return "said"
    if isinstance(error, (httpx.TimeoutException, TimeoutError)) or \
            from_browser and type(error).__name__ == "TimeoutError":
        return "slow"
    if isinstance(error, (httpx.TransportError, OSError)):
        return "offline"
    if isinstance(error, sqlite3.Error):
        return "busy"
    return "browser" if from_browser else "odd"


def explain(error: BaseException, name: str) -> str:
    """A failed read of the platform called `name`, in a sentence fit to show."""
    kind = kind_of(error)
    # A message's first line only: browser errors run long.
    return str(error).strip().splitlines()[0] if kind == "said" else KINDS[kind].format(name=name)


def detail(error: BaseException) -> str:
    """An error as the log keeps it: its kind and its own words, on one line (browser errors run to many)."""
    lines = [line.strip() for line in str(error).splitlines()[:6] if line.strip()]
    return f"{type(error).__name__}: " + " | ".join(lines)


@dataclass
class Account:
    handle: str
    followers: int | None
    views: int | None = None  # None: ReelForge sums the views of the posts it can see
    likes: int | None = None
    posts: int | None = None
    # Part of the read failed and the rest was kept (TikTok's video list, say): what to tell you, and the error's
    # own words for data/refresh.log.
    warning: str = ""
    detail: str = ""


@dataclass
class Post:
    post_id: str
    title: str
    url: str | None
    published_at: str | int
    views: int = 0
    likes: int = 0
    comments: int = 0
    shares: int | None = 0
    saves: int | None = None


@dataclass
class PublishItem:
    video: Path
    caption: str  # one line, e.g. "What if your coffee doubled every 2 seconds?"
    tags: str     # "#whatif #pov ...": up to 5 are used

    def text_for(self, platform: str) -> str:
        """The platform's post text (see videos.post_text); for YouTube, the description."""
        from ..videos import post_text

        return post_text(platform, self.caption, self.tags)

    @property
    def title(self) -> str:
        """The caption without hashtags: YouTube's title and the episode's name in ReelForge."""
        return self.text_for("x")[:100]

    @property
    def youtube_title(self) -> str:
        """What YouTube's title box gets: the caption, then #shorts, in YouTube's 100 characters."""
        return f"{self.title[:92].rstrip()} #shorts" if self.title else ""


_NOT_A_HANDLE = re.compile(r"[\s/\\?#%&=:@\x00-\x1f\x7f]|^\.+$")  # (letters of any alphabet are fine: YouTube's)


class Platform:
    key = ""
    name = ""
    color = "#ffffff"
    setup_note = ""  # where the stats come from, shown on the Accounts tab
    public_stats = False  # True: a handle in config.toml is enough for stats (no login)
    browser_stats = False  # True: logging in once in ReelForge's browser gives stats

    def __init__(self, cfg: dict):
        self.cfg = cfg

    @property
    def public_handle(self) -> str:
        """The handle from config.toml, without the @. It goes into the addresses ReelForge opens (in a browser
        that's logged in), so one that would change which page that is (a slash, a ?, a #, a space: a link pasted
        into config.toml, say) counts as none. The platform then shows as not connected, and Connect on the Accounts
        tab, which reads the handle out of a link, puts it right."""
        handle = str(self.cfg.get("handle", "")).strip().lstrip("@")
        return "" if _NOT_A_HANDLE.search(handle) else handle

    @property
    def logged_in(self) -> bool:
        """Logged in once in ReelForge's browser (only platforms that read their stats that way)."""
        from . import browser

        return self.browser_stats and browser.logged_in(self.key)

    @property
    def stats_source(self) -> str | None:
        """Where stats come from: "browser" (logged in once in ReelForge's browser), "public" (just the handle), or
        None."""
        if not self.public_handle:
            return None
        if self.logged_in:
            return "browser"
        return "public" if self.public_stats else None

    @property
    def has_stats(self) -> bool:
        return self.stats_source is not None

    def connected(self, stored=frozenset()) -> bool:
        """Whether this platform is on in ReelForge: its card, charts and their keys, Videos filter, Compare chart,
        Publish buttons, best time and notifications. It is once you've added your handle on the Accounts tab and
        ReelForge can read it: straight away for TikTok and YouTube, after the one log-in for Instagram and X. Stats
        already stored for it (`stored`: the platforms with any) keep it on too, so a log-in that runs out, or stats
        typed in by hand, never hide its history."""
        return bool(self.public_handle) and (not self.browser_stats or self.logged_in or self.key in stored)

    @property
    def uses_browser(self) -> bool:
        """Whether a refresh opens ReelForge's background browser (slower, so it refreshes less often)."""
        from . import browser

        return self.stats_source == "browser" or (self.stats_source == "public" and self.key == "tiktok"
                                                  and browser.available())

    @property
    def handle(self) -> str:
        return "@" + self.public_handle if self.public_handle else ""

    def fetch(self) -> tuple[Account, list[Post]]:
        raise NotImplementedError


def first_line(text: str | None, limit: int = 200) -> str:
    return (text or "").strip().split("\n")[0][:limit]
