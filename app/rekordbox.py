"""Export the library to a rekordbox XML collection (File > Import in rekordbox, or the
"rekordbox xml" view in Preferences > Advanced). Carries BPM + beat grid, key, rating,
comment, hot cues (A-H) and memory cues, and playlists."""
from __future__ import annotations

import math
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import quote

from . import __version__, camelot

CUE_COLORS = [(40, 226, 20), (48, 90, 255), (255, 160, 0), (230, 40, 40), (180, 50, 255), (0, 200, 200), (255, 220, 0), (255, 100, 200)]
KIND = {".mp3": "MP3 File", ".wav": "WAV File", ".flac": "FLAC File", ".aiff": "AIFF File", ".aif": "AIFF File", ".m4a": "M4A File", ".mp4": "M4A File", ".ogg": "OGG File"}


def location(path: str) -> str:
    p = Path(path)
    s = p.as_posix() if not p.drive else str(p).replace("\\", "/")
    if not s.startswith("/"):
        s = "/" + s
    return "file://localhost" + quote(s, safe="/:")


def tonality(code: str | None) -> str:
    if not code:
        return ""
    try:
        return camelot.standard_name(code, short=True)
    except Exception:
        return ""


def _f(x: float, n: int = 3) -> str:
    return f"{x:.{n}f}"


def build(tracks: list[dict], playlists: list[tuple[str, list[int]]]) -> bytes:
    root = ET.Element("DJ_PLAYLISTS", Version="1.0.0")
    ET.SubElement(root, "PRODUCT", Name="Blendora", Version=__version__, Company="Blendora")
    coll = ET.SubElement(root, "COLLECTION", Entries=str(len(tracks)))
    for t in tracks:
        ext = Path(t["path"]).suffix.lower()
        attrs = {
            "TrackID": str(t["id"]), "Name": t.get("title") or Path(t["path"]).stem, "Artist": t.get("artist") or "",
            "Album": t.get("album") or "", "Genre": t.get("genre") or "", "Kind": KIND.get(ext, "MP3 File"),
            "TotalTime": str(int(round(t.get("duration") or 0))), "Year": str(t.get("year") or ""),
            "AverageBpm": _f(t.get("bpm") or 0, 2), "Tonality": tonality(t.get("key")),
            "Comments": t.get("comment") or "", "Rating": str(min(5, max(0, int(t.get("rating") or 0))) * 51),
            "Location": location(t["path"]),
        }
        el = ET.SubElement(coll, "TRACK", attrs)
        bpm = t.get("bpm") or 0
        if bpm > 0:
            beat = 60.0 / bpm
            bar = 4 * beat
            fd = t.get("first_downbeat")
            fd = (t.get("beat_offset") or 0.0) if fd is None else fd
            inizio = fd - math.floor(fd / bar) * bar  # first bar start that is >= 0
            ET.SubElement(el, "TEMPO", Inizio=_f(inizio), Bpm=_f(bpm, 2), Metro="4/4", Battito="1")
        for i, c in enumerate((t.get("cues") or [])[:8]):
            r, g, b = CUE_COLORS[i]
            ET.SubElement(el, "POSITION_MARK", Name=f"Cue {i + 1}", Type="0", Start=_f(c), Num=str(i), Red=str(r), Green=str(g), Blue=str(b))
            ET.SubElement(el, "POSITION_MARK", Name=f"Cue {i + 1}", Type="0", Start=_f(c), Num="-1")
    pls = ET.SubElement(root, "PLAYLISTS")
    node = ET.SubElement(pls, "NODE", Type="0", Name="ROOT", Count="1")
    folder = ET.SubElement(node, "NODE", Type="0", Name="Blendora", Count=str(len(playlists)))
    known = {t["id"] for t in tracks}
    for name, ids in playlists:
        ids = [i for i in ids if i in known]
        pl = ET.SubElement(folder, "NODE", Name=name, Type="1", KeyType="0", Entries=str(len(ids)))
        for i in ids:
            ET.SubElement(pl, "TRACK", Key=str(i))
    ET.indent(root)
    return b'<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="utf-8")
