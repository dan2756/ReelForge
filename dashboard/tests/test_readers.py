"""The readers: what ReelForge makes of each platform's pages (dashboard/platforms). Nothing here goes near the
internet or a real browser: YouTube's pages are answered by a stand-in (httpx.MockTransport), and ReelForge's browser
by stand-in windows and pages."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import httpx

from dashboard import db
from dashboard.platforms import PlatformError, YouTube, public

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
            kind, usual = ("feed", FEED) if "feeds/videos.xml" in url else ("shorts", SHORTS) if url.endswith("/shorts") \
                else ("watch", WATCH) if "watch?v=" in url else ("channel", CHANNEL)
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


if __name__ == "__main__":
    unittest.main()
