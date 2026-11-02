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


def detect_key(y: np.ndarray, sr: int = SR) -> dict:
    harm = librosa.effects.harmonic(y, margin=2.0)
    chroma = librosa.feature.chroma_cqt(y=harm, sr=sr, hop_length=HOP * 2, fmin=librosa.note_to_hz("C2"), n_octaves=6, bins_per_octave=36)
    # weight frames by their harmonic energy so silent/noisy parts count less
    rms = librosa.feature.rms(y=harm, hop_length=HOP * 2)[0][: chroma.shape[1]]
    w = rms / (rms.max() + 1e-9)
    profile = (np.log1p(10 * chroma) * w).sum(axis=1)
    # bass chroma helps separate relative major/minor (the tonic sits in the bass most often)
    bass = librosa.feature.chroma_cqt(y=harm, sr=sr, hop_length=HOP * 2, fmin=librosa.note_to_hz("C1"), n_octaves=2, bins_per_octave=36)
    bass_prof = (bass[:, : len(w)] * w[: bass.shape[1]]).sum(axis=1)
    bass_prof = bass_prof / (bass_prof.max() + 1e-9)
    scores = np.zeros((2, 12))
    for maj, minr in PROFILES.values():
        maj, minr = np.array(maj), np.array(minr)
        for pc in range(12):
            scores[0, pc] += _zcorr(profile, np.roll(maj, pc))
            scores[1, pc] += _zcorr(profile, np.roll(minr, pc))
    scores /= len(PROFILES)
    for pc in range(12):
        scores[:, pc] += 0.25 * (bass_prof[pc] - bass_prof.mean())
    mode_i, pc = np.unravel_index(np.argmax(scores), scores.shape)
    mode = "major" if mode_i == 0 else "minor"
    flat = np.sort(scores.ravel())
    confidence = float(np.clip((flat[-1] - flat[-2]) * 10, 0, 1))
    code = camelot.to_camelot(int(pc), mode)
    return {"key": code, "key_name": camelot.standard_name(code), "key_confidence": round(confidence, 3)}


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


def detect_tempo(y: np.ndarray, sr: int = SR) -> dict:
    hop = 256
    fps = sr / hop
    env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop, aggregate=np.median)
    env = env / (env.max() + 1e-9)
    tempo = librosa.feature.tempo(onset_envelope=env, sr=sr, hop_length=hop, start_bpm=124, max_tempo=200)
    bpm0 = _fold_bpm(float(np.atleast_1d(tempo)[0]))
    # coarse then fine comb search around the estimate
    bpm, phase, _ = _comb_fit(env, fps, bpm0, 3.0, 0.1)
    bpm, phase, _ = _comb_fit(env, fps, bpm, 0.12, 0.005)
    if abs(bpm - round(bpm)) < 0.06:
        bpm = float(round(bpm))
    # phase from the low band (kick drum), so the grid does not lock onto off-beat hats
    mel = librosa.feature.melspectrogram(y=y, sr=sr, hop_length=hop, n_mels=64, fmax=8000)
    mel_f = librosa.mel_frequencies(n_mels=64, fmax=8000)
    low_env = librosa.onset.onset_strength(S=librosa.power_to_db(mel[mel_f < 180]), sr=sr, hop_length=hop)
    low_env = low_env / (low_env.max() + 1e-9)
    _, phase, _ = _comb_fit(low_env + 0.3 * env[: len(low_env)], fps, bpm, 0.0, 1.0)
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
    best = max(range(4), key=lambda k: strength[k::4].mean() if len(strength[k::4]) else 0)
    # onset envelopes peak about one hop after the attack
    first_downbeat = max(0.0, phase + best * period - hop / sr)
    phase = max(0.0, phase - hop / sr)
    return {"bpm": round(bpm, 2), "beat_offset": round(float(phase), 4), "first_downbeat": round(float(first_downbeat), 4)}


