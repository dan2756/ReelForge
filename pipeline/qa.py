"""Quality check for a finished episode. Analyses the FINAL encoded video (what would be posted), not the
intermediate files, and writes build/qa/report.md + qa.json + contact sheets for a visual review.

  python pipeline/qa.py episodes/<id>          (exit code 1 if any check FAILs)
  python pipeline/qa.py episodes/<id> --lint   (before the render: what episode.json and series.json can tell;
                                                with --before-render only what the frames depend on fails it)
  python pipeline/qa.py episodes/<id> --audio  (after the audio step: the sound checks on build/audio.wav)

Checks: file/streams, duration within the channel's length (studio.toml), black and frozen frames outside intended
moments, seamless loop, clipping, loudness (LUFS, loudest 3 s, true peak), unintended silence, static/hiss, loudness
blasts (A-weighted), long samples piled on themselves, key sound hits audible at their moment, story-line
overlaps/wrapping, HUD coverage and contrast, caption rules. Intended exceptions come from the spec (blackout,
glitches with "stutter", the rewind/loop hold) or an optional
"qa": {"black_ok": [[t0, t1]], "frozen_ok": [...], "silent_ok": [...], "pop_ok": [...]} block.
Three more only warn, to say where a review should look: frames that pop (one to four frames unlike the frames
either side), a story line on screen across a cut, the subject mostly behind the HUD or a line.
`make_episode.py --steps deliver` refuses to copy a video whose qa.json is missing, failing or stale.
"""
import json
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config  # noqa: E402

SHEET_W, SHEET_H = 270, 480


def video_fingerprint(path):
    st = path.stat()
    return f"{st.st_size}:{int(st.st_mtime)}"


def probe(path):
    ffprobe = str(Path(config.require_ffmpeg()).with_name("ffprobe" + Path(config.FFMPEG).suffix))
    out = subprocess.run([ffprobe, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
                         check=True, capture_output=True, text=True).stdout
    return json.loads(out)


def in_windows(t, windows, pad=0.0):
    return any(a - pad <= t <= b + pad for a, b in windows)


def runs(mask, times, min_len):
    """[(t_start, t_end, n)] for consecutive True stretches of at least min_len samples."""
    out, start = [], None
    for i, m in enumerate(list(mask) + [False]):
        if m and start is None:
            start = i
        elif not m and start is not None:
            if i - start >= min_len:
                out.append((times[start], times[i - 1], i - start))
            start = None
    return out


class Pops:
    """Finds frames that pop: one to four frames that differ strongly from the frames either side of them, while
    those two look alike. A camera that flips for a frame, a ground that goes black for one, a frame from inside a
    tree. QA's black check needs three black frames in a row, so these used to be found by eye, if at all.
    Feed it every frame in order; `found` is [(index of the first popped frame, how many frames)]."""
    JUMP = 8.0      # mean difference (0-255) between two small frames in a row that counts as a jump

    def __init__(self):
        self.frames, self.jumps = [], []    # the last few frames (int16), and how far each was from the one before
        self.seen = 0
        self.found = []

    def add(self, frame, diff):
        if self.seen and diff > self.JUMP:
            for k in range(1, min(4, len(self.frames) - 1) + 1):
                # the k frames before this one popped if they were jumped into as well, and this frame is back at
                # what the frame before them showed (after a real cut it is not: the old shot is gone)
                if self.jumps[-k] > self.JUMP and \
                        float(np.abs(frame - self.frames[-k - 1]).mean()) < 0.35 * min(diff, self.jumps[-k]):
                    self.found.append((self.seen - k, k))
                    break
        self.frames = (self.frames + [frame])[-6:]
        self.jumps = (self.jumps + [diff if self.seen else 0.0])[-6:]
        self.seen += 1


def _rel_lum(rgb):
    c = rgb.astype(np.float32) / 255.0
    c = np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)
    return c[..., 0] * 0.2126 + c[..., 1] * 0.7152 + c[..., 2] * 0.0722


def hud_line_contrast(band, m, x0, y0, chunk=56):
    """Worst readability of one HUD line in a frame: the luminance ratio (WCAG style, 1..21) between the glyphs and
    the 1-4 px just round them, per 56 px slice of the line. band: the frame's pixels (rows from y0's frame of
    reference), m: the line's glyph coverage placed at (x0, y0). The HUD jitters sideways in glitches, so the
    glyphs are first matched to the brightest spot within +-16 px."""
    from PIL import ImageFilter
    h, w = m.shape
    core = m > 200
    g = band.max(axis=2).astype(np.float32)
    best = (-1.0, x0, y0)
    for dy in range(-3, 4):
        for dx in range(-16, 17):
            xs, ys = x0 + dx, y0 + dy
            if xs < 0 or ys < 0 or xs + w > band.shape[1] or ys + h > band.shape[0]:
                continue
            v = float(g[ys:ys + h, xs:xs + w][core].mean())
            if v > best[0]:
                best = (v, xs, ys)
    _, x0, y0 = best
    L = _rel_lum(band[y0:y0 + h, x0:x0 + w])
    mi = Image.fromarray(((m > 40) * 255).astype(np.uint8))
    ring = (np.asarray(mi.filter(ImageFilter.MaxFilter(9))) > 0) & ~(np.asarray(mi.filter(ImageFilter.MaxFilter(3))) > 0)
    cols = np.where(core.any(0))[0]
    worst = 21.0
    for c0 in range(int(cols.min()), int(cols.max()) + 1, chunk) if len(cols) else []:
        cc, rr = core[:, c0:c0 + chunk], ring[:, c0:c0 + chunk]
        if cc.sum() < 15 or rr.sum() < 15:
            continue
        lc, lr = float(L[:, c0:c0 + chunk][cc].mean()), float(L[:, c0:c0 + chunk][rr].mean())
        worst = min(worst, (max(lc, lr) + 0.05) / (min(lc, lr) + 0.05))
    return worst


