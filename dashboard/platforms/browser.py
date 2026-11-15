"""Stats read through ReelForge's own browser.

ReelForge drives a browser already on your computer (Edge on Windows, Chrome on macOS and Linux: studio/browsers.py
has the order) with a profile of its own in data/browser, separate from your normal browsing (and each ReelForge
profile, each channel, has its own: their log-ins never mix). You log in to
Instagram and X there once, from the Accounts tab; after that ReelForge opens your profile in the
background about once an hour and reads the numbers the page loads, the same ones you'd
see. TikTok works without logging in. Nothing is posted, liked or followed.
"""

from __future__ import annotations

import json
import re
import threading
import time
from contextlib import contextmanager
from datetime import datetime
from urllib.parse import urlsplit

from studio import browsers

from .. import config
from .base import Account, PlatformError, Post, detail, first_line

LOGIN_URLS = {"instagram": "https://www.instagram.com/accounts/login/", "x": "https://x.com/i/flow/login"}
HOME_URLS = {"instagram": "https://www.instagram.com/", "x": "https://x.com/home"}
SESSION_COOKIES = {"instagram": ("instagram.com", "sessionid"), "x": ("x.com", "auth_token")}
# Every site a platform keeps its log-in on (X still sets some of its cookies on its old address).
COOKIE_SITES = {"instagram": ("instagram.com",), "x": ("x.com", "twitter.com")}
# Each site's own pages that mean "not logged in" and "prove it's you", by the leading parts of their path.
LOGIN_PAGES = {"instagram": ("accounts/login",), "x": ("login", "i/flow")}
CHECK_PAGES = {"instagram": ("challenge", "checkpoint"), "x": ()}

_lock = threading.Lock()  # a browser profile can only be open once at a time


def profile_dir():
    """The browser profile this ReelForge profile reads with: its data/browser."""
    return config.DATA_DIR / "browser"


def chosen_file():
    """Which browser that profile belongs to (studio.browsers.key)."""
    return profile_dir() / "reelforge-browser.txt"


def chosen() -> browsers.Browser | None:
    """The browser ReelForge reads stats with: the one its profile was made with while that's still installed."""
    try:
        remembered = chosen_file().read_text(encoding="utf-8").strip()
    except OSError:
        remembered = ""
    return browsers.find(remembered)


NAME = getattr(chosen(), "name", "Chromium")  # for messages: "Edge", "Chrome" or "Chromium" (setup gets one)


def available() -> bool:
    try:
        import playwright  # noqa: F401
    except ImportError:
        return False
    return True


def logged_in(platform: str) -> bool:
    """Whether you've logged in to this platform in ReelForge's browser (as far as ReelForge knows)."""
    return config.get_token(f"browser:{platform}") is not None


def forget(platform: str) -> None:
    config.set_token(f"browser:{platform}", None)


BUSY = "ReelForge's browser was busy with another read; it tries again next refresh."
# A log-in or a log-out that finds the browser busy: nothing tries those again by itself.
PRESS_AGAIN = "ReelForge's browser is busy reading stats right now. Press {} again in a moment."


@contextmanager
def _browser(headless: bool = True, busy: str = BUSY):
    """ReelForge's browser, open on its own profile: in the background for a read (headless), or with a window for
    a log-in. busy: what to say when another ReelForge still has the profile open after a few tries."""
    if not available():
        raise PlatformError("Browser stats need Playwright: run setup again to install it.")
    b = chosen()
    if b is None:
        raise PlatformError("Browser stats need Chrome, Edge or Chromium: run setup again (reelforge setup) and it "
                            "downloads Chromium.")
    from playwright.sync_api import sync_playwright

    with _lock, sync_playwright() as pw:
        # The browser exits with code 21 when the profile is still held, e.g. by the previous read's browser
        # finishing up or by the hourly background refresh; give it a few seconds before giving up.
        for attempt in range(8):
            try:
                ctx = pw.chromium.launch_persistent_context(
                    str(profile_dir()), executable_path=str(b.exe) if b.exe else None,
                    channel=None if b.exe else "chromium", headless=headless,
                    user_agent=browsers.user_agent(b) if headless else None,
                    args=["--disable-blink-features=AutomationControlled"],
                    viewport={"width": 1280, "height": 900} if headless else None,
                    no_viewport=not headless,
                )
                break
            except Exception as e:
                held = "exitCode=21" in str(e)
                if held and attempt < 7:
                    time.sleep(2.5)
                    continue
                if held:
                    raise PlatformError(busy) from e
                raise PlatformError(f"Couldn't start {b.describe()}: {str(e).splitlines()[0]}") from e
        try:
            chosen_file().write_text(browsers.key(b), encoding="utf-8")
        except OSError:
            pass
        try:
            yield ctx
        finally:
            ctx.close()


