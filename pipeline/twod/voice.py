"""The voice step of a 2D episode: speak the script with Kokoro (free, offline) and time every word for the captions.

  python pipeline/twod/voice.py episodes/<id>

Reads episode.json's "script" (a list of beats, each {"say": "...", "id": "...", "show": "...", "gap": 0.2,
"at": 12.0, "hold": 0.0}) and "voice" ({"voice": "af_heart", "speed": 1.0, "start": 0.3, "gap": 0.2, "tail": 1.2}),
and writes:
  build/voice/voice.wav       the narration, 48 kHz mono, placed at each beat's time (high-passed at 40 Hz)
  build/voice/timeline.json   every beat's start and end, every caption word's start and end
  build/series.json           frames + events (beats, words) for the mixers and compose.py

Each beat is spoken on its own and kept in build/voice/cache/, so changing one line only re-speaks that line. The
voice model lives in tools/kokoro/ (setup downloads it: python -m studio setup, or setup.bat / setup.sh).
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402

KOKORO_SR = 24000
PUNCT = set(" .,!?;:—–-…\"'“”‘’()[]¡¿«»")
DEFAULTS = {"speed": 1.0, "start": 0.3, "gap": 0.18, "tail": 1.2, "lang": ""}


def voice_settings(spec: dict) -> dict:
    v = dict(DEFAULTS)
    v["voice"] = config.SETTINGS["video"].get("voice") or "af_heart"
    v.update(spec.get("voice") or {})
    if not v["lang"]:
        v["lang"] = "en-gb" if v["voice"].startswith("b") else "en-us"
    return v


def clean(word: str) -> str:
    return re.sub(r"[^a-z0-9$%]+", "", word.lower())


def script_hash(spec: dict) -> str:
    """What the voiceover depends on: if this changes, the timeline is out of date."""
    key = json.dumps([spec.get("script", []), voice_settings(spec)], sort_keys=True, ensure_ascii=False)
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


_kokoro = None


def kokoro():
    global _kokoro
    if _kokoro is None:
        sys.path.insert(0, str(config.ROOT))
        from studio import voice as V
        model, voices = V.model_files()
        if not (model.exists() and voices.exists()):
            raise SystemExit("2D: the voice model isn't downloaded. Run setup again and tick 'Voice' (setup.bat / "
                             "./setup.sh, or: python -m studio setup --yes).")
        try:
            from kokoro_onnx import Kokoro
        except ImportError:
            raise SystemExit("2D: the voice packages aren't installed. Run setup again and tick 'Voice'.") from None
        _kokoro = Kokoro(str(model), str(voices))
    return _kokoro


def _voiced(ph: str) -> int:
    return sum(1 for c in ph if c not in PUNCT and not c.isspace())


def speak(text: str, v: dict, cache: Path) -> tuple[np.ndarray, list[dict]]:
    """One beat -> (24 kHz mono float32 audio, [{text, start, end}] word times within it)."""
    key = hashlib.sha1(json.dumps([text, v["voice"], v["speed"], v["lang"]]).encode()).hexdigest()[:20]
    wav, meta = cache / f"{key}.npy", cache / f"{key}.json"
    if wav.exists() and meta.exists():
        return np.load(wav), json.loads(meta.read_text(encoding="utf-8"))
    k = kokoro()
    audio, sr, tm = k.create_timed(text, v["voice"], float(v["speed"]), v["lang"])
    audio = np.asarray(audio, np.float32)
    words = align(text, [(x.phoneme, x.start, x.end) for x in tm], lambda w: k.tokenizer.phonemize(w, v["lang"]),
                  len(audio) / sr)
    cache.mkdir(parents=True, exist_ok=True)
    np.save(wav, audio)
    meta.write_text(json.dumps(words), encoding="utf-8")
    return audio, words


def align(text: str, phones: list[tuple[str, float, float]], phonemize, length: float) -> list[dict]:
    """Word times from the model's phoneme times. The phonemes of the whole line are matched to the text's words by
    how many phonemes each word has on its own; when the line has exactly one phoneme group per word, the groups'
    own times are used."""
    words = text.split()
    if not words:
        return []
    voiced = [(s, e) for p, s, e in phones if p not in PUNCT and not p.isspace()]
    groups, cur = [], []
    for p, s, e in phones:
        if p.isspace() or p in PUNCT:
            if cur:
                groups.append((cur[0][0], cur[-1][1]))
                cur = []
        else:
            cur.append((s, e))
    if cur:
        groups.append((cur[0][0], cur[-1][1]))
    if not voiced:                                   # no timings from this model: spread the words evenly
        step = length / len(words)
        return [{"text": w, "start": round(i * step, 3), "end": round((i + 1) * step, 3)} for i, w in enumerate(words)]
    if len(groups) == len(words):
        return [{"text": w, "start": round(s, 3), "end": round(e, 3)} for w, (s, e) in zip(words, groups)]
    sizes = []
    for w in words:
        try:
            sizes.append(max(_voiced(phonemize(w)), 0))
        except Exception:
            sizes.append(len(clean(w)))
    total = sum(sizes) or 1
    n = len(voiced)
    out, cum = [], 0
    for w, sz in zip(words, sizes):
        a = int(round(cum / total * n))
        cum += sz
        b = max(a + 1, int(round(cum / total * n)))
        a, b = min(a, n - 1), min(b, n)
        out.append({"text": w, "start": round(voiced[a][0], 3), "end": round(voiced[b - 1][1], 3)})
    for i in range(1, len(out)):                     # a word with no phonemes of its own sits with its neighbour
        out[i]["start"] = max(out[i]["start"], out[i - 1]["start"])
    return out


def spread(show: str, start: float, end: float) -> list[dict]:
    """Caption words for a beat whose on-screen text ("show") differs from what's said: spread over the speech."""
    ws = show.split()
    if not ws:
        return []
    lens = [len(w) + 2 for w in ws]
    total, t, out = sum(lens), start, []
    for w, n in zip(ws, lens):
        d = (end - start) * n / total
        out.append({"text": w, "start": round(t, 3), "end": round(t + d, 3)})
        t += d
    return out


