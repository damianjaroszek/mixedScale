"""Audio analysis: key (Camelot), tempo + beat grid, energy level, cue points, waveform."""
from __future__ import annotations

import math

import librosa
import numpy as np

from . import camelot
from .ffmpeg import decode

SR = 22050
HOP = 512
WAVE_PPS = 50  # waveform points per second

# Key profiles (major, minor), tonic at index 0.
PROFILES = {
    "krumhansl": (
        [6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88],
        [6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17],
    ),
    "temperley": (
        [0.748, 0.060, 0.488, 0.082, 0.670, 0.460, 0.096, 0.715, 0.104, 0.366, 0.057, 0.400],
        [0.712, 0.084, 0.474, 0.618, 0.049, 0.460, 0.105, 0.747, 0.404, 0.067, 0.133, 0.330],
    ),
    "shaath": (
        [6.6, 2.0, 3.5, 2.3, 4.6, 4.0, 2.5, 5.2, 2.4, 3.7, 2.3, 3.4],
        [6.5, 2.7, 3.5, 5.4, 2.6, 3.5, 2.5, 5.2, 4.0, 2.7, 4.3, 3.2],
    ),
}


def _zcorr(a: np.ndarray, b: np.ndarray) -> float:
    a = (a - a.mean()) / (a.std() + 1e-9)
    b = (b - b.mean()) / (b.std() + 1e-9)
    return float((a * b).mean())


TRIADS = {}
for _r in range(12):
    for _q, _iv in (("M", (0, 4, 7)), ("m", (0, 3, 7))):
        _v = np.zeros(12)
        _v[[(_r + i) % 12 for i in _iv]] = 1
        TRIADS[(_r, _q)] = _v
# weight of a chord (degree above the tonic, quality) as evidence for a key
CHORD_FUNC = {
    "major": {(0, "M"): 1.0, (5, "M"): 0.55, (7, "M"): 0.6, (9, "m"): 0.35, (2, "m"): 0.3, (4, "m"): 0.25},
    "minor": {(0, "m"): 1.0, (5, "m"): 0.55, (7, "m"): 0.45, (7, "M"): 0.55, (8, "M"): 0.35, (3, "M"): 0.3, (10, "M"): 0.35},
}
CHORD_WEIGHT = 2.0
BASS_WEIGHT = 0.15


def key_features(y: np.ndarray, bpm: float, first_downbeat: float, sr: int = SR) -> dict:
    """Compact harmonic description of a track, stored so keys can be re-scored without re-analysis."""
    harm = librosa.effects.harmonic(y, margin=2.0)
    C = librosa.feature.chroma_cqt(y=harm, sr=sr, hop_length=HOP, fmin=librosa.note_to_hz("C2"), n_octaves=6, bins_per_octave=36, tuning=0.0)
    B = librosa.feature.chroma_cqt(y=harm, sr=sr, hop_length=HOP, fmin=librosa.note_to_hz("C1"), n_octaves=2, bins_per_octave=36, tuning=0.0)
    rms = librosa.feature.rms(y=harm, hop_length=HOP)[0]
    n = min(C.shape[1], B.shape[1], len(rms))
    C, B, rms = C[:, :n], B[:, :n], rms[:n]
    w = rms / (rms.max() + 1e-9)
    times = librosa.frames_to_time(np.arange(n), sr=sr, hop_length=HOP)
    bar = 240.0 / bpm
    start = first_downbeat
    while start - bar >= 0:
        start -= bar
    chords, energy = [], []
    for a in np.arange(start, times[-1] - bar / 2, bar):
        m = (times >= a) & (times < a + bar)
        if m.sum() < 2:
            continue
        c, b = C[:, m].mean(axis=1), B[:, m].mean(axis=1)
        best = max(TRIADS, key=lambda k: _zcorr(c, TRIADS[k]) + 0.3 * b[k[0]] / (b.max() + 1e-9))
        chords.append(f"{best[0]}{best[1]}")
        energy.append(round(float(rms[m].mean()), 5))
    return {
        "chroma": [round(float(v), 4) for v in (C * w).sum(axis=1)],
        "bass": [round(float(v), 4) for v in (B * w).sum(axis=1)],
        "chords": chords,
        "bar_energy": energy,
    }


