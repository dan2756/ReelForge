"""The readers: what ReelForge makes of each platform's pages (dashboard/platforms). Nothing here goes near the
internet or a real browser: YouTube's pages are answered by a stand-in (httpx.MockTransport), and ReelForge's browser
by stand-in windows and pages."""

from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import httpx

from dashboard import config, db, sync
from dashboard.platforms import Account, PlatformError, TikTok, YouTube, browser, public

AGAIN = "ReelForge tries again at the next refresh."
CHANNEL = ('<link rel="canonical" href="https://www.youtube.com/channel/UC' + "a" * 22 + '">'
           '{"content":"1.2K subscribers"}')
OLDER = [f"old{n:08d}" for n in range(30)]  # Shorts the feed no longer lists, newest first
WATCH = '"viewCount":"123","publishDate":"2026-10-01T10:00:00-07:00","likeCount":"7"'


def entry(n: int, likes: bool = True) -> str:
    return (f"<entry><yt:videoId>feed{n:07d}</yt:videoId><title>Feed &amp; {n}</title>"
            "<published>2026-11-01T10:00:00+00:00</published>"
            + ('<media:starRating count="4"/>' if likes else "") + f'<media:statistics views="{900 + n}"/></entry>')


FEED = "<feed>" + "".join(entry(n) for n in range(15)) + "</feed>"
SHORTS = "".join(f'"videoId":"feed{n:07d}"' for n in range(15)) + "".join(f'"videoId":"{v}"' for v in OLDER)


