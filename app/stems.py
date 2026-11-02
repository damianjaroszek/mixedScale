"""Stem separation (vocals / instrumental).

Primary engine: Demucs (htdemucs, high quality, runs locally on CPU or GPU).
Fallback: a quick ffmpeg-based mid/side split so the app works before Demucs is installed
(low quality, clearly labelled in the UI).
"""
from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

from . import ffmpeg

STEMS = ("vocals", "instrumental")


def demucs_command() -> list[str] | None:
    """How to launch Demucs: the bundled module (dev install) or a `demucs` CLI on PATH."""
    if os.environ.get("BLENDORA_STEMS", "") == "quick":
        return None
    if not getattr(sys, "frozen", False) and importlib.util.find_spec("demucs") is not None:
        return [sys.executable, "-m", "demucs"]
    cli = shutil.which("demucs")
    return [cli] if cli else None


def demucs_available() -> bool:
    return demucs_command() is not None


def stem_dir(cache: Path, track_id: int) -> Path:
    return cache / "stems" / str(track_id)


def stem_path(cache: Path, track_id: int, stem: str) -> Path:
    return stem_dir(cache, track_id) / f"{stem}.flac"


def has_stems(cache: Path, track_id: int) -> bool:
    return all(stem_path(cache, track_id, s).exists() for s in STEMS)


def _separate_demucs(src: str, out: Path, model: str, progress=None) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        cmd = [*demucs_command(), "--two-stems=vocals", "-n", model, "-o", tmp, "--filename", "{stem}.{ext}", "--flac", src]
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", bufsize=1, **ffmpeg.NO_WINDOW)
        tail = []
        buf = ""
        while True:
            ch = proc.stdout.read(1)
            if not ch:
                break
            if ch in "\r\n":
                line = buf.strip()
                buf = ""
                if line:
                    tail = (tail + [line])[-20:]
                    if progress and "%|" in line:
                        try:
                            progress(float(line.split("%|")[0].split()[-1]) / 100.0)
                        except ValueError:
                            pass
            else:
                buf += ch
        if proc.wait() != 0:
            raise RuntimeError("Demucs zakończył się błędem:\n" + "\n".join(tail[-6:]))
        base = Path(tmp) / model
        shutil.move(str(base / "vocals.flac"), out / "vocals.flac")
        shutil.move(str(base / "no_vocals.flac"), out / "instrumental.flac")


def _separate_quick(src: str, out: Path) -> None:
    # instrumental: remove centre-panned content (vocals are usually centred), keep bass mono
    ffmpeg.run([
        "-i", src, "-filter_complex",
        "[0:a]asplit=2[a][b];[a]pan=stereo|c0=0.5*c0-0.5*c1|c1=0.5*c1-0.5*c0[side];"
        "[b]lowpass=f=150,pan=stereo|c0=0.5*c0+0.5*c1|c1=0.5*c0+0.5*c1[bass];"
        "[side][bass]amix=inputs=2:normalize=0[out]",
        "-map", "[out]", "-c:a", "flac", str(out / "instrumental.flac"),
    ])
    # acapella: centre channel, band-limited to the vocal range
    ffmpeg.run([
        "-i", src, "-af", "pan=mono|c0=0.5*c0+0.5*c1,highpass=f=180,lowpass=f=7000", "-ac", "2",
        "-c:a", "flac", str(out / "vocals.flac"),
    ])


def separate(src: str, cache: Path, track_id: int, model: str = "htdemucs", progress=None) -> str:
    out = stem_dir(cache, track_id)
    out.mkdir(parents=True, exist_ok=True)
    if demucs_available():
        _separate_demucs(src, out, model, progress)
        engine = "demucs"
    else:
        _separate_quick(src, out)
        engine = "quick"
    (out / "engine.txt").write_text(engine)
    return engine


def vocal_profile(cache: Path, track_id: int, bars: list[tuple[float, float]] | None = None) -> dict:
    """How much of the track has vocals: overall ratio and per-second activity (0-255)."""
    v = ffmpeg.decode(str(stem_path(cache, track_id, "vocals")), sr=8000)
    i = ffmpeg.decode(str(stem_path(cache, track_id, "instrumental")), sr=8000)
    n = min(len(v), len(i)) // 8000 * 8000
    vr = np.sqrt((v[:n].reshape(-1, 8000) ** 2).mean(axis=1))
    ir = np.sqrt((i[:n].reshape(-1, 8000) ** 2).mean(axis=1))
    active = vr > 0.25 * (ir + 1e-4)
    return {"vocal_ratio": float(active.mean()) if len(active) else 0.0, "activity": (np.clip(vr / (vr.max() + 1e-9), 0, 1) * 255).astype(int).tolist()}