def key_scores(f: dict, learned: dict | None = None) -> np.ndarray:
    """2x12 score matrix (major/minor x tonic pitch class)."""
    chroma, bass = np.array(f["chroma"]), np.array(f["bass"])
    profs = [PROFILES["shaath"]]
    if learned:
        profs = profs + [(learned["major"], learned["minor"])] * 2  # trust the user's own library more
    s = np.zeros((2, 12))
    for maj, minr in profs:
        maj, minr = np.array(maj), np.array(minr)
        for pc in range(12):
            s[0, pc] += _zcorr(chroma, np.roll(maj, pc))
            s[1, pc] += _zcorr(chroma, np.roll(minr, pc))
    s /= len(profs)
    bp = bass / (bass.max() + 1e-9)
    s += BASS_WEIGHT * (bp - bp.mean())[None, :]
    # chord evidence: which key explains the bar-by-bar chords best (tonic on phrase starts counts more)
    ch = [(int(c[:-1]), c[-1]) for c in f.get("chords", [])]
    if ch:
        be = np.array(f.get("bar_energy") or [1.0] * len(ch))
        be = be / (be.max() + 1e-9)
        cs = np.zeros((2, 12))
        for mi, mode in enumerate(("major", "minor")):
            func = CHORD_FUNC[mode]
            for pc in range(12):
                tot = 0.0
                for i, (r, q) in enumerate(ch):
                    v = func.get(((r - pc) % 12, q), -0.4)
                    if v == 1.0 and i % 4 == 0:
                        v = 1.5
                    elif v == 1.0 and i % 4 == 3:
                        v = 1.2
                    tot += v * be[i]
                cs[mi, pc] = tot / max(1e-9, be.sum())
        s += CHORD_WEIGHT * cs
    return s


def key_from_features(f: dict, learned: dict | None = None) -> dict:
    s = key_scores(f, learned)
    order = np.argsort(s.ravel())[::-1]
    codes = []
    for idx in order[:2]:
        mode_i, pc = divmod(int(idx), 12)
        codes.append(camelot.to_camelot(pc, "major" if mode_i == 0 else "minor"))
    flat = s.ravel()[order]
    spread = flat[0] - np.median(flat)
    confidence = float(np.clip((flat[0] - flat[1]) / (spread + 1e-9) * 2.5, 0, 1))
    return {"key": codes[0], "key_name": camelot.standard_name(codes[0]), "key_confidence": round(confidence, 3), "key2": codes[1]}


def learn_profiles(items: list[tuple[dict, str]]) -> dict | None:
    """Average tonic-aligned chroma per mode from tracks whose key the user trusts (e.g. MIK tags)."""
    acc = {"major": [], "minor": []}
    for f, code in items:
        pc, mode = camelot.camelot_to_pitch(code)
        v = np.roll(np.array(f["chroma"], dtype=float), -pc)
        v = (v - v.mean()) / (v.std() + 1e-9)
        acc[mode].append(v)
    if len(acc["major"]) < 5 or len(acc["minor"]) < 5:
        return None
    return {m: [round(float(x), 4) for x in np.mean(acc[m], axis=0)] for m in acc}


def _fold_bpm(bpm: float, lo: float = 78.0, hi: float = 180.0) -> float:
    while bpm < lo:
        bpm *= 2
    while bpm > hi:
        bpm /= 2
    return bpm


def _comb_fit(env: np.ndarray, fps: float, bpm0: float, span: float, step: float) -> tuple[float, float, float]:
    """Find (bpm, phase, score) maximising onset strength sampled on a straight beat grid."""
    t_env = np.arange(len(env)) / fps
    dur = t_env[-1]
    best = (bpm0, 0.0, -1.0)
    for bpm in np.arange(bpm0 - span, bpm0 + span + 1e-9, step):
        period = 60.0 / bpm
        phases = np.linspace(0, period, 48, endpoint=False)
        k = np.arange(int(dur / period) - 1)
        grid = phases[:, None] + k[None, :] * period  # (phases, beats)
        vals = np.interp(grid, t_env, env).mean(axis=1)
        i = int(np.argmax(vals))
        if vals[i] > best[2]:
            best = (float(bpm), float(phases[i]), float(vals[i]))
    return best


