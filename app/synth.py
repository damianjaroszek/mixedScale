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
