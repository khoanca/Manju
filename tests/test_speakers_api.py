"""Endpoints lớp speaker — gán cụm→tên, CRUD người, guard diarize (DB tạm)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import db, diarize, transcribe


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DATA", tmp_path)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(db, "DEFAULT_RECORDINGS", tmp_path / "rec")
    monkeypatch.setattr(transcribe, "TRANSCRIPTS", tmp_path / "tx")
    monkeypatch.setattr(transcribe, "RECORDINGS", tmp_path / "rec")
    (tmp_path / "tx").mkdir()
    (tmp_path / "rec").mkdir()
    from app.main import app
    with TestClient(app) as c:
        yield c


def _seed_transcript(tid="20260101-000000-hop"):
    db.insert_transcript(db.TranscriptRecord(
        transcript_id=tid, title="Họp", language="vi", model="m", duration=6.0,
        created_at="2026-01-01T00:00:00+07:00", text="a b", raw_text=None,
        segments=[{"start": 0.0, "end": 3.0, "text": "a", "spk": 0},
                  {"start": 3.0, "end": 6.0, "text": "b", "spk": 1}],
        llm_model=None, audio_file=None, audio_dir=None,
        speaker_map={"0": None, "1": None},
    ))


def test_assign_cluster_creates_speaker_and_maps(client):
    _seed_transcript()
    r = client.put("/api/transcripts/20260101-000000-hop/speaker-map",
                   json={"cluster": 0, "name": "Minh"})
    assert r.status_code == 200
    body = r.json()
    sid = body["speaker_map"]["0"]
    assert sid is not None
    assert body["speakers"][sid] == "Minh"
    # gán cùng tên cho transcript khác → tái dùng speaker_id (không tạo trùng)
    assert len(client.get("/api/speakers").json()) == 1


def test_assign_same_name_reuses_speaker(client):
    _seed_transcript()
    r1 = client.put("/api/transcripts/20260101-000000-hop/speaker-map",
                    json={"cluster": 0, "name": "An"})
    r2 = client.put("/api/transcripts/20260101-000000-hop/speaker-map",
                    json={"cluster": 1, "name": "an"})  # khác hoa/thường
    assert r1.json()["speaker_map"]["0"] == r2.json()["speaker_map"]["1"]


def test_unassign_cluster(client):
    _seed_transcript()
    client.put("/api/transcripts/20260101-000000-hop/speaker-map",
               json={"cluster": 0, "name": "Minh"})
    r = client.put("/api/transcripts/20260101-000000-hop/speaker-map",
                   json={"cluster": 0, "speaker_id": None})
    assert r.json()["speaker_map"]["0"] is None


def test_rename_and_delete_speaker_clears_map(client):
    _seed_transcript()
    client.put("/api/transcripts/20260101-000000-hop/speaker-map",
               json={"cluster": 0, "name": "Minh"})
    sid = db.read_transcript("20260101-000000-hop")["speaker_map"]["0"]
    assert client.patch(f"/api/speakers/{sid}", json={"name": "Minh Nguyễn"}).status_code == 200
    assert db.speaker_names()[sid] == "Minh Nguyễn"
    assert client.delete(f"/api/speakers/{sid}").status_code == 200
    # xóa người → gỡ khỏi speaker_map, KHÔNG rớt câu
    data = db.read_transcript("20260101-000000-hop")
    assert data["speaker_map"]["0"] is None
    assert len(data["segments"]) == 2


def test_assign_missing_transcript_404(client):
    r = client.put("/api/transcripts/khong-co/speaker-map", json={"cluster": 0, "name": "X"})
    assert r.status_code == 404


def test_patch_speaker_region_valid_empty_and_untouched(client):
    sid = db.find_or_create_speaker("Minh")
    r = client.patch(f"/api/speakers/{sid}", json={"name": "Minh", "region": "nam"})
    assert r.status_code == 200
    assert client.get("/api/speakers").json()[0]["region"] == "nam"
    # rỗng → NULL
    r = client.patch(f"/api/speakers/{sid}", json={"name": "Minh", "region": ""})
    assert r.status_code == 200
    assert client.get("/api/speakers").json()[0]["region"] is None
    # không gửi region → giữ nguyên giá trị đang có
    db.set_speaker_region(sid, "bac")
    assert client.patch(f"/api/speakers/{sid}", json={"name": "Minh mới"}).status_code == 200
    row = client.get("/api/speakers").json()[0]
    assert (row["name"], row["region"]) == ("Minh mới", "bac")


def test_patch_speaker_region_invalid_400(client):
    sid = db.find_or_create_speaker("Minh")
    r = client.patch(f"/api/speakers/{sid}", json={"name": "Minh", "region": "tay-bac"})
    assert r.status_code == 400
    assert client.get("/api/speakers").json()[0]["region"] is None


# ── Hook mine từ vựng cá nhân sau diarize/gán tên cụm ──────────────────────
def test_assign_cluster_mines_speaker_terms(client):
    db.insert_transcript(db.TranscriptRecord(
        transcript_id="t-mine", title="Họp", language="vi", model="m", duration=3.0,
        created_at="2026-01-01T00:00:00+07:00", text="x", raw_text=None,
        segments=[{"start": 0.0, "end": 3.0,
                   "text": "deploy Kubernetes rồi lại Kubernetes", "spk": 0}],
        llm_model=None, audio_file=None, audio_dir=None, speaker_map={"0": None},
    ))
    r = client.put("/api/transcripts/t-mine/speaker-map", json={"cluster": 0, "name": "Minh"})
    assert r.status_code == 200
    sid = r.json()["speaker_map"]["0"]
    assert "Kubernetes" in db.personal_terms([sid])


def test_assign_cluster_200_when_mining_raises(client, monkeypatch):
    from app import corrections

    def boom(tid):
        raise RuntimeError("mine nổ giả lập")

    monkeypatch.setattr(corrections, "mine_speaker_terms", boom)
    _seed_transcript()
    r = client.put("/api/transcripts/20260101-000000-hop/speaker-map",
                   json={"cluster": 0, "name": "Minh"})
    assert r.status_code == 200
    assert r.json()["speaker_map"]["0"] is not None


def test_diarize_200_when_mining_raises(client, monkeypatch, tmp_path):
    from app import corrections

    def boom(tid):
        raise RuntimeError("mine nổ giả lập")

    monkeypatch.setattr(corrections, "mine_speaker_terms", boom)
    monkeypatch.setattr(diarize, "models_present", lambda: True)
    monkeypatch.setattr(diarize, "diarize_file", lambda audio, n: [(0.0, 3.0, 0)])
    monkeypatch.setattr(diarize, "assign_speakers", lambda segs, spans: segs)
    monkeypatch.setattr(diarize, "initial_speaker_map", lambda segs: {"0": None})
    monkeypatch.setattr(diarize, "to_np_voiceprints", lambda vps: [])
    monkeypatch.setattr(diarize, "identify_clusters", lambda audio, segs, smap, vps: smap)
    (tmp_path / "rec" / "a.wav").write_bytes(b"\x00")
    db.insert_transcript(db.TranscriptRecord(
        transcript_id="t-d", title="Họp", language="vi", model="m", duration=3.0,
        created_at="2026-01-01T00:00:00+07:00", text="a", raw_text=None,
        segments=[{"start": 0.0, "end": 3.0, "text": "a", "spk": 0}],
        llm_model=None, audio_file="a.wav", audio_dir=str(tmp_path / "rec"),
    ))
    r = client.post("/api/transcripts/t-d/diarize")
    assert r.status_code == 200


def test_diarize_guard_no_models(client, monkeypatch):
    _seed_transcript()
    monkeypatch.setattr(diarize, "models_present", lambda: False)
    r = client.post("/api/transcripts/20260101-000000-hop/diarize")
    assert r.status_code == 503


def test_diarize_guard_missing_transcript(client, monkeypatch):
    monkeypatch.setattr(diarize, "models_present", lambda: True)
    r = client.post("/api/transcripts/khong-co/diarize")
    assert r.status_code == 404


# ── Settings API: glossary — server là nguồn chân lý (US-803 AC3) ───────────
def _stub_engine(monkeypatch):
    # GET /api/settings đọc engine info — stub để test không probe engine thật.
    from types import SimpleNamespace

    from app import engines
    info = SimpleNamespace(tier="cpu", model_name="stub")
    monkeypatch.setattr(engines, "get_engine", lambda: SimpleNamespace(info=info))


def test_settings_glossary_default_empty(client, monkeypatch):
    _stub_engine(monkeypatch)
    assert client.get("/api/settings").json()["glossary"] == ""


def test_settings_glossary_put_then_get(client, monkeypatch):
    _stub_engine(monkeypatch)
    r = client.put("/api/settings", json={"glossary": "Kubernetes, RAG pipeline"})
    assert r.status_code == 200
    assert r.json()["glossary"] == "Kubernetes, RAG pipeline"
    assert client.get("/api/settings").json()["glossary"] == "Kubernetes, RAG pipeline"


def test_settings_put_without_glossary_keeps_it(client, monkeypatch):
    _stub_engine(monkeypatch)
    client.put("/api/settings", json={"glossary": "MLOps"})
    # PUT key khác không đụng glossary; PUT glossary không đụng key khác.
    client.put("/api/settings", json={"diarize_enabled": True})
    s = client.get("/api/settings").json()
    assert s["glossary"] == "MLOps"
    assert s["diarize"]["enabled"] is True
    client.put("/api/settings", json={"glossary": ""})
    s = client.get("/api/settings").json()
    assert s["glossary"] == ""  # cho phép xóa glossary bằng chuỗi rỗng
    assert s["diarize"]["enabled"] is True


def test_settings_flag_words_and_denoise_roundtrip(client, monkeypatch):
    _stub_engine(monkeypatch)
    s = client.get("/api/settings").json()
    assert (s["flag_words"], s["denoise_enabled"], s["live_ident"]) == (False, False, False)
    r = client.put(
        "/api/settings", json={"flag_words": True, "denoise_enabled": True, "live_ident": True}
    )
    assert r.status_code == 200
    assert (r.json()["flag_words"], r.json()["denoise_enabled"]) == (True, True)
    assert r.json()["live_ident"] is True
    assert db.get_setting("flag_words") == "1"
    assert db.get_setting("denoise_enabled") == "1"
    assert db.get_setting("live_ident") == "1"
    # PUT 1 key không đụng key kia
    client.put("/api/settings", json={"flag_words": False})
    s = client.get("/api/settings").json()
    assert (s["flag_words"], s["denoise_enabled"]) == (False, True)
