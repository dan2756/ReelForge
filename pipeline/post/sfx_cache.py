"""Convert every sound in assets/sfx/manifest.json to 48 kHz stereo 16-bit WAV in assets/sfx/_cache/<name>.wav.

  python pipeline/post/sfx_cache.py          # converts anything missing
"""
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402

SFX = config.ROOT / "assets" / "sfx"


def cached(name):
    return SFX / "_cache" / f"{name}.wav"


def cue_sounds(cues):
    """The names of the recorded sounds a cue sheet (episode.json "audio") uses: its beds, hits and scatter pools."""
    names = {b["sound"] for b in cues.get("beds", [])}
    names |= {h["sound"] for h in cues.get("hits", []) if "sound" in h}
    return names | {name for s in cues.get("scatter", []) for name in s.get("pool", [])}


def build_cache(force=False, needed=None):
    """Convert the manifest's sounds that aren't in the cache yet. `needed` is the set of sounds a mix uses
    (cue_sounds): only one of those being absent or unreadable stops it, so a single download that failed or was
    withdrawn no longer blocks every episode. Without `needed` (this script run by hand) every sound is required."""
    manifest = json.loads((SFX / "manifest.json").read_text(encoding="utf-8"))
    sounds = manifest["sounds"]
    (SFX / "_cache").mkdir(exist_ok=True)
    unknown = sorted(set(needed or ()) - set(sounds))
    if unknown:
        raise FileNotFoundError(f"the cue sheet names sounds that aren't in assets/sfx/manifest.json: "
                                f"{', '.join(unknown)}")
    missing = [n for n, m in sounds.items() if not (SFX / m["file"]).exists() and not cached(n).exists()]
    wanted = [n for n in missing if needed is None or n in needed]
    if wanted:
        raise FileNotFoundError(f"{len(wanted)} sounds aren't downloaded yet ({', '.join(wanted[:4])}"
                                f"{', ...' if len(wanted) > 4 else ''}). Run setup again, or: python -m studio sounds")
    if missing:
        print(f"  note: {len(missing)} sounds aren't downloaded ({', '.join(missing[:4])}"
              f"{', ...' if len(missing) > 4 else ''}); this episode doesn't use them")
    for name, meta in sounds.items():
        src = SFX / meta["file"]
        out = cached(name)
        if out.exists() and not force:
            continue
        if not src.exists():
            continue
        try:
            subprocess.run([config.require_ffmpeg(), "-v", "error", "-y", "-i", str(src), "-ac", "2", "-ar",
                            str(config.SAMPLE_RATE), "-c:a", "pcm_s16le", str(out)], check=True)
        except subprocess.CalledProcessError:
            out.unlink(missing_ok=True)
            if needed is None or name in needed:
                raise
            print(f"  note: {meta['file']} could not be read; this episode doesn't use it")
            continue
        print(f"  cached {name}")
    return manifest


if __name__ == "__main__":
    build_cache(force="--force" in sys.argv)
