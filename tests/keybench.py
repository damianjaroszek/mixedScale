"""Key-detection benchmark on synthetic, musically-correct tracks (24 keys x several progressions,
random detuning, drums). Usage: python tests/keybench.py OUTDIR [N_PER_KEY]"""
import json
import os
import sys

import numpy as np
import soundfile as sf

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.synth import SR, hat, kick, place  # noqa: E402

MINOR = [  # (chord root degree in semitones, chord intervals)
    [(0, "m"), (8, "M"), (3, "M"), (10, "M")],   # i VI III VII
    [(0, "m"), (5, "m"), (7, "M"), (0, "m")],    # i iv V i
    [(0, "m"), (10, "M"), (8, "M"), (10, "M")],  # i VII VI VII
    [(0, "m"), (0, "m"), (5, "m"), (8, "M")],    # i i iv VI
]
MAJOR = [
    [(0, "M"), (7, "M"), (9, "m"), (5, "M")],    # I V vi IV
    [(0, "M"), (5, "M"), (7, "M"), (0, "M")],    # I IV V I
    [(0, "M"), (9, "m"), (5, "M"), (7, "M")],    # I vi IV V
    [(5, "M"), (7, "M"), (0, "M"), (0, "M")],    # IV V I I
]
IV = {"m": [0, 3, 7], "M": [0, 4, 7]}
SCALE = {"minor": [0, 2, 3, 5, 7, 8, 10], "major": [0, 2, 4, 5, 7, 9, 11]}


def tone(freq, dur, rng, amp):
    t = np.arange(int(dur * SR)) / SR
    n = int(rng.integers(3, 9))
    w = sum(np.sin(2 * np.pi * freq * k * t + rng.uniform(0, 6)) / k ** rng.uniform(0.8, 1.6) for k in range(1, n))
    env = np.minimum(1, t / 0.02) * np.exp(-t * rng.uniform(0.5, 3))
    return amp * w * env


def make(path, tonic, mode, prog_i, seed, bpm=124, rotate=0):
    rng = np.random.default_rng(seed)
    cents = rng.uniform(-35, 35)
    tune = 2 ** (cents / 1200)
    hz = lambda m: 440 * 2 ** ((m - 69) / 12) * tune
    prog = (MINOR if mode == "minor" else MAJOR)[prog_i % 4]
    prog = prog[rotate:] + prog[:rotate]
    beat = 60 / bpm
    bars = 24
    buf = np.zeros(int((bars * 4 * beat + 2) * SR))
    root = 48 + tonic
    scale = SCALE[mode]
    for b in range(bars):
        t0 = b * 4 * beat
        deg, q = prog[b % 4]
        for iv in IV[q]:
            place(buf, tone(hz(root + deg + iv), 4 * beat, rng, 0.05), t0)
        place(buf, tone(hz(root - 24 + deg), 4 * beat, rng, 0.18), t0)
        # melody on scale notes, ending phrases on the tonic
        for k in range(8):
            note = scale[int(rng.integers(0, 7))] if not (b % 4 == 3 and k >= 6) else 0
            place(buf, tone(hz(root + 12 + note), beat / 2, rng, 0.06), t0 + k * beat / 2)
        if b >= 4:
            for k in range(4):
                place(buf, kick(0.8), t0 + k * beat)
                place(buf, hat(0.1), t0 + k * beat + beat / 2)
    buf /= np.abs(buf).max() + 1e-9
    sf.write(path, (buf * 0.9).astype(np.float32), SR)


if __name__ == "__main__":
    out = sys.argv[1]
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 2
    seed0 = int(sys.argv[3]) if len(sys.argv) > 3 else 0
    rotate = int(sys.argv[4]) if len(sys.argv) > 4 else 0
    os.makedirs(out, exist_ok=True)
    truth = {}
    i = 0
    for mode in ("minor", "major"):
        for tonic in range(12):
            for j in range(n):
                name = f"k{i:03d}.wav"
                make(os.path.join(out, name), tonic, mode, j + tonic + seed0, seed=i + 1000 * seed0, bpm=int(118 + (i * 7) % 12), rotate=rotate)
                truth[name] = [tonic, mode]
                i += 1
    json.dump(truth, open(os.path.join(out, "truth.json"), "w"))
    print(i, "tracks")
