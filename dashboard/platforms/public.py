"""Stats from public profile pages, with no login: just set a handle.

Only TikTok and YouTube show numbers to logged-out visitors; Instagram and X don't. This reads the
same pages a browser would, so it can break if a site changes its layout.
"""

from __future__ import annotations

import html
import json
import re

import httpx

from .base import AGAIN, Account, PlatformError, Post, detail

BROWSER = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"),
    "Accept-Language": "en-US,en;q=0.9",
}


def _client() -> httpx.Client:
    # SOCS skips YouTube's cookie-consent page.
    return httpx.Client(headers=BROWSER, cookies={"SOCS": "CAI"}, follow_redirects=True, timeout=15)


def parse_count(text: str) -> int:
    """'21.4M' -> 21400000, '1,234' -> 1234."""
    text = text.replace(",", "").strip()
    scale = {"K": 1e3, "M": 1e6, "B": 1e9}.get(text[-1:].upper(), 1)
    return int(round(float(text.rstrip("KMBkmb")) * scale))  # 2.01K is 2010, not 2009


def tiktok(handle: str) -> tuple[Account, list[Post]]:
    """Followers, total likes and video count. TikTok's page doesn't include per-video numbers."""
    with _client() as c:
        r = c.get(f"https://www.tiktok.com/@{handle}")
    m = re.search(r'<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__"[^>]*>(.*?)</script>', r.text, re.S)
    detail = json.loads(m.group(1)).get("__DEFAULT_SCOPE__", {}).get("webapp.user-detail", {}) if m else {}
    stats = detail.get("userInfo", {}).get("stats")
    if not stats:
        raise PlatformError(f"TikTok didn't return @{handle}'s profile (wrong handle, or it blocked a "
                            "logged-out request; it tries again next refresh).")
    return Account(handle=f"@{handle}", followers=stats.get("followerCount"), likes=stats.get("heartCount"),
                   posts=stats.get("videoCount")), []


WATCH_PAGES = 10  # how many videos' own pages one read of YouTube opens, at most


def youtube(handle: str, last_read: dict[str, str] | None = None) -> tuple[Account, list[Post]]:
    """Subscribers from the channel page, plus views and likes for the latest 15 videos from its RSS feed, and for
    up to WATCH_PAGES more from their own pages: the ones ReelForge read longest ago first (last_read: video id ->
    when), so a new upload the feed hasn't got yet comes first and the older ones take turns.

    Only the channel page has to answer. The feed, the Shorts tab and each video's page are extras: one that fails
    is left out of this read and the rest is kept."""
    with _client() as c:
        page = c.get(f"https://www.youtube.com/@{handle}")
        channel = re.search(r'<link rel="canonical" href="https://www\.youtube\.com/channel/(UC[\w-]{22})"', page.text)
        if page.status_code == 404:
            raise PlatformError(f"Couldn't find the YouTube channel @{handle}.")
        if page.status_code == 429 or page.status_code >= 500:
            raise PlatformError("YouTube is busy or limiting how often it's read right now. " + AGAIN)
        if not channel:  # a consent or "are you a robot" page, or a layout that changed
            raise PlatformError(f"YouTube's page for @{handle} wasn't what ReelForge expected. " + AGAIN)
        subs = re.search(r'"content":"([\d.,]+[KMB]?) subscribers?"', page.text)

        posts, missed, feed = [], [], None
        try:
            feed = c.get(f"https://www.youtube.com/feeds/videos.xml?channel_id={channel.group(1)}")
        except httpx.HTTPError as e:
            missed.append(f"the feed: {detail(e)}")
        if feed is not None and feed.status_code == 200:
            for entry in re.findall(r"<entry>(.*?)</entry>", feed.text, re.S):
                vid = re.search(r"<yt:videoId>([\w-]+)</yt:videoId>", entry)
                views = re.search(r'<media:statistics views="(\d+)"', entry)
                published = re.search(r"<published>(.*?)</published>", entry)
                if not vid or not views or not published:
                    continue
                likes = re.search(r'<media:starRating count="(\d+)"', entry)
                title = re.search(r"<title>(.*?)</title>", entry, re.S)
                posts.append(Post(
                    post_id=vid.group(1),
                    title=html.unescape(title.group(1)) if title else "",
                    url=f"https://youtube.com/shorts/{vid.group(1)}",
                    published_at=published.group(1),
                    views=int(views.group(1)),
                    likes=int(likes.group(1)) if likes else None,  # not given: unknown, which keeps the last count
                    comments=None,
                    shares=None,
                ))
        # The feed is missing for new channels (404) and lags new uploads, so also read the Shorts tab
        # and look up Shorts the feed didn't list on their own pages.
        known = {p.post_id for p in posts}
        ids = []
        try:
            shorts = c.get(f"https://www.youtube.com/@{handle}/shorts")
            ids = re.findall(r'"videoId":"([\w-]{11})"', shorts.text) if shorts.status_code == 200 else []
        except httpx.HTTPError as e:
            missed.append(f"the Shorts tab: {detail(e)}")
        ids = [v for v in dict.fromkeys(ids) if v not in known]  # newest first, as the tab lists them
        for vid in sorted(ids, key=lambda v: (last_read or {}).get(v, ""))[:WATCH_PAGES]:
            try:
                post = _youtube_video(c, vid)
            except httpx.HTTPError as e:
                missed.append(f"{vid}: {detail(e)}")
                continue
            if post:
                posts.append(post)
    account = Account(handle=f"@{handle}", followers=parse_count(subs.group(1)) if subs else None)
    if missed and not posts:  # every way to the videos failed: say so, instead of a read that looks whole
        account.warning = "YouTube's videos weren't read this time, only its subscribers. " + AGAIN
        account.detail = "; ".join(missed)
    return account, posts


def _youtube_video(c: httpx.Client, vid: str) -> Post | None:
    """Views, likes, title and upload time from a video's own page (None when the page doesn't have them)."""
    got = c.get(f"https://www.youtube.com/watch?v={vid}")
    if got.status_code != 200:
        return None
    page = got.text
    views = re.search(r'"viewCount":"(\d+)"', page)
    published = re.search(r'"(?:publishDate|uploadDate)":"([^"]+)"', page)
    if not views or not published:
        return None
    title = re.search(r'"videoDetails":\{"videoId":"' + re.escape(vid) + r'","title":"((?:[^"\\]|\\.)*)"', page)
    likes = re.search(r'"likeCount":"(\d+)"', page)
    return Post(
        post_id=vid,
        title=json.loads(f'"{title.group(1)}"') if title else "",
        url=f"https://youtube.com/shorts/{vid}",
        published_at=published.group(1),
        views=int(views.group(1)),
        likes=int(likes.group(1)) if likes else None,
        comments=None,
        shares=None,
    )
