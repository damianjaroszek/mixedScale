"""Export the library to a Traktor Pro 3 collection file (.nml).

In Traktor: Browser > Explorer, right-click "Track Collection" > Import Another Collection,
or drag the .nml file in. Carries BPM + locked beat grid, key, rating, comment, 8 hot cues
and playlists (in a "Blendora" folder)."""
from __future__ import annotations

import math
import time
import uuid
import xml.etree.ElementTree as ET
from pathlib import PurePosixPath, PureWindowsPath

from . import camelot


def split_location(path: str) -> tuple[str, str, str]:
    """-> (VOLUME, DIR, FILE) in Traktor's '/:' notation."""
    if len(path) > 1 and path[1] == ":" or "\\" in path:
        p = PureWindowsPath(path)
        volume = p.drive
        parts = p.parts[1:-1]
    else:
        p = PurePosixPath(path)
        volume = ""
        parts = p.parts[1:-1]
    d = "/:" + "".join(f"{x}/:" for x in parts)
    return volume, d, p.name


def primary_key(path: str) -> str:
    v, d, f = split_location(path)
    return v + d + f


def key_value(code: str | None) -> int | None:
    """Traktor MUSICAL_KEY: 0-11 = C..B major, 12-23 = Cm..Bm."""
    if not code:
        return None
    try:
        pc, mode = camelot.camelot_to_pitch(code)
    except Exception:
        return None
    return pc + (12 if mode == "minor" else 0)


def build(tracks: list[dict], playlists: list[tuple[str, list[int]]]) -> bytes:
    now = time.localtime()
    date = f"{now.tm_year}/{now.tm_mon}/{now.tm_mday}"
    root = ET.Element("NML", VERSION="19")
    ET.SubElement(root, "HEAD", COMPANY="www.native-instruments.com", PROGRAM="Traktor")
    ET.SubElement(root, "MUSICFOLDERS")
    coll = ET.SubElement(root, "COLLECTION", ENTRIES=str(len(tracks)))
    for t in tracks:
        e = ET.SubElement(coll, "ENTRY", MODIFIED_DATE=date, MODIFIED_TIME=str(now.tm_hour * 3600 + now.tm_min * 60),
                          LOCK="1", LOCK_MODIFICATION_TIME=f"{date}T00:00:00", TITLE=t.get("title") or "", ARTIST=t.get("artist") or "")
        v, d, f = split_location(t["path"])
        ET.SubElement(e, "LOCATION", DIR=d, FILE=f, VOLUME=v, VOLUMEID=v)
        if t.get("album"):
            ET.SubElement(e, "ALBUM", TITLE=t["album"])
        info = {"GENRE": t.get("genre") or "", "COMMENT": t.get("comment") or "", "KEY": t.get("key") or "",
                "PLAYTIME": str(int(round(t.get("duration") or 0))), "PLAYTIME_FLOAT": f"{t.get('duration') or 0:.6f}",
                "RANKING": str(min(5, max(0, int(t.get("rating") or 0))) * 51), "IMPORT_DATE": date}
        if t.get("year"):
            info["RELEASE_DATE"] = f"{t['year']}/1/1"
        ET.SubElement(e, "INFO", info)
        bpm = t.get("bpm") or 0
        if bpm > 0:
            ET.SubElement(e, "TEMPO", BPM=f"{bpm:.6f}", BPM_QUALITY="100.000000")
        kv = key_value(t.get("key"))
        if kv is not None:
            ET.SubElement(e, "MUSICAL_KEY", VALUE=str(kv))
        if bpm > 0:
            bar = 4 * 60.0 / bpm
            fd = t.get("first_downbeat")
            fd = (t.get("beat_offset") or 0.0) if fd is None else fd
            grid = fd - math.floor(fd / bar) * bar
            ET.SubElement(e, "CUE_V2", NAME="AutoGrid", DISPL_ORDER="0", TYPE="4", START=f"{grid * 1000:.6f}",
                          LEN="0.000000", REPEATS="-1", HOTCUE="-1")
        for i, c in enumerate((t.get("cues") or [])[:8]):
            ET.SubElement(e, "CUE_V2", NAME=f"Cue {i + 1}", DISPL_ORDER="0", TYPE="0", START=f"{c * 1000:.6f}",
                          LEN="0.000000", REPEATS="-1", HOTCUE=str(i))
    pls = ET.SubElement(root, "PLAYLISTS")
    top = ET.SubElement(ET.SubElement(pls, "NODE", TYPE="FOLDER", NAME="$ROOT"), "SUBNODES", COUNT="1")
    folder = ET.SubElement(ET.SubElement(top, "NODE", TYPE="FOLDER", NAME="Blendora"), "SUBNODES", COUNT=str(len(playlists)))
    by_id = {t["id"]: t for t in tracks}
    for name, ids in playlists:
        ids = [i for i in ids if i in by_id]
        pl = ET.SubElement(ET.SubElement(folder, "NODE", TYPE="PLAYLIST", NAME=name), "PLAYLIST",
                           ENTRIES=str(len(ids)), TYPE="LIST", UUID=uuid.uuid4().hex)
        for i in ids:
            ET.SubElement(ET.SubElement(pl, "ENTRY"), "PRIMARYKEY", TYPE="TRACK", KEY=primary_key(by_id[i]["path"]))
    ET.SubElement(root, "INDEXING")
    ET.indent(root)
    return b'<?xml version="1.0" encoding="UTF-8" standalone="no" ?>\n' + ET.tostring(root, encoding="utf-8")
