"""Endpoint rà & sửa lặp/nhiễu toàn văn — bản xem trước, không tự lưu (DB tạm)."""
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


def _seed(text: str, tid: str = TID) -> None:
    db.insert_transcript(db.TranscriptRecord(
        transcript_id=tid, title="Họp", language="vi", model="m", duration=6.0,
        created_at="2026-01-01T00:00:00+07:00", text=text, raw_text=text,
        segments=None, llm_model=None, audio_file=None, audio_dir=None,
        speaker_map=None,
    ))


def test_review_fix_collapses_loop(client):
    _seed("ok " + "tick là " * 40 + "xong")
    r = client.post(f"/api/transcripts/{TID}/review-fix")
    assert r.status_code == 200
    j = r.json()
    assert j["changed"] is True
    assert j["chars_removed"] > 0
    assert "tick là tick là" not in j["cleaned"]


def test_review_fix_noop_on_clean_text(client):
    _seed("một câu hội thoại bình thường không lặp")
    j = client.post(f"/api/transcripts/{TID}/review-fix").json()
    assert j["changed"] is False
    assert j["chars_removed"] == 0
    assert j["dropped"] == []


def test_review_fix_404_when_missing(client):
    assert client.post("/api/transcripts/khong-ton-tai/review-fix").status_code == 404


def test_review_fix_uses_edited_text_when_present(client):
    # Có bản sửa tay → rà trên bản đó, không phải bản máy.
    _seed("bản máy sạch")
    db.set_edited_text(TID, "ha " + "lô lô " * 30 + "kết")
    j = client.post(f"/api/transcripts/{TID}/review-fix").json()
    assert j["changed"] is True
    assert "lô lô lô" not in j["cleaned"]
