"""Quick self-check used by the Windows build: synthesise a track, analyse it, verify key/BPM."""
from __future__ import annotations

import json
import os
import tempfile


def run() -> bool:
    from . import ffmpeg
    from .analysis import analyze_file
    from .synth import make_track

    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "Selftest - Am 124.wav")
        make_track(path, "A", "minor", 124, bars=32, vocals_like=True, offset=0.25)
        res = analyze_file(path)
        shifted = os.path.join(tmp, "shift.flac")
        ffmpeg.render(path, shifted, semitones=2, tempo=1.02)
        ok = res["key"] == "8A" and abs(res["bpm"] - 124) < 0.5 and os.path.getsize(shifted) > 10000
        print(json.dumps({"ok": ok, "key": res["key"], "bpm": res["bpm"], "energy": res["energy"],
                          "ffmpeg": ffmpeg.ffmpeg_bin(), "rubberband": ffmpeg.has_rubberband()}))
        return ok
