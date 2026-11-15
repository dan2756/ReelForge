"""TikTok: stats from the public profile, no login; reading it in ReelForge's background browser adds per-video numbers."""

from __future__ import annotations

from . import browser, public
from .base import Account, Platform, Post, detail, explain


class TikTok(Platform):
    key = "tiktok"
    name = "TikTok"
    color = "#25f4ee"
    public_stats = True
    setup_note = ("Stats come from your public profile, read in the background about once an hour: followers and "
                  "total likes, plus each video's views, likes, comments, shares and saves. No login needed.")

    def fetch(self) -> tuple[Account, list[Post]]:
        if not browser.available():
            return public.tiktok(self.public_handle)
        try:
            return browser.tiktok(self.public_handle)  # per-video numbers too
        except Exception as e:
            # A captcha, a timeout, a page that changed: the plain page still gives the totals. Why the videos
            # weren't read goes with them, so the read doesn't pass for a whole one (it used to, for good).
            account, posts = public.tiktok(self.public_handle)
            account.warning = ("Only TikTok's totals were read this time, not its videos. "
                               + explain(e, "TikTok's video list"))
            account.detail = detail(e)
            return account, posts
