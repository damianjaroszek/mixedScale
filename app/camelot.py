"""Camelot wheel helpers: key naming, transposition and harmonic compatibility."""
from __future__ import annotations

PITCH_NAMES = ["C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"]
# Pitch class -> Camelot number
MAJOR_NUM = {11: 1, 6: 2, 1: 3, 8: 4, 3: 5, 10: 6, 5: 7, 0: 8, 7: 9, 2: 10, 9: 11, 4: 12}
MINOR_NUM = {8: 1, 3: 2, 10: 3, 5: 4, 0: 5, 7: 6, 2: 7, 9: 8, 4: 9, 11: 10, 6: 11, 1: 12}
NUM_MAJOR = {v: k for k, v in MAJOR_NUM.items()}
NUM_MINOR = {v: k for k, v in MINOR_NUM.items()}
MINOR_DISPLAY = {8: "G#", 3: "Eb", 10: "Bb", 5: "F", 0: "C", 7: "G", 2: "D", 9: "A", 4: "E", 11: "B", 6: "F#", 1: "C#"}


def to_camelot(pitch_class: int, mode: str) -> str:
    pc = pitch_class % 12
    if mode == "minor":
        return f"{MINOR_NUM[pc]}A"
    return f"{MAJOR_NUM[pc]}B"


def parse_camelot(code: str) -> tuple[int, str]:
    code = code.strip().upper()
    return int(code[:-1]), code[-1]


def camelot_to_pitch(code: str) -> tuple[int, str]:
    num, letter = parse_camelot(code)
    if letter == "A":
        return NUM_MINOR[num], "minor"
    return NUM_MAJOR[num], "major"


def standard_name(code: str, short: bool = False) -> str:
    """'7A' -> 'D Minor' (or 'Dm' when short)."""
    pc, mode = camelot_to_pitch(code)
    if mode == "minor":
        root = MINOR_DISPLAY[pc]
        return f"{root}m" if short else f"{root} Minor"
    root = PITCH_NAMES[pc]
    return root if short else f"{root} Major"


def transpose(code: str, semitones: int) -> str:
    """Moving a key up one semitone moves it +7 on the Camelot wheel."""
    num, letter = parse_camelot(code)
    new = (num - 1 + 7 * semitones) % 12 + 1
    return f"{new}{letter}"


# (relation name, score). Higher = smoother harmonic blend.
def relation(a: str, b: str) -> tuple[str, float] | None:
    na, la = parse_camelot(a)
    nb, lb = parse_camelot(b)
    d = (nb - na) % 12
    if la == lb:
        if d == 0:
            return "Ta sama tonacja", 1.0
        if d in (1, 11):
            return ("+1 na kole" if d == 1 else "-1 na kole"), 0.85
        if d in (2, 10):
            return ("+2 (energy boost)" if d == 2 else "-2"), 0.55
        if d == 7:
            return "+1 półton (energy boost)", 0.5
        return None
    # different letter
    if d == 0:
        return "Równoległa dur/moll", 0.9
    if (la == "A" and d == 3) or (la == "B" and d == 9):
        return "Ta sama tonika (dur/moll)", 0.45
    if (la == "A" and d == 1) or (la == "B" and d == 11):
        return "Przekątna", 0.6
    return None


def compatible_keys(code: str) -> list[str]:
    out = []
    for n in range(1, 13):
        for l in "AB":
            k = f"{n}{l}"
            if relation(code, k):
                out.append(k)
    return out


def best_shift(a: str, b: str, max_shift: int = 4) -> tuple[int, str, float] | None:
    """Find the smallest pitch shift for b to blend with a. Returns (shift, relation, score)."""
    best = None
    for s in sorted(range(-max_shift, max_shift + 1), key=abs):
        r = relation(a, transpose(b, s))
        if not r:
            continue
        # penalise shifting: each semitone costs a little
        score = r[1] - 0.06 * abs(s)
        if best is None or score > best[2] + 1e-9:
            best = (s, r[0], score)
    return best