class YouTubePages(unittest.TestCase):
    """public.youtube against a stand-in YouTube: a channel whose feed lists its newest 15 videos and whose Shorts
    tab lists those and 30 older ones."""

    def site(self, **answers) -> list[str]:
        """Stand in for YouTube. answers: what a kind of page ("channel", "feed", "shorts", "watch", or one video's
        id) gives instead of its usual page: a status code, a page's text, or an error to raise. Returns the list the
        addresses asked for are added to."""
        asked: list[str] = []

        def answer(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            asked.append(url)
            kind, usual = ("feed", FEED) if "feeds/videos.xml" in url else \
                ("shorts", SHORTS) if url.endswith("/shorts") else \
                ("watch", WATCH) if "watch?v=" in url else ("channel", CHANNEL)
            given = answers.get(url.rsplit("=", 1)[-1], answers.get(kind, usual))
            if isinstance(given, Exception):
                raise given
            return httpx.Response(given, text="") if isinstance(given, int) else httpx.Response(200, text=given)

        patch = mock.patch.object(public, "_client", lambda: httpx.Client(
            transport=httpx.MockTransport(answer), follow_redirects=True, timeout=15))
        patch.start()
        self.addCleanup(patch.stop)
        return asked

    @staticmethod
    def watched(asked: list[str]) -> list[str]:
        return [url.rsplit("=", 1)[-1] for url in asked if "watch?v=" in url]

    def test_a_whole_read(self):
        asked = self.site()
        account, posts = public.youtube("somechannel")
        self.assertEqual((account.handle, account.followers, account.warning), ("@somechannel", 1200, ""))
        self.assertEqual(len(posts), 25)
        self.assertEqual((posts[0].post_id, posts[0].title, posts[0].views, posts[0].likes),
                         ("feed0000000", "Feed & 0", 900, 4))
        self.assertEqual((posts[-1].post_id, posts[-1].views, posts[-1].likes), ("old00000009", 123, 7))
        self.assertEqual(len(asked), 3 + public.WATCH_PAGES)

    def test_older_videos_take_turns(self):
        """Ten of the videos the feed doesn't list are looked up per read: those read longest ago, a new one first.
        (It used to be the same ten every time, so the rest never moved.)"""
        last_read: dict[str, str] = {}
        seen = []
        for hour in range(3):
            asked = self.site()
            _, posts = public.youtube("somechannel", last_read)
            seen.append(self.watched(asked))
            last_read.update({p.post_id: f"2026-11-15T{10 + hour}:00:00+00:00" for p in posts})
        self.assertEqual(seen, [OLDER[:10], OLDER[10:20], OLDER[20:]])
        asked = self.site(shorts=SHORTS.replace('"videoId":"old', '"videoId":"new00000001""videoId":"old', 1))
        public.youtube("somechannel", last_read)  # a Short the feed hasn't got yet: first, then the stalest
        self.assertEqual(self.watched(asked), ["new00000001", *OLDER[:9]])

    def test_a_page_that_fails_only_loses_itself(self):
        """Only the channel page has to answer: the feed, the Shorts tab and each video's page are extras."""
        asked = self.site(old00000003=httpx.ReadTimeout("timed out"), old00000005=503)
        account, posts = public.youtube("somechannel")
        self.assertEqual((account.followers, len(posts), account.warning), (1200, 23, ""))
        self.assertEqual(len(self.watched(asked)), 10)  # it went on after the one that timed out

        self.site(feed=httpx.ReadTimeout("timed out"))
        account, posts = public.youtube("somechannel")
        self.assertEqual((account.followers, account.warning), (1200, ""))
        self.assertEqual([p.post_id for p in posts][:2], ["feed0000000", "feed0000001"])  # from their own pages
        self.assertEqual(len(posts), 10)

        self.site(shorts=httpx.ConnectError("reset"))
        account, posts = public.youtube("somechannel")
        self.assertEqual((account.followers, len(posts), account.warning), (1200, 15, ""))

    def test_no_videos_at_all_is_said(self):
        self.site(feed=httpx.ReadTimeout("timed out"), shorts=httpx.ConnectError("reset"))
        account, posts = public.youtube("somechannel")
        self.assertEqual((account.followers, posts), (1200, []))
        self.assertEqual(account.warning, f"YouTube's videos weren't read this time, only its subscribers. {AGAIN}")
        self.assertEqual(account.detail, "the feed: ReadTimeout: timed out; the Shorts tab: ConnectError: reset")
        self.site(feed=404, shorts="")  # a new channel with nothing up yet isn't a failure
        account, posts = public.youtube("somechannel")
        self.assertEqual((posts, account.warning), ([], ""))

    def test_the_channel_page_says_what_went_wrong(self):
        """A wrong handle is the only thing reported as one: a busy YouTube or a page that isn't the channel's used
        to send people off to retype a handle that was right."""
        for given, said in ((404, "Couldn't find the YouTube channel @somechannel."),
                            (429, f"YouTube is busy or limiting how often it's read right now. {AGAIN}"),
                            (503, f"YouTube is busy or limiting how often it's read right now. {AGAIN}"),
                            ("<title>Before you continue to YouTube</title>",
                             f"YouTube's page for @somechannel wasn't what ReelForge expected. {AGAIN}")):
            asked = self.site(channel=given)
            with self.assertRaises(PlatformError) as caught:
                public.youtube("somechannel")
            self.assertEqual(str(caught.exception), said)
            self.assertEqual(len(asked), 1)
        self.site(channel=httpx.ConnectError("no route"))  # no internet is the read's failure, as it was
        with self.assertRaises(httpx.ConnectError):
            public.youtube("somechannel")

    def test_a_like_count_that_isnt_there_is_unknown_not_zero(self):
        """Zero would replace the count ReelForge already has; unknown keeps it (db.record_snapshot)."""
        self.site(feed=f"<feed>{entry(0, likes=False)}{entry(1)}</feed>", shorts='"videoId":"old00000000"',
                  watch='"viewCount":"5","uploadDate":"2026-10-01"')
        _, posts = public.youtube("somechannel")
        self.assertEqual([(p.post_id, p.likes) for p in posts],
                         [("feed0000000", None), ("feed0000001", 4), ("old00000000", None)])

    def test_the_platform_hands_over_when_each_video_was_last_read(self):
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        self.addCleanup(setattr, db, "_path", db._path)
        db.init(Path(tmp.name) / "stats.db")
        asked = self.site()
        account, posts = YouTube({"handle": "@somechannel"}).fetch()
        self.assertEqual(self.watched(asked), OLDER[:10])
        db.record_snapshot("youtube", account, posts)
        asked = self.site()
        YouTube({"handle": "@somechannel"}).fetch()
        self.assertEqual(self.watched(asked), OLDER[10:20])


# --- ReelForge's browser, stood in for ---------------------------------------------------------------------------

def browser_error(name: str) -> type[Exception]:
    """An error class as Playwright's own look to ReelForge: by where they come from and what they're called."""
    return type(name, (Exception,), {"__module__": "playwright._impl._errors"})


TargetClosedError, NavigationError, BrowserTimeout = (browser_error(n) for n in ("TargetClosedError", "Error",
                                                                                 "TimeoutError"))
SESSION = {"instagram": {"name": "sessionid", "domain": ".instagram.com"},
           "x": {"name": "auth_token", "domain": ".x.com"}}


class Clock:
    """Time that passes only when the code under test waits, with things set to happen at given seconds."""

    def __init__(self) -> None:
        self.now = 0.0
        self.due: list[tuple[float, object]] = []

    def at(self, seconds: float, happen) -> None:
        self.due.append((seconds, happen))

    def monotonic(self) -> float:
        return self.now

    def time(self) -> float:
        return 1_800_000_000 + self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds
        for when, happen in sorted(self.due, key=lambda d: d[0]):
            if when <= self.now:
                self.due.remove((when, happen))
                happen()


class Reply:
    """Something a page loaded in the background: its address, the query's name (Instagram) and what it said."""

    def __init__(self, url: str, data, name: str = "") -> None:
        self.url, self.data = url, data
        self.request = SimpleNamespace(headers={"x-fb-friendly-name": name})

    def json(self):
        return self.data

    def text(self) -> str:
        return json.dumps(self.data)


class Page:
    def __init__(self, window: "Window") -> None:
        self.window, self.url, self.heard = window, "about:blank", []
        self.mouse = SimpleNamespace(wheel=lambda x, y: None)

    def on(self, event: str, hear) -> None:
        self.heard.append(hear)

    def goto(self, url: str, **how) -> None:
        self.window.check()
        self.window.opened.append(url)
        if self.window.wont_open:
            raise self.window.wont_open
        self.url, replies, self.html = self.window.site(url)
        for reply in replies:
            for hear in self.heard:
                hear(reply)

    def content(self) -> str:
        return self.html

    def wait_for_timeout(self, ms: float) -> None:
        self.window.clock.sleep(ms / 1000)
        self.window.check()


class Window:
    """ReelForge's browser, open: its cookies, its pages and the site they show. site(address) gives where the page
    ends up, what it loads in the background and its HTML."""

    def __init__(self, clock: Clock, cookies=(), site=None) -> None:
        self.clock, self.jar, self.gone = clock, list(cookies), False
        self.site = site or (lambda url: (url, [], ""))
        self.opened: list[str] = []
        self.wont_open: Exception | None = None
        self.pages = [Page(self)]

    def check(self) -> None:
        if self.gone:  # the last window was closed, which ends the browser: every call fails like this
            raise TargetClosedError("BrowserContext.cookies: Target page, context or browser has been closed")

    def cookies(self) -> list[dict]:
        self.check()
        return list(self.jar)

    def new_page(self) -> Page:
        self.pages.append(Page(self))
        return self.pages[-1]

    def clear_cookies(self, domain=None) -> None:
        self.jar = [c for c in self.jar if not domain.search(c["domain"])]


class BrowserTest(unittest.TestCase):
    """A reader or a log-in on a stand-in for ReelForge's browser, with the log-ins it knows of in a scratch file."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        self.clock = Clock()
        self.windows: list[bool] = []  # each time the browser was opened: in the background (headless) or not

        @contextmanager
        def opened(headless: bool = True):
            self.windows.append(headless)
            yield self.window

        for patch in (mock.patch.object(config, "TOKENS_PATH", Path(tmp.name) / "tokens.json"),
                      mock.patch.object(browser, "_browser", opened), mock.patch.object(browser, "time", self.clock)):
            patch.start()
            self.addCleanup(patch.stop)

    def said(self, do) -> str:
        with self.assertRaises(PlatformError) as caught:
            do()
        return str(caught.exception)


def x_site(lands: str | None = None, tweets: list | None = None):
    """A healthy, logged-in X for any handle (or one that sends the page to `lands`)."""
    tweet = {"id_str": "9", "full_text": "a video post", "user_id_str": "1",
             "created_at": "Wed Oct 14 20:19:24 +0000 2026", "favorite_count": 3,
             "extended_entities": {"media": [{"type": "video"}]}}
    user = {"data": {"user": {"result": {"rest_id": "1", "legacy": {"followers_count": 5, "statuses_count": 1}}}}}
    timeline = {"data": [{"legacy": t} for t in (tweets if tweets is not None else [tweet])]}
    return lambda url: (lands or url, [] if lands else [
        Reply("https://x.com/i/api/graphql/abc/UserByScreenName?variables=1", user),
        Reply("https://x.com/i/api/graphql/abc/UserTweets?variables=1", timeline)], "")


def instagram_site(lands: str | None = None, items: list | None = None):
    def site(url: str):
        handle = url.split("instagram.com/")[1].split("/")[0]
        reel = {"pk": "3400000000000000000", "code": "AbC", "like_count": 2, "media_type": 2, "play_count": 9,
                "caption": {"text": "a reel"}}
        media = {"items": items if items is not None else [reel]}
        return lands or url, [] if lands else [
            Reply(url, {"data": {"user": {"username": handle, "follower_count": 7, "media_count": 1}}},
                  "PolarisProfilePageContentQuery"),
            Reply(url, media, "PolarisProfileReelsTabContentQuery"), Reply(url, media, "PolarisProfilePostsQuery")], ""
    return site


class OddItems(BrowserTest):
    """One video the page describes in a way the reader doesn't expect is left out, and said: it used to fail the
    whole platform's read."""

    LEFT_OUT = "left out of this read: ReelForge couldn't make sense of what the page said about them."

    def test_x(self):
        good = {"id_str": "9", "full_text": "a video post", "user_id_str": "1",
                "created_at": "Wed Oct 14 20:19:24 +0000 2026", "extended_entities": {"media": [{"type": "video"}]}}
        odd = {**good, "id_str": "10"}
        del odd["created_at"]
        self.window = Window(self.clock, [SESSION["x"]], x_site(tweets=[odd, good]))
        account, posts = browser.x("me")
        self.assertEqual([p.post_id for p in posts], ["9"])
        self.assertEqual((account.warning, account.detail), (f"1 of X's videos was {self.LEFT_OUT}",
                                                             "KeyError: 'created_at'"))
        self.window = Window(self.clock, [SESSION["x"]], x_site())
        self.assertEqual(browser.x("me")[0].warning, "")

    def test_instagram(self):
        reel = {"pk": "3400000000000000000", "code": "AbC", "like_count": 2, "media_type": 2, "play_count": 9}
        odd = [{**reel, "pk": f"not-a-number-{n}", "code": f"x{n}"} for n in range(2)]
        self.window = Window(self.clock, [SESSION["instagram"]], instagram_site(items=[*odd, reel]))
        account, posts = browser.instagram("me")
        self.assertEqual([p.post_id for p in posts], ["3400000000000000000"])
        self.assertEqual(account.warning, f"2 of Instagram's videos were {self.LEFT_OUT}")

    def test_tiktok(self):
        stats = {"__DEFAULT_SCOPE__": {"webapp.user-detail": {"userInfo": {"stats": {
            "followerCount": 11, "heartCount": 22, "videoCount": 2}}}}}
        html = f'<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__" type="application/json">{json.dumps(stats)}</script>'
        items = [{"id": "777", "desc": "no upload time", "stats": {"playCount": 5}},
                 {"id": "778", "desc": "a video", "createTime": 1790000000, "stats": {"playCount": 6, "diggCount": 1}}]
        self.window = Window(self.clock, site=lambda url: (url, [
            Reply("https://www.tiktok.com/api/post/item_list/?x=1", {"itemList": items})], html))
        account, posts = browser.tiktok("me")
        self.assertEqual((account.followers, [(p.post_id, p.views) for p in posts]), (11, [("778", 6)]))
        self.assertEqual((account.warning, account.detail), (f"1 of TikTok's videos was {self.LEFT_OUT}",
                                                             "KeyError: 'createTime'"))


class TikTokFallback(unittest.TestCase):
    """TikTok is read in ReelForge's browser for its videos; when that fails, the plain page still gives the totals.
    That used to pass for a whole read, with no word of why the videos had stopped."""

    AGAIN = "ReelForge tries again at the next refresh."

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        self.addCleanup(setattr, db, "_path", db._path)
        db.init(Path(tmp.name) / "stats.db")
        for state in (sync._attempted, sync.last_errors, sync.last_warnings):
            state.clear()
            self.addCleanup(state.clear)
        self.videos: Exception | None = None  # what reading the videos runs into, if anything
        for patch in (mock.patch.object(browser, "available", lambda: True),
                      mock.patch.object(browser, "tiktok", self.in_browser),
                      mock.patch.object(public, "tiktok", lambda handle: (Account(f"@{handle}", 11, likes=22), []))):
            patch.start()
            self.addCleanup(patch.stop)

    def in_browser(self, handle: str):
        if self.videos:
            raise self.videos
        return Account(f"@{handle}", 11, likes=22), [SimpleNamespace(
            post_id="778", title="a video", url=None, published_at=1790000000, views=6, likes=1, comments=0,
            shares=0, saves=None)]

    def test_the_totals_come_with_why_the_videos_didnt(self):
        tiktok = TikTok({"handle": "@me"})
        only = "Only TikTok's totals were read this time, not its videos. "
        for error, why, kept in (
                (PlatformError("TikTok didn't load @me's profile (wrong handle, or a captcha); it tries again next "
                               "refresh."),
                 "TikTok didn't load @me's profile (wrong handle, or a captcha); it tries again next refresh.",
                 "PlatformError: TikTok didn't load @me's profile (wrong handle, or a captcha); it tries again next "
                 "refresh."),
                (BrowserTimeout("Page.goto: Timeout 45000ms exceeded.\nCall log:\n  - navigating"),
                 f"TikTok's video list took too long to answer. {self.AGAIN}",
                 "TimeoutError: Page.goto: Timeout 45000ms exceeded. | Call log: | - navigating"),
                (KeyError("createTime"),
                 f"TikTok's video list wasn't read: its page wasn't what ReelForge expected. {self.AGAIN}",
                 "KeyError: 'createTime'")):
            self.videos = error
            account, posts = tiktok.fetch()
            self.assertEqual((account.followers, account.likes, posts), (11, 22, []))
            self.assertEqual((account.warning, account.detail), (only + why, kept))
        self.videos = None
        account, posts = tiktok.fetch()
        self.assertEqual((account.warning, len(posts)), ("", 1))

    def test_a_refresh_keeps_it_for_every_window_until_a_whole_read(self):
        ps = {"tiktok": TikTok({"handle": "@me"})}
        self.videos = BrowserTimeout("Page.goto: Timeout 45000ms exceeded.")
        result = sync.refresh(ps, {"refresh_minutes": 30}, manual=True, force=True)
        self.assertEqual((result.done, result.errors), (["TikTok"], []))  # the totals were read: not a failed read
        self.assertEqual(result.details, ["TikTok: TimeoutError: Page.goto: Timeout 45000ms exceeded."])
        said = sync.last_warnings["tiktok"][1]
        self.assertTrue(said.startswith("Only TikTok's totals were read this time, not its videos."))
        sync.last_warnings.clear()  # a window that opens later (another process) takes it on
        self.assertTrue(sync.adopt(sync.shared()))
        self.assertEqual(sync.last_warnings["tiktok"][1], said)
        self.videos = None
        sync.refresh(ps, {"refresh_minutes": 30}, manual=True, force=True)
        self.assertNotIn("tiktok", sync.last_warnings)
        self.assertEqual(sync.shared()["warnings"], {})


class Handles(unittest.TestCase):
    def test_a_handle_that_would_change_the_address_counts_as_none(self):
        """config.toml is edited by hand too. The handle goes into the addresses ReelForge opens, logged in."""
        for typed, handle in (("@me", "me"), ("me.too_ok-1", "me.too_ok-1"), ("日本語", "日本語"), ("", ""),
                              ("https://x.com/me", ""), ("x.com/me", ""), ("me?tab=1", ""), ("me#top", ""),
                              ("me and you", ""), ("..", ""), ("me@evil.example", ""), ("a\\b", "")):
            self.assertEqual(TikTok({"handle": typed}).public_handle, handle, typed)
        self.assertFalse(YouTube({"handle": "youtube.com/@me"}).connected())


if __name__ == "__main__":
    unittest.main()
