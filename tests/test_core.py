"""End-to-end checks on synthetic tracks with known key/tempo.

Run:  python -m pytest -q tests
"""
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
HOME = Path(tempfile.mkdtemp(prefix="blendora-test-"))
os.environ["BLENDORA_HOME"] = str(HOME)
os.environ["BLENDORA_STEMS"] = "quick"

from app.synth import make_track  # noqa: E402

CASES = [  # (name, tonic, mode, bpm, expected camelot)
    ("Alpha - Minor 124", "A", "minor", 124, "8A"),
    ("Beta - Major 124", "C", "major", 124, "8B"),
    ("Gamma - Dm 126", "D", "minor", 126, "7A"),
    ("Delta - G 128", "G", "major", 128, "9B"),
    ("Eps - Bm 87", "B", "minor", 87, "10A"),
]


@pytest.fixture(scope="module")
def client():
    music = HOME / "music"
    music.mkdir()
    for name, t, m, bpm, _ in CASES:
        make_track(str(music / f"{name}.wav"), t, m, bpm, bars=48, vocals_like=True, offset=0.25)
    # one MP3 to exercise ID3 tag writing
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(music / "Alpha - Minor 124.wav"), "-b:a", "192k", str(music / "Zeta - Mp3 Copy.mp3")], check=True)
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        r = c.post("/api/import/folder", json={"path": str(music)})
        assert r.json()["added"] == len(CASES) + 1
        for _ in range(300):
            st = c.get("/api/status").json()["counts"]
            if st.get("done", 0) + st.get("error", 0) == len(CASES) + 1:
                break
            time.sleep(1)
        yield c


def by_title(client):
    return {t["artist"]: t for t in client.get("/api/tracks").json()}


def test_key_and_tempo(client):
    tracks = by_title(client)
    for name, _, _, bpm, key in CASES:
        t = tracks[name.split(" - ")[0]]
        assert t["status"] == "done", t["error"]
        assert t["key"] == key, (name, t["key"])
        assert abs(t["bpm"] - bpm) < 0.5, (name, t["bpm"])
        assert 4 <= len(t["cues"]) <= 8
        assert abs(t["cues"][0] - 0.25) < 0.05, t["cues"][0]  # first downbeat


def test_ideas_and_shift(client):
    tracks = by_title(client)
    a = tracks["Alpha"]
    ideas = client.post("/api/ideas", json={"track_id": a["id"], "mode": "mashup"}).json()
    ids = {i["track_id"]: i for i in ideas}
    assert tracks["Beta"]["id"] in ids and ids[tracks["Beta"]["id"]]["shift_b"] == 0  # relative major
    assert tracks["Eps"]["id"] in ids and ids[tracks["Eps"]["id"]]["tempo_factor"] == 2.0  # half-time
    mix = client.post("/api/ideas", json={"track_id": a["id"], "mode": "mix"}).json()
    assert all(i["shift_b"] == 0 for i in mix)
    shifted = client.post("/api/ideas", json={"track_id": a["id"], "shift_a": 2, "mode": "mix"}).json()
    assert {i["track_id"] for i in shifted} != {i["track_id"] for i in mix}


def test_audio_variants_and_export(client):
    tracks = by_title(client)
    a, b = tracks["Alpha"], tracks["Gamma"]
    r = client.get(f"/api/tracks/{a['id']}/audio", params={"semitones": 2})
    assert r.status_code == 200 and len(r.content) > 10000
    job = client.post(f"/api/tracks/{a['id']}/stems").json()
    for _ in range(120):
        job = client.get(f"/api/jobs/{job['id']}").json()
        if job["status"] != "running":
            break
        time.sleep(0.5)
    assert job["status"] == "done", job
    r = client.get(f"/api/tracks/{a['id']}/audio", params={"stem": "vocals", "semitones": -1})
    assert r.status_code == 200
    job = client.post("/api/export", json={"track_id": a["id"], "stem": "instrumental", "semitones": 1, "format": "mp3"}).json()
    for _ in range(120):
        job = client.get(f"/api/jobs/{job['id']}").json()
        if job["status"] != "running":
            break
        time.sleep(0.5)
    assert job["status"] == "done", job
    assert Path(job["result"]["path"]).exists() and "[9B" not in job["result"]["path"]
    assert "(Instrumental) [3A +1]" in job["result"]["path"]
    job = client.post("/api/export/mashup", json={"track_a": a["id"], "track_b": b["id"], "stem_a": "vocals", "stem_b": "full", "shift_b": 1, "bars": 8, "format": "wav", "cue_b": b["cues"][1]}).json()
    for _ in range(120):
        job = client.get(f"/api/jobs/{job['id']}").json()
        if job["status"] != "running":
            break
        time.sleep(0.5)
    assert job["status"] == "done", job
    import soundfile as sf

    info = sf.info(job["result"]["path"])
    assert abs(info.duration - 8 * 4 * 60 / 124) < 0.2


