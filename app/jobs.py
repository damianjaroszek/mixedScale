"""Background work: the analysis queue and long-running jobs (stems, exports)."""
from __future__ import annotations

import json
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


class Analyzer(threading.Thread):
    """Single worker thread that analyses tracks with status 'pending'."""

    def __init__(self, db: DB):
        super().__init__(daemon=True)
        self.db = db
        self.wake = threading.Event()
        self.current: int | None = None

    def kick(self):
        self.wake.set()

    def run(self):
        while True:
            row = self.db.one("SELECT * FROM tracks WHERE status = 'pending' ORDER BY id LIMIT 1")
            if not row:
                self.current = None
                self.wake.wait(timeout=5)
                self.wake.clear()
                continue
            self.analyze(row)

    def analyze(self, row: dict):
        tid = row["id"]
        self.current = tid
        self.db.update("tracks", tid, {"status": "analyzing", "error": None})
        try:
            res = analysis.analyze_file(row["path"])
            wave = analysis.waveform(row["path"])
            wp = wave_path(tid)
            wp.parent.mkdir(parents=True, exist_ok=True)
            wp.write_text(json.dumps(wave))
            fields = {k: res[k] for k in ("duration", "key", "key_name", "key_confidence", "bpm", "beat_offset", "first_downbeat", "energy", "cues", "segments")}
            fields.update(status="done", analyzed_at=time.time())
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
            self.db.update("tracks", tid, {"status": "error", "error": str(exc)[:500]})
        finally:
            self.current = None


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