def _energy_raw(db_active: float, onset_rate: float, centroid: float, bpm: float) -> float:
    loud = np.clip((db_active + 32) / 24, 0, 1)
    dens = np.clip(onset_rate / 6.0, 0, 1)
    bright = np.clip((centroid - 900) / 3000, 0, 1)
    tempo = np.clip((bpm - 80) / 90, 0, 1)
    return float(0.45 * loud + 0.2 * dens + 0.2 * bright + 0.15 * tempo)


def to_level(raw: float) -> int:
    return int(np.clip(round(1 + 9 * raw), 1, 10))


def analyze_structure(y: np.ndarray, bpm: float, first_downbeat: float, sr: int = SR) -> dict:
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

    # --- bar-level features ---
    bar = 4 * 60.0 / bpm
    start = first_downbeat
    while start - bar >= 0:
        start -= bar  # include pickup bars before the detected downbeat
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
    # checkerboard novelty on the bar self-similarity matrix
    Fn = (F - F.mean(axis=0)) / (F.std(axis=0) + 1e-6)
    Fn /= np.linalg.norm(Fn, axis=1, keepdims=True) + 1e-9
    ssm = Fn @ Fn.T
    L = 4
    nov = np.zeros(nb)
    for i in range(L, nb - L + 1):
        past = ssm[i - L : i, i - L : i].mean()
        fut = ssm[i : i + L, i : i + L].mean()
        cross = ssm[i - L : i, i : i + L].mean()
        nov[i] = past + fut - 2 * cross
    # loudness jumps are what DJs care about most (drops/breakdowns)
    bdb = np.array(bar_db)
    jump = np.zeros(nb)
    for i in range(2, nb - 1):
        jump[i] = abs(bdb[i : i + 2].mean() - bdb[i - 2 : i].mean()) / 6.0
    score = nov / (nov.max() + 1e-9) + jump
    # first musical bar = first bar louder than silence
    first_bar = int(np.argmax(bdb > (bdb.max() - 35))) if nb else 0
    cands = [i for i in range(first_bar + 4, nb - 2) if (i - first_bar) % 4 == 0]
    cands.sort(key=lambda i: -score[i])
    chosen = [first_bar]
    for i in cands:
        if len(chosen) >= 8:
            break
        if score[i] < 0.15:
            break
        if all(abs(i - c) >= 8 for c in chosen):
            chosen.append(i)
    # fill remaining cues on a 16/32-bar grid
    step = 32
    while len(chosen) < 8 and step >= 8:
        for i in range(first_bar + step, nb - 2, step):
            if len(chosen) >= 8:
                break
            if all(abs(i - c) >= step // 2 for c in chosen):
                chosen.append(i)
        step //= 2
    chosen = sorted(set(chosen))[:8]
    cues = [round(float(edges[i]), 3) for i in chosen]

    # segment energy between cues
    segments = []
    bounds = chosen + [nb]
    for a, b in zip(bounds[:-1], bounds[1:]):
        sl = slice(a, max(a + 1, b))
        seg_db = float(np.mean(bdb[sl]))
        seg_on = float(np.mean(bar_on[sl]))
        seg_cen = float(np.mean(bar_cen[sl]))
        raw = _energy_raw(seg_db, seg_on, seg_cen, bpm)
        lvl = int(np.clip(round(energy + (raw - track_raw) * 9 * 1.3), 1, 10))
        segments.append({"start": round(float(edges[a]), 3), "end": round(float(edges[min(b, nb)]), 3), "energy": lvl})
    return {"energy": energy, "cues": cues, "segments": segments, "bar_db": [round(v, 2) for v in bdb.tolist()]}


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


def analyze_file(path: str) -> dict:
    y = decode(path, sr=SR, mono=True)
    if len(y) < SR * 5:
        raise ValueError("Plik jest za krótki (min. 5 s).")
    dur = len(y) / SR
    # Key detection on up to ~8 minutes from the middle for speed
    max_len = SR * 480
    yk = y if len(y) <= max_len else y[(len(y) - max_len) // 2 : (len(y) + max_len) // 2]
    key = detect_key(yk)
    tempo = detect_tempo(y)
    struct = analyze_structure(y, tempo["bpm"], tempo["first_downbeat"])
    return {"duration": round(dur, 3), **key, **tempo, **struct}
