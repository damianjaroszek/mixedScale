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
        assert 3 <= len(t["cues"]) <= 8
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


def test_cues_on_kick_changes_and_downbeat_vote(tmp_path):
    """Odd section lengths, a 1-bar gap before the drop; cues must sit on the beat where the kick
    stops / returns, and a wrong downbeat guess is corrected by those changes."""
    from app import analysis
    from app.ffmpeg import decode
    from app.synth import make_realistic

    p = str(tmp_path / "odd.wav")
    sec = [("intro", 6), ("build", 16), ("break", 12), ("gap", 1), ("drop", 24), ("break", 8), ("drop", 16), ("outro", 12)]
    gt = make_realistic(p, "A", "minor", 126, offset=0.37, sections=sec, seed=1)
    beat = 60 / 126
    y = decode(p, sr=analysis.SR, mono=True)
    for wrong in (0, 1, 2, 3):
        s = analysis.analyze_structure(y, 126.0, gt["downbeat"] + wrong * beat)
        assert abs(s["first_downbeat"] - gt["downbeat"]) < 0.02, (wrong, s["first_downbeat"])
        kinds = {round(g["start"], 1): g["kind"] for g in s["segments"]}
        for (name, _), b in zip(sec, gt["boundaries"]):
            if name == "gap":
                continue
            hit = [k for t, k in kinds.items() if abs(t - b) < beat / 2]
            assert hit, (name, b, kinds)
            if name in ("break", "outro"):
                assert hit[0] == name, (name, hit)
        drops = [t for t, k in kinds.items() if k == "drop"]
        assert any(abs(t - gt["boundaries"][4]) < beat / 2 for t in drops)  # the drop after the gap


def test_grid_patch(client):
    t = by_title(client)["Alpha"]
    r = client.patch(f"/api/tracks/{t['id']}", json={"bpm": t["bpm"] * 2, "first_downbeat": 0.5, "cues": [3.0, 1.0, 2.0]}).json()
    assert r["bpm"] == t["bpm"] * 2 and r["first_downbeat"] == 0.5 and r["cues"] == [1.0, 2.0, 3.0]
    assert client.patch(f"/api/tracks/{t['id']}", json={"bpm": 500}).status_code == 400
    client.patch(f"/api/tracks/{t['id']}", json={"bpm": t["bpm"], "first_downbeat": t["first_downbeat"], "cues": t["cues"]})


def test_tag_key_parsing():
    from app.camelot import parse_any

    cases = {"8A": "8A", "08A - Energy 6": "8A", "Am": "8A", "A minor": "8A", "F#m": "11A", "Ebmin": "2A", "C": "8B", "Bbmaj": "6B", "Gm": "6A", "nonsense": None}
    for text, code in cases.items():
        assert parse_any(text) == code, text


def test_keycheck_and_learning(tmp_path):
    """Profiles learned from tagged tracks are cross-validated and only applied when they help."""
    from app.analysis import key_from_features, learn_profiles

    rng = __import__("numpy").random.default_rng(0)
    from app import camelot

    # synthetic features: tonic-aligned chroma with a library-specific bias
    items = []
    for i in range(60):
        mode = "minor" if i % 2 else "major"
        pc = i % 12
        base = [1.0, 0.1, 0.5, 0.9 if mode == "minor" else 0.15, 0.2 if mode == "minor" else 0.9, 0.6, 0.1, 0.95, 0.6 if mode == "minor" else 0.2, 0.3, 0.5 if mode == "minor" else 0.2, 0.3 if mode == "minor" else 0.4]
        chroma = __import__("numpy").roll(__import__("numpy").array(base) + rng.uniform(0, 0.15, 12), pc)
        items.append(({"chroma": chroma.tolist(), "bass": chroma.tolist(), "chords": []}, camelot.to_camelot(pc, mode)))
    prof = learn_profiles(items)
    assert prof is not None
    acc = sum(key_from_features(f, prof)["key"] == k for f, k in items) / len(items)
    assert acc > 0.9
    r = key_from_features(items[0][0])
    assert set(r) >= {"key", "key2", "key_confidence"} and r["key"] != r["key2"]


def test_rekordbox_xml():
    import xml.etree.ElementTree as ET
    from app import rekordbox
    t = {"id": 3, "path": "/music/a b.mp3", "title": "T", "artist": "A", "album": "", "genre": "House", "duration": 200.4,
         "year": "2020", "bpm": 125.0, "key": "8A", "comment": "", "rating": 4, "first_downbeat": 4.1, "beat_offset": 0.26, "cues": [4.1, 34.82]}
    x = ET.fromstring(rekordbox.build([t], [("Set", [3, 99])]))
    tr = x.find("COLLECTION/TRACK")
    assert tr.get("Tonality") == "Am" and tr.get("AverageBpm") == "125.00" and tr.get("Rating") == "204"
    assert tr.get("Location") == "file://localhost/music/a%20b.mp3"
    tempo = tr.find("TEMPO")
    assert 0 <= float(tempo.get("Inizio")) < 1.92 and abs((4.1 - float(tempo.get("Inizio"))) % 1.92) < 1e-3
    hot = [m for m in tr.findall("POSITION_MARK") if m.get("Num") != "-1"]
    assert [m.get("Num") for m in hot] == ["0", "1"]
    pl = x.find("PLAYLISTS/NODE/NODE/NODE")
    assert pl.get("Name") == "Set" and pl.get("Entries") == "1"