def resample(raw: Path, final: Path) -> None:
    """The narration as the mixers take it: high-passed at 40 Hz and at 48 kHz, resampled with soxr where this ffmpeg
    has it. An ffmpeg built without soxr (the one setup downloads on Windows is) stops at the resampler with the file
    already made, empty. So whether to go again without soxr is read from the command's own answer and from what is
    in the file, not from the file being there: the mix stopped on an empty voice.wav, and no 2D video got made."""
    ffmpeg = config.require_ffmpeg()

    def run(how: str) -> subprocess.CompletedProcess:
        return subprocess.run([ffmpeg, "-y", "-v", "error", "-i", str(raw), "-af",
                               f"highpass=f=40,aresample={config.SAMPLE_RATE}{how}", "-ac", "1", "-c:a", "pcm_s16le",
                               str(final)], capture_output=True, text=True, encoding="utf-8", errors="replace")

    def written() -> bool:
        return final.is_file() and final.stat().st_size > 44        # more than a WAV file's header

    done = run(":resampler=soxr")
    if done.returncode != 0 or not written():
        done = run("")
    if done.returncode != 0 or not written():
        why = (done.stderr or "").strip().splitlines()[-1:] or ["it gave no reason"]
        try:
            final.unlink(missing_ok=True)       # not left to be taken for this script's voice
        except OSError:                         # open in another program (a player): ffmpeg couldn't write it either
            raise SystemExit(f"2D: ffmpeg couldn't write the voiceover ({why[0]}): {final} is open in another "
                             "program. Close it there, then run --steps build again.") from None
        raise SystemExit(f"2D: ffmpeg couldn't write the voiceover ({why[0]}). Run python -m studio doctor to check "
                         "ffmpeg, then --steps build again.")