def _attack_times(y: np.ndarray, sr: int, beats: np.ndarray, win: float = 0.05) -> tuple[np.ndarray, np.ndarray]:
    """Precise kick attack time near each predicted beat: where the low-band (<160 Hz) energy
    first reaches half of its jump (ms accuracy, no STFT frame bias). A sustained sub bass or
    drone is ignored because it does not jump. Returns (times, strength)."""
    from scipy.signal import butter, sosfiltfilt

    sos = butter(4, 160, btype="low", fs=sr, output="sos")
    lo = sosfiltfilt(sos, y)
    k = max(1, int(0.012 * sr))  # >= one ripple period of a 40 Hz bass, centred
    env = np.convolve(lo * lo, np.ones(k) / k, mode="same")
    w, pre0, pre1 = int(win * sr), int(0.075 * sr), int(0.02 * sr)
    times, strength = np.full(len(beats), np.nan), np.zeros(len(beats))
    for i, b in enumerate(beats):
        c = int(b * sr)
        a0, a1 = max(0, c - w), min(len(env), c + w)
        if a1 - a0 < w:
            continue
        seg = env[a0:a1]
        pk = int(np.argmax(seg))
        p_abs = a0 + pk
        before = env[max(0, p_abs - pre0) : max(1, p_abs - pre1)]
        base = float(np.median(before)) if len(before) else 0.0
        peak = float(seg[pk])
        if peak <= 1e-9 or peak < 2.5 * (base + 1e-12):
            continue  # nothing hits here (or only a sustained bass)
        thr = base + 0.5 * (peak - base)
        j = int(np.argmax(seg[: pk + 1] >= thr))
        times[i] = (a0 + j) / sr
        strength[i] = np.sqrt(peak) - np.sqrt(base)
    return times, strength


def _tempo_steady(env: np.ndarray, fps: float, hop: int, sr: int, bpm: float) -> bool:
    """Does the tempo stay constant? Local tempo over 32-beat windows of a beat tracker that is
    free to follow tempo changes (live drums, old records, DJ edits with a tempo ramp)."""
    try:
        _, bt = librosa.beat.beat_track(onset_envelope=env, sr=sr, hop_length=hop, bpm=bpm, tightness=100, units="time")
    except Exception:
        return True
    if len(bt) < 80:
        return True
    period = 60.0 / bpm
    ioi = np.diff(bt)
    local = []
    for i in range(0, len(bt) - 33, 16):
        if np.all(np.abs(ioi[i : i + 32] / period - 1) < 0.25):
            local.append(32 / (bt[i + 32] - bt[i]))
    if len(local) < 4:
        return True
    local = np.array(local)
    return float((np.percentile(local, 90) - np.percentile(local, 10)) / np.median(local)) < 0.004


def _fit_grid(y: np.ndarray, sr: int, bpm: float, phase: float, tol: float = 0.012) -> tuple[float, float, float, dict]:
    """Robust straight-line fit of the beat grid to the real kick attacks over the whole track.
    Returns (bpm, phase, rms residual s, info) — info tells whether the tempo is steady."""
    period = 60.0 / bpm
    dur = len(y) / sr
    beats = phase + np.arange(int((dur - phase) / period)) * period
    t, st = _attack_times(y, sr, beats)
    ok = ~np.isnan(t)
    if ok.sum() < 16:
        return bpm, phase, 1.0, {"steady": True, "used": int(ok.sum())}
    ok &= st > 0.25 * np.percentile(st[ok], 90)
    idx = np.arange(len(beats))
    for _ in range(4):
        if ok.sum() < 16:
            break
        A = np.vstack([np.ones(ok.sum()), idx[ok]]).T
        (p0, per), *_ = np.linalg.lstsq(A, t[ok], rcond=None)
        res = t - (p0 + per * idx)
        mad = np.nanmedian(np.abs(res[ok])) + 1e-4
        ok &= np.abs(res) < max(tol, 3 * mad)
    A = np.vstack([np.ones(ok.sum()), idx[ok]]).T
    (p0, per), *_ = np.linalg.lstsq(A, t[ok], rcond=None)
    rms = float(np.sqrt(np.mean((t[ok] - (p0 + per * idx[ok])) ** 2)))
    # steady tempo? compare the first and second half of the track
    steady = True
    h = idx[ok]
    if len(h) > 64:
        mid = np.median(h)
        pers = []
        for m in (h < mid, h >= mid):
            if m.sum() > 16:
                pers.append(np.polyfit(h[m], t[ok][m], 1)[0])
        if len(pers) == 2 and abs(pers[0] - pers[1]) / per > 0.0015:
            steady = False
    return 60.0 / per, float(p0 % per), rms, {"steady": steady, "used": int(ok.sum())}


