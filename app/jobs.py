"""Background work: the analysis queue and long-running jobs (stems, exports)."""
from __future__ import annotations

import json
import os
import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor

from . import analysis, stems, tags
from .db import CACHE, DB


class Jobs:
    def __init__(self):
        self.jobs: dict[str, dict] = {}
        self.pool = ThreadPoolExecutor(max_workers=2)
        self.lock = threading.Lock()

    def submit(self, kind: str, fn, *args, **meta) -> dict:
        jid = uuid.uuid4().hex[:12]
        job = {"id": jid, "kind": kind, "status": "running", "progress": 0.0, "result": None, "error": None, "created": time.time(), **meta}
        with self.lock:
            self.jobs[jid] = job

        def progress(p: float):
            job["progress"] = round(float(p), 3)

        def runner():
            try:
                job["result"] = fn(*args, progress=progress)
                job["status"] = "done"
                job["progress"] = 1.0
            except Exception as exc:  # report to the UI
                traceback.print_exc()
                job["status"] = "error"
                job["error"] = str(exc)

        self.pool.submit(runner)
        return job

    def get(self, jid: str) -> dict | None:
        return self.jobs.get(jid)

    def active(self) -> list[dict]:
        cutoff = time.time() - 600
        return [j for j in self.jobs.values() if j["status"] == "running" or j["created"] > cutoff]


def wave_path(track_id: int):
    return CACHE / "wave" / f"{track_id}.json"


def analyze_job(path: str, learned: dict | None, bpm_range: tuple[float, float]) -> tuple[dict, dict]:
    """Runs in a worker process: full analysis + waveform for one file."""
    res = analysis.analyze_file(path, learned=learned, bpm_range=bpm_range)
    wave = analysis.waveform(path)
    return res, wave


def worker_count(setting) -> int:
    try:
        n = int(setting or 0)
    except (TypeError, ValueError):
        n = 0
    if n <= 0:
        n = max(1, min((os.cpu_count() or 2) - 1, 8))
    return n


def parse_range(text: str) -> tuple[float, float]:
    try:
        lo, hi = (float(x) for x in str(text).replace("–", "-").split("-"))
        if 40 <= lo < hi <= 250:
            return lo, hi
    except ValueError:
        pass
    return 78.0, 180.0


class Analyzer(threading.Thread):
    """Feeds tracks with status 'pending' to a pool of worker processes (all CPU cores)."""

    def __init__(self, db: DB):
        super().__init__(daemon=True)
        self.db = db
        self.wake = threading.Event()
        self.running: dict[int, object] = {}
        self.pool = None
        self.pool_size = 0

    @property
    def current(self):
        return next(iter(self.running), None)

    def kick(self):
        self.wake.set()

    def _ensure_pool(self, n: int):
        if self.pool is None or self.pool_size != n:
            if self.pool is not None:
                self.pool.shutdown(wait=True)
            import multiprocessing as mp
            from concurrent.futures import ProcessPoolExecutor

            self.pool = ProcessPoolExecutor(max_workers=n, mp_context=mp.get_context("spawn"))
            self.pool_size = n

    def run(self):
        while True:
            settings = self.db.settings()
            n = worker_count(settings.get("analysis_workers"))
            free = n - len(self.running)
            rows = []
            if free > 0:
                ids = tuple(self.running) or (-1,)
                rows = self.db.q(f"SELECT id, path FROM tracks WHERE status = 'pending' AND id NOT IN ({','.join('?' * len(ids))}) ORDER BY id LIMIT ?", (*ids, free))
            if rows:
                if not self.running:
                    self._ensure_pool(n)
                learned = settings.get("learned_profiles")
                rng = parse_range(settings.get("bpm_range"))
                for row in rows:
                    self.db.update("tracks", row["id"], {"status": "analyzing", "error": None})
                    try:
                        fut = self.pool.submit(analyze_job, row["path"], learned, rng)
                    except Exception:  # broken pool (e.g. a worker crashed): rebuild it
                        self.pool = None
                        self._ensure_pool(n)
                        fut = self.pool.submit(analyze_job, row["path"], learned, rng)
                    fut.add_done_callback(lambda f, r=row: self._done(r, f))
                    self.running[row["id"]] = fut
                continue
            self.wake.wait(timeout=2 if self.running else 5)
            self.wake.clear()

    def _done(self, row: dict, fut):
        tid = row["id"]
        try:
            res, wave = fut.result()
            wp = wave_path(tid)
            wp.parent.mkdir(parents=True, exist_ok=True)
            wp.write_text(json.dumps(wave))
            fields = {k: res[k] for k in ("duration", "key", "key_name", "key_confidence", "key2", "key_features", "bpm", "beat_offset", "first_downbeat", "energy", "cues", "segments")}
            fields.update(status="done", analyzed_at=time.time(), key_manual=0)
            self.db.update("tracks", tid, fields)
            settings = self.db.settings()
            if settings.get("write_tags_after_analysis"):
                try:
                    comment = settings["comment_template"].format(key=res["key"], energy=res["energy"], bpm=round(res["bpm"]))
                    tags.write_tags(row["path"], {"comment": comment}, key=res["key"], bpm=res["bpm"])
                    self.db.update("tracks", tid, {"comment": comment})
                except Exception:
                    traceback.print_exc()
        except Exception as exc:
            traceback.print_exc()
            self.db.update("tracks", tid, {"status": "error", "error": str(exc)[:500] or exc.__class__.__name__})
        finally:
            self.running.pop(tid, None)
            self.wake.set()


def run_stems(db: DB, track_id: int, progress=None) -> dict:
    t = db.track(track_id)
    db.update("tracks", track_id, {"stems": "running"})
    try:
        engine = stems.separate(t["path"], CACHE, track_id, db.settings().get("stem_model", "htdemucs"), progress)
        prof = stems.vocal_profile(CACHE, track_id)
        (stems.stem_dir(CACHE, track_id) / "vocals.json").write_text(json.dumps(prof))
        db.update("tracks", track_id, {"stems": engine, "vocal_ratio": round(prof["vocal_ratio"], 3)})
        return {"engine": engine, "vocal_ratio": prof["vocal_ratio"]}
    except Exception:
        db.update("tracks", track_id, {"stems": "none"})
        raise
