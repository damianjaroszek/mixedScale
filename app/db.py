"""SQLite storage for the library, playlists, saved mashups and settings."""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from pathlib import Path

HOME = Path(os.environ.get("BLENDORA_HOME", Path.home() / "Blendora"))
CACHE = HOME / "cache"
IMPORTED = HOME / "imported"

SCHEMA = """
CREATE TABLE IF NOT EXISTS tracks (
  id INTEGER PRIMARY KEY,
  path TEXT UNIQUE NOT NULL,
  artist TEXT DEFAULT '', title TEXT DEFAULT '', album TEXT DEFAULT '', genre TEXT DEFAULT '',
  comment TEXT DEFAULT '', year TEXT DEFAULT '', rating INTEGER DEFAULT 0,
  duration REAL, key TEXT, key_name TEXT, key_confidence REAL,
  bpm REAL, beat_offset REAL, first_downbeat REAL, energy INTEGER,
  cues TEXT DEFAULT '[]', segments TEXT DEFAULT '[]',
  status TEXT DEFAULT 'pending', error TEXT,
  stems TEXT DEFAULT 'none', vocal_ratio REAL,
  has_cover INTEGER DEFAULT 0,
  added_at REAL, analyzed_at REAL
);
CREATE TABLE IF NOT EXISTS playlists (
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, created_at REAL, sort INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS playlist_tracks (
  playlist_id INTEGER REFERENCES playlists(id) ON DELETE CASCADE,
  track_id INTEGER REFERENCES tracks(id) ON DELETE CASCADE,
  position INTEGER,
  PRIMARY KEY (playlist_id, track_id)
);
CREATE TABLE IF NOT EXISTS mashups (
  id INTEGER PRIMARY KEY,
  track_a INTEGER REFERENCES tracks(id) ON DELETE CASCADE,
  track_b INTEGER REFERENCES tracks(id) ON DELETE CASCADE,
  shift_a INTEGER DEFAULT 0, shift_b INTEGER DEFAULT 0,
  cue_a REAL DEFAULT 0, cue_b REAL DEFAULT 0,
  stem_a TEXT DEFAULT 'full', stem_b TEXT DEFAULT 'full',
  rating INTEGER DEFAULT 0, notes TEXT DEFAULT '', created_at REAL
);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
"""

DEFAULT_SETTINGS = {
    "traktor_collection": None,
    "export_dir": str(HOME / "exports"),
    "export_format": "wav",
    "write_tags_after_analysis": False,
    "comment_template": "{key} - Energy {energy}",
    "stem_model": "htdemucs",
    "analysis_workers": 0,          # 0 = automatic (CPU cores - 1)
    "bpm_range": "78-180",          # tempo is folded into this range (e.g. "88-176" for drum & bass)
    "learned_profiles": None,       # key profiles learned from the user's tagged library
}

JSON_FIELDS = ("cues", "segments", "key_features")

MIGRATIONS = [
    "ALTER TABLE tracks ADD COLUMN key2 TEXT",
    "ALTER TABLE tracks ADD COLUMN key_features TEXT",
    "ALTER TABLE tracks ADD COLUMN tag_key TEXT",
    "ALTER TABLE tracks ADD COLUMN key_manual INTEGER DEFAULT 0",
    "ALTER TABLE tracks ADD COLUMN tempo_steady INTEGER DEFAULT 1",
    "ALTER TABLE tracks ADD COLUMN tag_bpm REAL",
    "ALTER TABLE tracks ADD COLUMN trk_bpm REAL",
    "ALTER TABLE tracks ADD COLUMN trk_grid REAL",
    "ALTER TABLE tracks ADD COLUMN trk_key TEXT",
    "ALTER TABLE tracks ADD COLUMN stem_file TEXT",
]


class DB:
    def __init__(self, path: Path | None = None):
        HOME.mkdir(parents=True, exist_ok=True)
        CACHE.mkdir(parents=True, exist_ok=True)
        self.path = path or HOME / "library.db"
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA journal_mode = WAL")
        self.conn.executescript(SCHEMA)
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(tracks)")}
        for m in MIGRATIONS:
            if m.split("ADD COLUMN ")[1].split()[0] not in cols:
                self.conn.execute(m)
        # analyses interrupted by a crash/restart go back to the queue
        self.conn.execute("UPDATE tracks SET status='pending' WHERE status='analyzing'")
        self.conn.commit()

    # --- helpers ---
    def q(self, sql: str, args: tuple = ()) -> list[dict]:
        with self.lock:
            return [self._row(r) for r in self.conn.execute(sql, args).fetchall()]

    def one(self, sql: str, args: tuple = ()) -> dict | None:
        rows = self.q(sql, args)
        return rows[0] if rows else None

    def x(self, sql: str, args: tuple = ()) -> int:
        with self.lock:
            cur = self.conn.execute(sql, args)
            self.conn.commit()
            return cur.lastrowid

    @staticmethod
    def _row(r: sqlite3.Row) -> dict:
        d = dict(r)
        for f in JSON_FIELDS:
            if f in d and isinstance(d[f], str):
                d[f] = json.loads(d[f] or "[]")
        return d

    def update(self, table: str, id_: int, fields: dict) -> None:
        if not fields:
            return
        vals = [json.dumps(v) if k in JSON_FIELDS else v for k, v in fields.items()]
        cols = ", ".join(f"{k} = ?" for k in fields)
        self.x(f"UPDATE {table} SET {cols} WHERE id = ?", (*vals, id_))

    # --- tracks ---
    def add_track(self, path: str, tags: dict) -> int | None:
        existing = self.one("SELECT id FROM tracks WHERE path = ?", (path,))
        if existing:
            return None
        cols = ["path", "added_at", *tags.keys()]
        vals = [path, time.time(), *tags.values()]
        return self.x(f"INSERT INTO tracks ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", tuple(vals))

    def track(self, id_: int) -> dict | None:
        return self.one("SELECT * FROM tracks WHERE id = ?", (id_,))

    # --- settings ---
    def settings(self) -> dict:
        out = dict(DEFAULT_SETTINGS)
        for r in self.q("SELECT key, value FROM settings"):
            out[r["key"]] = json.loads(r["value"])
        return out

    def set_settings(self, values: dict) -> dict:
        for k, v in values.items():
            if k in DEFAULT_SETTINGS:
                self.x("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (k, json.dumps(v)))
        return self.settings()