def detect_tempo(y: np.ndarray, sr: int = SR, bpm_range: tuple[float, float] = (78.0, 180.0)) -> dict:
    hop = 256
    fps = sr / hop
    env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop, aggregate=np.median)
    env = env / (env.max() + 1e-9)
    tempo = librosa.feature.tempo(onset_envelope=env, sr=sr, hop_length=hop, start_bpm=124, max_tempo=200)
    bpm0 = _fold_bpm(float(np.atleast_1d(tempo)[0]), *bpm_range)
    # coarse then fine comb search around the estimate
    bpm, phase, _ = _comb_fit(env, fps, bpm0, 3.0, 0.1)
    bpm, phase, _ = _comb_fit(env, fps, bpm, 0.12, 0.005)
    # Most tracks are produced at an integer (or .5) BPM. Snap only when the grid fits at least
    # as well there; a wrong snap would make synced decks drift over a few minutes.
    _, _, fit_score = _comb_fit(env, fps, bpm, 0.0, 1.0)
    for snapped in (round(bpm), round(bpm * 2) / 2):
        if abs(snapped - bpm) < 0.05 and _comb_fit(env, fps, float(snapped), 0.0, 1.0)[2] >= fit_score * 0.998:
            bpm = float(snapped)
            break
    # phase from the low band (kick drum), so the grid does not lock onto off-beat hats
    mel = librosa.feature.melspectrogram(y=y, sr=sr, hop_length=hop, n_mels=64, fmax=8000)
    mel_f = librosa.mel_frequencies(n_mels=64, fmax=8000)
    low_env = librosa.onset.onset_strength(S=librosa.power_to_db(mel[mel_f < 180]), sr=sr, hop_length=hop)
    low_env = low_env / (low_env.max() + 1e-9)
    _, phase, _ = _comb_fit(low_env + 0.3 * env[: len(low_env)], fps, bpm, 0.0, 1.0)
    # precise grid from the real kick attacks (fixes slow drift and the onset-detector lag)
    steady = _tempo_steady(env, fps, hop, sr, bpm)
    bpm_f, phase_f, rms, info = _fit_grid(y, sr, bpm, max(0.0, phase - hop / sr), tol=0.012 if steady else 0.06)
    if info["used"] >= 32 and abs(bpm_f - bpm) < 0.5:
        steady = steady and info["steady"]
        bpm_r = round(bpm_f * 2) / 2
        if abs(bpm_r - bpm_f) < 0.03:  # produced at an integer / .5 BPM? keep it if it fits as well
            b2, p2, rms2, _ = _fit_grid(y, sr, bpm_r, phase_f)
            per_r = 60.0 / bpm_r
            n = (len(y) / sr) / per_r
            if abs(b2 - bpm_r) * n * per_r / bpm_r < 0.004:
                bpm_f, phase_f = bpm_r, p2
        bpm, phase = float(bpm_f), float(phase_f) + hop / sr  # (phase gets the lag removed below)
    period = 60.0 / bpm
    # downbeat: beat-in-bar position with the strongest low-band + harmonic change
    S = np.abs(librosa.stft(y, n_fft=2048, hop_length=HOP))
    freqs = librosa.fft_frequencies(sr=sr, n_fft=2048)
    low = S[freqs < 150].sum(axis=0)
    low_nov = np.maximum(0, np.diff(low, prepend=low[0]))
    chroma = librosa.feature.chroma_stft(S=S**2, sr=sr)
    ch_nov = np.concatenate([[0], np.linalg.norm(np.diff(chroma, axis=1), axis=0)])
    nov = low_nov / (low_nov.max() + 1e-9) + 2 * ch_nov / (ch_nov.max() + 1e-9)
    dur = len(y) / sr
    n_beats = int((dur - phase) / period)
    beat_frames = librosa.time_to_frames(phase + np.arange(n_beats) * period, sr=sr, hop_length=HOP)
    beat_frames = beat_frames[beat_frames < len(nov)]
    # take max in a small window around each beat to tolerate grid jitter
    strength = np.array([nov[max(0, f - 2) : f + 3].max() for f in beat_frames]) if len(beat_frames) else np.zeros(4)
    # big changes (drops, breakdowns, new layers) land on the first beat of a bar
    rms_b = np.array([low[max(0, f - 2) : f + 3].mean() for f in beat_frames]) if len(beat_frames) else np.zeros(4)
    jump = np.zeros(len(rms_b))
    for i in range(8, len(rms_b) - 8):
        jump[i] = abs(np.log1p(rms_b[i : i + 8].mean()) - np.log1p(rms_b[i - 8 : i].mean()))
    if jump.max() > 0:
        jump /= jump.max()
    big = jump * (jump > 0.3)

    def phase_score(k):
        sl = strength[k::4]
        return (sl.mean() if len(sl) else 0) + 1.5 * big[k::4].sum() / max(1.0, big.sum())

    best = max(range(4), key=phase_score)
    # onset envelopes peak about one hop after the attack
    first_downbeat = max(0.0, phase + best * period - hop / sr)
    phase = max(0.0, phase - hop / sr)
    return {"bpm": round(bpm, 4), "beat_offset": round(float(phase), 4), "first_downbeat": round(float(first_downbeat), 4),
            "tempo_steady": bool(steady)}


