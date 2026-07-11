"""SQLite store — round-trip trên DB tạm (không đụng data/manju.db thật)."""
from __future__ import annotations

import pytest

from app import db


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DATA", tmp_path)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(db, "DEFAULT_RECORDINGS", tmp_path / "recordings")
    db.init()
    return tmp_path


def _insert(tid="20260101-000000-hop", corrected=False):
    db.insert_transcript(
        transcript_id=tid,
        title="Họp sprint",
        language="vi",
        model="large-v3-turbo",
        duration=42.4,
        created_at="2026-01-01T00:00:00+07:00",
        text="triển khai Kubernetes",
        raw_text="triển khai cu bơ nét" if corrected else None,
        segments=[{"start": 0.0, "text": "triển khai Kubernetes"}],
        llm_model="gemma4:e4b" if corrected else None,
        audio_file=None,
        audio_dir=None,
    )


def test_insert_read_roundtrip(tmp_db):
    _insert(corrected=True)
    row = db.read_transcript("20260101-000000-hop")
    assert row is not None
    assert row["text"] == "triển khai Kubernetes"
    assert row["raw_text"] == "triển khai cu bơ nét"
    assert row["corrected"] is True
    assert row["segments"] == [{"start": 0.0, "text": "triển khai Kubernetes"}]
    assert row["words"] == 3


def test_read_missing_returns_none(tmp_db):
    assert db.read_transcript("khong-ton-tai") is None


def test_list_orders_newest_first(tmp_db):
    _insert(tid="20260101-000000-a")
    db.insert_transcript(
        transcript_id="20260102-000000-b", title="B", language="vi", model="m",
        duration=1.0, created_at="2026-01-02T00:00:00+07:00", text="xin chào",
        raw_text=None, segments=None, llm_model=None, audio_file=None, audio_dir=None,
    )
    ids = [m["id"] for m in db.list_transcripts()]
    assert ids == ["20260102-000000-b", "20260101-000000-a"]


def test_settings_roundtrip(tmp_db):
    assert db.get_setting("missing", "fallback") == "fallback"
    db.set_setting("audio_dir", "/tmp/rec")
    assert db.get_setting("audio_dir") == "/tmp/rec"


def test_set_audio_dir_rejects_relative(tmp_db):
    with pytest.raises(ValueError):
        db.set_audio_dir("relative/path")


def test_set_audio_dir_accepts_absolute(tmp_db, tmp_path):
    target = tmp_path / "my-recordings"
    result = db.set_audio_dir(str(target))
    assert result == target
    assert db.get_audio_dir() == target


def test_sync_state_roundtrip_and_upsert(tmp_db):
    _insert()
    tid = "20260101-000000-hop"
    assert db.get_sync_state(tid) is None
    db.set_sync_state(tid, org_id="org-1", remote_id="r-1", pushed_at="2026-01-01T00:00:00+07:00")
    st = db.get_sync_state(tid)
    assert st["status"] == "pushed"
    assert st["remote_id"] == "r-1"
    # Push lại = cập nhật (upsert), không tạo dòng thứ hai.
    db.set_sync_state(tid, org_id="org-1", remote_id="r-2", pushed_at="2026-01-02T00:00:00+07:00")
    assert db.get_sync_state(tid)["remote_id"] == "r-2"


def test_list_includes_sync_status(tmp_db):
    _insert()
    assert db.list_transcripts()[0]["sync"] is None
    db.set_sync_state("20260101-000000-hop", org_id="o", remote_id="r", pushed_at="2026-01-01T00:00:00+07:00")
    assert db.list_transcripts()[0]["sync"] == "pushed"
