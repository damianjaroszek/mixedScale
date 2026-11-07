"""Mashup ideas and harmonic set ordering."""
from __future__ import annotations

import math

from . import camelot


def tempo_match(bpm_a: float, bpm_b: float, allow_half_double: bool = True) -> tuple[float, float]:
    """Return (factor, diff_pct): factor applied to B's BPM (0.5/1/2) and remaining % difference."""
    factors = (0.5, 1.0, 2.0) if allow_half_double else (1.0,)
    best = min(factors, key=lambda f: abs(math.log((bpm_b * f) / bpm_a)))
    return best, abs(bpm_b * best - bpm_a) / bpm_a * 100.0


def score_pair(a: dict, b: dict, shift_a: int = 0, opts: dict | None = None) -> dict | None:
    opts = opts or {}
    mode = opts.get("mode", "mashup")
    if not a.get("key") or not b.get("key") or not a.get("bpm") or not b.get("bpm"):
        return None
    key_a = camelot.transpose(a["key"], shift_a)
    factor, diff = tempo_match(a["bpm"], b["bpm"], opts.get("half_double", True))
    tol = opts.get("tempo_tol")
    if tol is None:
        tol = 6.0 if mode == "mix" else None
    if tol is not None and diff > tol:
        return None
    max_shift = int(opts.get("max_shift_b", 0 if mode == "mix" else 4)) if opts.get("allow_shift_b", mode == "mashup") else 0
    if max_shift:
        found = camelot.best_shift(key_a, b["key"], max_shift)
        if not found:
            return None
        shift_b, rel, kscore = found
    else:
        r = camelot.relation(key_a, b["key"])
        if not r:
            return None
        shift_b, rel, kscore = 0, r[0], r[1]
    tscore = max(0.0, 1.0 - diff / 10.0)
    ea, eb = a.get("energy") or 5, b.get("energy") or 5
    if mode == "mix":
        de = eb - ea
        escore = 1.0 if de in (0, 1) else max(0.0, 1.0 - abs(de) / 4)
    else:
        escore = max(0.0, 1.0 - abs(ea - eb) / 5)
    score = 100 * (0.55 * kscore + 0.3 * tscore + 0.15 * escore)
    reasons = [rel]
    if shift_b:
        reasons.append(f"deck B {shift_b:+d} półt.")
    if factor != 1.0:
        reasons.append("half-time" if factor == 2.0 else "double-time")
    reasons.append(f"tempo ±{diff:.1f}%")
    va, vb = a.get("vocal_ratio"), b.get("vocal_ratio")
    if mode == "mashup" and va is not None and vb is not None:
        if va < 0.25 and vb > 0.35:
            score += 8
            reasons.append("wokal z B na podkład A")
        elif va > 0.35 and vb < 0.25:
            score += 6
            reasons.append("podkład z B pod wokal A")
        elif (va > 0.25) != (vb > 0.25):
            score += 4
            reasons.append("wokal + instrumental")
    return {
        "track_id": b["id"],
        "score": round(min(100.0, score), 1),
        "shift_b": shift_b,
        "key_b": camelot.transpose(b["key"], shift_b),
        "tempo_factor": factor,
        "tempo_diff": round(diff, 2),
        "relation": rel,
        "reasons": reasons,
    }


def ideas(a: dict, tracks: list[dict], shift_a: int = 0, opts: dict | None = None) -> list[dict]:
    opts = opts or {}
    energy = set(opts.get("energy") or [])
    genres = {g.lower() for g in (opts.get("genres") or [])}
    pool = opts.get("pool")  # set of allowed ids (playlists)
    out = []
    for b in tracks:
        if b["id"] == a["id"] or b.get("status") != "done":
            continue
        if pool is not None and b["id"] not in pool:
            continue
        if energy and b.get("energy") not in energy:
            continue
        if genres and (b.get("genre") or "").lower() not in genres:
            continue
        s = score_pair(a, b, shift_a, opts)
        if s:
            out.append(s)
    out.sort(key=lambda s: (-s["score"], abs(s["shift_b"])))
    return out


def transition_score(a: dict, b: dict) -> float:
    r = camelot.relation(a["key"], b["key"]) if a.get("key") and b.get("key") else None
    k = r[1] if r else 0.0
    _, diff = tempo_match(a["bpm"] or 120, b["bpm"] or 120)
    return 0.6 * k + 0.4 * max(0.0, 1 - diff / 8)


def energy_target(i: int, n: int, curve: str, lo: float, hi: float) -> float:
    x = i / max(1, n - 1)
    if curve == "build":
        return lo + (hi - lo) * x
    if curve == "peak":  # warm up, peak at ~70%, cool down
        return lo + (hi - lo) * (x / 0.7 if x <= 0.7 else (1 - x) / 0.3 * 0.6 + 0.4)
    return (lo + hi) / 2


def harmonic_order(tracks: list[dict], curve: str = "peak", start_id: int | None = None) -> list[int]:
    """Greedy ordering: each next track flows harmonically and follows the energy curve."""
    todo = [t for t in tracks if t.get("key") and t.get("bpm")]
    rest = [t for t in tracks if t not in todo]
    if not todo:
        return [t["id"] for t in tracks]
    energies = [t.get("energy") or 5 for t in todo]
    lo, hi = min(energies), max(energies)
    n = len(todo)
    if start_id is not None and any(t["id"] == start_id for t in todo):
        cur = next(t for t in todo if t["id"] == start_id)
    else:
        target = energy_target(0, n, curve, lo, hi)
        cur = min(todo, key=lambda t: (abs((t.get("energy") or 5) - target), t["bpm"]))
    order = [cur]
    todo.remove(cur)
    while todo:
        target = energy_target(len(order), n, curve, lo, hi)
        nxt = max(todo, key=lambda t: transition_score(cur, t) - 0.12 * abs((t.get("energy") or 5) - target))
        order.append(nxt)
        todo.remove(nxt)
        cur = nxt
    return [t["id"] for t in order] + [t["id"] for t in rest]