def _energy_raw(db_active: float, onset_rate: float, centroid: float, bpm: float) -> float:
    loud = np.clip((db_active + 32) / 24, 0, 1)
    dens = np.clip(onset_rate / 6.0, 0, 1)
    bright = np.clip((centroid - 900) / 3000, 0, 1)
    tempo = np.clip((bpm - 80) / 90, 0, 1)
    return float(0.45 * loud + 0.2 * dens + 0.2 * bright + 0.15 * tempo)


def to_level(raw: float) -> int:
    return int(np.clip(round(1 + 9 * raw), 1, 10))


def _beat_kicks(y: np.ndarray, sr: int, beats: np.ndarray) -> np.ndarray:
    """Kick strength on each beat: how sharply the low end (<160 Hz) jumps right at the beat.
    A sustained bassline or pad in a breakdown gives ~0, a kick gives a clear attack."""
    _, st = _attack_times(y, sr, beats, win=0.04)
    return st


def _kick_threshold(k: np.ndarray) -> float:
    """Split beats into 'kick' / 'no kick' (Otsu on log strength), never below 20% of a loud kick."""
    ref = float(np.percentile(k, 90)) if len(k) else 1.0
    v = np.log(k[k > 1e-6] + 1e-9)
    if len(v) < 16:
        return 0.35 * ref
    hist, edges = np.histogram(v, bins=40)
    centers = (edges[:-1] + edges[1:]) / 2
    best, thr = -1.0, float(np.median(v))
    for i in range(1, len(hist)):
        w0, w1 = hist[:i].sum(), hist[i:].sum()
        if not w0 or not w1:
            continue
        m0 = (hist[:i] * centers[:i]).sum() / w0
        m1 = (hist[i:] * centers[i:]).sum() / w1
        between = w0 * w1 * (m0 - m1) ** 2
        if between > best:
            best, thr = between, float(edges[i])
    return max(float(np.exp(thr)), 0.2 * ref)


def _runs(state: np.ndarray) -> list[tuple[bool, int, int]]:
    out, i = [], 0
    while i < len(state):
        j = i
        while j < len(state) and state[j] == state[i]:
            j += 1
        out.append((bool(state[i]), i, j))
        i = j
    return out


