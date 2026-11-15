"""For the drawer's tests (ui/test/dashboard.test.tsx): the demo as scripts/shots_preload.py sets it up, with the same
four notifications at any hour of the day. The demo's own follow its made-up numbers (a video "took off" three hours
after it went up, or it didn't), so which there are and what order they come in depends on the clock. These are,
newest first: a finished video, a video past 100K views (one of the demo's TikTok posts, so a click shows it on the
Videos tab), the update, and a followers milestone that's been read. Run inside the window's Python side before it
starts (REELFORGE_BRIDGE_PRELOAD)."""

import runpy
from datetime import timedelta
from pathlib import Path

from dashboard import db, demo, notify

runpy.run_path(str(Path(__file__).resolve().parent.parent.parent / "scripts" / "shots_preload.py"), run_name="preload")


def fixed(now, start, posted, video) -> None:
    # where the milestones start from, as the demo's own notifications set it (so a refresh announces nothing more)
    notify.check_milestones({cls.key: cls.name for cls in demo.ORDER}, {cls.key for cls in demo.ORDER})
    post = next(k for k in demo._SEEDED if k[0] == "tiktok")
    name, caption = video
    for hours, kind, title, body, read, key in (
        (3, "milestone", "TikTok passed 10K followers", "You have 10,240 followers on TikTok.", True,
         "milestone:followers:tiktok:10000"),
        (2, "update", f"ReelForge {demo.SAMPLE_UPDATE[0]} is out", "Open the Updates tab to update and restart in one click.",
         False, f"update:{demo.SAMPLE_UPDATE[0]}"),
        (1, "milestone", "A video passed 100K views on TikTok", demo._SEEDED[post][0][:70], False,
         f"milestone:video:{post[0]}|{post[1]}:100000"),
        (0.5, "video", "A new video is ready to post", f"{caption[:70]} ({name})", False, f"video:{name.lower()}"),
    ):
        notify.add(kind, title, body, key=key, ts=db.to_iso(now - timedelta(hours=hours)), read=read)


demo._sample_notifications = fixed
