"""The voice of 2D videos: Kokoro, a free text-to-speech model that runs offline on the CPU.

Setup installs its Python packages (requirements-voice.txt, about 70 MB) into the kit's .venv and downloads the model
(about 350 MB, from the kokoro-onnx project's GitHub release, checked against its SHA-256) into tools/kokoro/. The
model is Apache 2.0, so the voices can be used on a monetized channel. pipeline/twod/voice.py speaks with it.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

from . import paths as P

RELEASE = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.1/"
FILES = {
    "kokoro-v1.0.onnx": ("beb0d1848dee9a49da392cc3df26958d46cfa35d321edf434f52949153f0df3a", 325),
    "voices-v1.0.bin": ("bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d", 28),
}
DOWNLOAD_MB = sum(mb for _, mb in FILES.values()) + 70     # the model and voices, plus the packages
REQUIREMENTS = P.ROOT / "requirements-voice.txt"

# The voices first-time setup offers (all Apache 2.0). Kokoro has more: any of its names works in episode.json.
VOICES = {
    "af_heart": "Heart: warm American woman (the clearest voice)",
    "af_bella": "Bella: bright American woman",
    "am_michael": "Michael: calm American man",
    "am_fenrir": "Fenrir: deep American man",
    "am_puck": "Puck: lively American man",
    "bf_emma": "Emma: British woman",
    "bm_george": "George: British man",
}
DEFAULT_VOICE = "af_heart"


def folder() -> Path:
    return P.TOOLS / "kokoro"


def model_files() -> tuple[Path, Path]:
    return folder() / "kokoro-v1.0.onnx", folder() / "voices-v1.0.bin"


def packages_ready() -> bool:
    return all(importlib.util.find_spec(m) is not None for m in ("kokoro_onnx", "onnxruntime"))


def model_ready() -> bool:
    return all(p.is_file() and p.stat().st_size > 1_000_000 for p in model_files())


def ready() -> bool:
    return packages_ready() and model_ready()


def missing() -> str:
    """What isn't there yet, in a few words ('' when the voice is ready)."""
    parts = []
    if not packages_ready():
        parts.append("its Python packages")
    if not model_ready():
        parts.append("the voice model")
    return " and ".join(parts)


def install(progress=None) -> str:
    """The packages (pip, into this Python's environment) and the model files (into tools/kokoro). Raises with a
    readable message if a step fails."""
    from . import tools

    if not packages_ready():
        if progress:
            progress("Installing the voice's Python packages", 0, 0)
        res = subprocess.run([sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "--quiet", "-r",
                              str(REQUIREMENTS)], capture_output=True, text=True)
        importlib.invalidate_caches()
        if res.returncode != 0 or not packages_ready():
            tail = (res.stderr or res.stdout).strip().splitlines()[-1:] or ["no message"]
            raise RuntimeError("pip couldn't install the voice packages: " + tail[0])
    for name, (sha, _mb) in FILES.items():
        dest = folder() / name
        if dest.is_file() and dest.stat().st_size > 1_000_000:
            continue
        tools.download(RELEASE + name, dest, f"the voice ({name})", sha, progress)
    return "voice ready (Kokoro, in " + P.rel(folder()) + ")"