def test_playlists_and_tags(client):
    tracks = by_title(client)
    pid = client.post("/api/playlists", json={"name": "Set"}).json()["id"]
    ids = [t["id"] for t in tracks.values() if t["status"] == "done"]
    client.post(f"/api/playlists/{pid}/tracks", json={"track_ids": ids})
    order = client.post(f"/api/playlists/{pid}/harmonic-sort", json={"curve": "build"}).json()
    assert sorted(order) == sorted(ids)
    assert "#EXTM3U" in client.get(f"/api/playlists/{pid}/m3u").text
    z = tracks["Zeta"]
    r = client.patch(f"/api/tracks/{z['id']}", json={"genre": "Melodic House", "comment": "8A - Energy 5", "write_file": True})
    assert r.status_code == 200, r.text
    from app.tags import read_tags

    tg = read_tags(z["path"])
    assert tg["genre"] == "Melodic House" and tg["comment"] == "8A - Energy 5"
    from mutagen.id3 import ID3

    assert str(ID3(z["path"])["TKEY"]) == "8A"
    mid = client.post("/api/mashups", json={"track_a": ids[0], "track_b": ids[1], "rating": 4}).json()["id"]
    client.patch(f"/api/mashups/{mid}", json={"rating": 5})
    assert client.get("/api/mashups").json()[0]["rating"] == 5


def test_camelot_rules():
    from app import camelot

    assert camelot.transpose("8A", 1) == "3A"
    assert camelot.transpose("8A", -1) == "1A"
    assert camelot.standard_name("8A") == "A Minor" and camelot.standard_name("12B") == "E Major"
    assert camelot.relation("8A", "8B")[1] > camelot.relation("8A", "10A")[1]
    assert camelot.best_shift("8A", "1A")[0] == 1  # G#m up one semitone = Am


def test_realistic_grid_and_phrase_cues(tmp_path):
    """House-style track with a beatless breakdown and a non-integer tempo."""
    from app.analysis import analyze_file
    from app.synth import make_realistic

    p = str(tmp_path / "real.wav")
    gt = make_realistic(p, "B", "minor", 123.7, offset=1.1)
    r = analyze_file(p)
    beat = 60 / 123.7
    assert abs(r["bpm"] - 123.7) < 0.01
    # first downbeat on the right beat of the bar, within 15 ms
    assert abs(r["first_downbeat"] - gt["downbeat"]) < 0.015
    # every section start (intro, build, breakdown, drop, outro) gets a cue
    for b in gt["boundaries"]:
        assert any(abs(c - b) < beat / 2 for c in r["cues"]), (b, r["cues"])
    # grid stays on the beat to the end of the track
    last = gt["boundaries"][-1] + 15 * 4 * beat
    k = round((last - r["beat_offset"]) / (60 / r["bpm"]))
    assert abs(r["beat_offset"] + k * 60 / r["bpm"] - last) < 0.015


def test_grid_patch(client):
    t = by_title(client)["Alpha"]
    r = client.patch(f"/api/tracks/{t['id']}", json={"bpm": t["bpm"] * 2, "first_downbeat": 0.5, "cues": [3.0, 1.0, 2.0]}).json()
    assert r["bpm"] == t["bpm"] * 2 and r["first_downbeat"] == 0.5 and r["cues"] == [1.0, 2.0, 3.0]
    assert client.patch(f"/api/tracks/{t['id']}", json={"bpm": 500}).status_code == 400
    client.patch(f"/api/tracks/{t['id']}", json={"bpm": t["bpm"], "first_downbeat": t["first_downbeat"], "cues": t["cues"]})