def _on_site(host: str, site: str) -> bool:
    """Whether a cookie's domain (".instagram.com", "www.instagram.com") is the site or part of it. Not any domain
    that merely contains its name (x.com is in dropbox.com)."""
    host = host.lstrip(".").lower()
    return host == site or host.endswith("." + site)


def _has_session(ctx, platform: str) -> bool:
    site, name = SESSION_COOKIES[platform]
    return any(c["name"] == name and _on_site(c["domain"], site) for c in ctx.cookies())


def _at(url: str, handle: str, *pages: str) -> bool:
    """Whether an address is one of a site's own pages ("accounts/login", "challenge"): its path starts with that
    page's whole parts. A profile whose handle only starts the same way (x.com/loginvideos,
    instagram.com/challengeaccepted/reels/) isn't one, and neither is the profile itself."""
    parts = [part for part in urlsplit(url).path.split("/") if part]
    if handle and parts and parts[0].lower() == handle.lower():
        return False
    return any(parts[:len(want)] == want for want in (page.split("/") for page in pages))


def _gone(error: Exception) -> bool:
    """Whether an error is the browser's own (Playwright's): during a log-in that means its window was closed."""
    return type(error).__module__.startswith("playwright")


SETTLED = 5  # seconds a site has to stay clear of its login and check pages before a second log-in counts as done


def login(platform: str, timeout: float = 600) -> None:
    """Open a visible window of ReelForge's browser for you to log in to the platform, and wait until you have.

    Not logged in there yet: the site's login page, until it has set its session cookie. Logged in already (Log in
    again, because the site wants to check it's you, or showed its login page all the same): the site itself, for
    you to do what it asks. That's done once it has shown its login or check page and then stayed clear of them
    for a few seconds, or when you close the window still logged in. (The window used to close by itself after 3
    seconds whenever the cookie was there, before you could do anything.)
    """
    with _browser(headless=False, busy=PRESS_AGAIN.format("Log in")) as ctx:
        had = has = False  # logged in when the window opened, and when last looked
        ended = "closed"
        try:
            had = has = _has_session(ctx, platform)
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            try:
                page.goto(HOME_URLS[platform] if had else LOGIN_URLS[platform], wait_until="domcontentloaded",
                          timeout=45000)
            except Exception as e:
                if not _gone(e) or type(e).__name__ == "TargetClosedError":
                    raise
                if type(e).__name__ != "TimeoutError":  # (a slow page is still a page to log in on)
                    raise PlatformError(f"{NAMES[platform]}'s page didn't open in ReelForge's browser (is this "
                                        "computer online?). Press Log in to try again.") from e
            asked, clear = False, 0  # the site showed a login or check page; seconds it has been clear of them since
            ended, deadline = "late", time.monotonic() + timeout
            while time.monotonic() < deadline:
                if not ctx.pages:  # you closed the window (where that leaves the browser running)
                    ended = "closed"
                    break
                has = _has_session(ctx, platform)
                if has and not had:
                    page.wait_for_timeout(3000)  # let the site finish setting cookies
                    ended = "done"
                    break
                if had and any(_at(p.url, "", *LOGIN_PAGES[platform], *CHECK_PAGES[platform]) for p in ctx.pages):
                    asked, clear = True, 0
                elif had and asked and has:
                    clear += 1
                    if clear >= SETTLED:
                        ended = "done"
                        break
                time.sleep(1)
        except Exception as e:  # closing the last window ends the browser on Windows and Linux: every call fails
            if not _gone(e):
                raise
            ended = "closed"
    # A window closed or left open while logged in counts as done: logged in all along, or closed in the moment
    # after the site set its cookie (which used to count as closed before logging in).
    if ended != "done" and not has:
        raise PlatformError("The login window closed before you finished logging in." if ended == "closed" else
                            f"The login window was open for {timeout / 60:.0f} minutes without a log-in, so ReelForge "
                            "closed it. Press Log in to try again.")
    config.set_token(f"browser:{platform}", {"since": time.time()})


