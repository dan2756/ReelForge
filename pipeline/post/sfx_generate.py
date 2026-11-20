"""Synthesize a few short, tonal sound effects into assets/sfx/generated/*.wav and register them in the manifest
(they are then cached like any other sound).

Only short tonal accents belong here. Never synthesize ambience or long effects out of noise (surf, crowds, rain,
wind, engines): surf and crowd beds made this way were heard as "super loud static". Beds come from real
recordings in assets/sfx; qa.py fails a video that has stretches of static.

  python pipeline/post/sfx_generate.py          # writes any that are missing
  python pipeline/post/sfx_generate.py --force

Sounds:
  coins         2 s: a spray of coins clinking
  kaching       1.2 s: cash register (drawer clunk + bell)
  shimmer       1.6 s: a bright rising magic shimmer
  shutter       0.2 s: a camera shutter click
  pulse_bed     8 s loop: a light, upbeat bed for 2D voiceover videos (soft kick, plucked chords, 120 bpm, tonal only)
  calm_bed      8 s loop: a calm pad for 2D voiceover videos (slow chords, no drums)
  bubble_pop    0.3 s: a round, tonal pop for something appearing
  rise_tone     1.5 s: a tonal riser (rising chord, no noise) into a big moment
"""
import json
import sys
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402

SR = config.SAMPLE_RATE
SFX = config.ROOT / "assets" / "sfx"
OUT = SFX / "generated"


def _lp(x, cutoff):
    f = np.fft.rfftfreq(len(x), 1.0 / SR)
    return np.fft.irfft(np.fft.rfft(x) / np.sqrt(1 + (f / max(cutoff, 1.0)) ** 4), n=len(x))


def _bp(x, lo, hi):
    f = np.fft.rfftfreq(len(x), 1.0 / SR)
    X = np.fft.rfft(x)
    g = 1.0 / np.sqrt(1 + (f / max(hi, 1.0)) ** 4) * (1.0 - 1.0 / np.sqrt(1 + (f / max(lo, 1.0)) ** 4))
    return np.fft.irfft(X * g, n=len(x))



def _norm(x, peak=0.9):
    return x / (np.max(np.abs(x)) + 1e-9) * peak


def _st(m, width=0.0, rng=None):
    if width and rng is not None:
        d = int(0.012 * SR)
        r = np.roll(m, d)
        return np.stack([m * (1 - width) + r * width, m], 1)
    return np.stack([m, m], 1)


def ping(f, dur, rng, decay=9.0):
    n = int(dur * SR)
    t = np.arange(n) / SR
    s = (np.sin(2 * np.pi * f * t) + 0.6 * np.sin(2 * np.pi * f * 2.76 * t + 1.0) * np.exp(-t * 4)
         + 0.35 * np.sin(2 * np.pi * f * 5.4 * t) * np.exp(-t * 7))
    return s * np.minimum(1.0, t / 0.0015) * np.exp(-t * decay)


def coins(rng, dur=2.0):
    n = int(dur * SR)
    m = np.zeros(n)
    for _ in range(60):
        t0 = rng.uniform(0.0, 1.3) ** 1.6
        f = rng.uniform(2600, 6200)
        p = ping(f, 0.5, rng, rng.uniform(10, 22)) * rng.uniform(0.2, 1.0)
        i = int(t0 * SR)
        m[i:i + len(p)] += p[: n - i]
    return _st(_norm(m, 0.85), 0.4, rng)


def kaching(rng, dur=1.2):
    n = int(dur * SR)
    t = np.arange(n) / SR
    clunk = _lp(rng.standard_normal(n), 1800.0) * np.exp(-t * 60.0) * 1.2
    clunk += np.sin(2 * np.pi * 120 * t) * np.exp(-t * 30.0) * 0.6
    bell = np.zeros(n)
    t1 = 0.06
    for f, a, d in ((2100.0, 1.0, 3.2), (3420.0, 0.6, 4.0), (5230.0, 0.35, 6.0), (1040.0, 0.25, 2.5)):
        bell += a * np.sin(2 * np.pi * f * np.clip(t - t1, 0, None)) * np.exp(-np.clip(t - t1, 0, None) * d) * (t > t1)
    return _st(_norm(clunk + bell * 0.8, 0.9))


def shimmer(rng, dur=1.6):
    n = int(dur * SR)
    t = np.arange(n) / SR
    u = t / dur
    m = np.zeros(n)
    for _ in range(40):
        f = rng.uniform(1800, 7000) * (1 + 0.5 * u)
        ph = np.cumsum(2 * np.pi * f / SR)
        trem = 0.5 + 0.5 * np.sin(2 * np.pi * rng.uniform(8, 25) * t + rng.uniform(0, 6))
        m += np.sin(ph) * trem * rng.uniform(0.2, 1.0)
    env = np.sin(np.pi * u) ** 1.5 * (0.4 + 0.6 * u)
    return _st(_norm(m * env, 0.7), 0.5, rng)


def shutter(rng, dur=0.2):
    n = int(dur * SR)
    t = np.arange(n) / SR
    m = np.zeros(n)
    for t0, a in ((0.0, 1.0), (0.055, 0.7)):
        i = int(t0 * SR)
        k = n - i
        tt = np.arange(k) / SR
        click = _bp(rng.standard_normal(k), 1500.0, 9000.0) * np.exp(-tt * 140.0) * a
        click += np.sin(2 * np.pi * 900 * tt) * np.exp(-tt * 90.0) * 0.4 * a
        m[i:] += click
    return _st(_norm(m, 0.9))


