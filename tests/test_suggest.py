"""Gợi ý phương án cho từ khả nghi (US suspect-words):
suggest_from_library (thư viện) + suggest_alternatives (LLM mock)
+ endpoint /api/suggest hybrid — DB tạm, KHÔNG gọi LLM thật."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import correct, corrections, db, transcribe


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """DB tạm, không cần app — cho test thuần suggest_from_library."""
    monkeypatch.setattr(db, "DATA", tmp_path)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(db, "DEFAULT_RECORDINGS", tmp_path / "rec")
    monkeypatch.setattr(transcribe, "TRANSCRIPTS", tmp_path / "tx")
    (tmp_path / "tx").mkdir()
    (tmp_path / "rec").mkdir()
    db.init()
    return tmp_path


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


def _approve(wrong: str, right: str, times: int = 2) -> None:
    """Đưa cặp lên approved (cần ≥2 lần theo upsert_correction)."""
    for _ in range(times):
        db.upsert_correction(wrong, right)


# ── suggest_from_library ────────────────────────────────────────────────────
def test_library_exact_wrong_match(tmp_db):
    _approve("cu bơ nết", "kubernetes")
    assert corrections.suggest_from_library("cu bơ nết") == ["kubernetes"]


def test_library_case_insensitive(tmp_db):
    _approve("Cu Bơ Nết", "Kubernetes")
    assert corrections.suggest_from_library("cu bơ nết") == ["Kubernetes"]


def test_library_exact_before_partial(tmp_db):
    # exact wrong==word ưu tiên trên chứa/được-chứa dù count thấp hơn.
    _approve("đíp lôi cu bơ nết", "deploy kubernetes", times=5)  # partial (chứa key)
    _approve("cu bơ nết", "kubernetes", times=2)  # exact
    got = corrections.suggest_from_library("cu bơ nết")
    assert got[0] == "kubernetes"
    assert "deploy kubernetes" in got


def test_library_unique_and_pending_excluded(tmp_db):
    _approve("cu bơ nết", "kubernetes")
    db.upsert_correction("cu bơ nết", "k8s")  # count 1 → vẫn pending, bị loại
    assert corrections.suggest_from_library("cu bơ nết") == ["kubernetes"]


def test_library_no_match_returns_empty(tmp_db):
    _approve("cu bơ nết", "kubernetes")
    assert corrections.suggest_from_library("redis") == []


def test_library_empty_word(tmp_db):
    assert corrections.suggest_from_library("   ") == []


# ── suggest_alternatives (LLM mock) ─────────────────────────────────────────
def test_alternatives_parses_json_array(monkeypatch):
    monkeypatch.setattr(
        correct, "chat_once", lambda system, user: '["kubernetes", "cu bơ nết"]'
    )
    assert correct.suggest_alternatives("cu bơ nết", "triển khai trên cloud") == [
        "kubernetes",
        "cu bơ nết",
    ]


def test_alternatives_caps_at_n(monkeypatch):
    monkeypatch.setattr(correct, "chat_once", lambda s, u: '["a", "b", "c", "d"]')
    assert correct.suggest_alternatives("x", "ngữ cảnh", n=2) == ["a", "b"]


def test_alternatives_extracts_array_from_noise(monkeypatch):
    # LLM lỡ kèm lời dẫn → regex trích mảng đầu tiên.
    monkeypatch.setattr(
        correct, "chat_once", lambda s, u: 'Đây là gợi ý: ["redis", "rét đít"] nhé'
    )
    assert correct.suggest_alternatives("rét đít", "cache trong") == ["redis", "rét đít"]


def test_alternatives_bad_json_returns_empty(monkeypatch):
    monkeypatch.setattr(correct, "chat_once", lambda s, u: "không phải json gì cả")
    assert correct.suggest_alternatives("x", "ngữ cảnh") == []


def test_alternatives_llm_error_returns_empty(monkeypatch):
    def boom(system, user):
        raise RuntimeError("LLM tắt")

    monkeypatch.setattr(correct, "chat_once", boom)
    assert correct.suggest_alternatives("x", "ngữ cảnh") == []


def test_alternatives_non_list_json_returns_empty(monkeypatch):
    monkeypatch.setattr(correct, "chat_once", lambda s, u: '{"word": "kubernetes"}')
    assert correct.suggest_alternatives("x", "ngữ cảnh") == []


def test_alternatives_empty_word(monkeypatch):
    called = False

    def track(system, user):
        nonlocal called
        called = True
        return "[]"

    monkeypatch.setattr(correct, "chat_once", track)
    assert correct.suggest_alternatives("  ", "ngữ cảnh") == []
    assert called is False  # không gọi LLM khi từ rỗng


# ── Endpoint /api/suggest (hybrid) ──────────────────────────────────────────
def test_suggest_library_only_when_enough(client, monkeypatch):
    _approve("cu bơ nết", "kubernetes")
    _approve("cu bơ nết", "k8s")
    _approve("cu bơ nết", "container orchestration")
    # Đủ 3 từ thư viện → KHÔNG gọi LLM.
    def boom(*a, **k):
        raise AssertionError("không được gọi LLM khi thư viện đủ")

    monkeypatch.setattr(correct, "suggest_alternatives", boom)
    r = client.post("/api/suggest", json={"word": "cu bơ nết", "context": "c"})
    assert r.status_code == 200
    assert set(r.json()["alternatives"]) == {"kubernetes", "k8s", "container orchestration"}


def test_suggest_hybrid_merges_llm(client, monkeypatch):
    _approve("cu bơ nết", "kubernetes")  # 1 từ thư viện < 3 → bổ sung LLM
    monkeypatch.setattr(
        correct, "suggest_alternatives", lambda w, c, lang: ["k8s", "cu bơ nết"]
    )
    r = client.post("/api/suggest", json={"word": "cu bơ nết", "context": "trên cloud"})
    alts = r.json()["alternatives"]
    # dedupe casefold + loại phần tử == từ gốc ("cu bơ nết").
    assert alts == ["kubernetes", "k8s"]


def test_suggest_dedupe_and_cap_5(client, monkeypatch):
    _approve("từ", "một")
    monkeypatch.setattr(
        correct,
        "suggest_alternatives",
        lambda w, c, lang: ["Một", "hai", "ba", "bốn", "năm", "sáu"],
    )
    r = client.post("/api/suggest", json={"word": "từ", "context": ""})
    alts = r.json()["alternatives"]
    assert alts == ["một", "hai", "ba", "bốn", "năm"]  # "Một" trùng "một" (casefold), cap 5


def test_suggest_never_fail_empty(client, monkeypatch):
    monkeypatch.setattr(correct, "suggest_alternatives", lambda w, c, lang: [])
    r = client.post("/api/suggest", json={"word": "không có gì", "context": ""})
    assert r.status_code == 200
    assert r.json() == {"alternatives": []}