def logout(platform: str) -> None:
    """Log ReelForge's browser out of a platform: the cookies its site set are removed from the browser profile, and
    the log-in is forgotten (Disconnect alone keeps both). Your normal browser has nothing to do with it."""
    with _browser(busy=PRESS_AGAIN.format("Log out")) as ctx:
        for site in COOKIE_SITES[platform]:
            ctx.clear_cookies(domain=re.compile(rf"(^|\.){re.escape(site)}$"))
    forget(platform)


NAMES = {"instagram": "Instagram", "x": "X"}


def _logged_out(platform: str) -> PlatformError:
    """The session cookie is gone: ReelForge's browser really is logged out."""
    was = logged_in(platform)
    forget(platform)
    if was:
        return PlatformError(f"{NAMES[platform]} logged ReelForge's browser out. Log in again on the Accounts tab.",
                             needs_login=True)
    return PlatformError(f"Log in to {NAMES[platform]} once on the Accounts tab so ReelForge can read your stats.",
                         needs_login=True)


def _sent_to_login(platform: str) -> PlatformError:
    """The site showed its login page although the session cookie is there. That can be a one-off, so ReelForge only
    forgets the login the second time in a row."""
    entry = config.get_token(f"browser:{platform}") or {}
    if entry.get("misses", 0) >= 1:
        return _logged_out(platform)
    config.set_token(f"browser:{platform}", {**entry, "misses": entry.get("misses", 0) + 1})
    return PlatformError(f"{NAMES[platform]} showed ReelForge's browser its login page; it tries again next refresh. "
                         "If it keeps happening, press Log in again on the Accounts tab.")


def _read_ok(platform: str) -> None:
    entry = config.get_token(f"browser:{platform}")
    if entry and entry.get("misses"):
        config.set_token(f"browser:{platform}", {**entry, "misses": 0})


