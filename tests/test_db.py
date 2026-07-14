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
    db.insert_transcript(db.TranscriptRecord(
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
    ))


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


def test_speaker_map_roundtrip(tmp_db):
    db.insert_transcript(db.TranscriptRecord(
        transcript_id="20260101-000000-spk", title="Họp", language="vi", model="m",
        duration=5.0, created_at="2026-01-01T00:00:00+07:00", text="a b",
        raw_text=None,
        segments=[{"start": 0.0, "end": 2.0, "text": "a", "spk": 0},
                  {"start": 2.0, "end": 4.0, "text": "b", "spk": 1}],
        llm_model=None, audio_file=None, audio_dir=None,
        speaker_map={"0": "spk-an", "1": None},
    ))
    row = db.read_transcript("20260101-000000-spk")
    assert row["speaker_map"] == {"0": "spk-an", "1": None}
    assert row["segments"][0]["spk"] == 0


def test_update_speaker_layer_overwrites(tmp_db):
    _insert()
    db.update_speaker_layer(
        "20260101-000000-hop",
        [{"start": 0.0, "end": 1.0, "text": "triển khai Kubernetes", "spk": 0}],
        {"0": "spk-x"},
    )
    row = db.read_transcript("20260101-000000-hop")
    assert row["speaker_map"] == {"0": "spk-x"}
    assert row["segments"][0]["spk"] == 0


def test_ensure_columns_adds_speaker_map_to_legacy_db(tmp_db):
    """DB cũ (transcripts thiếu speaker_map) → _ensure_columns tự ALTER thêm cột."""
    import sqlite3
    conn = sqlite3.connect(db.DB_PATH)
    conn.execute("ALTER TABLE transcripts DROP COLUMN speaker_map")
    cols = {r[1] for r in conn.execute("PRAGMA table_info(transcripts)")}
    assert "speaker_map" not in cols
    conn.close()

    db.init()  # phải thêm lại cột, không lỗi
    with sqlite3.connect(db.DB_PATH) as c2:
        cols2 = {r[1] for r in c2.execute("PRAGMA table_info(transcripts)")}
    assert "speaker_map" in cols2


def test_init_idempotent(tmp_db):
    db.init()
    db.init()  # gọi nhiều lần không lỗi
    assert db.speaker_names() == {}


def test_voiceprint_save_get_load_roundtrip(tmp_db):
    sid = db.find_or_create_speaker("An")
    emb = b"\x00\x00\x80?" * 4  # 4 float32 = 1.0
    db.save_voiceprint(sid, emb, 4, 2, "tid-1")
    assert db.get_voiceprint(sid) == (emb, 2)
    assert (sid, emb) in db.load_voiceprints()
    # ghi đè (thêm mẫu) → 1 hàng/người, sample_count cập nhật
    db.save_voiceprint(sid, emb, 4, 3, "tid-2")
    assert db.get_voiceprint(sid) == (emb, 3)
    assert len(db.load_voiceprints()) == 1
    # list_speakers báo số voiceprint
    assert db.list_speakers()[0]["voiceprints"] == 1


def test_delete_speaker_removes_voiceprint(tmp_db):
    sid = db.find_or_create_speaker("An")
    db.save_voiceprint(sid, b"\x00\x00\x80?" * 4, 4, 1, None)
    db.delete_speaker(sid)
    assert db.get_voiceprint(sid) is None
    assert db.load_voiceprints() == []


def test_list_orders_newest_first(tmp_db):
    _insert(tid="20260101-000000-a")
    db.insert_transcript(db.TranscriptRecord(
        transcript_id="20260102-000000-b", title="B", language="vi", model="m",
        duration=1.0, created_at="2026-01-02T00:00:00+07:00", text="xin chào",
        raw_text=None, segments=None, llm_model=None, audio_file=None, audio_dir=None,
    ))
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
    db.set_sync_state(tid, db.SyncState(org_id="org-1", remote_id="r-1", pushed_at="2026-01-01T00:00:00+07:00"))
    st = db.get_sync_state(tid)
    assert st["status"] == "pushed"
    assert st["remote_id"] == "r-1"
    # Push lại = cập nhật (upsert), không tạo dòng thứ hai.
    db.set_sync_state(tid, db.SyncState(org_id="org-1", remote_id="r-2", pushed_at="2026-01-02T00:00:00+07:00"))
    assert db.get_sync_state(tid)["remote_id"] == "r-2"


def test_list_includes_sync_status(tmp_db):
    _insert()
    assert db.list_transcripts()[0]["sync"] is None
    db.set_sync_state("20260101-000000-hop", db.SyncState(org_id="o", remote_id="r", pushed_at="2026-01-01T00:00:00+07:00"))
    assert db.list_transcripts()[0]["sync"] == "pushed"