BPM = 120
CHORDS = ((57, 60, 64), (53, 57, 60), (48, 52, 55), (55, 59, 62))      # Am F C G, one bar each (MIDI notes)


def _hz(note):
    return 440.0 * 2 ** ((note - 69) / 12)


def _soft_saw(f, t, n_harm=8):
    return sum(np.sin(2 * np.pi * f * k * t) / k * (0.85 ** k) for k in range(1, n_harm + 1))


def pulse_bed(rng, dur=8.0):
    """Four bars at 120 bpm (exactly 8 s, so it loops in time): a sine kick on every beat, plucked chord tones on
    the eighths, a soft pad underneath. Sine and soft-saw tones only: nothing made of noise."""
    n = int(dur * SR)
    t = np.arange(n) / SR
    beat = 60.0 / BPM
    m = np.zeros(n)
    for bar, chord in enumerate(CHORDS):
        b0 = bar * 4 * beat
        i0, i1 = int(b0 * SR), int((b0 + 4 * beat) * SR)
        tt = t[i0:i1] - b0
        pad = sum(_soft_saw(_hz(note - 12), tt, 5) for note in chord)
        m[i0:i1] += _lp(pad, 900.0) * 0.05 * np.minimum(1, tt / 0.25) * np.minimum(1, (4 * beat - tt) / 0.1)
        for k in range(8):                                          # eighth-note plucks up and down the chord
            note = (chord + (chord[0] + 12,))[[0, 1, 2, 3, 2, 1, 2, 3][k]] + 12
            s0 = b0 + k * beat / 2
            j0 = int(s0 * SR)
            u = np.arange(min(int(0.45 * SR), n - j0)) / SR
            pl = (np.sin(2 * np.pi * _hz(note) * u) + 0.3 * np.sin(2 * np.pi * _hz(note) * 2 * u)) * np.exp(-u * 9)
            m[j0:j0 + len(u)] += pl * np.minimum(1, u / 0.003) * 0.11
        for k in range(4):                                           # kick: a falling sine thump
            j0 = int((b0 + k * beat) * SR)
            u = np.arange(min(int(0.35 * SR), n - j0)) / SR
            kick = np.sin(2 * np.pi * (45 + 90 * np.exp(-u * 30)) * u) * np.exp(-u * 11)
            m[j0:j0 + len(u)] += kick * (0.55 if k % 2 == 0 else 0.4)
    return _st(_norm(m, 0.8), 0.3, rng)


def calm_bed(rng, dur=8.0):
    n = int(dur * SR)
    t = np.arange(n) / SR
    beat = 60.0 / BPM
    m = np.zeros(n)
    for bar, chord in enumerate(CHORDS):
        b0 = bar * 4 * beat
        i0, i1 = int(b0 * SR), int((b0 + 4 * beat) * SR)
        tt = t[i0:i1] - b0
        swell = np.sin(np.pi * np.clip(tt / (4 * beat), 0, 1)) ** 0.6
        for note in chord:
            m[i0:i1] += _soft_saw(_hz(note), tt, 4) * swell * (1 + 0.15 * np.sin(2 * np.pi * 0.5 * tt))
        m[i0:i1] += np.sin(2 * np.pi * _hz(chord[0] - 24) * tt) * swell * 1.2
    return _st(_norm(_lp(m, 1400.0), 0.7), 0.5, rng)


def bubble_pop(rng, dur=0.3):
    n = int(dur * SR)
    t = np.arange(n) / SR
    f = 380 + 900 * (1 - np.exp(-t * 40))
    s = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 22) * np.minimum(1, t / 0.002)
    return _st(_norm(s, 0.85))


def rise_tone(rng, dur=1.5):
    n = int(dur * SR)
    t = np.arange(n) / SR
    u = t / dur
    m = np.zeros(n)
    for note in (57, 64, 69, 76):
        f = _hz(note) * 2 ** (u * 1.0)
        m += _soft_saw(1, np.cumsum(f) / SR, 6)
    env = u ** 2.2 * np.minimum(1, (dur - t) / 0.03)
    return _st(_norm(_lp(m, 3000.0) * env, 0.8), 0.4, rng)


SOUNDS = {"coins": coins, "kaching": kaching, "shimmer": shimmer, "shutter": shutter, "pulse_bed": pulse_bed,
          "calm_bed": calm_bed, "bubble_pop": bubble_pop, "rise_tone": rise_tone}
TITLES = {"pulse_bed": "Upbeat pulse bed, 120 bpm loop (synth)", "calm_bed": "Calm pad bed, loop (synth)",
          "bubble_pop": "Bubble pop (synth)", "rise_tone": "Tonal riser (synth)"}


def write(name, x):
    OUT.mkdir(parents=True, exist_ok=True)
    pcm = (np.clip(x, -1, 1) * 32767).astype("<i2")
    with wave.open(str(OUT / f"{name}.wav"), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())


def main(force=False):
    mpath = SFX / "manifest.json"
    manifest = json.loads(mpath.read_text(encoding="utf-8"))
    changed = False
    for k, (name, fn) in enumerate(SOUNDS.items()):
        f = OUT / f"{name}.wav"
        if force or not f.exists():
            write(name, fn(np.random.default_rng(100 + k)))
            print(f"  generated {name}")
        if name not in manifest["sounds"]:
            manifest["sounds"][name] = {"file": f"generated/{name}.wav", "source": "generated",
                                        "title": TITLES.get(name, name),
                                        "license": "generated (pipeline/post/sfx_generate.py)"}
            changed = True
    if changed:
        mpath.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        print("  manifest updated")


if __name__ == "__main__":
    main(force="--force" in sys.argv)
