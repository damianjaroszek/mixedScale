"""Thin wrappers around ffmpeg: decoding, pitch/tempo rendering, mixing and encoding."""
from __future__ import annotations

import functools
import os
import shutil
import subprocess

import sys

import numpy as np

# On Windows, keep child processes (ffmpeg, demucs) from flashing console windows.
NO_WINDOW = {"creationflags": 0x08000000} if sys.platform.startswith("win") else {}


def app_dir() -> str:
    """Folder of the running program (next to Blendora.exe when frozen)."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@functools.lru_cache(maxsize=1)
def ffmpeg_bin() -> str:
    env = os.environ.get("BLENDORA_FFMPEG")
    if env:
        return env
    exe = "ffmpeg.exe" if sys.platform.startswith("win") else "ffmpeg"
    dirs = [app_dir(), os.path.join(app_dir(), "ffmpeg")]
    if hasattr(sys, "_MEIPASS"):  # PyInstaller puts bundled binaries in _internal
        dirs.insert(0, sys._MEIPASS)
    for cand in (os.path.join(d, exe) for d in dirs):
        if os.path.isfile(cand):
            return cand
    path = shutil.which("ffmpeg")
    if path:
        return path
    try:  # optional bundled binary
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as exc:  # pragma: no cover
        raise RuntimeError("Nie znaleziono ffmpeg. Zainstaluj ffmpeg i dodaj go do PATH.") from exc


@functools.lru_cache(maxsize=1)
def has_rubberband() -> bool:
    try:
        out = subprocess.run([ffmpeg_bin(), "-hide_banner", "-filters"], capture_output=True, text=True, timeout=20, **NO_WINDOW)
        return " rubberband " in out.stdout
    except Exception:
        return False


def run(args: list[str]) -> None:
    proc = subprocess.run([ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-y", *args], capture_output=True, text=True, **NO_WINDOW)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg error: {proc.stderr.strip()[-800:]}")


def decode(path: str, sr: int = 22050, mono: bool = True) -> np.ndarray:
    """Decode any audio file to float32 numpy (mono: shape (n,), stereo: shape (2, n))."""
    ch = 1 if mono else 2
    proc = subprocess.run(
        [ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-i", path, "-vn", "-f", "f32le", "-ac", str(ch), "-ar", str(sr), "pipe:1"],
        capture_output=True,
        **NO_WINDOW,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"Nie udało się zdekodować pliku: {proc.stderr.decode(errors='ignore')[-400:]}")
    data = np.frombuffer(proc.stdout, dtype=np.float32)
    if not mono:
        data = data.reshape(-1, 2).T
    return data


def pitch_tempo_filter(semitones: float = 0.0, tempo: float = 1.0, sr: int = 44100) -> str | None:
    """Filter chain that shifts pitch by `semitones` and speeds up by `tempo` independently."""
    if abs(semitones) < 1e-6 and abs(tempo - 1.0) < 1e-6:
        return None
    ratio = 2.0 ** (semitones / 12.0)
    if has_rubberband():
        return f"rubberband=pitch={ratio:.6f}:tempo={tempo:.6f}:pitchq=quality:transients=crisp:window=standard"
    # Fallback: resample (changes pitch+tempo) then fix tempo with atempo.
    chain = []
    if abs(ratio - 1.0) > 1e-6:
        chain.append(f"asetrate={sr * ratio:.3f},aresample={sr}")
    at = tempo / ratio
    while at > 2.0:
        chain.append("atempo=2.0")
        at /= 2.0
    while at < 0.5:
        chain.append("atempo=0.5")
        at /= 0.5
    if abs(at - 1.0) > 1e-6:
        chain.append(f"atempo={at:.6f}")
    return ",".join(chain) if chain else None


CODECS = {
    ".flac": ["-c:a", "flac"],
    ".wav": ["-c:a", "pcm_s16le"],
    ".mp3": ["-c:a", "libmp3lame", "-b:a", "320k"],
}


def render(src: str, dst: str, semitones: float = 0.0, tempo: float = 1.0, sr: int = 44100) -> str:
    args = ["-i", src, "-vn", "-ar", str(sr), "-ac", "2"]
    flt = pitch_tempo_filter(semitones, tempo, sr)
    if flt:
        args += ["-af", flt]
    args += CODECS[os.path.splitext(dst)[1].lower()]
    tmp = dst + ".part" + os.path.splitext(dst)[1]
    run(args + [tmp])
    os.replace(tmp, dst)
    return dst


def mixdown(parts: list[dict], dst: str, duration: float, sr: int = 44100) -> str:
    """parts: [{path, start (s in source), delay (s in output), gain}] -> one mixed file."""
    args: list[str] = []
    for p in parts:
        args += ["-ss", f"{max(0.0, p['start']):.4f}", "-i", p["path"]]
    chains = []
    for i, p in enumerate(parts):
        ms = int(round(max(0.0, p.get("delay", 0.0)) * 1000))
        chains.append(
            f"[{i}:a]aresample={sr},aformat=channel_layouts=stereo,volume={p.get('gain', 1.0):.3f},adelay={ms}|{ms}[a{i}]"
        )
    mix_in = "".join(f"[a{i}]" for i in range(len(parts)))
    graph = ";".join(chains) + f";{mix_in}amix=inputs={len(parts)}:normalize=0,alimiter=limit=0.95[out]"
    args += ["-filter_complex", graph, "-map", "[out]", "-t", f"{duration:.3f}"]
    args += CODECS[os.path.splitext(dst)[1].lower()]
    run(args + [dst])
    return dst
