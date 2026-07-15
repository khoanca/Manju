"""API sửa transcript tay (US-801) + CRUD thư viện sửa lỗi (US-802) — DB tạm."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import db, transcribe


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
        created_at="2026-01-01T00:00:00+07:00", text="triển khai Kubernetes",
        raw_text="triển khai cu bơ nét", segments=None,
        llm_model="gemma4:e4b", audio_file=None, audio_dir=None,
    ))


# ── PATCH /api/transcripts/{id}/text ───────────────────────────────────────
def test_patch_text_saves_and_detail_returns_edited(client):
    _seed_transcript()
    r = client.patch("/api/transcripts/20260101-000000-hop/text",
                     json={"edited_text": "triển khai K8s"})
    assert r.status_code == 200
    assert r.json() == {"ok": True, "edited_text": "triển khai K8s"}
    detail = client.get("/api/transcripts/20260101-000000-hop").json()
    # Đủ 3 bản để UI toggle: raw / pass-2 / user sửa (US-801 AC2)
    assert detail["edited_text"] == "triển khai K8s"
    assert detail["text"] == "triển khai Kubernetes"
    assert detail["raw_text"] == "triển khai cu bơ nét"


def test_patch_text_none_clears_edit(client):
    _seed_transcript()
    client.patch("/api/transcripts/20260101-000000-hop/text",
                 json={"edited_text": "bản sửa"})
    r = client.patch("/api/transcripts/20260101-000000-hop/text",
                     json={"edited_text": None})
    assert r.status_code == 200
    assert "edited_text" not in client.get("/api/transcripts/20260101-000000-hop").json()


def test_patch_text_missing_404(client):
    r = client.patch("/api/transcripts/khong-co/text", json={"edited_text": "x"})
    assert r.status_code == 404


def test_patch_text_stale_base_409(client):
    _seed_transcript()
    r = client.patch(
        "/api/transcripts/20260101-000000-hop/text",
        json={"edited_text": "bản mới", "base_text": "bản đã cũ (lệch)"},
    )
    assert r.status_code == 409
    # Không ghi đè khi lệch
    assert "edited_text" not in client.get("/api/transcripts/20260101-000000-hop").json()


def test_patch_text_base_matches_effective_text(client):
    """base_text so với bản hiệu lực: edited_text nếu có, không thì text."""
    _seed_transcript()
    r1 = client.patch(
        "/api/transcripts/20260101-000000-hop/text",
        json={"edited_text": "sửa lần 1", "base_text": "triển khai Kubernetes"},
    )
    assert r1.status_code == 200
    # Lần 2 base_text phải là bản sửa hiện tại, không phải bản máy nữa
    r2 = client.patch(
        "/api/transcripts/20260101-000000-hop/text",
        json={"edited_text": "sửa lần 2", "base_text": "sửa lần 1"},
    )
    assert r2.status_code == 200
    r3 = client.patch(
        "/api/transcripts/20260101-000000-hop/text",
        json={"edited_text": "x", "base_text": "triển khai Kubernetes"},
    )
    assert r3.status_code == 409


# ── CRUD /api/corrections ──────────────────────────────────────────────────
def test_corrections_list_filter_patch_delete(client):
    cid = db.upsert_correction("cu bơ nét", "Kubernetes")["id"]
    db.upsert_correction("mô có", "mô khó", tag="trung", source="seed")

    rows = client.get("/api/corrections").json()
    assert len(rows) == 2
    assert client.get("/api/corrections", params={"source": "seed"}).json()[0]["tag"] == "trung"
    assert client.get("/api/corrections", params={"status": "rejected"}).json() == []

    r = client.patch(f"/api/corrections/{cid}", json={"status": "approved", "tag": "bac"})
    assert r.status_code == 200
    row = client.get("/api/corrections", params={"status": "approved"}).json()[0]
    assert (row["id"], row["tag"]) == (cid, "bac")

    assert client.delete(f"/api/corrections/{cid}").status_code == 200
    assert len(client.get("/api/corrections").json()) == 1


def test_corrections_patch_validates(client):
    cid = db.upsert_correction("gít", "git")["id"]
    assert client.patch(f"/api/corrections/{cid}", json={}).status_code == 400
    assert client.patch(f"/api/corrections/{cid}", json={"status": "sai"}).status_code == 400


def test_corrections_missing_404(client):
    assert client.patch("/api/corrections/cor-khongco",
                        json={"status": "approved"}).status_code == 404
    assert client.patch("/api/corrections/cor-khongco",
                        json={"tag": "bac"}).status_code == 404
    assert client.delete("/api/corrections/cor-khongco").status_code == 404
