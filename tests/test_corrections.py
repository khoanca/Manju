"""API sửa transcript tay (US-801) + CRUD thư viện sửa lỗi (US-802) — DB tạm."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import db, transcribe
from app.corrections import extract_pairs


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


def _seed_transcript(tid="20260101-000000-hop", text="triển khai Kubernetes"):
    db.insert_transcript(db.TranscriptRecord(
        transcript_id=tid, title="Họp", language="vi", model="m", duration=6.0,
        created_at="2026-01-01T00:00:00+07:00", text=text,
        raw_text="triển khai cu bơ nét", segments=None,
        llm_model="gemma4:e4b", audio_file=None, audio_dir=None,
    ))


# ── PATCH /api/transcripts/{id}/text ───────────────────────────────────────
def test_patch_text_saves_and_detail_returns_edited(client):
    _seed_transcript()
    r = client.patch("/api/transcripts/20260101-000000-hop/text",
                     json={"edited_text": "triển khai K8s"})
    assert r.status_code == 200
    # Không gửi base_text → hook trích cặp không chạy, pairs_extracted = 0
    assert r.json() == {"ok": True, "edited_text": "triển khai K8s", "pairs_extracted": 0}
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


# ── extract_pairs (T-005) ──────────────────────────────────────────────────
def test_extract_pairs_transliteration():
    """Cặp phiên âm lệch số từ ("cu bơ nét" 3 từ → "Kubernetes" 1 từ) phải qua
    ngưỡng ratio 0.3 (thực đo 0.42) — đây là ca chốt ngưỡng."""
    pairs = extract_pairs(
        "triển khai cu bơ nét cho dự án", "triển khai Kubernetes cho dự án"
    )
    assert pairs == [("cu bơ nét", "Kubernetes")]


def test_extract_pairs_full_rewrite_returns_nothing():
    # Viết lại toàn bộ câu (văn phong) → span dài/khác hẳn → 0 cặp (AC3)
    pairs = extract_pairs(
        "hôm nay chúng ta bàn về vấn đề ngân sách quý tới",
        "cuộc họp tập trung thảo luận kế hoạch chi tiêu sắp tới",
    )
    assert pairs == []


def test_extract_pairs_reorder_long_phrase_returns_nothing():
    # Đảo cụm dài đầu-cuối câu → chỉ insert/delete, không phải cặp sửa lỗi
    pairs = extract_pairs(
        "chúng ta sẽ triển khai hệ thống mới vào tuần sau",
        "vào tuần sau chúng ta sẽ triển khai hệ thống mới",
    )
    assert pairs == []


def test_extract_pairs_case_and_punctuation_only():
    pairs = extract_pairs(
        "triển khai kubernetes ngay.", "Triển khai Kubernetes ngay!"
    )
    assert pairs == []


def test_extract_pairs_multiple_pairs_one_text():
    pairs = extract_pairs(
        "dùng cu bơ nét và gít háp để deploy",
        "dùng Kubernetes và GitHub để deploy",
    )
    assert pairs == [("cu bơ nét", "Kubernetes"), ("gít háp", "GitHub")]


def test_extract_pairs_equal_text():
    assert extract_pairs("không đổi gì cả", "không đổi gì cả") == []


def test_extract_pairs_keeps_vietnamese_diacritics():
    # Sửa sai dấu 1-1 theo từ: giữ nguyên dấu tiếng Việt hai phía
    pairs = extract_pairs(
        "anh Hùng phụ trách phần triễn khai", "anh Hùng phụ trách phần triển khai"
    )
    assert pairs == [("triễn", "triển")]


# ── Hook PATCH text → thư viện (T-006) ─────────────────────────────────────
def test_patch_text_hook_extracts_pending_pair(client):
    _seed_transcript(text="triển khai cu bơ nét trên server")
    r = client.patch(
        "/api/transcripts/20260101-000000-hop/text",
        json={
            "edited_text": "triển khai Kubernetes trên server",
            "base_text": "triển khai cu bơ nét trên server",
        },
    )
    assert r.status_code == 200
    assert r.json()["pairs_extracted"] == 1
    rows = client.get("/api/corrections").json()
    assert len(rows) == 1
    row = rows[0]
    assert (row["wrong"], row["right"]) == ("cu bơ nét", "Kubernetes")
    assert (row["status"], row["source"], row["count"]) == ("pending", "user", 1)


def test_patch_text_hook_repeat_pair_auto_approves(client):
    # Cùng cặp lặp lại ở transcript thứ 2 → count=2, tự approved (US-802 AC2)
    _seed_transcript(tid="20260101-000000-hop", text="triển khai cu bơ nét ngay")
    _seed_transcript(tid="20260102-000000-hop", text="học lại cu bơ nét từ đầu")
    client.patch(
        "/api/transcripts/20260101-000000-hop/text",
        json={"edited_text": "triển khai Kubernetes ngay",
              "base_text": "triển khai cu bơ nét ngay"},
    )
    r = client.patch(
        "/api/transcripts/20260102-000000-hop/text",
        json={"edited_text": "học lại Kubernetes từ đầu",
              "base_text": "học lại cu bơ nét từ đầu"},
    )
    assert r.status_code == 200
    rows = client.get("/api/corrections").json()
    assert len(rows) == 1
    assert (rows[0]["count"], rows[0]["status"]) == (2, "approved")


def test_patch_text_hook_never_fails_patch(client, monkeypatch):
    # extract_pairs nổ → PATCH vẫn 200, bản sửa vẫn lưu (never-fail)
    from app import corrections

    def boom(machine, edited):
        raise RuntimeError("nổ giả lập")

    monkeypatch.setattr(corrections, "extract_pairs", boom)
    _seed_transcript(text="triển khai cu bơ nét")
    r = client.patch(
        "/api/transcripts/20260101-000000-hop/text",
        json={"edited_text": "triển khai Kubernetes",
              "base_text": "triển khai cu bơ nét"},
    )
    assert r.status_code == 200
    assert r.json()["pairs_extracted"] == 0
    detail = client.get("/api/transcripts/20260101-000000-hop").json()
    assert detail["edited_text"] == "triển khai Kubernetes"
    assert client.get("/api/corrections").json() == []


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
