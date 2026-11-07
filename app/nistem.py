"""Native Instruments Stem files (.stem.mp4) for Traktor's stem decks.

An MP4 with five AAC tracks (full mix, then drums, bass, melody, vocals) and a 'stem' box in
moov/udta holding JSON with the stem names/colours and the (disabled) mastering DSP."""
from __future__ import annotations

import json
import os
import struct

from . import ffmpeg

STEM_META = {
    "mastering_dsp": {
        "compressor": {"enabled": False, "ratio": 3, "output_gain": 0.5, "release": 0.3, "attack": 0.003,
                       "input_gain": 0.5, "threshold": 0, "hp_cutoff": 300, "dry_wet": 50},
        "limiter": {"enabled": False, "release": 0.05, "threshold": 0, "ceiling": -0.35},
    },
    "version": 1,
    "stems": [
        {"color": "#FFB547", "name": "Drums"},
        {"color": "#FF5D73", "name": "Bass"},
        {"color": "#2DE0A7", "name": "Melody"},
        {"color": "#8B7BFF", "name": "Vocals"},
    ],
}

CONTAINERS = {b"moov", b"trak", b"mdia", b"minf", b"stbl", b"udta", b"edts", b"dinf"}


def _boxes(data: bytes, start: int, end: int):
    """Yield (offset, size, type, header_len) of the boxes in data[start:end]."""
    i = start
    while i + 8 <= end:
        size, typ = struct.unpack(">I4s", data[i : i + 8])
        hl = 8
        if size == 1:
            size = struct.unpack(">Q", data[i + 8 : i + 16])[0]
            hl = 16
        elif size == 0:
            size = end - i
        if size < hl:
            break
        yield i, size, typ, hl
        i += size


def _shift_chunk_offsets(buf: bytearray, start: int, end: int, delta: int) -> None:
    for off, size, typ, hl in list(_boxes(buf, start, end)):
        if typ in CONTAINERS:
            _shift_chunk_offsets(buf, off + hl, off + size, delta)
        elif typ == b"stco":
            n = struct.unpack(">I", buf[off + hl + 4 : off + hl + 8])[0]
            p = off + hl + 8
            for k in range(n):
                v = struct.unpack(">I", buf[p + 4 * k : p + 4 * k + 4])[0]
                struct.pack_into(">I", buf, p + 4 * k, v + delta)
        elif typ == b"co64":
            n = struct.unpack(">I", buf[off + hl + 4 : off + hl + 8])[0]
            p = off + hl + 8
            for k in range(n):
                v = struct.unpack(">Q", buf[p + 8 * k : p + 8 * k + 8])[0]
                struct.pack_into(">Q", buf, p + 8 * k, v + delta)


def add_stem_box(path: str, meta: dict = STEM_META) -> None:
    data = bytearray(open(path, "rb").read())
    top = list(_boxes(data, 0, len(data)))
    moov = next((b for b in top if b[2] == b"moov"), None)
    if moov is None:
        raise ValueError("Brak moov w pliku MP4")
    m_off, m_size, _, m_hl = moov
    payload = json.dumps(meta, separators=(",", ":")).encode()
    stem_box = struct.pack(">I4s", 8 + len(payload), b"stem") + payload
    udta = next((b for b in _boxes(data, m_off + m_hl, m_off + m_size) if b[2] == b"udta"), None)
    if udta is not None:
        u_off, u_size, _, u_hl = udta
        insert_at, add = u_off + u_size, stem_box
        struct.pack_into(">I", data, u_off, u_size + len(add))  # (32-bit sized udta)
    else:
        insert_at = m_off + m_size
        add = struct.pack(">I4s", 8 + len(stem_box), b"udta") + stem_box
    if m_hl == 8:
        struct.pack_into(">I", data, m_off, m_size + len(add))
    else:
        struct.pack_into(">Q", data, m_off + 8, m_size + len(add))
    # media data after moov moves by len(add): fix the chunk offsets
    if any(b[2] == b"mdat" and b[0] > m_off for b in top):
        _shift_chunk_offsets(data, m_off + m_hl, m_off + m_size, len(add))
    data[insert_at:insert_at] = add
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)


def write_stem_file(mix: str, parts: list[str], dst: str, tags: dict) -> str:
    """parts = [drums, bass, other, vocals]."""
    tmp = dst + ".part.mp4"
    args = []
    for p in [mix, *parts]:
        args += ["-i", p]
    for i in range(5):
        args += ["-map", f"{i}:a:0"]
    args += ["-c:a", "aac", "-b:a", "256k", "-ar", "44100", "-ac", "2", "-disposition:a:0", "default"]
    for i in range(1, 5):
        args += [f"-disposition:a:{i}", "0"]
    for k in ("title", "artist", "album", "genre"):
        if tags.get(k):
            args += ["-metadata", f"{k}={tags[k]}"]
    args += ["-f", "mp4", tmp]
    ffmpeg.run(args)
    try:  # BPM and key the way Traktor / iTunes read them
        from mutagen.mp4 import MP4, MP4FreeForm

        f = MP4(tmp)
        if tags.get("bpm"):
            f["tmpo"] = [int(round(tags["bpm"]))]
        if tags.get("key"):
            f["----:com.apple.iTunes:initialkey"] = [MP4FreeForm(tags["key"].encode())]
        f.save()
    except Exception:
        pass
    add_stem_box(tmp)
    os.replace(tmp, dst)
    return dst
