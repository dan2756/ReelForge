"""Is a finished episode ready in ReelForge's Publish tab?

The Publish tab lists every episodes/<id>/final/<id>.mp4 and fills the post from episode.json's `title` and the
hashtag line of final/caption.txt. This check runs the dashboard's own listing code when it can (so it sees exactly
what the tab will show), and otherwise the same rules directly, then prints what each platform's post will say.

  python -m studio publish-check episodes/<id>
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from . import settings as S
from . import paths as P
from . import profiles
from .paths import ROOT

# A review's verdict. An approval is the whole word APPROVED in capitals, as the guides ask for it. A rejection is
# "rejected" or "not ... approved" however it's written. Prose after the verdict ("what to fix before this is
# approved", "the shots approved before") is not a verdict, and DISAPPROVED or pre-approved is not an approval.
# A "not ... approved" ("no longer approved" too) is one statement. On one line it may have a word or two in it ("NOT
# YET APPROVED"). Across a line break it is one only where the line can't have ended there: a sentence wrapped right
# after "not", or after "not" and a word no statement ends on ("not yet", "not been", "not fully", "not an"). In a
# review wrapped at 120 columns, "... so this cut is NOT" and then "APPROVED." is a rejection: read line by line it
# was an approval, and the cut shipped. A list's last line that ends "... not cut off", with the verdict APPROVED on
# the line after it, is an approval (read across the line break it was "NOT CUT OFF APPROVED", a rejection), and so
# is an APPROVED after an empty line, whatever the line before ends in.
_APPROVAL = re.compile(r"(?<![\w-])APPROVED(?![\w-])")
_GAP = r"[^\S\n]+"                      # the space between two words on a line
_WRAP = r"[^\S\n]*\n[^\S\n]*"           # ... and where a line was wrapped: one line break, not an empty line
_NOT = rf"(?:not|no(?:{_GAP}|{_WRAP})longer)"
_GOES_ON = ("(?:yet|been|being|be|to|fully|quite|entirely|completely|formally|officially|currently|finally|even"
            "|an|a|the)")               # after "not", a word no line of a review ends its statement on
_REJECTION = re.compile(
    r"\b(?:rejected"
    rf"|{_NOT}{_GAP}(?:\w+{_GAP}){{0,2}}approved"                   # on one line
    rf"|{_NOT}{_WRAP}(?:\w+{_GAP}){{0,2}}approved"                  # wrapped after "not"
    rf"|{_NOT}(?:{_GAP}{_GOES_ON}){{1,2}}{_WRAP}approved"           # ... after "not yet", "not yet been", "not to be"
    rf"|{_NOT}{_GAP}{_GOES_ON}{_WRAP}\w+{_GAP}approved"             # ... or after the first of two such words
    r")\b", re.IGNORECASE)
_LINE_BREAK = re.compile(r"\r+\n?|\n|[\v\f\x1c-\x1e\x85\u2028\u2029]")     # each one that str.splitlines() knows
_RECEIPT = re.compile(r"^Review-SHA256:\s*([0-9a-f]{64})\s*$", re.MULTILINE)


def _breaks(text: str) -> str:
    """The text with each line break in it as one "\\n". CRs before an LF are that one line break, however many
    (CR LF, and CR CR LF where text that had CRLF already was written out as text on Windows). CRs with no LF after
    them are a line break each. One pass over each run of CRs: a file that is CRs for 200 kB is read in a moment."""
    return _LINE_BREAK.sub(lambda m: "\n" * len(m.group()) if m.group().endswith("\r") else "\n", text)


def _read(review: Path) -> str:
    """A review's text, each line break in it one "\\n". The bytes are read as they are: read as text, a file with
    CR CR LF line ends came back with an empty line after every line, and a rejection wrapped onto two lines read
    as two statements."""
    return _breaks(review.read_bytes().decode("utf-8", errors="replace"))


def _verdict(text: str) -> str:
    """approved, rejected, or "" when the review gives no verdict. The latest one wins: an approval stands only
    if no rejection comes after it."""
    text = _breaks(text)
    rejections = [m.span() for m in _REJECTION.finditer(text)]
    approvals = [m.start() for m in _APPROVAL.finditer(text)
                 if not any(a <= m.start() < b for a, b in rejections)]
    if approvals and not any(a > approvals[-1] for a, _ in rejections):
        return "approved"
    return "rejected" if rejections else ""


def review_approved(review: Path, fingerprint: str | None = None) -> bool:
    """True if build/qa/review.md's latest verdict is APPROVED (a later REJECTED wins over an earlier APPROVED).

    Only the whole word in capitals counts: DISAPPROVED is not an approval, "NOT APPROVED" is a rejection, and so is
    a review that says REJECTED and then mentions "approved" in a later sentence. With a fingerprint, the review must
    also carry it as its last "Review-SHA256: <fingerprint>" line."""
    try:
        text = _read(review)
    except OSError:
        return False
    if _verdict(text) != "approved":
        return False
    if fingerprint is None:
        return True
    found = _RECEIPT.findall(text)
    return bool(found) and found[-1] == fingerprint


def review_state(ep_dir: Path) -> str:
    """Where an episode's review stands for the video that's in final/ now:

    approved    its latest verdict is APPROVED, and it was written after this cut was made
    stale       it says APPROVED, but the video is newer than the review: it reviewed an earlier cut
    rejected    its latest verdict is a rejection
    unreviewed  there's no review, or it gives no verdict yet
    """
    ep_dir = Path(ep_dir)
    review = ep_dir / "build" / "qa" / "review.md"
    try:
        text = _read(review)
        written = review.stat().st_mtime
    except OSError:
        return "unreviewed"
    verdict = _verdict(text)
    if verdict != "approved":
        return verdict or "unreviewed"
    newest = 0.0
    for video in (ep_dir / "final").glob("*.mp4"):
        try:
            newest = max(newest, video.stat().st_mtime)
        except OSError:
            pass
    # the same second counts as after: some drives keep times to the second (or two)
    return "stale" if newest > written + 2 else "approved"


def _dashboard_listing():
    """(find(cfg) -> [Path], meta(path, cfg) -> ({caption, tags}, warnings), cfg) from the dashboard itself, or
    None."""
    try:
        from dashboard import config as dash_config
        from dashboard import videos as dash_videos
        cfg = dash_config.load_config()
    except Exception:
        return None
    return dash_videos.find, dash_videos.meta, cfg


def check(ep_dir: Path) -> tuple[bool, str]:
    ep_dir = Path(ep_dir)
    if not ep_dir.is_absolute() and not ep_dir.exists():
        ep_dir = P.EPISODES / ep_dir.name
    ep_dir = ep_dir.resolve()
    here = profiles.of(ep_dir)  # the channel it belongs to: its settings, and its own ReelForge profile
    profiles.use(here)
    lines: list[str] = []
    ok = True

    spec_file = ep_dir / "episode.json"
    if not spec_file.exists():
        return False, f"  FAIL  no episode.json in {ep_dir}"
    try:
        spec = json.loads(spec_file.read_text(encoding="utf-8"))
        if not isinstance(spec, dict) or not spec.get("id"):
            raise ValueError('it has no "id"')
    except ValueError as e:     # a typo from an edit by hand: one FAIL line, like every other problem here
        return False, f"  FAIL  {spec_file} can't be read: {e}"
    video = ep_dir / "final" / f"{spec['id']}.mp4"
    cap = ep_dir / "final" / "caption.txt"
    qa = ep_dir / "build" / "qa" / "qa.json"

    def item(good: bool, text: str):
        nonlocal ok
        ok = ok and good
        lines.append(f"  {'OK  ' if good else 'FAIL'}  {text}")

    item(video.exists(), f"video file {video.relative_to(ROOT) if video.is_relative_to(ROOT) else video}")
    item(cap.exists(), "final/caption.txt (the Publish tab reads the hashtags from it)")
    if qa.exists() and video.exists():
        q = json.loads(qa.read_text(encoding="utf-8"))
        st = video.stat()
        fresh = q.get("fingerprint") == f"{st.st_size}:{int(st.st_mtime)}"
        item(bool(q.get("pass")) and fresh, "QA passed on this exact file" if q.get("pass") and fresh
             else "QA failed, or the video changed after QA (rerun --steps qa)")
    else:
        item(False, "QA report (run --steps post)")
    # as deliver and the Publish tab ask it: an APPROVED written before this cut was made is of an earlier cut
    reviewed = review_state(ep_dir)
    item(reviewed == "approved", "your review (build/qa/review.md) says APPROVED" if reviewed != "stale" else
         "your review says APPROVED, but of an earlier cut: the video changed after it (look at this cut, then write "
         "build/qa/review.md again)")

    title = " ".join(str(spec.get("title", "")).split())
    tags_line = ""
    if cap.exists():
        parts = [p.strip() for p in cap.read_text(encoding="utf-8").split("\n\n") if p.strip()]
        if len(parts) > 1 and all(w.startswith("#") for w in parts[-1].split()):
            tags_line = parts[-1]
    s = S.load(here.settings)
    listing = _dashboard_listing()
    if listing and video.exists():
        find, meta, cfg = listing
        # The tab lists each video once: this final/ file, or its copy in completed videos/ (same file name).
        shown = next((p for p in find(cfg) if p.name.lower() == video.name.lower()), None)
        item(shown is not None, "the Publish tab lists it (checked with the dashboard's own code)")
        got, warnings = meta(shown or video, cfg)
        for w in warnings:
            lines.append(f"  note  {w}")
        item(got.get("caption", "").strip() == S.strip_hashtags(title), f"Publish caption: {got.get('caption', '')!r}")
        tags_line = got.get("tags") or tags_line
    elif video.exists():
        item(True, "the Publish tab will list it (episodes/<id>/final/<id>.mp4)")
        lines.append("  note  the dashboard isn't set up yet, so this used the same rules directly")
    tags = S.tag_list(tags_line or spec.get("hashtags", []))
    if not tags and s["captions"]["max_hashtags"] > 0:
        item(False, "hashtags (episode.json 'hashtags')")
    posts = S.post_texts(title, tags, s)
    lines.append("")
    lines.append("  What each platform will get:")
    for p, text in posts.items():
        shown = text.strip().replace("\n\n", "  |  description: ")
        lines.append(f"    {S.PLATFORM_NAMES[p]:<16} {shown}")
    done = ([d for d in here.completed.glob(f"{spec['id']} - *") if (d / video.name).exists()]
            if here.completed.exists() else [])
    lines.append("")
    lines.append(f"  Copy in completed videos: {'yes' if done else 'no (run --steps deliver)'}")
    head = "READY in ReelForge -> Publish" if ok else "NOT READY for ReelForge's Publish tab"
    return ok, f"  {head}: {spec['id']}\n" + "\n".join(lines)


def main(argv: list[str]) -> int:
    if not argv:
        print("usage: python -m studio publish-check episodes/<id>")
        return 2
    ok, report = check(Path(argv[0]))
    print(report)
    return 0 if ok else 1
