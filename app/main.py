"""Blendora HTTP API + static web UI."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import analysis, camelot, ffmpeg, mashup, stems, tags
from .db import CACHE, HOME, IMPORTED, DB
from .jobs import Analyzer, Jobs, run_stems, wave_path

WEB = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent)) / "web"
BROWSER_EXT = {".mp3", ".wav", ".flac", ".ogg", ".m4a", ".aac", ".opus"}
STEM_CHOICES = ("full", "vocals", "instrumental", "drums", "bass", "other")
STEM_LABEL = {"full": "", "vocals": " (Acapella)", "instrumental": " (Instrumental)", "drums": " (Perkusja)", "bass": " (Bas)", "other": " (Melodia)"}

db = DB()
jobs = Jobs()
analyzer = Analyzer(db)


@asynccontextmanager
async def lifespan(_app):
    _startup()
    yield


app = FastAPI(title="Blendora", lifespan=lifespan)


def _startup():
    if not analyzer.is_alive():
        analyzer.start()
    # drop rendered variants older than 14 days
    rdir = CACHE / "render"
    if rdir.exists():
        cutoff = time.time() - 14 * 86400
        for f in rdir.iterdir():
            if f.stat().st_mtime < cutoff:
                f.unlink(missing_ok=True)


def _track_or_404(tid: int) -> dict:
    t = db.track(tid)
    if not t:
        raise HTTPException(404, "Nie ma takiego utworu")
    return t


def _safe_name(s: str) -> str:
    return re.sub(r'[\\/:*?"<>|]+', "_", s).strip() or "track"


# ---------------------------------------------------------------- status / settings
@app.get("/api/status")
def status():
    counts = {r["status"]: r["n"] for r in db.q("SELECT status, COUNT(*) n FROM tracks GROUP BY status")}
    return {
        "home": str(HOME),
        "counts": counts,
        "analyzing": analyzer.current,
        "workers": len(analyzer.running),
        "rubberband": ffmpeg.has_rubberband(),
        "demucs": stems.demucs_available(),
        "jobs": jobs.active(),
    }


@app.get("/api/settings")
def get_settings():
    return db.settings()


@app.put("/api/settings")
def put_settings(values: dict):
    old = db.settings()
    new = db.set_settings({k: v for k, v in values.items() if k != "learned_profiles"})
    if new.get("bpm_range") != old.get("bpm_range"):
        refold_tempos()
    analyzer.kick()
    return new


def refold_tempos() -> int:
    """Apply the tempo range to analysed tracks (x2 / /2) without re-analysing them."""
    from .jobs import parse_range

    lo, hi = parse_range(db.settings().get("bpm_range"))
    n = 0
    for t in db.q("SELECT id, bpm FROM tracks WHERE status = 'done' AND bpm IS NOT NULL"):
        b = t["bpm"]
        while b < lo:
            b *= 2
        while b >= hi:
            b /= 2
        if abs(b - t["bpm"]) > 1e-6:
            db.update("tracks", t["id"], {"bpm": round(b, 3)})
            n += 1
    return n


# ---------------------------------------------------------------- key check against tags (e.g. Mixed In Key)
def _relation(a: str, b: str) -> str:
    if a == b:
        return "same"
    pa, ma = camelot.camelot_to_pitch(a)
    pb, mb = camelot.camelot_to_pitch(b)
    na, la = camelot.parse_camelot(a)
    nb, lb = camelot.parse_camelot(b)
    if na == nb:
        return "relative"
    if la == lb and (na - nb) % 12 in (1, 11):
        return "fifth"
    if pa == pb:
        return "parallel"
    return "other"


@app.get("/api/keycheck")
def keycheck():
    rows = db.q("SELECT id, artist, title, key, key2, key_confidence, tag_key, key_manual FROM tracks WHERE status = 'done' AND tag_key IS NOT NULL AND key IS NOT NULL")
    counts = {"same": 0, "relative": 0, "fifth": 0, "parallel": 0, "other": 0}
    diffs = []
    for r in rows:
        rel = _relation(r["key"], r["tag_key"])
        counts[rel] += 1
        if rel != "same":
            diffs.append({**r, "relation": rel})
    diffs.sort(key=lambda d: (d["relation"] == "relative", -(d["key_confidence"] or 0)))
    missing = db.one("SELECT COUNT(*) n FROM tracks WHERE status = 'done' AND key_features IS NULL")["n"]
    return {"total": len(rows), "counts": counts, "diffs": diffs[:300], "learned": bool(db.settings().get("learned_profiles")), "need_reanalysis": missing}


def _rescore_all(learned: dict | None) -> int:
    n = 0
    for t in db.q("SELECT id, key_features, key FROM tracks WHERE status = 'done' AND key_features IS NOT NULL AND COALESCE(key_manual, 0) = 0"):
        k = analysis.key_from_features(t["key_features"], learned)
        if k["key"] != t["key"]:
            n += 1
        db.update("tracks", t["id"], k)
    return n


@app.post("/api/keycheck/learn")
def keycheck_learn():
    """Learn key profiles from tracks whose tag key (e.g. from Mixed In Key) we trust, then re-score."""
    items = [(t["key_features"], t["tag_key"]) for t in db.q("SELECT key_features, tag_key FROM tracks WHERE key_features IS NOT NULL AND tag_key IS NOT NULL")]
    if len(items) < 20:
        raise HTTPException(400, f"Za mało utworów z tonacją w tagach i nową analizą ({len(items)}). Potrzeba co najmniej 20.")
    # honest estimate: 5-fold cross-validation, profiles never see the track they score
    import random

    random.Random(0).shuffle(items)
    folds = [items[i::5] for i in range(5)]
    before = after = 0
    for i, test in enumerate(folds):
        train = [x for j, f in enumerate(folds) if j != i for x in f]
        prof = analysis.learn_profiles(train)
        for f, code in test:
            before += analysis.key_from_features(f)["key"] == code
            after += analysis.key_from_features(f, prof)["key"] == code
    prof = analysis.learn_profiles(items)
    if not prof:
        raise HTTPException(400, "Potrzeba co najmniej 5 utworów durowych i 5 molowych z tonacją w tagach.")
    if after < before:
        return {"applied": False, "before": before / len(items), "after": after / len(items), "changed": 0}
    db.set_settings({"learned_profiles": prof})
    changed = _rescore_all(prof)
    return {"applied": True, "before": before / len(items), "after": after / len(items), "changed": changed}


@app.post("/api/keycheck/reset")
def keycheck_reset():
    db.set_settings({"learned_profiles": None})
    return {"changed": _rescore_all(None)}


@app.post("/api/tracks/rescan-tags")
def rescan_tag_keys():
    n = 0
    for t in db.q("SELECT id, path, comment FROM tracks"):
        k = tags.read_tag_key(t["path"], t["comment"] or "")
        db.update("tracks", t["id"], {"tag_key": k})
        n += bool(k)
    return {"with_key": n}


@app.get("/api/camelot")
def camelot_info():
    keys = [f"{n}{l}" for n in range(1, 13) for l in "AB"]
    return {k: {"name": camelot.standard_name(k), "short": camelot.standard_name(k, True), "compatible": camelot.compatible_keys(k)} for k in keys}


# ---------------------------------------------------------------- import
class FolderReq(BaseModel):
    path: str


def _add_file(path: str) -> int | None:
    path = os.path.abspath(path)
    tid = db.add_track(path, tags.read_tags(path))
    if tid:
        cov = tags.read_cover(path)
        if cov:
            (CACHE / "covers").mkdir(parents=True, exist_ok=True)
            (CACHE / "covers" / f"{tid}.img").write_bytes(cov[0])
            db.update("tracks", tid, {"has_cover": 1})
    return tid


@app.post("/api/import/folder")
def import_folder(req: FolderReq):
    root = os.path.expanduser(req.path.strip().strip('"'))
    if os.path.isfile(root) and tags.is_audio(root):
        files = [root]
    elif os.path.isdir(root):
        files = [os.path.join(d, f) for d, _, fs in os.walk(root) for f in fs if tags.is_audio(f)]
    else:
        raise HTTPException(400, "Folder nie istnieje")
    added = [tid for f in sorted(files) if (tid := _add_file(f))]
    analyzer.kick()
    return {"found": len(files), "added": len(added)}


@app.post("/api/import/upload")
async def import_upload(files: list[UploadFile] = File(...)):
    IMPORTED.mkdir(parents=True, exist_ok=True)
    added = 0
    for f in files:
        name = _safe_name(os.path.basename(f.filename or "track"))
        if not tags.is_audio(name):
            continue
        dst = IMPORTED / name
        i = 1
        while dst.exists():
            dst = IMPORTED / f"{Path(name).stem} ({i}){Path(name).suffix}"
            i += 1
        with open(dst, "wb") as out:
            shutil.copyfileobj(f.file, out)
        if _add_file(str(dst)):
            added += 1
    analyzer.kick()
    return {"added": added}


# ---------------------------------------------------------------- tracks
TRACK_COLS = "id, path, artist, title, album, genre, comment, year, rating, duration, key, key_name, key_confidence, key2, tag_key, key_manual, tempo_steady, bpm, beat_offset, first_downbeat, energy, cues, segments, status, error, stems, vocal_ratio, has_cover, added_at"


@app.get("/api/tracks")
def list_tracks():
    return db.q(f"SELECT {TRACK_COLS} FROM tracks ORDER BY artist COLLATE NOCASE, title COLLATE NOCASE")


@app.get("/api/tracks/{tid}")
def get_track(tid: int):
    return _track_or_404(tid)


class TrackPatch(BaseModel):
    artist: str | None = None
    title: str | None = None
    album: str | None = None
    genre: str | None = None
    comment: str | None = None
    year: str | None = None
    rating: int | None = None
    key: str | None = None
    bpm: float | None = None
    first_downbeat: float | None = None
    beat_offset: float | None = None
    cues: list[float] | None = None
    segments: list[dict] | None = None
    key_manual: int | None = None
    write_file: bool = False


@app.patch("/api/tracks/{tid}")
def patch_track(tid: int, p: TrackPatch):
    t = _track_or_404(tid)
    fields = {k: v for k, v in p.model_dump().items() if v is not None and k != "write_file"}
    if "key" in fields:
        fields["key"] = fields["key"].upper()
        camelot.parse_camelot(fields["key"])
        fields["key_name"] = camelot.standard_name(fields["key"])
        fields.setdefault("key_manual", 1)
    if "cues" in fields:
        fields["cues"] = sorted(round(max(0.0, c), 3) for c in fields["cues"])[:8]
    if "bpm" in fields and not 40 <= fields["bpm"] <= 250:
        raise HTTPException(400, "BPM poza zakresem 40–250")
    if "rating" in fields:
        fields["rating"] = max(0, min(5, fields["rating"]))
    db.update("tracks", tid, fields)
    if p.write_file:
        t = db.track(tid)
        try:
            tags.write_tags(t["path"], {k: t[k] for k in ("artist", "title", "album", "genre", "comment", "year")}, key=t["key"], bpm=t["bpm"])
        except Exception as exc:
            raise HTTPException(500, f"Nie udało się zapisać tagów w pliku: {exc}")
    return db.track(tid)


class BulkTags(BaseModel):
    ids: list[int]
    action: str  # comment_key | write_file | reanalyze | remove


@app.post("/api/tracks/bulk")
def bulk(req: BulkTags):
    s = db.settings()
    done = 0
    for tid in req.ids:
        t = db.track(tid)
        if not t:
            continue
        if req.action == "comment_key" and t["key"]:
            c = s["comment_template"].format(key=t["key"], energy=t["energy"], bpm=round(t["bpm"] or 0))
            db.update("tracks", tid, {"comment": c})
            try:
                tags.write_tags(t["path"], {"comment": c}, key=t["key"], bpm=t["bpm"])
            except Exception:
                pass
        elif req.action == "write_file":
            tags.write_tags(t["path"], {k: t[k] for k in ("artist", "title", "album", "genre", "comment", "year")}, key=t["key"], bpm=t["bpm"])
        elif req.action == "reanalyze":
            db.update("tracks", tid, {"status": "pending"})
        elif req.action == "remove":
            db.x("DELETE FROM tracks WHERE id = ?", (tid,))
        done += 1
    analyzer.kick()
    return {"done": done}


@app.get("/api/tracks/{tid}/waveform")
def track_waveform(tid: int):
    p = wave_path(tid)
    if not p.exists():
        raise HTTPException(404, "Brak waveformu (utwór nie jest jeszcze przeanalizowany)")
    return FileResponse(p, media_type="application/json")


@app.get("/api/tracks/{tid}/vocals")
def track_vocals(tid: int):
    p = stems.stem_dir(CACHE, tid) / "vocals.json"
    if not p.exists():
        raise HTTPException(404, "Brak stemów")
    return FileResponse(p, media_type="application/json")


@app.get("/api/tracks/{tid}/stemwave")
def track_stemwave(tid: int):
    p = stems.stem_dir(CACHE, tid) / "stemwave.json"
    if not p.exists():
        if not stems.has_stems4(CACHE, tid):
            raise HTTPException(404, "Brak 4 stemów")
        p.write_text(json.dumps(stems.stem_waves(CACHE, tid)))
    return FileResponse(p, media_type="application/json")


@app.get("/api/tracks/{tid}/cover")
def track_cover(tid: int):
    p = CACHE / "covers" / f"{tid}.img"
    if not p.exists():
        raise HTTPException(404)
    data = p.read_bytes()
    mime = "image/png" if data[:4] == b"\x89PNG" else "image/jpeg"
    return Response(data, media_type=mime, headers={"Cache-Control": "max-age=86400"})


def _stem_label(stem: str) -> str:
    if stem.startswith("mute:"):
        muted = [m for m in stem[5:].split(",") if m]
        return f" (bez: {', '.join(STEM_LABEL.get(m, m).strip(' ()').lower() for m in muted)})" if muted else ""
    return STEM_LABEL.get(stem, "")


def _source_for(t: dict, stem: str) -> str:
    if stem == "full" or stem == "mute:":
        return t["path"]
    if stem.startswith("mute:"):  # 4-stem mix with some stems muted
        muted = {m for m in stem[5:].split(",") if m}
        keep = [s for s in stems.STEMS4 if s not in muted]
        if not stems.has_stems4(CACHE, t["id"]):
            raise HTTPException(409, "Najpierw rozdziel utwór na 4 stemy")
        if not keep:
            raise HTTPException(400, "Wszystkie stemy są wyciszone")
        if len(keep) == 1:
            return str(stems.stem_path(CACHE, t["id"], keep[0]))
        dst = stems.stem_dir(CACHE, t["id"]) / f"mix_{'_'.join(keep)}.flac"
        if not dst.exists():
            ins = sum((["-i", str(stems.stem_path(CACHE, t["id"], s))] for s in keep), [])
            ffmpeg.run([*ins, "-filter_complex", "".join(f"[{i}:a]" for i in range(len(keep))) + f"amix=inputs={len(keep)}:normalize=0[o]",
                        "-map", "[o]", "-c:a", "flac", str(dst)])
        return str(dst)
    if not stems.stem_path(CACHE, t["id"], stem).exists():
        raise HTTPException(409, "Najpierw rozdziel utwór na stemy" + (" (4 ścieżki wymagają Demucs, wersja Pro)" if stem in ("drums", "bass", "other") else ""))
    return str(stems.stem_path(CACHE, t["id"], stem))


_inflight: dict[str, threading.Event] = {}
_inflight_lock = threading.Lock()
_prefetch_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="prefetch")


def _variant(t: dict, stem: str, semitones: int, tempo: float, ext: str = ".flac", fast: bool = False) -> str:
    if stem not in STEM_CHOICES and not stem.startswith("mute:"):
        raise HTTPException(400, "Zły stem")
    if not -12 <= semitones <= 12 or not 0.5 <= tempo <= 2.0:
        raise HTTPException(400, "Zakres zmiany tonacji/tempa poza limitem")
    src = _source_for(t, stem)
    # Only uncompressed/lossless files are served as-is. Everything else is decoded by ffmpeg, the same
    # decoder the analysis uses, so cue points and the beat grid line up exactly with what you hear
    # (browsers handle MP3/AAC encoder delay differently).
    if semitones == 0 and abs(tempo - 1) < 1e-4 and Path(src).suffix.lower() in (".wav", ".flac") and ext == ".flac":
        return src
    rdir = CACHE / "render"
    rdir.mkdir(parents=True, exist_ok=True)
    tag = hashlib.md5(f"{src}|{os.path.getmtime(src)}".encode()).hexdigest()[:8]
    dst = rdir / f"{t['id']}_{stem}_{semitones:+d}_{tempo:.5f}_{tag}{'_p' if fast else ''}{ext}"
    # a high-quality render can always stand in for a preview one, not the other way round
    if fast and (hq := rdir / dst.name.replace("_p" + ext, ext)).exists():
        return str(hq)
    key = str(dst)
    while True:
        if dst.exists():
            return key
        with _inflight_lock:
            ev = _inflight.get(key)
            if ev is None:
                ev = _inflight[key] = threading.Event()
                mine = True
            else:
                mine = False
        if not mine:
            ev.wait(600)  # the same variant is already being rendered (e.g. prefetched): reuse it
            continue
        try:
            ffmpeg.render(src, key, semitones, tempo, fast=fast)
        finally:
            with _inflight_lock:
                _inflight.pop(key, None)
            ev.set()
        return key


@app.get("/api/tracks/{tid}/audio")
def track_audio(tid: int, stem: str = "full", semitones: int = 0, tempo: float = 1.0):
    t = _track_or_404(tid)
    path = _variant(t, stem, semitones, round(tempo, 5), fast=True)
    return FileResponse(path, headers={"Cache-Control": "no-cache"})


class PrefetchReq(BaseModel):
    stem: str = "full"
    semitones: list[int] = []
    tempo: float = 1.0
    deck: str = "A"


_prefetch_gen: dict[str, int] = {}


@app.post("/api/tracks/{tid}/prefetch")
def prefetch(tid: int, req: PrefetchReq):
    """Render likely next variants (neighbouring keys at the current tempo) in the background,
    so a key change on the deck is instant."""
    t = _track_or_404(tid)
    gen = _prefetch_gen[req.deck] = _prefetch_gen.get(req.deck, 0) + 1

    def job(s: int):
        if _prefetch_gen.get(req.deck) == gen:  # skip if the deck has moved on to another track/tempo
            _variant(t, req.stem, s, round(req.tempo, 5), fast=True)

    for s in req.semitones[:4]:
        if -12 <= s <= 12:
            _prefetch_pool.submit(job, s)
    return {"queued": len(req.semitones[:4])}


@app.post("/api/tracks/{tid}/stems")
def make_stems(tid: int, force: bool = False):
    t = _track_or_404(tid)
    if not force and stems.has_stems(CACHE, tid) and t["stems"] not in ("none", "running"):
        return {"status": "done", "engine": t["stems"]}
    for j in jobs.active():
        if j["kind"] == "stems" and j.get("track_id") == tid and j["status"] == "running":
            return j
    return jobs.submit("stems", lambda progress: run_stems(db, tid, progress), track_id=tid, label=f"Stemy: {t['artist']} - {t['title']}")


@app.get("/api/jobs/{jid}")
def job(jid: str):
    j = jobs.get(jid)
    if not j:
        raise HTTPException(404)
    return j


# ---------------------------------------------------------------- export
class ExportReq(BaseModel):
    track_id: int
    stem: str = "full"
    semitones: int = 0
    format: str | None = None


def _export_dir() -> Path:
    d = Path(os.path.expanduser(db.settings()["export_dir"]))
    d.mkdir(parents=True, exist_ok=True)
    return d


@app.post("/api/export")
def export_track(req: ExportReq):
    t = _track_or_404(req.track_id)
    fmt = (req.format or db.settings()["export_format"]).lower()
    if fmt not in ("wav", "flac", "mp3"):
        raise HTTPException(400, "Format: wav, flac lub mp3")
    _source_for(t, req.stem)  # validates stems exist

    def work(progress):
        key = camelot.transpose(t["key"], req.semitones) if t["key"] else ""
        shift = f" {req.semitones:+d}" if req.semitones else ""
        name = _safe_name(f"{t['artist']} - {t['title']}{_stem_label(req.stem)} [{key}{shift}]")
        dst = _export_dir() / f"{name}.{fmt}"
        src = _source_for(t, req.stem)
        ffmpeg.render(src, str(dst), req.semitones, 1.0)
        return {"path": str(dst)}

    return jobs.submit("export", work, label=f"Eksport: {t['title']}")


class MashupExport(BaseModel):
    track_a: int
    track_b: int
    shift_a: int = 0
    shift_b: int = 0
    stem_a: str = "vocals"
    stem_b: str = "instrumental"
    cue_a: float = 0.0
    cue_b: float = 0.0
    bars: int = 32
    gain_a: float = 1.0
    gain_b: float = 1.0
    format: str | None = None


@app.post("/api/export/mashup")
def export_mashup(req: MashupExport):
    a, b = _track_or_404(req.track_a), _track_or_404(req.track_b)
    _source_for(a, req.stem_a)
    _source_for(b, req.stem_b)
    fmt = (req.format or db.settings()["export_format"]).lower()

    def work(progress):
        factor, _ = mashup.tempo_match(a["bpm"], b["bpm"])
        tb = round(a["bpm"] / (b["bpm"] * factor), 5)  # time-stretch of B to A's tempo
        va = _variant(a, req.stem_a, req.shift_a, 1.0, ".flac")
        progress(0.4)
        vb = _variant(b, req.stem_b, req.shift_b, tb, ".flac")
        progress(0.8)
        duration = req.bars * 4 * 60.0 / a["bpm"]
        name = _safe_name(f"Mashup - {a['artist']} - {a['title']} x {b['artist']} - {b['title']}")
        dst = _export_dir() / f"{name}.{fmt}"
        ffmpeg.mixdown(
            [
                {"path": va, "start": req.cue_a, "gain": req.gain_a},
                {"path": vb, "start": req.cue_b / tb, "gain": req.gain_b},
            ],
            str(dst),
            duration,
        )
        return {"path": str(dst)}

    return jobs.submit("export", work, label=f"Mashup: {a['title']} x {b['title']}")


@app.post("/api/open-folder")
def open_folder(path: dict | None = None):
    target = str((path or {}).get("path") or _export_dir())
    if os.path.isfile(target):
        target = os.path.dirname(target)
    try:
        if sys.platform.startswith("win"):
            os.startfile(target)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", target])
        else:
            subprocess.Popen(["xdg-open", target])
    except Exception as exc:
        raise HTTPException(500, str(exc))
    return {"opened": target}


# ---------------------------------------------------------------- mashup ideas
class IdeasReq(BaseModel):
    track_id: int
    shift_a: int = 0
    mode: str = "mashup"
    energy: list[int] = []
    tempo_tol: float | None = None
    playlists: list[int] = []
    genres: list[str] = []
    allow_shift_b: bool | None = None
    max_shift_b: int = 4
    half_double: bool = True


@app.post("/api/ideas")
def get_ideas(req: IdeasReq):
    a = _track_or_404(req.track_id)
    if a["status"] != "done":
        raise HTTPException(409, "Utwór nie jest jeszcze przeanalizowany")
    opts = req.model_dump()
    if req.allow_shift_b is None:
        opts["allow_shift_b"] = req.mode == "mashup"
    if req.playlists:
        ph = ",".join("?" * len(req.playlists))
        opts["pool"] = {r["track_id"] for r in db.q(f"SELECT track_id FROM playlist_tracks WHERE playlist_id IN ({ph})", tuple(req.playlists))}
    tracks = db.q(f"SELECT {TRACK_COLS} FROM tracks WHERE status = 'done'")
    return mashup.ideas(a, tracks, req.shift_a, opts)


# ---------------------------------------------------------------- playlists
class PlaylistReq(BaseModel):
    name: str


@app.get("/api/playlists")
def playlists():
    rows = db.q("SELECT p.id, p.name, COUNT(pt.track_id) AS count FROM playlists p LEFT JOIN playlist_tracks pt ON pt.playlist_id = p.id GROUP BY p.id ORDER BY p.sort, p.id")
    return rows


@app.post("/api/playlists")
def create_playlist(req: PlaylistReq):
    pid = db.x("INSERT INTO playlists (name, created_at) VALUES (?, ?)", (req.name.strip() or "Nowa playlista", time.time()))
    return {"id": pid, "name": req.name, "count": 0}


@app.patch("/api/playlists/{pid}")
def rename_playlist(pid: int, req: PlaylistReq):
    db.x("UPDATE playlists SET name = ? WHERE id = ?", (req.name, pid))
    return {"ok": True}


@app.delete("/api/playlists/{pid}")
def delete_playlist(pid: int):
    db.x("DELETE FROM playlists WHERE id = ?", (pid,))
    return {"ok": True}


@app.get("/api/playlists/{pid}/tracks")
def playlist_tracks(pid: int):
    return [r["track_id"] for r in db.q("SELECT track_id FROM playlist_tracks WHERE playlist_id = ? ORDER BY position", (pid,))]


class IdsReq(BaseModel):
    track_ids: list[int]


@app.post("/api/playlists/{pid}/tracks")
def add_to_playlist(pid: int, req: IdsReq):
    pos = (db.one("SELECT MAX(position) m FROM playlist_tracks WHERE playlist_id = ?", (pid,)) or {}).get("m") or 0
    for tid in req.track_ids:
        pos += 1
        db.x("INSERT OR IGNORE INTO playlist_tracks (playlist_id, track_id, position) VALUES (?, ?, ?)", (pid, tid, pos))
    return {"ok": True}


@app.post("/api/playlists/{pid}/remove")
def remove_from_playlist(pid: int, req: IdsReq):
    for tid in req.track_ids:
        db.x("DELETE FROM playlist_tracks WHERE playlist_id = ? AND track_id = ?", (pid, tid))
    return {"ok": True}


@app.put("/api/playlists/{pid}/order")
def reorder_playlist(pid: int, req: IdsReq):
    for i, tid in enumerate(req.track_ids):
        db.x("UPDATE playlist_tracks SET position = ? WHERE playlist_id = ? AND track_id = ?", (i, pid, tid))
    return {"ok": True}


class OrderReq(BaseModel):
    curve: str = "peak"
    start_id: int | None = None


@app.post("/api/playlists/{pid}/harmonic-sort")
def harmonic_sort(pid: int, req: OrderReq):
    ids = playlist_tracks(pid)
    tracks = [db.track(i) for i in ids]
    order = mashup.harmonic_order([t for t in tracks if t], req.curve, req.start_id)
    reorder_playlist(pid, IdsReq(track_ids=order))
    return order


@app.get("/api/playlists/{pid}/m3u")
def playlist_m3u(pid: int):
    p = db.one("SELECT name FROM playlists WHERE id = ?", (pid,))
    if not p:
        raise HTTPException(404)
    lines = ["#EXTM3U"]
    for tid in playlist_tracks(pid):
        t = db.track(tid)
        lines.append(f"#EXTINF:{int(t['duration'] or 0)},{t['artist']} - {t['title']}")
        lines.append(t["path"])
    return PlainTextResponse("\n".join(lines) + "\n", headers={"Content-Disposition": f'attachment; filename="{_safe_name(p["name"])}.m3u8"'})


@app.post("/api/playlists/{pid}/m3u")
def playlist_m3u_save(pid: int):
    """Save the playlist as .m3u8 into the export folder (works in the desktop window too)."""
    p = db.one("SELECT name FROM playlists WHERE id = ?", (pid,))
    if not p:
        raise HTTPException(404)
    dst = _export_dir() / f"{_safe_name(p['name'])}.m3u8"
    dst.write_text(playlist_m3u(pid).body.decode(), encoding="utf-8")
    return {"path": str(dst)}


@app.post("/api/export/rekordbox")
def export_rekordbox():
    """Whole analysed library + all playlists as rekordbox XML in the export folder."""
    from . import rekordbox
    tracks = db.q(f"SELECT {TRACK_COLS} FROM tracks WHERE status = 'done'")
    pls = [(p["name"], playlist_tracks(p["id"])) for p in db.q("SELECT id, name FROM playlists ORDER BY sort, id")]
    dst = _export_dir() / "blendora_rekordbox.xml"
    dst.write_bytes(rekordbox.build(tracks, pls))
    return {"path": str(dst), "tracks": len(tracks), "playlists": len(pls)}


@app.post("/api/export/traktor")
def export_traktor():
    """Whole analysed library + all playlists as a Traktor Pro collection (.nml) in the export folder."""
    from . import traktor
    tracks = db.q(f"SELECT {TRACK_COLS} FROM tracks WHERE status = 'done'")
    pls = [(p["name"], playlist_tracks(p["id"])) for p in db.q("SELECT id, name FROM playlists ORDER BY sort, id")]
    dst = _export_dir() / "blendora_traktor.nml"
    dst.write_bytes(traktor.build(tracks, pls))
    return {"path": str(dst), "tracks": len(tracks), "playlists": len(pls)}


# ---------------------------------------------------------------- saved mashups
class MashupReq(BaseModel):
    track_a: int
    track_b: int
    shift_a: int = 0
    shift_b: int = 0
    cue_a: float = 0
    cue_b: float = 0
    stem_a: str = "full"
    stem_b: str = "full"
    rating: int = 0
    notes: str = ""


@app.get("/api/mashups")
def list_mashups():
    return db.q(
        """SELECT m.*, a.artist AS artist_a, a.title AS title_a, a.key AS key_a, a.bpm AS bpm_a,
                  b.artist AS artist_b, b.title AS title_b, b.key AS key_b, b.bpm AS bpm_b
           FROM mashups m JOIN tracks a ON a.id = m.track_a JOIN tracks b ON b.id = m.track_b
           ORDER BY m.created_at DESC"""
    )


@app.post("/api/mashups")
def save_mashup(req: MashupReq):
    d = req.model_dump()
    d["created_at"] = time.time()
    cols = ", ".join(d)
    mid = db.x(f"INSERT INTO mashups ({cols}) VALUES ({', '.join('?' * len(d))})", tuple(d.values()))
    return {"id": mid}


class MashupPatch(BaseModel):
    rating: int | None = None
    notes: str | None = None


@app.patch("/api/mashups/{mid}")
def patch_mashup(mid: int, p: MashupPatch):
    db.update("mashups", mid, {k: v for k, v in p.model_dump().items() if v is not None})
    return {"ok": True}


@app.delete("/api/mashups/{mid}")
def delete_mashup(mid: int):
    db.x("DELETE FROM mashups WHERE id = ?", (mid,))
    return {"ok": True}


# ---------------------------------------------------------------- web UI
app.mount("/", StaticFiles(directory=WEB, html=True), name="web")