def _count(value) -> int | None:
    """A count from a page, or None when the page didn't include it."""
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _walk(node):
    """Every dict inside a JSON tree."""
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from _walk(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk(v)


def _posts(items, make, name: str) -> tuple[list[Post], str, str]:
    """A Post from each item (make(item); None for one that isn't a video). An item that isn't made the way the
    reader expects is left out instead of failing the whole read. Returns the posts, and when any were left out what
    to tell you and the first one's error, for the log."""
    posts, odd = [], []
    for item in items:
        try:
            post = make(item)
        except (KeyError, TypeError, ValueError, AttributeError) as e:
            odd.append(e)
            continue
        if post is not None:
            posts.append(post)
    if not odd:
        return posts, "", ""
    return posts, (f"{len(odd)} of {name}'s videos {'was' if len(odd) == 1 else 'were'} left out of this read: "
                   "ReelForge couldn't make sense of what the page said about them."), detail(odd[0])


# --- TikTok (no login) -----------------------------------------------------------------------

def tiktok_profile_stats(page_html: str) -> dict | None:
    m = re.search(r'<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__"[^>]*>(.*?)</script>', page_html, re.S)
    if not m:
        return None
    detail = json.loads(m.group(1)).get("__DEFAULT_SCOPE__", {}).get("webapp.user-detail", {})
    return detail.get("userInfo", {}).get("stats")


def tiktok(handle: str) -> tuple[Account, list[Post]]:
    """Followers and totals, plus plays/likes/comments/shares/saves for each recent video."""
    with _browser() as ctx:
        page = ctx.new_page()
        items: dict[str, dict] = {}

        def on_response(resp):
            if "/api/post/item_list" in resp.url:
                try:
                    for it in json.loads(resp.text() or "{}").get("itemList") or []:
                        items[str(it["id"])] = it
                except Exception:
                    pass

        page.on("response", on_response)
        page.goto(f"https://www.tiktok.com/@{handle}", wait_until="domcontentloaded", timeout=45000)
        stats = tiktok_profile_stats(page.content())
        if stats and stats.get("videoCount"):
            # The video list arrives a moment after the page; stop once it has settled.
            seen, quiet = 0, 0
            for _ in range(15):
                page.wait_for_timeout(1000)
                quiet = quiet + 1 if len(items) == seen and items else 0
                seen = len(items)
                if quiet >= 2:
                    break
    if not stats:
        raise PlatformError(f"TikTok didn't load @{handle}'s profile (wrong handle, or a captcha); "
                            "it tries again next refresh.")

    def post(it: dict) -> Post:
        s = {**(it.get("stats") or {}), **{k: int(v) for k, v in (it.get("statsV2") or {}).items() if str(v).isdigit()}}
        return Post(
            post_id=str(it["id"]),
            title=first_line(it.get("desc")),
            url=f"https://www.tiktok.com/@{handle}/video/{it['id']}",
            published_at=int(it["createTime"]),
            views=_count(s.get("playCount")),
            likes=int(s.get("diggCount", 0)),
            comments=int(s.get("commentCount", 0)),
            shares=int(s.get("shareCount", 0)),
            saves=int(s["collectCount"]) if "collectCount" in s else None,
        )

    posts, warning, why = _posts(items.values(), post, "TikTok")
    return Account(handle=f"@{handle}", followers=stats.get("followerCount"), likes=stats.get("heartCount"),
                   posts=stats.get("videoCount"), warning=warning, detail=why), posts


# --- Instagram (log in once) -----------------------------------------------------------------

# Instagram's own profile page loads these queries (identified by their x-fb-friendly-name header).
IG_QUERIES = ("PolarisProfilePageContentQuery", "PolarisProfilePostsQuery", "PolarisProfileReelsTabContentQuery")
IG_EPOCH_MS = 1314220021721  # media ids carry their upload time: (pk >> 23) + this epoch, in ms


def instagram(handle: str) -> tuple[Account, list[Post]]:
    """Open the Reels tab of your profile, as you would, and read what the page loads."""
    with _browser() as ctx:
        if not _has_session(ctx, "instagram"):
            raise _logged_out("instagram")
        page = ctx.new_page()
        got: dict[str, list] = {q: [] for q in IG_QUERIES}

        def on_response(resp):
            name = resp.request.headers.get("x-fb-friendly-name", "")
            if name in got:
                try:
                    got[name].append(resp.json())
                except Exception:
                    pass

        page.on("response", on_response)
        page.goto(f"https://www.instagram.com/{handle}/reels/", wait_until="domcontentloaded", timeout=45000)
        for _ in range(15):
            page.wait_for_timeout(1000)
            if got["PolarisProfilePageContentQuery"] and got["PolarisProfileReelsTabContentQuery"]:
                break
        # The Reels tab carries no captions; the profile's main grid does.
        if not _at(page.url, handle, *LOGIN_PAGES["instagram"]) and not got["PolarisProfilePostsQuery"]:
            page.goto(f"https://www.instagram.com/{handle}/", wait_until="domcontentloaded", timeout=45000)
            for _ in range(10):
                page.wait_for_timeout(1000)
                if got["PolarisProfilePostsQuery"]:
                    break
        on_login_page = _at(page.url, handle, *LOGIN_PAGES["instagram"])
        challenge = _at(page.url, handle, *CHECK_PAGES["instagram"])
    if challenge:
        raise PlatformError("Instagram wants to check it's you: press Log in again on the Accounts tab and "
                            "follow its steps in the window that opens.")
    if on_login_page:
        raise _sent_to_login("instagram")
    result = parse_instagram(got, handle)
    _read_ok("instagram")
    return result


def parse_instagram(got: dict[str, list], handle: str) -> tuple[Account, list[Post]]:
    """Profile query -> followers; posts tab -> dates and captions; Reels tab -> play counts."""
    user = next((d for d in _walk(got.get("PolarisProfilePageContentQuery", []))
                 if "follower_count" in d and str(d.get("username", handle)).lower() == handle.lower()), None)
    if user is None:
        raise PlatformError(f"Instagram didn't load @{handle}'s profile (wrong handle?); it tries again next refresh.")

    def media(query: str) -> dict[str, dict]:
        return {str(d["pk"]): d for d in _walk(got.get(query, []))
                if "pk" in d and "code" in d and "like_count" in d and "media_type" in d}

    posts_tab, reels_tab = media("PolarisProfilePostsQuery"), media("PolarisProfileReelsTabContentQuery")

    def post(pk: str) -> Post | None:
        m = {**posts_tab.get(pk, {}), **{k: v for k, v in reels_tab.get(pk, {}).items() if v is not None}}
        if m.get("media_type") != 2 and m.get("product_type") != "clips":
            return None  # photos and carousels aren't episodes
        taken = m.get("taken_at") or ((int(pk) >> 23) + IG_EPOCH_MS) // 1000
        return Post(
            post_id=pk,
            title=first_line((m.get("caption") or {}).get("text")),
            url=f"https://www.instagram.com/reel/{m['code']}/",
            published_at=int(taken),
            views=_count(m.get("play_count") or m.get("ig_play_count") or m.get("view_count")),
            likes=int(m.get("like_count") or 0),
            comments=int(m.get("comment_count") or 0),
            shares=m.get("reshare_count"),
        )

    # every reel, newest first, without repeats
    posts, warning, why = _posts(dict.fromkeys([*reels_tab, *posts_tab]), post, "Instagram")
    return Account(handle=f"@{handle}", followers=user.get("follower_count"), posts=user.get("media_count"),
                   warning=warning, detail=why), posts


# --- X (log in once) -------------------------------------------------------------------------

X_TIMELINES = ("UserOriginalsTimeline", "UserTweets")  # X has used both names for the profile's posts


def _x_tweet(d: dict) -> dict | None:
    if d.get("__typename") == "TweetWithVisibilityResults":
        d = d.get("tweet") or {}
    legacy = d.get("legacy")
    if not isinstance(legacy, dict) or "id_str" not in legacy or "full_text" not in legacy:
        return None
    return {**legacy, "_views": (d.get("views") or {}).get("count")}


def x(handle: str) -> tuple[Account, list[Post]]:
    with _browser() as ctx:
        if not _has_session(ctx, "x"):
            raise _logged_out("x")
        page = ctx.new_page()
        got: dict[str, list] = {"UserByScreenName": [], **{t: [] for t in X_TIMELINES}}

        def on_response(resp):
            if "/graphql/" not in resp.url:
                return
            op = resp.url.split("?")[0].rstrip("/").rsplit("/", 1)[-1]
            if op in got:
                try:
                    got[op].append(resp.json())
                except Exception:
                    pass

        def timeline_loaded() -> bool:
            return any(got[t] for t in X_TIMELINES)

        page.on("response", on_response)
        page.goto(f"https://x.com/{handle}", wait_until="domcontentloaded", timeout=45000)
        for _ in range(20):
            page.wait_for_timeout(1000)
            if got["UserByScreenName"] and timeline_loaded():
                break
        if timeline_loaded():
            page.mouse.wheel(0, 6000)  # one scroll loads the next ~20 posts
            page.wait_for_timeout(2500)
        on_login_page = _at(page.url, handle, *LOGIN_PAGES["x"])

    if on_login_page:
        raise _sent_to_login("x")
    result = parse_x(got, handle)
    _read_ok("x")
    return result


def parse_x(got: dict[str, list], handle: str) -> tuple[Account, list[Post]]:
    """Turn captured profile and timeline responses into an account and its video posts."""
    user = next((d for d in _walk(got.get("UserByScreenName", []))
                 if "relationship_counts" in d or (isinstance(d.get("legacy"), dict) and "followers_count" in d["legacy"])),
                None)
    if user is None:
        raise PlatformError(f"X didn't load @{handle}'s profile; it tries again next refresh.")
    legacy_user = user.get("legacy") if isinstance(user.get("legacy"), dict) else {}
    followers = (user.get("relationship_counts") or {}).get("followers", legacy_user.get("followers_count"))
    user_id = user.get("rest_id")

    seen = set()

    def post(d: dict) -> Post | None:
        t = _x_tweet(d)
        if not t or t["id_str"] in seen or (user_id and t.get("user_id_str") != user_id):
            return None
        seen.add(t["id_str"])
        if "retweeted_status_result" in t:
            return None
        media = ((t.get("extended_entities") or {}).get("media") or []) + ((t.get("entities") or {}).get("media") or [])
        if not any(m.get("type") in ("video", "animated_gif") for m in media):
            return None  # only video posts are episodes
        return Post(
            post_id=t["id_str"],
            title=first_line(t["full_text"]),
            url=f"https://x.com/{handle}/status/{t['id_str']}",
            published_at=datetime.strptime(t["created_at"], "%a %b %d %H:%M:%S %z %Y").isoformat(),
            views=_count(t["_views"]),
            likes=t.get("favorite_count", 0),
            comments=t.get("reply_count", 0),
            shares=t.get("retweet_count", 0) + t.get("quote_count", 0),
            saves=t.get("bookmark_count"),
        )

    posts, warning, why = _posts(_walk([got.get(t, []) for t in X_TIMELINES]), post, "X")
    return Account(handle=f"@{handle}", followers=followers, posts=legacy_user.get("statuses_count"),
                   warning=warning, detail=why), posts