def test_traktor_nml():
    import xml.etree.ElementTree as ET
    from app import traktor
    assert traktor.split_location(r"C:\Users\Damian\Music\a b.mp3") == ("C:", "/:Users/:Damian/:Music/:", "a b.mp3")
    assert traktor.primary_key(r"D:\x\y.mp3") == "D:/:x/:y.mp3"
    assert traktor.key_value("8A") == 21 and traktor.key_value("8B") == 0
    t = {"id": 1, "path": r"C:\M\t.mp3", "title": "T", "artist": "A", "album": "", "genre": "", "duration": 100, "year": "",
         "bpm": 120.0, "key": "8A", "comment": "", "rating": 5, "first_downbeat": 2.5, "beat_offset": 0.5, "cues": [2.5, 34.5]}
    x = ET.fromstring(traktor.build([t], [("Set", [1])]))
    e = x.find("COLLECTION/ENTRY")
    cues = e.findall("CUE_V2")
    assert cues[0].get("TYPE") == "4" and abs(float(cues[0].get("START")) - 500) < 1e-3
    assert [c.get("HOTCUE") for c in cues[1:]] == ["0", "1"] and cues[2].get("START") == "34500.000000"
    assert e.find("MUSICAL_KEY").get("VALUE") == "21" and e.find("INFO").get("RANKING") == "255"
    assert x.find(".//PRIMARYKEY").get("KEY") == "C:/:M/:t.mp3"


def test_four_stems_mute_mix(client):
    """Stem mode: muted stems are left out of the mix served to the deck / export; lanes waveform."""
    from app import ffmpeg as ff
    from app import stems
    from app.db import CACHE

    t = next(iter(by_title(client).values()))
    d = stems.stem_dir(CACHE, t["id"])
    d.mkdir(parents=True, exist_ok=True)
    for st, flt in (("drums", "highpass=f=4000"), ("bass", "lowpass=f=150"), ("other", "bandpass=f=800"), ("vocals", "bandpass=f=2000")):
        ff.run(["-i", t["path"], "-af", flt, "-t", "20", "-c:a", "flac", str(d / f"{st}.flac")])
    assert stems.has_stems4(CACHE, t["id"])
    r = client.get(f"/api/tracks/{t['id']}/audio", params={"stem": "mute:vocals,drums", "semitones": 1})
    assert r.status_code == 200 and len(r.content) > 10000
    assert (d / "mix_bass_other.flac").exists()
    assert client.get(f"/api/tracks/{t['id']}/audio", params={"stem": "mute:drums,bass,other,vocals"}).status_code == 400
    w = client.get(f"/api/tracks/{t['id']}/stemwave").json()
    assert set(w) >= {"pps", "drums", "bass", "other", "vocals"} and max(w["bass"]) > 0


def test_traktor_collection_roundtrip(tmp_path):
    from app import traktor
    t = {"id": 1, "path": r"C:\M\t.mp3", "title": "T", "artist": "A", "album": "", "genre": "", "duration": 100, "year": "",
         "bpm": 124.5, "key": "8A", "comment": "", "rating": 0, "first_downbeat": None, "beat_offset": 0.5, "cues": []}
    p = tmp_path / "collection.nml"
    p.write_bytes(traktor.build([t], []))
    got = traktor.read_collection(str(p))
    e = got[str(Path(r"C:\M\t.mp3")) if os.name == "nt" else next(iter(got))]
    assert e["bpm"] == 124.5 and abs(e["grid"] - 0.5) < 1e-3 and e["key"] == "8A"


def test_tempocheck_against_traktor(client, tmp_path):
    from app import traktor
    tracks = list(by_title(client).values())
    t = client.get(f"/api/tracks/{tracks[0]['id']}").json()
    ref = dict(t, beat_offset=(t["beat_offset"] or 0) + 0.025, first_downbeat=(t["first_downbeat"] or 0) + 0.025)
    nml = tmp_path / "collection.nml"
    nml.write_bytes(traktor.build([ref], []))
    r = client.post("/api/traktor/compare", json={"path": str(nml)}).json()
    assert r["matched"] >= 1
    d = client.get("/api/tempocheck").json()
    row = next(x for x in d["diffs"] if x["id"] == t["id"])
    assert row["kind"] == "same" and abs(row["phase_ms"] + 25) < 1.5
    client.post("/api/tempocheck/use-traktor", json={"ids": [t["id"]]})
    assert not any(x["id"] == t["id"] for x in client.get("/api/tempocheck").json()["diffs"])
    rep = client.post(f"/api/tracks/{t['id']}/report", json={"note": "test"}).json()
    assert Path(rep["path"]).exists()


def test_ni_stem_box(tmp_path):
    from app import ffmpeg as ff
    from app import nistem
    src = tmp_path / "a.wav"
    ff.run(["-f", "lavfi", "-i", "sine=f=440:d=2", str(src)])
    dst = tmp_path / "x.stem.mp4"
    nistem.write_stem_file(str(src), [str(src)] * 4, str(dst), {"title": "X", "artist": "Y", "bpm": 128.0, "key": "8A"})
    data = dst.read_bytes()
    assert b"stem" in data and (b"Drums" in data or b"drums" in data)
    info = subprocess.run([ff.ffmpeg_bin(), "-hide_banner", "-i", str(dst)], capture_output=True, text=True).stderr
    assert info.count("Audio: aac") == 5
    dec = subprocess.run([ff.ffmpeg_bin(), "-v", "error", "-i", str(dst), "-map", "0:4", "-f", "null", "-"], capture_output=True, text=True)
    assert dec.returncode == 0 and not dec.stderr.strip()