def analyze_structure(y: np.ndarray, bpm: float, first_downbeat: float, sr: int = SR) -> dict:
    """Energy level, cue points on real section changes and the section type of each part.

    Cues go where DJs expect them: the first beat of the music, the beat where the kick drops
    out (breakdown) and the beat where it comes back (drop), then other strong changes on the
    4/8-bar phrase grid. The kick changes also vote for the bar's downbeat ("1")."""
    dur = len(y) / sr
    rms = librosa.feature.rms(y=y, hop_length=HOP)[0]
    db = librosa.amplitude_to_db(rms, ref=1.0)
    onset_env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=HOP)
    onsets = librosa.onset.onset_detect(onset_envelope=onset_env, sr=sr, hop_length=HOP, units="time")
    centroid = librosa.feature.spectral_centroid(y=y, sr=sr, hop_length=HOP)[0]
    mfcc = librosa.feature.mfcc(y=y, sr=sr, hop_length=HOP, n_mfcc=13)
    chroma = librosa.feature.chroma_stft(y=y, sr=sr, hop_length=HOP)
    times = librosa.frames_to_time(np.arange(len(rms)), sr=sr, hop_length=HOP)

    active = db > np.percentile(db, 40)
    db_active = float(db[active].mean()) if active.any() else float(db.mean())
    onset_rate = len(onsets) / max(dur, 1)
    cen = float(centroid[active].mean()) if active.any() else float(centroid.mean())
    track_raw = _energy_raw(db_active, onset_rate, cen, bpm)
    energy = to_level(track_raw)

    beat = 60.0 / bpm
    bar = 4 * beat
    # --- kick on every beat of the grid ---
    b0 = first_downbeat - math.floor(first_downbeat / beat + 0.03) * beat  # first beat (>= -1 ms)
    b0 = max(b0, 0.0)
    beats = np.arange(b0, dur - 0.05, beat)
    kicks = _beat_kicks(y, sr, beats)
    kthr = _kick_threshold(kicks)
    has_kick = kicks > kthr
    soft_kick = kicks > 0.5 * kthr  # the first kick of a drop can be masked by the riser/drone tail
    # beat index of the first downbeat
    d0 = int(round((first_downbeat - b0) / beat))

    def kick_events(phase: int):
        """(kind, beat index) for kick-out runs of >= 2 bars, with bars starting at `phase`."""
        nbar = (len(beats) - phase) // 4
        if nbar < 4:
            return [], np.zeros(0, bool), nbar
        kb = has_kick[phase : phase + nbar * 4].reshape(nbar, 4).mean(axis=1) >= 0.5
        ev = []
        runs = _runs(kb)
        for k, (state, s0, e0) in enumerate(runs):
            if state or e0 - s0 < 2:
                continue
            if s0 > 0:  # kick stops: the beat after the last kick
                last = phase + s0 * 4 - 1
                while last > 0 and not has_kick[last]:
                    last -= 1
                ev.append(("out", last + 1, e0 - s0))
            if e0 < nbar:  # kick returns: the first kick beat
                first = phase + e0 * 4 - 4
                while first < len(has_kick) - 1 and not (soft_kick[first] and has_kick[first + 1: first + 3].any()):
                    first += 1
                ev.append(("in", first, e0 - s0))
        return ev, kb, nbar

    # downbeat vote: section changes almost always land on the "1"
    ev, _, _ = kick_events(d0 % 4)
    votes = np.zeros(4)
    for kind, bi, n in ev:
        votes[bi % 4] += 2 if kind == "in" else 1
    if votes.sum() >= 3:
        best = int(np.argmax(votes))
        if best != d0 % 4 and votes[best] >= 0.6 * votes.sum():
            d0 = best
    first_downbeat = float(beats[d0 % 4]) if len(beats) > d0 % 4 else first_downbeat
    phase = d0 % 4
    ev, kick_bar, nkb = kick_events(phase)

    # --- bar features (bars start on the downbeat) ---
    start = first_downbeat
    while start - bar >= -0.01:
        start -= bar
    start = max(0.0, start)
    edges = np.arange(start, dur + bar, bar)
    feats, bar_db, bar_on, bar_cen = [], [], [], []
    for a, b in zip(edges[:-1], edges[1:]):
        m = (times >= a) & (times < b)
        if not m.any():
            m = np.zeros_like(times, dtype=bool)
            m[min(len(times) - 1, int(a * sr / HOP))] = True
        bd = float(db[m].mean())
        bar_db.append(bd)
        bar_on.append(float(((onsets >= a) & (onsets < b)).sum()) / bar)
        bar_cen.append(float(centroid[m].mean()))
        feats.append(np.concatenate([[bd / 10.0], mfcc[1:, m].mean(axis=1) / 50.0, chroma[:, m].mean(axis=1)]))
    F = np.array(feats)
    nb = len(F)
    bdb = np.array(bar_db)
    Fn = (F - F.mean(axis=0)) / (F.std(axis=0) + 1e-6)
    Fn /= np.linalg.norm(Fn, axis=1, keepdims=True) + 1e-9
    ssm = Fn @ Fn.T
    L = 4
    nov = np.zeros(nb)
    for i in range(L, nb - L + 1):
        nov[i] = ssm[i - L : i, i - L : i].mean() + ssm[i : i + L, i : i + L].mean() - 2 * ssm[i - L : i, i : i + L].mean()
    jump = np.zeros(nb)
    for i in range(2, nb - 1):
        jump[i] = abs(bdb[i : i + 2].mean() - bdb[i - 2 : i].mean()) / 6.0
    score = nov / (nov.max() + 1e-9) + jump

    def bar_of(t: float) -> int:
        return int(round((t - start) / bar))

    def beat_time(bi: int) -> float:
        return float(beats[min(bi, len(beats) - 1)])

    # --- cue candidates: (priority, time, kind) ---
    cands: list[tuple[float, float, str]] = []
    silent = bdb.max() - 35 if nb else 0
    first_bar = int(np.argmax(bdb > silent)) if nb else 0
    # first beat of the music (its first transient on the grid)
    beat_db = np.array([db[max(0, f - 2) : f + 3].max() for f in librosa.time_to_frames(beats, sr=sr, hop_length=HOP)]) if len(beats) else np.zeros(1)
    loud_beats = np.flatnonzero(beat_db > silent)
    t_first = beat_time(int(loud_beats[0])) if len(loud_beats) else 0.0
    cands.append((100.0, t_first, "intro"))
    end_t = dur - 8 * bar  # a kick change in the last bars (fade-out) is no use as a cue
    for kind, bi, n in ev:
        t = beat_time(bi)
        if t >= end_t or t <= t_first + beat:
            continue
        near_end = t > dur * 0.8
        if kind == "out":
            k = "outro" if near_end and not any(e[0] == "in" and beat_time(e[1]) > t for e in ev) else "break"
            cands.append((50 + min(n, 16) * 2, t, k))
        else:
            cands.append((60 + min(n, 16) * 2, t, "drop" if n >= 4 else "main"))
    # phrase grid anchored on the first drop (or the first kick)
    anchors = [t for _, t, k in cands if k == "drop"] or [t_first]
    origin = bar_of(anchors[0]) % 4
    for i in np.argsort(-score):
        if score[i] < 0.6 or i <= first_bar or i >= nb - 2:
            continue
        if score[i] < score[max(0, i - 2) : i + 3].max():
            continue  # only the peak of a change, never a neighbouring bar
        if (i - origin) % 4 and score[i] < 1.0:  # off the phrase grid only for a clear change
            continue
        up = bdb[i : i + 2].mean() - bdb[max(0, i - 2) : i].mean()
        cands.append((10 + 10 * float(score[i]), float(edges[i]), "drop" if up >= 6 else "main"))
    # pick the strongest, at least 4 bars apart (8 for weaker ones)
    chosen: list[tuple[float, str]] = []
    for pr, t, k in sorted(cands, key=lambda c: -c[0]):
        if len(chosen) >= 8:
            break
        if all(abs(t - c) >= 4 * bar - 0.05 for c, _ in chosen):
            chosen.append((t, k))
    # tracks with few clear changes: add phrase cues (every 32, then 16 bars), but only where
    # the kick plays, so a cue never lands in the middle of a breakdown
    o_bar = bar_of(anchors[0])
    for step in (32, 16):
        for i in range(o_bar % step, nb - 8, step):
            if len(chosen) >= 4:
                break
            t = float(edges[i])
            in_kick = nkb == 0 or (i < nkb and kick_bar[i:i + 4].mean() >= 0.75)
            if i > first_bar and in_kick and all(abs(t - c) >= (step // 2) * bar - 0.05 for c, _ in chosen):
                chosen.append((t, "main"))
    chosen.sort()
    # name the parts from what is actually playing in them
    labelled = []
    for idx, (t, k) in enumerate(chosen):
        t_next = chosen[idx + 1][0] if idx + 1 < len(chosen) else dur
        b_a, b_b = bar_of(t), max(bar_of(t) + 1, bar_of(t_next))
        kb = kick_bar[max(0, min(b_a, nkb)) : max(0, min(b_b, nkb))] if nkb else np.zeros(0, bool)
        kick_frac = float(kb.mean()) if len(kb) else 1.0
        if idx == 0:
            k = "intro"
        elif k == "main" and kick_frac < 0.5:
            k = "build" if idx + 1 < len(chosen) and chosen[idx + 1][1] in ("drop", "main") else "break"
        labelled.append((t, k))
    if len(labelled) > 1 and labelled[-1][1] == "main" and dur - labelled[-1][0] < 0.25 * dur:
        a, p0 = bar_of(labelled[-1][0]), bar_of(labelled[-2][0])
        if a < nb and bdb[a:].mean() < bdb[p0:a].mean() - 1.0:
            labelled[-1] = (labelled[-1][0], "outro")
    cues = [round(t, 3) for t, _ in labelled]

    # segment energy between cues
    segments = []
    bounds = [bar_of(t) for t in cues] + [nb]
    for (t, k), a, b in zip(labelled, bounds[:-1], bounds[1:]):
        a = max(0, min(a, nb - 1))
        sl = slice(a, max(a + 1, b))
        raw = _energy_raw(float(np.mean(bdb[sl])), float(np.mean(bar_on[sl])), float(np.mean(bar_cen[sl])), bpm)
        lvl = int(np.clip(round(energy + (raw - track_raw) * 9 * 1.3), 1, 10))
        segments.append({"start": round(t, 3), "end": round(float(edges[min(b, nb)]) if b < nb else dur, 3), "energy": lvl, "kind": k})
    for i in range(len(segments) - 1):
        segments[i]["end"] = segments[i + 1]["start"]
    return {"energy": energy, "cues": cues, "segments": segments, "first_downbeat": round(first_downbeat, 4),
            "bar_db": [round(v, 2) for v in bdb.tolist()]}


def waveform(path: str, duration_hint: float | None = None) -> dict:
    y = decode(path, sr=SR, mono=True)
    hop = SR // WAVE_PPS
    S = np.abs(librosa.stft(y, n_fft=1024, hop_length=hop)) ** 2
    freqs = librosa.fft_frequencies(sr=SR, n_fft=1024)
    bands = [S[freqs < 200].sum(axis=0), S[(freqs >= 200) & (freqs < 2500)].sum(axis=0), S[freqs >= 2500].sum(axis=0)]
    bands = [np.sqrt(b) for b in bands]
    n = min(len(b) for b in bands)
    pad = (-len(y)) % hop
    yy = np.pad(np.abs(y), (0, pad)).reshape(-1, hop).max(axis=1)
    n = min(n, len(yy))
    peak = yy[:n] / (yy.max() + 1e-9)
    total = bands[0][:n] + bands[1][:n] + bands[2][:n] + 1e-9
    out = {"pps": WAVE_PPS, "duration": len(y) / SR, "peak": np.round(peak * 255).astype(int).tolist()}
    for name, b in zip(("low", "mid", "high"), bands):
        out[name] = np.round(np.clip(b[:n] / total, 0, 1) * 255).astype(int).tolist()
    return out


def analyze_file(path: str, learned: dict | None = None, bpm_range: tuple[float, float] = (78.0, 180.0)) -> dict:
    y = decode(path, sr=SR, mono=True)
    if len(y) < SR * 5:
        raise ValueError("Plik jest za krótki (min. 5 s).")
    dur = len(y) / SR
    # Key detection on up to ~8 minutes from the middle for speed
    max_len = SR * 480
    yk = y if len(y) <= max_len else y[(len(y) - max_len) // 2 : (len(y) + max_len) // 2]
    tempo = detect_tempo(y, bpm_range=bpm_range)
    struct = analyze_structure(y, tempo["bpm"], tempo["first_downbeat"])
    tempo["first_downbeat"] = struct.pop("first_downbeat")  # may be corrected by the section changes
    feats = key_features(yk, tempo["bpm"], tempo["first_downbeat"] - (len(y) - len(yk)) / 2 / SR)
    key = key_from_features(feats, learned)
    return {"duration": round(dur, 3), **key, **tempo, **struct, "key_features": feats}


def beat_deviation(y: np.ndarray, sr: int, bpm: float, offset: float) -> list[float]:
    """How far the real kick sits from the straight beat grid, per beat (seconds, smoothed, median 0).
    Synced decks use it to follow the actual beats, so tracks whose tempo wanders a little
    (live drums, old records, slightly wrong BPM) stay locked instead of slowly drifting apart."""
    period = 60.0 / bpm
    dur = len(y) / sr
    n = int((dur - offset) / period) + 1
    beats = offset + np.arange(max(0, n)) * period
    if len(beats) < 16:
        return []

    def smooth(dev: np.ndarray, w: int = 8) -> np.ndarray:
        ok = ~np.isnan(dev)
        if ok.sum() < 8:
            return np.zeros(len(dev))
        idx = np.arange(len(dev))
        out = np.array([np.nanmedian(dev[max(0, i - w): i + w + 1]) if ok[max(0, i - w): i + w + 1].sum() >= 3 else np.nan for i in idx])
        good = ~np.isnan(out)
        return np.interp(idx, idx[good], out[good])

    t, st = _attack_times(y, sr, beats, win=0.08)
    dev = t - beats
    if (~np.isnan(dev)).sum() < 16:
        return []
    weak = st < 0.25 * np.nanpercentile(st[st > 0], 90) if (st > 0).any() else np.ones(len(st), bool)
    dev[weak] = np.nan
    s1 = smooth(dev)
    # second pass: narrow window around where the kicks actually are (follows larger drifts)
    t2, st2 = _attack_times(y, sr, beats + s1, win=0.03)
    dev2 = t2 - beats
    dev2[st2 < 0.25 * np.percentile(st2, 90)] = np.nan
    s2 = smooth(dev2)
    s2 -= np.median(s2)
    return [round(float(x), 4) for x in s2]