def build(ep: Path) -> dict:
    spec = json.loads((ep / "episode.json").read_text(encoding="utf-8"))
    script = spec.get("script") or []
    if not script:
        raise SystemExit("2D: episode.json has no \"script\" (the beats the voice reads). See docs/2D.md.")
    v = voice_settings(spec)
    out = ep / "build" / "voice"
    cache = out / "cache"
    out.mkdir(parents=True, exist_ok=True)

    beats, words, clips = [], [], []
    t = float(v["start"])
    for i, b in enumerate(script):
        if isinstance(b, str):
            b = {"say": b}
        say = (b.get("say") or "").strip()
        if "at" in b:
            if b["at"] + 1e-6 < t:
                print(f"  note: beat {i + 1} wants to start at {b['at']} s, but the beat before it ends at {t:.2f} s")
            t = max(t, float(b["at"]))
        elif i:
            t += float(b.get("gap", v["gap"]))
        start = t
        if say:
            audio, ws = speak(say, v, cache)
            clips.append((start, audio))
            dur = len(audio) / KOKORO_SR
            if b.get("show") is not None:
                ws = spread(b["show"], min((w["start"] for w in ws), default=0.0), max((w["end"] for w in ws),
                                                                                        default=dur))
            for w in ws:
                words.append({"text": w["text"], "clean": clean(w["text"]), "start": round(start + w["start"], 3),
                              "end": round(start + w["end"], 3), "beat": i})
        else:
            dur = 0.0
        t = start + dur + float(b.get("hold", 0.0))
        beats.append({"i": i, "id": b.get("id", ""), "say": say, "start": round(start, 3), "end": round(t, 3)})
        print(f"  {start:6.2f}-{t:6.2f} s  {say[:70]}")

    spoken_end = t
    needed = round(spoken_end + float(v["tail"]), 2)
    dur = spec.get("duration")
    if dur in (None, "auto"):
        dur = max(needed, config.MIN_SECONDS + 0.4)
        if needed < config.MIN_SECONDS - 3:
            print(f"  note: the voice ends at {spoken_end:.1f} s, but the channel's videos run {config.MIN_SECONDS:g}-"
                  f"{config.MAX_SECONDS:g} s: write more script, or slow the voice (\"speed\")")
    elif needed > float(dur) + 0.01:
        raise SystemExit(f"2D: the voice runs to {spoken_end:.2f} s, past the episode's duration ({dur} s). "
                         f"Cut the script, speed up the voice, or set \"duration\" to {needed} (or \"auto\").")
    dur = float(dur)
    if dur > config.MAX_SECONDS + 0.01:
        print(f"  note: {dur:.1f} s is longer than the channel's {config.MAX_SECONDS:g} s: cut the script")

    # the narration track, then a 40 Hz high-pass (Kokoro leaves a sub-bass bias) and 48 kHz for the mixers
    track = np.zeros(int(dur * KOKORO_SR) + KOKORO_SR, np.float32)
    for start, audio in clips:
        i0 = int(round(start * KOKORO_SR))
        seg = audio[: max(0, len(track) - i0)]
        track[i0:i0 + len(seg)] += seg
    raw = out / "voice_24k.wav"
    with wave.open(str(raw), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(KOKORO_SR)
        w.writeframes((np.clip(track, -1, 1) * 32767).astype("<i2").tobytes())
    final = out / "voice.wav"
    resample(raw, final)

    timeline = {"script_hash": script_hash(spec), "voice": v, "duration": dur, "spoken_end": round(spoken_end, 3),
                "beats": beats, "words": words}
    (out / "timeline.json").write_text(json.dumps(timeline, indent=1, ensure_ascii=False), encoding="utf-8")
    fps = int(spec.get("fps", 30))
    series = {"frames": int(round(dur * fps)), "fps": fps,
              "events": {"beats": [b["start"] for b in beats], "words": [w["start"] for w in words]}}
    (ep / "build" / "series.json").write_text(json.dumps(series), encoding="utf-8")
    print(f"  voice: {len(words)} words, speech ends at {spoken_end:.2f} s, video {dur:.2f} s -> {final}")
    return timeline


if __name__ == "__main__":
    build(Path(sys.argv[1]).resolve())
