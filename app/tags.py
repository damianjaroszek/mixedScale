"""Reading and writing file tags (ID3, FLAC/Vorbis, MP4) with mutagen."""
from __future__ import annotations

import os
import re

import mutagen

AUDIO_EXT = {".mp3", ".wav", ".flac", ".aiff", ".aif", ".m4a", ".aac", ".ogg", ".opus", ".wma", ".alac"}

EASY_MAP = {"artist": "artist", "title": "title", "album": "album", "genre": "genre", "year": "date"}


def is_audio(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in AUDIO_EXT


def _guess_from_name(path: str) -> dict:
    base = os.path.splitext(os.path.basename(path))[0]
    base = re.sub(r"^\d+[\s.\-_]+", "", base)  # strip track number
    if " - " in base:
        artist, title = base.split(" - ", 1)
        return {"artist": artist.strip(), "title": title.strip()}
    return {"artist": "", "title": base.strip()}


def read_tags(path: str) -> dict:
    out = {"artist": "", "title": "", "album": "", "genre": "", "comment": "", "year": ""}
    try:
        f = mutagen.File(path, easy=True)
        if f is not None and f.tags:
            for k, src in EASY_MAP.items():
                v = f.tags.get(src)
                if v:
                    out[k] = str(v[0])
        full = mutagen.File(path)
        if full is not None and full.tags:
            for k in full.tags.keys():
                if str(k).startswith("COMM"):
                    out["comment"] = str(full.tags[k].text[0]) if full.tags[k].text else ""
                    break
            if "comment" in (full.tags.keys() if hasattr(full.tags, "keys") else []):
                out["comment"] = str(full.tags["comment"][0])
    except Exception:
        pass
    out["tag_key"] = read_tag_key(path, out.get("comment", ""))
    out["tag_bpm"] = read_tag_bpm(path)
    guess = _guess_from_name(path)
    if not out["title"]:
        out["title"] = guess["title"]
    if not out["artist"]:
        out["artist"] = guess["artist"]
    return out


def read_tag_bpm(path: str) -> float | None:
    """BPM written by another program (Mixed In Key, Traktor, Rekordbox): TBPM / tmpo / BPM."""
    try:
        f = mutagen.File(path)
        if f is None or not f.tags:
            return None
        t = f.tags
        for k in ("TBPM", "tmpo", "BPM", "bpm", "TEMPO", "----:com.apple.iTunes:BPM"):
            if k in t:
                v = t[k]
                v = v.text[0] if hasattr(v, "text") else (v[0] if isinstance(v, list) else v)
                if isinstance(v, bytes):
                    v = v.decode(errors="ignore")
                bpm = float(str(v).replace(",", ".").strip())
                if 40 <= bpm <= 250:
                    return round(bpm, 3)
    except Exception:
        return None
    return None


def read_cover(path: str) -> tuple[bytes, str] | None:
    try:
        f = mutagen.File(path)
        if f is None:
            return None
        tags = f.tags
        if tags is not None:
            for k in getattr(tags, "keys", lambda: [])():
                if str(k).startswith("APIC"):
                    return tags[k].data, tags[k].mime or "image/jpeg"
            if "covr" in tags:
                return bytes(tags["covr"][0]), "image/jpeg"
        pics = getattr(f, "pictures", None)
        if pics:
            return pics[0].data, pics[0].mime
    except Exception:
        pass
    return None


def write_tags(path: str, values: dict, key: str | None = None, bpm: float | None = None) -> None:
    """Write basic tags plus key (TKEY / initialkey) and BPM back to the file."""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".mp3":
        from mutagen.id3 import COMM, ID3, TBPM, TKEY, ID3NoHeaderError
        from mutagen.easyid3 import EasyID3

        try:
            easy = EasyID3(path)
        except ID3NoHeaderError:
            easy = mutagen.File(path, easy=True)
            easy.add_tags()
        for k, src in EASY_MAP.items():
            if values.get(k) is not None:
                easy[src] = str(values[k])
        easy.save()
        id3 = ID3(path)
        if values.get("comment") is not None:
            id3.delall("COMM")
            id3.add(COMM(encoding=3, lang="eng", desc="", text=str(values["comment"])))
        if key:
            id3.add(TKEY(encoding=3, text=key))
        if bpm:
            id3.add(TBPM(encoding=3, text=str(int(round(bpm)))))
        id3.save()
        return
    f = mutagen.File(path, easy=True)
    if f is None:
        raise ValueError("Nieobsługiwany format tagów")
    if f.tags is None:
        f.add_tags()
    for k, src in EASY_MAP.items():
        if values.get(k) is not None:
            f.tags[src] = str(values[k])
    try:
        if values.get("comment") is not None:
            f.tags["comment"] = str(values["comment"])
        if bpm:
            f.tags["bpm"] = str(int(round(bpm)))
        if key and ext in (".flac", ".ogg", ".opus"):
            f.tags["initialkey"] = key
    except Exception:
        pass  # some easy-tag backends (e.g. MP4/WAV) don't support every field
    f.save()


def read_tag_key(path: str, comment: str = "") -> str | None:
    """Key stored by other DJ software (Mixed In Key, Rekordbox, Traktor...), as Camelot."""
    from .camelot import parse_any

    try:
        f = mutagen.File(path)
        tags = f.tags if f is not None else None
        if tags is not None:
            for k in ("TKEY", "initialkey", "INITIALKEY", "----:com.apple.iTunes:initialkey", "KEY"):
                if k in tags:
                    v = tags[k]
                    v = v.text[0] if hasattr(v, "text") else v[0] if isinstance(v, list) else v
                    if isinstance(v, bytes):
                        v = v.decode(errors="ignore")
                    code = parse_any(str(v))
                    if code:
                        return code
    except Exception:
        pass
    return parse_any(comment.split("-")[0]) if comment else None
