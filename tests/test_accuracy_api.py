"""Cờ golden — đánh dấu bản sửa tay làm chuẩn đo WER (US-819, DB tạm)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import db, transcribe

TID = "20260101-000000-hop"


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


def _seed(tid=TID, text="bản máy thô"):
    db.insert_transcript(db.TranscriptRecord(
        transcript_id=tid, title="Họp", language="vi", model="m", duration=6.0,
        created_at="2026-01-01T00:00:00+07:00", text=text, raw_text=text,
        segments=None, llm_model=None, audio_file=None, audio_dir=None,
        speaker_map=None,
    ))


def test_golden_mac_dinh_tat(client):
    _seed()
    assert client.get(f"/api/transcripts/{TID}").json()["golden"] is False


def test_can_sua_tay_truoc_khi_danh_dau_chuan(client):
    # Cờ golden trên bản máy chưa ai soát → bộ đo sẽ chấm điểm chính output
    # của máy, ra số đẹp giả. Phải chặn.
    _seed()
    r = client.patch(f"/api/transcripts/{TID}/golden", json={"golden": True})
    assert r.status_code == 400
    assert client.get(f"/api/transcripts/{TID}").json()["golden"] is False


def test_danh_dau_va_go_chuan(client):
    _seed()
    db.set_edited_text(TID, "bản người sửa")
    assert client.patch(f"/api/transcripts/{TID}/golden", json={"golden": True}).status_code == 200
    assert client.get(f"/api/transcripts/{TID}").json()["golden"] is True
    assert client.patch(f"/api/transcripts/{TID}/golden", json={"golden": False}).status_code == 200
    assert client.get(f"/api/transcripts/{TID}").json()["golden"] is False


def test_golden_id_khong_ton_tai(client):
    assert client.patch("/api/transcripts/khong-co/golden", json={"golden": True}).status_code == 404


def test_danh_dau_khong_dung_toi_edited_text(client):
    _seed()
    db.set_edited_text(TID, "bản người sửa")
    client.patch(f"/api/transcripts/{TID}/golden", json={"golden": True})
    assert client.get(f"/api/transcripts/{TID}").json()["edited_text"] == "bản người sửa"


def test_golden_transcripts_bo_qua_ban_sua_rong(client):
    _seed()
    db.set_edited_text(TID, "bản người sửa")
    client.patch(f"/api/transcripts/{TID}/golden", json={"golden": True})
    assert [g["id"] for g in db.golden_transcripts()] == [TID]
    # Xoá bản sửa nhưng cờ vẫn bật → không còn là chuẩn hợp lệ.
    db.set_edited_text(TID, "   ")
    assert db.golden_transcripts() == []


def test_migration_golden_idempotent(client):
    # init() chạy lại trên DB đã có cột → không lỗi, không mất dữ liệu.
    _seed()
    db.set_edited_text(TID, "bản người sửa")
    db.set_golden(TID, True)
    db.init()
    db.init()
    assert [g["id"] for g in db.golden_transcripts()] == [TID]
