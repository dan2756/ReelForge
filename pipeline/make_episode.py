"""The episode pipeline: one episodes/<id>/episode.json -> a finished, checked 9:16 video in ReelForge's Publish tab.

  python pipeline/make_episode.py episodes/<id> --steps build,stills --stills 1,15,31
  python pipeline/make_episode.py episodes/<id> --steps render        (20-40 min; resumable)
  python pipeline/make_episode.py episodes/<id> --steps post          (audio, video, cover, captions, QA)
  python pipeline/make_episode.py episodes/<id> --steps ship          (deliver, clean, check the Publish tab)

Steps: build (Blender scene from episode.json) -> stills (preview frames) -> render (all frames, resumable)
       -> post (= audio, video, cover, caption, qa) -> your own review in build/qa/review.md
       -> ship (= deliver: copy to "completed videos/" only if QA passed; clean: delete render frames;
                 check: confirm ReelForge's Publish tab lists the video with the right caption and hashtags).
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config  # noqa: E402
import framecache  # noqa: E402
from studio import settings  # noqa: E402

ALL_STEPS = ["build", "stills", "render", "audio", "video", "cover", "caption", "qa", "deliver", "clean", "check"]
ALIASES = {"post": ["audio", "video", "cover", "caption", "qa"],     # then review build/qa/ before shipping
           "ship": ["deliver", "clean", "check"],
           "all": ALL_STEPS}
NEEDS_BLENDER = {"build", "stills", "render"}
NEEDS_FFMPEG = {"audio", "video", "cover", "qa"}
PROGRESS_EVERY = 20.0       # seconds between a long step's progress lines (the render's frame count)


def run(cmd, log=None, progress=None):
    """Run one step's command. With a log, its output goes to the end of that file (what an earlier run wrote stays
    above it, so a render that is carried on keeps its whole story), and if the step fails the last lines are printed
    here, not only the exit code. progress() gives a line to print every PROGRESS_EVERY seconds while it runs."""
    cmd = [str(c) for c in cmd]
    print(">", " ".join(f'"{c}"' if " " in c else c for c in cmd), flush=True)
    t0 = time.time()
    if not log:
        code = subprocess.run(cmd).returncode
    else:
        with open(log, "a", encoding="utf-8", errors="replace") as fh:
            fh.write(f"==== {time.strftime('%Y-%m-%d %H:%M:%S')}  {' '.join(cmd)}\n")
            fh.flush()
            start = os.fstat(fh.fileno()).st_size       # where this run's own output begins
            with subprocess.Popen(cmd, stdout=fh, stderr=subprocess.STDOUT) as proc:
                try:
                    while True:
                        try:
                            code = proc.wait(PROGRESS_EVERY)
                            break
                        except subprocess.TimeoutExpired:
                            if progress:
                                print("  " + progress(), flush=True)
                except BaseException:       # Ctrl-C too: don't leave the step running with nobody waiting for it
                    proc.kill()
                    raise
    if code:
        if log:
            print(log_tail(log, start), flush=True)
        hint = f" (see {log})" if log else ""
        raise SystemExit(f"step failed with exit code {code}{hint}")
    print(f"  done in {time.time() - t0:.0f}s", flush=True)


def log_tail(log, start=0, lines=20):
    """What to read first when a step fails: the end of what it wrote to its log from byte `start` on, and any
    earlier line of that which names an error."""
    with open(log, "rb") as fh:
        fh.seek(start)
        text = fh.read().decode("utf-8", "replace").splitlines()
    out = []
    errors = [line for line in text[:-lines] if "Traceback" in line or "Error" in line][-10:]
    if errors:
        out += [f"  lines of {log} that mention an error:"] + ["    " + line for line in errors]
    out += [f"  the last lines of {log}:"] + ["    " + line for line in text[-lines:]]
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("episode", type=Path)
    ap.add_argument("--steps", default="all")
    ap.add_argument("--stills", default="", help="comma-separated seconds for preview stills, or `lines`: every "
                    "story line's start and end, both sides of each cut, and the cover")
    ap.add_argument("--rerender", default="", metavar="all|SECONDS",
                    help="render: all frames again, or only these seconds (12-15.5,31-33) keeping the rest")
    ap.add_argument("--keep-frames", action="store_true",
                    help="render: keep the frames on disk although the scene was built again since")
    ap.add_argument("--status", action="store_true",
                    help="say where the episode stands (built, frames, audio, video, QA, review, delivered) and stop")
    args = ap.parse_args()

    ep = args.episode.resolve()
    if not (ep / "episode.json").exists():
        raise SystemExit(f"{ep / 'episode.json'} not found")
    spec = json.loads((ep / "episode.json").read_text(encoding="utf-8"))
    build = ep / "build"
    final = ep / "final"
    build.mkdir(exist_ok=True)
    final.mkdir(exist_ok=True)
    fps = spec["fps"]
    if args.status:
        print(status(ep, spec))
        return

    steps = []
    for s in args.steps.split(","):
        s = s.strip()
        if s not in ALIASES and s not in ALL_STEPS:
            raise SystemExit(f"unknown step {s!r}; steps: {', '.join(ALL_STEPS)} (or post, ship, all)")
        steps += ALIASES.get(s, [s])
    if NEEDS_BLENDER & set(steps):
        config.require_blender()
    if NEEDS_FFMPEG & set(steps):
        config.require_ffmpeg()

    if "build" in steps:
        run([config.BLENDER, "-b", "--factory-startup", "--python-exit-code", "1",
             "-P", config.PIPELINE / "blender" / "build_scene.py", "--", ep], log=build / "build.log")
        # a wrong line, a missing HUD number or sound, or a caption that breaks the rules stops here, in seconds,
        # instead of after the render and the compose
        run([sys.executable, config.PIPELINE / "qa.py", ep, "--lint"])
    if args.stills.strip() == "lines":      # the preset: the seconds where a story line can sit over the wrong shot
        args.stills = ",".join(f"{s:g}" for s in line_times(spec, build))

    if "stills" in steps:
        secs = [float(s) for s in args.stills.split(",") if s] or spec.get("preview_times") or \
            [1, 15, 20, 26, 31, 38, 47, 55, 63, 67]
        frames = ",".join(str(int(round(s * fps)) + 1) for s in secs)
        (build / "stills").mkdir(exist_ok=True)
        fresh_stills(build, built_scene(build, spec, "stills"), {int(n) for n in frames.split(",")})
        run([config.BLENDER, "-b", build / "scene.blend", "-o", build / "stills" / "s_####", "-F", "JPEG",
             "-f", frames], log=build / "stills.log")
        # the same seconds with the text and HUD drawn on, and a labelled contact sheet of them, in build/preview
        run([sys.executable, config.PIPELINE / "post" / "compose.py", ep, "--preview",
             ",".join(f"{s:g}" for s in secs)])

    if "render" in steps:
        run([sys.executable, config.PIPELINE / "qa.py", ep, "--lint"])
        render(build, spec, args.rerender, args.keep_frames)

    post = config.PIPELINE / "post"
    if "audio" in steps:
        mixer = "audio_mix.py" if spec.get("audio", {}).get("mixer") == "samples" else "audio.py"
        run([sys.executable, "-I", post / mixer, ep])
        run([sys.executable, config.PIPELINE / "qa.py", ep, "--audio"])     # sound faults stop before the compose
    if "video" in steps:
        run([sys.executable, post / "compose.py", ep, "--video"])
    if "cover" in steps:
        run([sys.executable, post / "compose.py", ep, "--cover"])
    if "caption" in steps:
        write_captions(spec, final)
    if "qa" in steps:
        run([sys.executable, config.PIPELINE / "qa.py", ep])
    if "deliver" in steps:
        deliver(spec, final, build)
    if "clean" in steps:
        clean(spec, build)
    if "check" in steps:
        check_publish(ep)


def completed_dir(spec):
    title = "".join(c for c in spec["title"] if c not in '<>:"/\\|?*').strip(" .")
    return config.COMPLETED / f"{spec['id']} - {title}"[:120].rstrip(" .")


def deliver(spec, final, build):
    """Copy the finished video, cover and captions to 'completed videos/<id> - <title>/', only if QA passed on
    this exact video file."""
    if spec.get("demo"):
        raise SystemExit("deliver: this is one of ReelForge's demos, so it can't ship: start your own episode with "
                         "reelforge new-episode and make the subject yours")
    video = final / f"{spec['id']}.mp4"
    if not video.exists():
        raise SystemExit(f"deliver: {video} not found, run the video step first")
    from studio.publish_check import review_state
    if review_state(final.parent) == "stale":
        raise SystemExit("deliver: the video changed after the review in build/qa/review.md, so that review is of an "
                         "earlier cut. Run --steps qa, look at this cut, and write the review again.")
    qa_file = build / "qa" / "qa.json"
    if not qa_file.exists():
        raise SystemExit("deliver: no QA report, run --steps qa first")
    qa = json.loads(qa_file.read_text(encoding="utf-8"))
    st = video.stat()
    if not qa.get("pass") or qa.get("fingerprint") != f"{st.st_size}:{int(st.st_mtime)}":
        raise SystemExit("deliver: QA failed or is out of date for this video; fix it and rerun --steps qa")
    from studio.publish_check import review_approved
    if not review_approved(build / "qa" / "review.md"):
        raise SystemExit("deliver: write your own review first: build/qa/review.md (picture, sound, facts) "
                         "ending with the verdict APPROVED (or REJECTED: why)")
    dst = completed_dir(spec)
    dst.mkdir(parents=True, exist_ok=True)
    files = [video, final / "cover.jpg", final / "caption.txt", *sorted(final.glob("caption_*.txt"))]
    for f in files:
        if f.exists():
            shutil.copy2(f, dst / f.name)
    print(f"  delivered {sum(f.exists() for f in files)} files to {dst}")


def clean(spec, build):
    """Delete the bulky render frames/stills/previews once the video is safely in 'completed videos'."""
    copy = completed_dir(spec) / f"{spec['id']}.mp4"
    if not copy.exists():
        raise SystemExit("clean: the video isn't in 'completed videos' yet; deliver first")
    freed = 0
    for sub in ("frames", "stills", "preview"):
        d = build / sub
        if d.exists():
            freed += sum(f.stat().st_size for f in d.rglob("*") if f.is_file())
            shutil.rmtree(d)
    print(f"  cleaned render files ({freed / 1e9:.2f} GB freed); scene.blend, series.json, audio and QA kept")


def built_scene(build, spec, step):
    """The stamp of the scene in build/scene.blend, once it's sure that this is still the scene episode.json and the
    scene code describe (None for a scene built before ReelForge kept stamps). Blender renders whatever scene.blend
    holds, so an edit made after the last build would not be in the pictures."""
    if not (build / "scene.blend").exists():
        raise SystemExit(f"{step}: build/scene.blend not found, run --steps build first")
    stamp = framecache.read_stamp(build / framecache.SCENE)
    if stamp is None and any((build / d / framecache.STAMP).exists() for d in ("frames", "stills")):
        raise SystemExit(f"{step}: the last build did not finish, so build/scene.blend is an older scene. "
                         "Run --steps build first.")
    old = framecache.outdated(stamp, spec, config.PIPELINE / "blender") if stamp else []
    if old:
        raise SystemExit(f"{step}: {', '.join(old)} changed after the scene was built, so build/scene.blend "
                         "still shows it as it was. Run --steps build first.")
    return stamp


def line_times(spec, build):
    """The seconds of `--stills lines`: each story line just after it has faded in and just before it goes, each
    cut from both sides (the effect lists its cuts in build/series.json), and the cover's frame. These are where a
    line ends up over the wrong shot, which is cheaper to see on stills than after the render."""
    times = set()
    for start, end, *_ in spec.get("lines", []):
        times |= {min(start + 0.45, end), max(end - 0.2, start)}
    try:
        events = json.loads((build / "series.json").read_text(encoding="utf-8")).get("events")
    except (OSError, ValueError):
        events = None
    for cut in (events.get("cuts", []) if isinstance(events, dict) else []):
        times |= {cut - 0.1, cut + 0.1}
    if spec.get("cover"):
        times.add(spec["cover"]["time"])
    last = (int(round(spec["render_until"] * spec["fps"])) - 1) / spec["fps"]      # the last frame Blender renders
    return sorted({round(t, 2) for t in times if 0 <= t <= last})


def fresh_stills(build, stamp, numbers):
    """Stills are rendered afresh every time: Blender skips a picture that is already on disk, and an old still of a
    scene that has changed since is the last thing to be looking at. Stills left from an older scene go as well."""
    stills = build / "stills"
    older = stamp != framecache.read_stamp(stills / framecache.STAMP)
    for f in stills.glob("s_*.jpg"):
        if older or framecache.number(f) in numbers:
            f.unlink()
    if stamp:
        framecache.write_stamp(stills / framecache.STAMP, stamp)


def render(build, spec, rerender="", keep=False):
    """Render every frame up to render_until into build/frames. Frames already there are kept (Blender skips them),
    which is what lets a stopped render carry on, but only while they show this scene: after the scene is built
    again the step stops and asks what to do with them. It never mixes old and new pictures on its own."""
    fps = spec["fps"]
    total = int(round(spec["render_until"] * fps))
    frames = build / "frames"
    if rerender and keep:
        raise SystemExit("render: --rerender and --keep-frames don't go together (--rerender SECONDS already keeps "
                         "the other frames)")
    stamp = built_scene(build, spec, "render")
    frames.mkdir(exist_ok=True)
    swept = framecache.sweep_empty(frames)
    if swept:
        print(f"  removed {len(swept)} empty or cut-off frames that a stopped render left behind")
    state, what = framecache.frames_state(build)
    if rerender == "all":
        print(f"  deleted {framecache.delete(frames, [(1, float('inf'))])} frames: rendering every frame again")
    elif rerender:
        try:
            spans = framecache.frames_for(rerender, fps, total)
        except ValueError as exc:
            raise SystemExit(f"render: --rerender {exc}") from None
        framecache.delete(frames, spans)
        print(f"  rendering {framecache.describe(spans, fps)} again; the other frames are kept")
    elif state in ("changed", "unstamped"):
        n = len(framecache.present(frames))
        if not keep:
            raise SystemExit(
                f"render: the {n} frames in build/frames {framecache.why(state, what)}.\n"
                "  Blender would reuse them as they are, so say what to do with them:\n"
                "    --rerender all             delete them and render every frame again\n"
                "    --rerender 12-15.5,31-33   render only those seconds again and keep the rest\n"
                "    --keep-frames              keep them all (the change did not touch the picture)")
        print(f"  keeping the {n} frames on disk, as asked")
    elif state == "untracked":
        print("  note: this scene was built by an older ReelForge, so its frames can't be checked against it "
              "(build again to turn the check on)")
    if stamp:
        framecache.write_stamp(frames / framecache.STAMP, stamp)
    # Blender renders in one go and never looks back at a frame it has passed, so check what came out and go back
    # once for anything that is missing before calling it a failure.
    log = build / "render.log"
    for attempt in ("first", "second"):
        todo = framecache.missing(frames, total)
        if not todo:
            break
        if attempt == "second":
            print(f"  {framecache.describe(todo, fps)} did not come out: going back once", flush=True)
        run([config.BLENDER, "-b", build / "scene.blend", "-a"], log=log, progress=render_progress(frames, total))
        framecache.sweep_empty(frames)
    todo = framecache.missing(frames, total)
    if todo:
        if log.exists():
            print(log_tail(log, max(0, log.stat().st_size - 8192)), flush=True)
        verb = "is" if framecache.count(todo) == 1 else "are"
        raise SystemExit(f"render: {framecache.describe(todo, fps)} {verb} still missing after going back once. "
                         "The end of build/render.log should say why.")
    print(f"  all {total} frames are in build/frames")


def render_progress(frames, total):
    """A line for run() to print while Blender renders, counted from the files that have appeared in build/frames:
    how far it is, how long a frame takes and how long the rest will take at that pace."""
    t0, before = time.time(), len(framecache.present(frames))

    def line():
        done = len(framecache.present(frames))
        return framecache.progress(done, total, done - before, time.time() - t0)
    return line


def status(ep, spec):
    """Where an episode stands, one line for each place there was to look: is the scene built and is it still the
    scene episode.json describes, how many frames are rendered and are they this scene's, the mix, the video, QA,
    the review, the copy in completed videos. It only reads."""
    build, final = ep / "build", ep / "final"
    total = int(round(spec["render_until"] * spec["fps"]))

    stamp = framecache.read_stamp(build / framecache.SCENE)
    if not (build / "scene.blend").exists():
        built = "no (run --steps build)"
    elif stamp is None:
        built = "yes, without a stamp (an older ReelForge built it, or the last build did not finish)"
    else:
        old = framecache.outdated(stamp, spec, config.PIPELINE / "blender")
        built = f"yes, but {', '.join(old)} changed since: build again" if old else "yes"

    frames_dir = build / "frames"
    state, what = framecache.frames_state(build)
    frames = f"{len(framecache.present(frames_dir) & set(range(1, total + 1)))} of {total}" + {
        "none": "", "match": ", they match the scene",
        "changed": f", rendered from an older scene (changed since: {', '.join(what) or 'the scene'})",
        "unstamped": ", with no record of the scene they show (it has been built again since)",
        "unbuilt": ", but the last build did not finish",
        "untracked": ", not checked against the scene (it has no stamp)"}[state]
    broken = sum(not framecache.whole(f) for f in frames_dir.glob("f_*.jpg"))
    if broken:
        frames += f"; {broken} empty or cut off (the render step sweeps them)"

    audio = "no"
    if (build / "audio.wav").exists():
        import wave
        with wave.open(str(build / "audio.wav")) as w:
            audio = f"yes, {w.getnframes() / w.getframerate():.1f} s"

    video_file = final / f"{spec['id']}.mp4"
    video = "no"
    if video_file.exists():
        st = video_file.stat()
        video = (f"final/{video_file.name}, {st.st_size / 1e6:.0f} MB, made "
                 f"{time.strftime('%Y-%m-%d %H:%M', time.localtime(st.st_mtime))}")
    qa_file = build / "qa" / "qa.json"
    qa_line = "not run"
    if qa_file.exists():
        import qa
        report = json.loads(qa_file.read_text(encoding="utf-8"))
        fails = [c["check"] for c in report.get("checks", []) if c["status"] == "FAIL"]
        qa_line = "passed" if report.get("pass") else f"FAILED ({', '.join(fails) or 'see build/qa/report.md'})"
        if not video_file.exists() or report.get("fingerprint") != qa.video_fingerprint(video_file):
            qa_line += ", but on an earlier video: run --steps qa"
        elif report.get("pass"):
            qa_line += " on this video"

    from studio.publish_check import review_state
    review = {"approved": "APPROVED", "rejected": "REJECTED", "unreviewed": "not written yet (build/qa/review.md)",
              "stale": "APPROVED, but of an earlier cut: the video changed after it"}[review_state(ep)]

    copy = completed_dir(spec) / video_file.name
    delivered = "no"
    if copy.exists():
        same = video_file.exists() and (copy.stat().st_size, int(copy.stat().st_mtime)) == \
            (video_file.stat().st_size, int(video_file.stat().st_mtime))
        delivered = f"yes, in completed videos/{copy.parent.name}/" if same else \
            f"an earlier cut is in completed videos/{copy.parent.name}/, this one is not"

    rows = [("built", built), ("frames", frames), ("audio", audio), ("video", video), ("QA", qa_line),
            ("review", review), ("delivered", delivered)]
    head = f"{spec['id']}  {' '.join(str(spec.get('title', '')).split())}"
    if spec.get("demo"):
        head += "  (a demo: it can't ship)"
    return "\n".join([head] + [f"  {name:<10} {text}" for name, text in rows])


def check_publish(ep):
    """Confirm ReelForge's Publish tab will list this video, with the caption and hashtags the channel's rules give."""
    from studio import publish_check
    ok, report = publish_check.check(ep)
    print(report)
    if not ok:
        raise SystemExit("check: the video isn't ready in ReelForge's Publish tab (see above)")


def write_captions(spec, final):
    """Per-platform posts in the channel's format: the caption is one short hook line (the episode `title`).
    TikTok/Instagram: caption + hashtags; YouTube: caption as title, hashtags as description; X: caption (hashtags
    only if the channel allows them there). Rules (max hashtags, TikTok's first tag) come from studio.toml."""
    s = config.SETTINGS
    tags = settings.tag_list(spec.get("hashtags", []))
    for problem in config.caption_problems(spec):       # the same rules the lint asks before the render
        raise SystemExit(f"caption: {problem}")
    hook = " ".join(spec["title"].split())
    if len(hook) > config.TITLE_MAX_CHARS:
        print(f"  note: the title is {len(hook)} characters (the channel aims for {config.TITLE_MAX_CHARS} or fewer)")
    out = settings.post_texts(hook, tags, s)
    for f in final.glob("caption_*.txt"):            # platforms switched off since the last run
        if f.stem.split("_", 1)[1] not in out:
            f.unlink()
    for platform, text in out.items():
        (final / f"caption_{platform}.txt").write_text(text, encoding="utf-8")
    # The Publish tab reads final/caption.txt: caption, blank line, hashtags (TikTok's order, so the first tag leads).
    tag_line = " ".join("#" + t for t in settings.platform_tags("tiktok", tags, s))
    (final / "caption.txt").write_text(f"{hook}\n\n{tag_line}\n" if tag_line else f"{hook}\n", encoding="utf-8")
    print(f"  wrote captions for {', '.join(out)} (hook: {len(hook)} chars)")


if __name__ == "__main__":
    main()