def check_hud_contrast(spec, video, R, step=6):
    """The HUD (counter + its label/sub-line) must always be readable, over any background, without a box.
    Every `step`-th frame, each HUD line's glyphs are compared with the pixels just round them.
    Glitch windows and the rewind are exempt (they are meant to be messy). FAIL if any line stays below
    overlay.HUD_MIN_CONTRAST for 0.5 s or more; WARN for single frames (random HUD glitches)."""
    sys.path.insert(0, str(config.PIPELINE / "post"))
    import compose
    import overlay
    if spec.get("hud", {}).get("panel"):
        R.add("HUD readable on every background", "WARN", "boxed HUD (spec hud.panel): not measured")
        return
    ep_dir = video.parent.parent
    series = json.loads((ep_dir / "build" / "series.json").read_text(encoding="utf-8"))
    fps, dur = spec["fps"], spec["duration"]
    exempt = [(g["start"] - 0.2, g["end"] + 0.2) for g in spec.get("glitches", [])]
    if spec.get("rewind"):
        exempt.append((spec["rewind"]["start"] - 0.1, dur))
    if spec.get("blackout"):
        exempt.append(tuple(spec["blackout"]))
    if spec.get("end_card"):
        exempt.append((spec["end_card"]["start"], dur))
    y_top, y_bot = overlay.HUD_Y - 70, overlay.HUD_Y + 290
    bw, bh = config.WIDTH, y_bot - y_top
    proc = subprocess.Popen([config.FFMPEG, "-v", "error", "-i", str(video), "-vf",
                             f"select=not(mod(n\\,{step})),crop={bw}:{bh}:0:{y_top}", "-fps_mode", "passthrough",
                             "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], stdout=subprocess.PIPE)
    masks, samples, k = {}, [], 0
    while True:
        buf = proc.stdout.read(bw * bh * 3)
        if len(buf) < bw * bh * 3:
            break
        idx, k = k * step, k + 1
        t = idx / fps
        if in_windows(t, exempt):
            continue
        hud = compose.hud_text(spec, series, t, idx)
        if not hud:
            continue
        band = np.frombuffer(buf, np.uint8).reshape(bh, bw, 3)
        worst = (21.0, "")
        for name, text, f, size, tr, blur, _ol, dy in overlay.hud_lines(*hud[:3]):
            if not text:
                continue
            key = (text, str(f), size, tr, blur)
            if key not in masks:
                ly = overlay.text_layer(text, f, size, tracking=tr, shadow=0, blur=blur, align="left")
                masks[key] = (np.asarray(ly.getchannel("A")), ly.info["pad"])
            m, pad = masks[key]
            c = hud_line_contrast(band, m, overlay.HUD_X - pad, overlay.HUD_Y + dy - pad - y_top)
            if c < worst[0]:
                worst = (c, f"{name} {text!r}")
        samples.append((t, worst[0], worst[1]))
    proc.wait()
    if not samples:
        R.add("HUD readable on every background", "WARN", "no HUD frames measured")
        return
    low = [s[1] < overlay.HUD_MIN_CONTRAST for s in samples]
    long_runs = runs(low, [s[0] for s in samples], max(2, int(np.ceil(0.5 * fps / step))))
    worst = sorted(samples, key=lambda s: s[1])[:4]
    vals = np.array([s[1] for s in samples])
    detail = (f"{len(samples)} frames, median {np.median(vals):.1f}:1, worst " +
              ", ".join(f"{s[1]:.1f}:1 at {s[0]:.1f}s ({s[2]})" for s in worst) +
              f"; minimum {overlay.HUD_MIN_CONTRAST}:1")
    if long_runs:
        detail = "hard to read at " + ", ".join(f"{a:.1f}-{b:.1f}s" for a, b, _ in long_runs) + "; " + detail
    R.add("HUD readable on every background", "FAIL" if long_runs else ("WARN" if any(low) else "PASS"), detail)


# What `reelforge new-episode` puts in a new episode.json until the hook line is written.
PLACEHOLDER_TITLE = "WRITE THE HOOK LINE HERE"


def lint_spec(spec, series, R):
    """The checks that need only episode.json and build/series.json: the title, the story lines, the HUD's
    chapters, sounds piled on themselves, the cover's frame. They run on the finished video like every other check,
    and also before it exists (lint, below), when a fault costs seconds to fix rather than a render and a compose.
    series is None when nothing has been built yet; a check that needs it is then left out."""
    dur = spec["duration"]
    sys.path.insert(0, str(config.PIPELINE / "post"))
    title = " ".join(str(spec.get("title", "")).split())
    if not title or title.upper().startswith(PLACEHOLDER_TITLE):
        R.add("title", "FAIL", "episode.json still has new-episode's placeholder: write the hook line (it is the "
              "title card and the caption)")
    else:
        R.add("title", "PASS", f"{len(title)} characters")

    lines = spec.get("lines", [])
    problems = [f"{a[2]!r} overlaps {b[2]!r}" for a, b in zip(lines, lines[1:]) if b[0] < a[1] - 1e-6]
    # 27.95 - 26.75 is 1.1999999999999993: a line of exactly 1.2 s is long enough
    problems += [f"{l[2]!r} is shorter than 1.2 s" for l in lines if l[1] - l[0] < 1.2 - 1e-6]
    problems += [f"{l[2]!r} runs past the end" for l in lines if l[1] > dur]
    try:
        import overlay
        fnt = overlay.font(config.FONT_SERIF_ITALIC, 48)
        problems += [f"{l[2]!r} wraps to {len(overlay.wrap(l[2], fnt, overlay.TEXT_MAX_W))} lines"
                     for l in lines if len(overlay.wrap(l[2], fnt, overlay.TEXT_MAX_W)) > 2]
    except Exception as exc:  # pragma: no cover
        problems.append(f"could not measure text: {exc}")
    R.add("story lines", "FAIL" if any("overlaps" in p or "past the end" in p for p in problems)
          else ("WARN" if problems else "PASS"), "; ".join(problems) or f"{len(lines)} lines OK")
    ch = sorted(spec.get("hud", {}).get("chapters", []), key=lambda c: c["from"])
    gaps = [f"{a['to']:.2f}-{b['from']:.2f}s" for a, b in zip(ch, ch[1:]) if b["from"] - a["to"] > 0.05]
    R.add("HUD covers the timeline", "WARN" if gaps else "PASS", ", ".join(gaps) or "no gaps")

    # piled-up long samples: the same sound > 1 s long started again before its earlier copies end
    piles, worst = [], 0
    try:
        from sfx_cache import cached
        starts = {}
        for h in spec.get("audio", {}).get("hits", []):
            if h.get("sound"):
                starts.setdefault(h["sound"], []).append((h["at"], h.get("rate", 1.0)))
        for name, ts in starts.items():
            p = cached(name)
            if not p.exists():
                continue
            with wave.open(str(p)) as w:
                length = w.getnframes() / w.getframerate()
            if length <= 1.0:
                continue
            most = max(sum(1 for t1, r1 in ts if t1 <= t0 < t1 + length / r1) for t0, _r in ts)
            if most > 3:
                piles.append(f"{name} ({length:.1f} s long): {most} copies playing at once")
                worst = max(worst, most)
    except Exception as exc:  # pragma: no cover
        piles.append(f"could not check: {exc}")
    R.add("no piled-up sounds", "FAIL" if worst > 4 else ("WARN" if piles else "PASS"),
          "; ".join(piles) or "no long sample overlaps itself more than 3 times")

    cover = spec.get("cover")
    if cover:
        late = int(round(cover["time"] * spec["fps"])) >= int(round(spec["render_until"] * spec["fps"]))
        R.add("cover frame", "FAIL" if late else "PASS",
              f"cover.time {cover['time']} s is past render_until ({spec['render_until']} s): the cover would be black"
              if late else f"the frame at {cover['time']} s")

    # ---- what reviews keep finding and no rule can call wrong. These only ever warn: they say where to look.
    events = series.get("events") if series else None
    cuts = events.get("cuts") if isinstance(events, dict) else None
    if lines and isinstance(cuts, list):
        across = [f"{l[2]!r} ({l[0]:g}-{l[1]:g} s) is on screen across the cut at {c:g} s"
                  for l in lines for c in cuts if l[0] + 0.15 < c < l[1] - 0.15]
        R.add("story lines stay on one shot", "WARN" if across else "PASS",
              "; ".join(across) or f"no line is on screen across one of the {len(cuts)} cuts")
    if series and series.get("subject_box"):
        behind = subject_behind_text(spec, series["subject_box"])
        R.add("the subject is clear of the text", "WARN" if behind else "PASS",
              "; ".join(behind[:8]) + (f" and {len(behind) - 8} more" if len(behind) > 8 else "")
              if behind else "never more than half covered by the HUD or a line")


def subject_behind_text(spec, boxes):
    """Where the subject (series "subject_box": its screen box for each frame) is mostly covered by the HUD's readout
    or by a story line for 0.3 s or more, as ["12.4-13.1 s behind the HUD", ...]. It counts when half of its box is
    under the text, so a subject big enough to fill the frame is never "behind" anything. With layout.avoid_subject
    compose moves each line off the subject itself, and the lines are left out."""
    import overlay
    fps, layout = spec["fps"], spec.get("layout", {})
    hud = spec.get("hud") or {}
    hud_box = (overlay.HUD_X, overlay.HUD_Y + 40, overlay.HUD_X + 560, overlay.HUD_Y + 215)
    line_y = layout.get("line_y", overlay.LINE_Y)
    try:
        fnt = overlay.font(config.FONT_SERIF_ITALIC, 48)
    except OSError:
        fnt = None

    def line_box(t, text, opt):
        y = opt[0]["y"] if opt and "y" in opt[0] else \
            (float(np.interp(t, *zip(*line_y))) if isinstance(line_y, list) else line_y)
        rows = overlay.wrap(text, fnt, overlay.TEXT_MAX_W) if fnt else [text]
        half_w = (max(fnt.getlength(r) for r in rows) if fnt else overlay.TEXT_MAX_W) / 2
        return (config.WIDTH / 2 - half_w, y - 34 * len(rows), config.WIDTH / 2 + half_w, y + 34 * len(rows))

    hidden = {}         # what covers it -> one flag per frame
    for i, b in enumerate(boxes):
        if not b or b[2] <= b[0] or b[3] <= b[1]:
            continue
        t = i / fps
        over = [("the HUD", hud_box)] if any(c["from"] <= t < c["to"] for c in hud.get("chapters", [])) else []
        if not layout.get("avoid_subject"):
            over += [(f"the line {text!r}", line_box(t, text, opt))
                     for start, end, text, *opt in spec.get("lines", []) if start <= t < end]
        for what, r in over:
            w, h = min(b[2], r[2]) - max(b[0], r[0]), min(b[3], r[3]) - max(b[1], r[1])
            if w > 0 and h > 0 and w * h >= 0.5 * (b[2] - b[0]) * (b[3] - b[1]):
                hidden.setdefault(what, [False] * len(boxes))[i] = True
    times = [i / fps for i in range(len(boxes))]
    return [f"{a:.1f}-{z:.1f} s behind {what}" for what, flags in hidden.items()
            for a, z, _ in runs(flags, times, max(2, int(round(0.3 * fps))))]


def check_audio(x, sr, spec, R):
    """The sound checks, on a mix as float samples (n, 2): the finished video's audio track, or build/audio.wav
    straight after the audio step, when a fault still costs seconds to fix. Returns the loudness per second, plain
    and as heard (A-weighted), for the report."""
    dur = spec["duration"]
    qa = spec.get("qa", {})
    peak = float(np.abs(x).max()) if len(x) else 0.0
    clipped = int((np.abs(x) >= 0.999).sum())
    R.add("no clipping", "PASS" if clipped == 0 and peak < 0.99 else "FAIL",
          f"peak {20 * np.log10(peak + 1e-12):.1f} dBFS, {clipped} clipped samples")
    m = x.mean(1)
    rms = float(np.sqrt(np.mean(m ** 2)) + 1e-12)
    # master loudness: the channel's target (config.TARGET_LUFS, from studio.toml)
    try:
        from post.loudness import measure
        L = measure(x, sr)
        tgt = config.TARGET_LUFS
        R.add("loudness (LUFS)", "FAIL" if L["I"] > config.MAX_LUFS else ("WARN" if abs(L["I"] - tgt) > 1.0 else "PASS"),
              f"integrated {L['I']:.1f} LUFS (target {tgt:.1f}, FAIL above {config.MAX_LUFS:.1f}); "
              f"overall RMS {20 * np.log10(rms):.1f} dB")
        R.add("loudest moment", "FAIL" if L["S_max"] > config.MAX_SHORT_TERM_LUFS else "PASS",
              f"loudest 3 s {L['S_max']:.1f} LUFS (FAIL above {config.MAX_SHORT_TERM_LUFS:.1f})")
        R.add("true peak", "FAIL" if L["TP"] > config.MAX_TRUE_PEAK else "PASS",
              f"{L['TP']:.1f} dBTP (FAIL above {config.MAX_TRUE_PEAK:.1f})")
    except Exception as exc:  # pragma: no cover
        R.add("loudness (LUFS)", "FAIL", f"could not measure: {exc}")
    R.add("no DC offset", "PASS" if abs(float(m.mean())) < 0.01 else "WARN", f"mean {float(m.mean()):.4f}")
    win = int(0.5 * sr)
    seg_db = [20 * np.log10(np.sqrt(np.mean(m[i:i + win] ** 2)) + 1e-12) for i in range(0, len(m) - win + 1, win // 2)]
    seg_t = [i * 0.25 for i in range(len(seg_db))]
    silent_ok = [tuple(spec["blackout"])] if spec.get("blackout") else []
    silent_ok += [tuple(s) for s in qa.get("silent_ok", [])]
    silences = [t for t, db in zip(seg_t, seg_db) if db < config.TARGET_LUFS - 40 and not in_windows(t, silent_ok, 0.25)]
    R.add("no unintended silence", "FAIL" if silences else "PASS",
          (", ".join(f"{t:.2f}s" for t in silences[:10]) + (" ..." if len(silences) > 10 else "")) or "none")
    per_sec = []
    for s in range(int(len(m) / sr)):
        seg = m[s * sr:(s + 1) * sr]
        per_sec.append(round(float(20 * np.log10(np.sqrt(np.mean(seg ** 2)) + 1e-12)), 1))

    # ---- static and blasts. Two ways a mix ends up sounding like "super loud static" to a listener, two checks,
    # calibrated on real mixes that had the problem against ones that were approved:
    #  hiss   a synthesized surf bed (white noise) under a whole opening. Per second, the share of the energy that
    #         is white-noise-like in the 1-4 kHz and 4-16 kHz bands (band share x spectral flatness) is <= 0.07 in
    #         approved videos and 0.19-0.30 for fake surf/crowd beds; above 0.12 counts as a hiss second.
    #  blast  13 overlapping copies of a 5 s 'hud_blip' sample (Kenney computerNoise) piled up over 9 s: only
    #         +2.6 dB in plain RMS, but +9 dB louder than the video's median to the ear (A-weighted). Approved
    #         videos peak at +4.0 to +4.7 dB. The overflow (glitch windows) and the rewind are exempt.
    noisy_ok = [(g["start"] - 0.5, g["end"] + 0.5) for g in spec.get("glitches", [])]
    if spec.get("rewind"):
        noisy_ok.append((spec["rewind"]["start"] - 0.5, dur))
    fa = np.fft.rfftfreq(sr, 1.0 / sr)
    f2 = np.maximum(fa, 1.0) ** 2
    a_w2 = ((12194.0 ** 2 * f2 ** 2) / ((f2 + 20.6 ** 2) * np.sqrt((f2 + 107.7 ** 2) * (f2 + 737.9 ** 2)) * (f2 + 12194.0 ** 2))
            / 0.7943) ** 2
    hann = np.hanning(sr)
    hiss_s, a_db = [], []
    for s in range(int(len(m) / sr)):
        seg = m[s * sr:(s + 1) * sr]
        P = np.abs(np.fft.rfft(seg * hann)) ** 2 + 1e-14
        a_db.append(round(float(10 * np.log10((P * a_w2).sum() / P.sum() * np.mean(seg ** 2) + 1e-14)), 1))
        if np.sqrt(np.mean(seg ** 2)) < 10 ** ((config.TARGET_LUFS - 30) / 20) or in_windows(s + 0.5, noisy_ok):
            continue
        tot = P[fa > 60].sum()
        score = 0.0
        for lo, hi in ((1000, 4000), (4000, 16000)):
            band = P[(fa >= lo) & (fa < hi)]
            score += band.sum() / tot * float(np.exp(np.mean(np.log(band))) / np.mean(band))
        if score > 0.12:
            hiss_s.append(f"{s}s ({score:.2f})")
    R.add("no static or hiss", "FAIL" if len(hiss_s) >= 2 else ("WARN" if hiss_s else "PASS"),
          ("hiss-like seconds: " + ", ".join(hiss_s[:10])) if hiss_s else
          "no white-noise-like seconds (presence-band hiss score <= 0.12 everywhere)")
    t_end = min(len(a_db), int(min((g["start"] for g in spec.get("glitches", []) if g["start"] > 5.0), default=dur)))
    if t_end >= 6:
        a_arr = np.array(a_db[:t_end])
        med = float(np.median(a_arr))
        w3 = np.convolve(a_arr, np.ones(3) / 3, mode="valid")
        k = int(np.argmax(w3))
        jump = float(w3[k] - med)
        R.add("no loudness blasts", "FAIL" if jump > 8.0 else ("WARN" if jump > 6.5 else "PASS"),
              f"loudest 3 s ({k}-{k + 3} s) is +{jump:.1f} dB over the median as heard (A-weighted); "
              "limit +6.5 (warn) / +8 (fail)")
    cue_lines, cue_fail = [], 0
    env = np.sqrt(np.convolve(m ** 2, np.ones(int(0.02 * sr)) / (0.02 * sr), mode="same"))
    for hit in spec.get("audio", {}).get("hits", []):
        if hit.get("align") != "peak" or hit.get("gain", 1.0) < 0.6:
            continue
        t = hit["at"]
        i0, i1 = int(max(0, t - 0.3) * sr), int(min(len(m) / sr, t + 0.3) * sr)
        b0, b1 = int(max(0, t - 2.0) * sr), int(max(0, t - 0.5) * sr)
        near = float(env[i0:i1].max()) if i1 > i0 else 0.0
        before = float(np.median(env[b0:b1])) if b1 > b0 else 1e-6
        lift = 20 * np.log10((near + 1e-9) / (before + 1e-9))
        okc = lift >= 4.0
        name = hit.get("sound") or f"synth {hit.get('synth')}"     # a hit is a sample or a synth
        cue_fail += 0 if okc else 1
        cue_lines.append(f"{name} @ {t:.2f}s: +{lift:.1f} dB over the 2 s before ({'ok' if okc else 'NOT AUDIBLE'})")
    if cue_lines:
        R.add("key sound hits audible on cue", "WARN" if cue_fail else "PASS", "; ".join(cue_lines))

    return per_sec, a_db


# The lint rows that are about what the scene gives: its series, and how far it is rendered. Putting one of them
# right can mean building or rendering again, so they stop a render before it starts. The other rows are about the
# text and the sound, which are worked on while the frames render and never cost one.
RENDER_ROWS = {"HUD numbers", "cover frame"}


def lint(ep_dir):
    """Before the render (`qa.py <episode> --lint`, which make_episode runs at the end of the build step and before
    the render): lint_spec, then what would otherwise stop a later step halfway: a HUD number that isn't in
    build/series.json, a sound that isn't in the manifest, a caption that breaks the channel's rules. -> Report"""
    spec = json.loads((ep_dir / "episode.json").read_text(encoding="utf-8"))
    series_file = ep_dir / "build" / "series.json"
    series = json.loads(series_file.read_text(encoding="utf-8")) if series_file.exists() else None
    R = Report()
    lint_spec(spec, series, R)

    # What would stop the audio and video steps halfway, asked while it is still cheap.
    hud = spec.get("hud") or {}
    if hud and series is not None:
        # compose.py fills each chapter's text from these per frame; a missing one stops the video step midway
        sys.path.insert(0, str(config.PIPELINE / "post"))
        import compose
        names = hud.get("vars", {})
        bad = [f"{k}: no format called {v.get('format')!r}" for k, v in names.items()
               if v.get("format") not in compose.FORMATS]
        bad += [f"{k}: build/series.json has no series {v.get('series')!r}" for k, v in names.items()
                if not isinstance(series.get(v.get("series")), list)]
        for c in hud.get("chapters", []):
            try:
                for part in ("label", "value", "sub"):
                    c[part].format(**dict.fromkeys(names, 0))
            except (KeyError, IndexError, ValueError) as exc:
                bad.append(f"the chapter from {c.get('from')} s: {type(exc).__name__} {exc} in its {part}")
        R.add("HUD numbers", "FAIL" if bad else "PASS",
              "; ".join(bad) or f"{len(names)} variables, all in build/series.json")

    cues = spec.get("audio", {})
    if cues.get("mixer") == "samples":
        from sfx_cache import SFX, cue_sounds
        known = json.loads((SFX / "manifest.json").read_text(encoding="utf-8"))["sounds"]
        unknown = sorted(cue_sounds(cues) - set(known))
        R.add("sounds in the cue sheet", "FAIL" if unknown else "PASS",
              ("not in assets/sfx/manifest.json: " + ", ".join(unknown)) if unknown
              else f"{len(cue_sounds(cues))} sounds, all in the manifest")

    problems = config.caption_problems(spec)
    # A demo is built under whatever rules the channel has (doctor --render-test builds the duck), and it can't
    # ship: its hashtags breaking those rules is no reason to stop a build. The caption step still stops on them.
    demo = bool(spec.get("demo"))
    R.add("caption rules (from episode.json)", "PASS" if not problems else ("WARN" if demo else "FAIL"),
          "; ".join(problems) + (" (a demo: only its caption step stops on this)" if problems and demo else "")
          or "the title and hashtags fit the channel's rules")
    return R


def audio_check(ep_dir):
    """Straight after the audio step: the sound checks on build/audio.wav, so a key hit nobody can hear or a hissing
    bed stops `post` in seconds instead of after the video is composed. -> Report"""
    spec = json.loads((ep_dir / "episode.json").read_text(encoding="utf-8"))
    with wave.open(str(ep_dir / "build" / "audio.wav")) as w:
        sr = w.getframerate()
        x = np.frombuffer(w.readframes(w.getnframes()), "<i2").reshape(-1, w.getnchannels()).astype(np.float32)
    R = Report()
    seconds = len(x) / sr
    R.add("audio length", "PASS" if abs(seconds - spec["duration"]) <= 0.1 else "FAIL",
          f"{seconds:.2f} s (the video is {spec['duration']} s)")
    check_audio(x / 32768.0, sr, spec, R)
    return R


class Report:
    def __init__(self):
        self.checks = []

    def add(self, name, status, detail):
        self.checks.append({"check": name, "status": status, "detail": detail})

    @property
    def passed(self):
        return all(c["status"] != "FAIL" for c in self.checks)


def check_captions(spec, final, R):
    """Check the generated posts against the channel's caption rules."""
    cap = {p: (final / f"caption_{p}.txt") for p in config.PLATFORMS}
    cap_problems = [f"caption_{p}.txt missing" for p, f in cap.items() if not f.exists()]
    if not (final / "caption.txt").exists():
        cap_problems.append("caption.txt (read by the Publish tab) missing")
    n_max, first = config.MAX_HASHTAGS, config.TIKTOK_FIRST_TAG
    if not cap_problems:
        if n_max > 0 and "tiktok" in cap and first:
            tags = [w for w in cap["tiktok"].read_text(encoding="utf-8").split() if w.startswith("#")]
            if not tags or tags[0].lower() != "#" + first.lower():
                cap_problems.append(f"TikTok's first hashtag isn't #{first}")
        for p, f in cap.items():
            k = f.read_text(encoding="utf-8").count("#")
            if k > n_max:
                cap_problems.append(f"{p} has {k} hashtags (max {n_max})")
        if "x" in cap and not config.HASHTAGS_ON_X and "#" in cap["x"].read_text(encoding="utf-8"):
            cap_problems.append("X post contains a hashtag")
        if "youtube" in cap and \
                cap["youtube"].read_text(encoding="utf-8").splitlines()[0].strip() != " ".join(spec["title"].split()):
            cap_problems.append("YouTube title isn't the episode title")
    rules = [f"max {n_max}"] + ([f"#{first} first on TikTok"] if n_max > 0 and first and "tiktok" in cap else []) + \
        (["none on X"] if "x" in cap and not config.HASHTAGS_ON_X else [])
    R.add("caption rules", "FAIL" if cap_problems else "PASS", "; ".join(cap_problems) or ", ".join(rules))


def check_episode(ep_dir):
    spec = json.loads((ep_dir / "episode.json").read_text(encoding="utf-8"))
    fps, dur = spec["fps"], spec["duration"]
    final = ep_dir / "final"
    video = final / f"{spec['id']}.mp4"
    qa_dir = ep_dir / "build" / "qa"
    qa_dir.mkdir(parents=True, exist_ok=True)
    R = Report()
    if not video.exists():
        R.add("video file", "FAIL", f"{video} missing")
        return spec, R, qa_dir, video
    qa = spec.get("qa", {})

    # ---- file and streams
    info = probe(video)
    v = next((s for s in info["streams"] if s["codec_type"] == "video"), None)
    a = next((s for s in info["streams"] if s["codec_type"] == "audio"), None)
    vdur = float(info["format"]["duration"])
    size_mb = int(info["format"]["size"]) / 1e6
    ok = v and v["codec_name"] == "h264" and (v["width"], v["height"]) == (1080, 1920) and \
        v["r_frame_rate"] == f"{fps}/1" and v.get("pix_fmt") == "yuv420p"
    R.add("video stream", "PASS" if ok else "FAIL",
          f"{v and v['codec_name']} {v and v['width']}x{v and v['height']} {v and v['r_frame_rate']} {v and v.get('pix_fmt')}")
    ok = a and a["codec_name"] == "aac" and int(a["channels"]) == 2 and int(a["sample_rate"]) == config.SAMPLE_RATE
    R.add("audio stream", "PASS" if ok else "FAIL",
          f"{a and a['codec_name']} {a and a['channels']}ch {a and a['sample_rate']} Hz" if a else "no audio track")
    lo, hi = config.MIN_SECONDS, config.MAX_SECONDS
    why = "; TikTok's Creator Rewards needs over a minute" if lo > 60 else ""
    R.add("duration", "PASS" if abs(vdur - dur) <= 0.1 and lo - 0.5 <= vdur <= hi + 0.5 else "FAIL",
          f"{vdur:.2f} s (spec {dur} s; the channel's length is {lo:g}-{hi:g} s{why})")
    if a:
        adur = float(a.get("duration", vdur))
        R.add("audio length matches video", "PASS" if abs(adur - vdur) <= 0.1 else "FAIL",
              f"audio {adur:.2f} s vs video {vdur:.2f} s")
    R.add("file size", "PASS" if size_mb < 250 else "WARN", f"{size_mb:.0f} MB")

    # ---- picture: decode every frame small
    w, h = SHEET_W, SHEET_H
    proc = subprocess.Popen([config.FFMPEG, "-v", "error", "-i", str(video), "-vf", f"scale={w}:{h}",
                             "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], stdout=subprocess.PIPE)
    luma_mean, luma_std, diffs, thumbs = [], [], [], {}
    pops = Pops()
    first = prev = last = None
    idx = 0
    sheet_every = max(1, int(round(2.0 * fps)))
    while True:
        buf = proc.stdout.read(w * h * 3)
        if len(buf) < w * h * 3:
            break
        f = np.frombuffer(buf, np.uint8).reshape(h, w, 3)
        y = f.astype(np.float32) @ np.array([0.299, 0.587, 0.114], np.float32)
        luma_mean.append(float(y.mean()))
        luma_std.append(float(y.std()))
        diffs.append(float(np.abs(f.astype(np.int16) - prev).mean()) if prev is not None else 99.0)
        if idx % sheet_every == 0:
            thumbs[idx] = f.copy()
        if first is None:
            first = f.astype(np.int16)
        prev = f.astype(np.int16)
        pops.add(prev, diffs[-1])
        last = prev
        idx += 1
    proc.wait()
    n = idx
    times = [i / fps for i in range(n)]
    R.add("frame count", "PASS" if abs(n - round(dur * fps)) <= 2 else "FAIL", f"{n} frames decoded")

    black_ok = [tuple(spec["blackout"])] if spec.get("blackout") else []
    black_ok += [tuple(x) for x in qa.get("black_ok", [])]
    end_card = spec.get("end_card")
    if end_card:            # the end card fades in from black over 0.6 s (compose.py), then holds still by design
        black_ok.append((end_card["start"] - 0.05, end_card["start"] + 0.6))
    black = [(m < 4 and s < 3) for m, s in zip(luma_mean, luma_std)]
    bad_black = [r for r in runs(black, times, 3) if not (in_windows(r[0], black_ok, 0.05) and in_windows(r[1], black_ok, 0.05))]
    R.add("no unintended black frames", "FAIL" if bad_black else "PASS",
          ", ".join(f"{a0:.2f}-{a1:.2f}s" for a0, a1, _ in bad_black) or "none")

    frozen_ok = [(g["start"], g["end"]) for g in spec.get("glitches", []) if g.get("stutter")]
    rw = spec.get("rewind")
    if rw and spec.get("loop_to_start"):
        frozen_ok.append((rw["end"] - 0.05, dur))
    frozen_ok += [tuple(x) for x in qa.get("frozen_ok", [])]
    if spec.get("blackout"):        # a blackout is black and still on purpose
        frozen_ok.append(tuple(spec["blackout"]))
    if end_card:
        frozen_ok.append((end_card["start"], dur))
    frozen = [d < 0.25 for d in diffs]
    bad_frozen = [r for r in runs(frozen, times, int(0.5 * fps))
                  if not (in_windows(r[0], frozen_ok, 0.1) and in_windows(r[1], frozen_ok, 0.1))]
    R.add("no frozen stretches", "FAIL" if bad_frozen else "PASS",
          ", ".join(f"{a0:.2f}-{a1:.2f}s" for a0, a1, _ in bad_frozen) or "none")
    if spec.get("loop_to_start") and first is not None:
        mae = float(np.abs(first - last).mean())
        R.add("seamless loop (last frame = first frame)", "PASS" if mae < 2.5 else "FAIL", f"mean difference {mae:.2f}")
    # frames that pop: a warning that says where to look, never a FAIL (a glitch, a flash and the rewind are meant
    # to jump, and so is anything listed in "qa": {"pop_ok": [[t0, t1]]})
    pop_ok = [(g["start"] - 0.2, g["end"] + 0.2) for g in spec.get("glitches", [])]
    pop_ok += [(f["at"] - f.get("rise", 0.05) - 0.1, f["at"] + 3 * f.get("fall", 0.3)) for f in spec.get("flashes", [])]
    pop_ok += [(rw["start"] - 0.1, dur)] if rw else []
    pop_ok += [tuple(spec["blackout"])] if spec.get("blackout") else []
    pop_ok += [tuple(x) for x in qa.get("pop_ok", [])]
    popped = [(i, k) for i, k in pops.found if not in_windows(i / fps, pop_ok)]
    R.add("no frames that pop", "WARN" if popped else "PASS",
          ("look at " + ", ".join(f"{i / fps:.2f} s ({k} frame{'s' if k > 1 else ''})" for i, k in popped[:12]) +
           (f" and {len(popped) - 12} more" if len(popped) > 12 else "") +
           ": they differ from the frames either side, which look alike") if popped else "none")

    # contact sheets for the visual review
    keys = sorted(thumbs)
    per = 12
    sheets = []
    for s in range(0, len(keys), per):
        chunk = keys[s:s + per]
        sheet = Image.new("RGB", (4 * w, 3 * h), "black")
        d = ImageDraw.Draw(sheet)
        for k, fi in enumerate(chunk):
            x, y0 = (k % 4) * w, (k // 4) * h
            sheet.paste(Image.fromarray(thumbs[fi]), (x, y0))
            d.rectangle([x, y0, x + 64, y0 + 18], fill=(200, 0, 0))
            d.text((x + 4, y0 + 3), f"{fi / fps:5.1f}s", fill="white")
        p = qa_dir / f"sheet_{len(sheets) + 1}.jpg"
        sheet.save(p, quality=88)
        sheets.append(p.name)

    # ---- sound: the encoded audio track (a video encoded without one has nothing to measure)
    per_sec, a_db = [], []
    if a:
        pcm = subprocess.run([config.FFMPEG, "-v", "error", "-i", str(video), "-vn", "-f", "f32le", "-ac", "2", "-ar",
                              str(config.SAMPLE_RATE), "-"], check=True, capture_output=True).stdout
        per_sec, a_db = check_audio(np.frombuffer(pcm, np.float32).reshape(-1, 2), config.SAMPLE_RATE, spec, R)

    # ---- story text, HUD, cue sheet, cover frame (the checks the build and render steps also run), then captions
    series_file = ep_dir / "build" / "series.json"
    lint_spec(spec, json.loads(series_file.read_text(encoding="utf-8")) if series_file.exists() else None, R)
    if spec.get("hud"):
        check_hud_contrast(spec, video, R)
    check_captions(spec, final, R)

    # ---- not one of ReelForge's demos: everyone has those, so a demo (or a copy with its hook) would be a repost
    demo_titles = set()
    for ex in (config.ROOT / "examples").glob("*/episode.json"):
        try:
            demo_titles.add(" ".join(json.loads(ex.read_text(encoding="utf-8"))["title"].lower().split()))
        except (OSError, ValueError, KeyError):
            pass
    if spec.get("demo"):
        R.add("your own video, not a demo", "FAIL", "this is one of ReelForge's demos (it carries a DEMO watermark): "
              "start your own with reelforge new-episode and make the subject yours")
    elif " ".join(spec["title"].lower().split()) in demo_titles:
        R.add("your own video, not a demo", "FAIL", "the title is a demo's: write your own hook and subject")
    else:
        R.add("your own video, not a demo", "PASS", "not a demo")

    result = {"episode": spec["id"], "pass": R.passed, "video": video.name, "fingerprint": video_fingerprint(video),
              "checks": R.checks, "sheets": sheets, "loudness_per_second_db": per_sec,
              "loudness_per_second_dba": a_db}
    (qa_dir / "qa.json").write_text(json.dumps(result, indent=1), encoding="utf-8")
    md = [f"# QA: {spec['id']}  ({'PASS' if R.passed else 'FAIL'})", "",
          "| Check | Result | Detail |", "|---|---|---|"]
    md += [f"| {c['check']} | {c['status']} | {c['detail']} |" for c in R.checks]
    md += ["", "Loudness per second (dB): " + " ".join(f"{s}:{v}" for s, v in enumerate(per_sec)), "",
           "Loudness per second as heard (dB, A-weighted): " + " ".join(f"{s}:{v}" for s, v in enumerate(a_db)), "",
           "Contact sheets (every 2 s): " + ", ".join(sheets),
           "", "**Still required:** look at every contact sheet and the story/facts before delivering."]
    (qa_dir / "report.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    return spec, R, qa_dir, video


def main():
    ep = Path(next(a for a in sys.argv[1:] if not a.startswith("--"))).resolve()
    if "--lint" in sys.argv:
        R, verdict = lint(ep), ("  lint passed", "  lint FAILED: fix episode.json before rendering")
    elif "--audio" in sys.argv:
        R, verdict = audio_check(ep), ("  sound check passed", "  sound check FAILED: fix the mix before the video")
    else:
        spec, R, qa_dir, video = check_episode(ep)
        verdict = [f"  QA {v}: report {qa_dir / 'report.md'}" for v in ("PASSED", "FAILED")]
    for c in R.checks:
        print(f"  [{c['status']}] {c['check']}: {c['detail']}")
    failed = {c["check"] for c in R.checks if c["status"] == "FAIL"}
    if "--before-render" in sys.argv and failed and not failed & RENDER_ROWS:
        # the render is often started again, or left running, while the text and the sound are still being written
        print("  lint: the FAILs above are in the text or the sound, which no frame depends on, so the render goes "
              "on. Fix them before --steps post.")
        sys.exit(0)
    print(verdict[0 if R.passed else 1])
    sys.exit(0 if R.passed else 1)


if __name__ == "__main__":
    main()
