"""Generate synthetic dance tracks with known key/BPM/structure for testing."""
from __future__ import annotations

import numpy as np
import soundfile as sf

SR = 44100
NOTE = {"C": 0, "C#": 1, "D": 2, "Eb": 3, "E": 4, "F": 5, "F#": 6, "G": 7, "Ab": 8, "A": 9, "Bb": 10, "B": 11}


def midi_hz(m):
    return 440.0 * 2 ** ((m - 69) / 12)


def tone(freq, dur, kind="saw", amp=0.2):
    t = np.arange(int(dur * SR)) / SR
    if kind == "sine":
        w = np.sin(2 * np.pi * freq * t)
    else:  # band-limited-ish saw (few harmonics)
        w = sum(np.sin(2 * np.pi * freq * k * t) / k for k in range(1, 8))
    env = np.minimum(1, t / 0.01) * np.exp(-t * 1.5)
    return amp * w * env


def kick(amp=0.9):
    t = np.arange(int(0.25 * SR)) / SR
    f = 50 + 120 * np.exp(-t * 30)
    return amp * np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 12)


def hat(amp=0.15):
    n = int(0.05 * SR)
    rng = np.random.default_rng(1)
    return amp * rng.standard_normal(n) * np.exp(-np.arange(n) / SR * 80)


def place(buf, x, at):
    i = int(at * SR)
    j = min(len(buf), i + len(x))
    if i < len(buf):
        buf[i:j] += x[: j - i]


def make_track(path, tonic="A", mode="minor", bpm=124.0, bars=96, vocals_like=False, offset=0.0):
    beat = 60.0 / bpm
    dur = bars * 4 * beat + offset + 1
    buf = np.zeros(int(dur * SR))
    root = 48 + NOTE[tonic]  # C3..B3
    if mode == "minor":
        prog = [(0, [0, 3, 7]), (0, [0, 3, 7]), (8, [0, 4, 7]), (10, [0, 4, 7])]  # i i VI VII
    else:
        prog = [(0, [0, 4, 7]), (0, [0, 4, 7]), (5, [0, 4, 7]), (7, [0, 4, 7])]  # I I IV V
    for b in range(bars):
        t0 = offset + b * 4 * beat
        section = b // 16
        drums = section not in (2,)  # breakdown in bars 32-47
        loud = 1.0 if section in (3, 4) else 0.6
        deg, chord = prog[b % 4]
        for n in chord:
            place(buf, tone(midi_hz(root + deg + n), 4 * beat, amp=0.07 * loud), t0)
        place(buf, tone(midi_hz(root - 24 + deg), 4 * beat, kind="sine", amp=0.25 * loud), t0)
        if vocals_like and section in (1, 3):
            for k in range(4):
                place(buf, tone(midi_hz(root + 12 + [0, 3 if mode == 'minor' else 4, 7, 5][k]), beat, kind="sine", amp=0.12), t0 + k * beat)
        if drums:
            for k in range(4):
                place(buf, kick(0.9 * loud), t0 + k * beat)
                place(buf, hat(0.12 * loud), t0 + k * beat + beat / 2)
    buf /= np.abs(buf).max() + 1e-9
    sf.write(path, (buf * 0.9).astype(np.float32), SR)
    return path


if __name__ == "__main__":
    import sys

    out = sys.argv[1]
    import os

    os.makedirs(out, exist_ok=True)
    specs = [
        ("Test Artist - Night Drive", "A", "minor", 124),
        ("Test Artist - Sunrise", "C", "major", 124),
        ("Synth Lab - Deep Pulse", "D", "minor", 126),
        ("Synth Lab - Ocean", "E", "minor", 122),
        ("Nova - Bright Days", "G", "major", 128),
        ("Nova - Midnight", "F", "minor", 120),
        ("Echo - Halftime", "B", "minor", 87),
        ("Echo - Fast Lane", "F#", "minor", 174),
    ]
    for name, t, m, bpm in specs:
        make_track(os.path.join(out, name + ".wav"), t, m, bpm, bars=64, vocals_like=True, offset=0.37)
        print(name)


def noise(dur, amp, decay, seed=0):
    n = int(dur * SR)
    rng = np.random.default_rng(seed)
    return amp * rng.standard_normal(n) * np.exp(-np.arange(n) / SR * decay)


SECTIONS = [("intro", 16), ("build", 16), ("break", 16), ("drop", 32), ("outro", 16)]


def make_realistic(path, tonic="A", mode="minor", bpm=124.0, offset=0.0, sections=SECTIONS, seed=0):
    """House-style track: beatless breakdown, off-beat bass, claps on 2/4, crashes on section starts.
    Returns ground truth: {'downbeat', 'bpm', 'boundaries' (seconds)}."""
    beat = 60.0 / bpm
    bars = sum(n for _, n in sections)
    dur = bars * 4 * beat + offset + 2
    buf = np.zeros(int(dur * SR))
    root = 48 + NOTE[tonic]
    prog = [(0, [0, 3, 7]), (8, [0, 4, 7]), (3, [0, 4, 7]), (10, [0, 4, 7])] if mode == "minor" else [(0, [0, 4, 7]), (9, [0, 3, 7]), (5, [0, 4, 7]), (7, [0, 4, 7])]
    clap = noise(0.12, 0.35, 30, seed + 1)
    bounds = []
    b0 = 0
    for name, n in sections:
        bounds.append(offset + b0 * 4 * beat)
        place(buf, noise(1.5, 0.25, 3, seed + 2), offset + b0 * 4 * beat)  # crash
        for b in range(b0, b0 + n):
            t0 = offset + b * 4 * beat
            deg, chord = prog[b % 4]
            has_kick = name not in ("break", "gap")
            has_chords = name in ("build", "break", "drop")
            has_bass = name in ("build", "drop")
            loud = 1.0 if name == "drop" else 0.7
            if has_chords:
                for k in chord:
                    place(buf, tone(midi_hz(root + deg + k), 4 * beat, amp=(0.09 if name == "break" else 0.05) * loud), t0)
            for k in range(4):
                if has_kick:
                    place(buf, kick(0.85 * loud), t0 + k * beat)
                if has_bass:
                    place(buf, tone(midi_hz(root - 24 + deg), beat / 2, kind="sine", amp=0.3 * loud), t0 + k * beat + beat / 2)
                if name not in ("break", "gap"):
                    for s in range(4):
                        place(buf, hat(0.06 if s % 2 else 0.1), t0 + k * beat + s * beat / 4)
                if k in (1, 3) and name in ("build", "drop", "outro"):
                    place(buf, clap * loud, t0 + k * beat)
            if name == "drop":
                for k in range(8):
                    place(buf, tone(midi_hz(root + 12 + [0, 3, 7, 10, 7, 3, 0, -2][k] + deg * 0), beat / 2, amp=0.05), t0 + k * beat / 2)
        b0 += n
    buf /= np.abs(buf).max() + 1e-9
    sf.write(path, (buf * 0.9).astype(np.float32), SR)
    return {"downbeat": offset, "bpm": bpm, "boundaries": bounds}
