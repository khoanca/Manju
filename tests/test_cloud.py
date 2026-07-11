"""FR-6 — CloudSession, backend cloud pass 2, endpoints ví (mock httpx, không mạng).

Wire schema 200/402 của llm-correct lấy từ fixtures DÙNG CHUNG với deno test
(supabase/functions/llm-correct/fixtures/) — 2 phía luôn test cùng contract.
"""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app import cloud, correct, live
from app.correct import CorrectionResult, LlmOpts
from app.main import app

FIXTURES = Path(__file__).resolve().parent.parent / "supabase/functions/llm-correct/fixtures"
OK_200 = json.loads((FIXTURES / "llm_correct_200.json").read_text())
ERR_402 = json.loads((FIXTURES / "llm_correct_402.json").read_text())


@pytest.fixture
def cloud_on(monkeypatch, tmp_path):
    """Bật CLOUD_BILLING + trỏ settings vào DB tạm, giả lập đã đăng nhập."""
    from app import db

    monkeypatch.setattr(db, "DATA", tmp_path)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    db.init()
    monkeypatch.setattr(cloud, "CLOUD_BILLING", True)
    monkeypatch.setattr(cloud, "SUPABASE_URL", "http://supabase.test")
    monkeypatch.setattr(cloud, "SUPABASE_ANON_KEY", "anon")
    monkeypatch.setattr(cloud, "access_token", lambda: "jwt-test")
    return db


def _resp(status: int, body: dict) -> httpx.Response:
    return httpx.Response(status, json=body, request=httpx.Request("POST", "http://x"))


# ── cloud.py ────────────────────────────────────────────────────────────────
def test_cloud_billing_enabled_gating(monkeypatch):
    monkeypatch.setattr(cloud, "CLOUD_BILLING", False)
    assert cloud.cloud_billing_enabled() is False
    monkeypatch.setattr(cloud, "CLOUD_BILLING", True)
    monkeypatch.setattr(cloud, "SUPABASE_URL", "")
    assert cloud.cloud_billing_enabled() is False
    monkeypatch.setattr(cloud, "SUPABASE_URL", "http://supabase.test")
    monkeypatch.setattr(cloud, "SUPABASE_ANON_KEY", "anon")
    assert cloud.cloud_billing_enabled() is True


def test_llm_correct_200_passthrough(cloud_on, monkeypatch):
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _resp(200, OK_200))
    data = cloud.llm_correct({"text": "x", "requestId": "r"}, timeout=5)
    assert data == OK_200


def test_llm_correct_402_raises_insufficient(cloud_on, monkeypatch):
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _resp(402, ERR_402))
    with pytest.raises(cloud.InsufficientCredits) as exc:
        cloud.llm_correct({"text": "x", "requestId": "r"}, timeout=5)
    # Wire là milli-credit → thuộc tính balance đã chia 1000.
    assert exc.value.balance == ERR_402["balanceCredits"] / 1000


def test_llm_correct_5xx_raises_cloud_error(cloud_on, monkeypatch):
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _resp(502, {}))
    with pytest.raises(cloud.CloudError):
        cloud.llm_correct({"text": "x", "requestId": "r"}, timeout=5)


# ── correct.py backend cloud ────────────────────────────────────────────────
def test_llm_backend_needs_flag_setting_and_session(cloud_on, monkeypatch):
    db = cloud_on
    monkeypatch.setattr(cloud, "session_info", lambda: {"email": "a@x.vn"})
    assert correct.llm_backend() == "ollama"  # chưa chọn trong Settings
    db.set_setting("llm_backend", "cloud")
    assert correct.llm_backend() == "cloud"
    monkeypatch.setattr(cloud, "session_info", lambda: None)  # logout
    assert correct.llm_backend() == "ollama"


def test_correct_sentence_cloud_200(cloud_on, monkeypatch):
    monkeypatch.setattr(correct, "llm_backend", lambda: "cloud")
    monkeypatch.setattr(cloud, "llm_correct", lambda payload, timeout: OK_200)
    # Bản raw phải đủ giống fixture (bản sửa) để qua _guard MIN_SIMILARITY.
    res = correct.correct_sentence(
        "Anh em đíp lôi service lên cu bơ nét ét trước buổi demo nhé.", LlmOpts()
    )
    assert res.ok and not res.blocked
    assert res.text == OK_200["text"]
    assert res.credits_spent == OK_200["credits"]["spentCredits"] / 1000
    assert res.balance == OK_200["credits"]["balanceCredits"] / 1000


def test_correct_sentence_cloud_402_blocked_no_ollama_fallback(cloud_on, monkeypatch):
    monkeypatch.setattr(correct, "llm_backend", lambda: "cloud")

    def raise_402(payload, timeout):
        raise cloud.InsufficientCredits(0.01)

    monkeypatch.setattr(cloud, "llm_correct", raise_402)
    ollama_called = []
    monkeypatch.setattr(correct, "_correct_chunk", lambda *a: ollama_called.append(1))
    res = correct.correct_sentence("một câu cần sửa", LlmOpts())
    assert res.blocked is True and res.ok is False
    assert res.text == "một câu cần sửa"  # bản gốc
    assert res.balance == 0.01
    assert not ollama_called  # US-606: không âm thầm fallback


def test_correct_sentence_cloud_transient_not_blocked(cloud_on, monkeypatch):
    monkeypatch.setattr(correct, "llm_backend", lambda: "cloud")

    def raise_err(payload, timeout):
        raise cloud.CloudError("mạng rớt")

    monkeypatch.setattr(cloud, "llm_correct", raise_err)
    res = correct.correct_sentence("một câu cần sửa", LlmOpts())
    assert res.ok is False and res.blocked is False
    assert res.text == "một câu cần sửa"


def test_correct_text_cloud_stops_chunks_on_402(cloud_on, monkeypatch):
    monkeypatch.setattr(correct, "llm_backend", lambda: "cloud")
    calls = []

    def fake_llm(payload, timeout):
        calls.append(payload["text"])
        if len(calls) >= 2:
            raise cloud.InsufficientCredits(0.0)
        # Thêm đuôi ngắn: đủ giống bản gốc để qua _guard MIN_SIMILARITY.
        return {"text": payload["text"] + " ok", "credits": {"spentCredits": 500, "balanceCredits": 100}}

    monkeypatch.setattr(cloud, "llm_correct", fake_llm)
    monkeypatch.setattr(correct, "_split_chunks", lambda text, size=0: ["câu một.", "câu hai.", "câu ba."])
    res = correct.correct_text("câu một. câu hai. câu ba.")
    assert res.blocked is True
    assert len(calls) == 2  # chunk 3 KHÔNG được gọi — dừng ngay khi 402
    assert res.credits_spent == 0.5  # chỉ chunk 1 trừ tiền
    assert res.text == "câu một. ok câu hai. câu ba."  # phần sửa dở + phần nguyên bản


# ── live.py — 402 giữa phiên ────────────────────────────────────────────────
def _bare_session() -> live.LiveSession:
    s = object.__new__(live.LiveSession)
    s.sentences = {}
    s.glossary = ""
    s.credits_spent = 0.0
    s.credit_blocked = False
    s.sent_msgs = []
    s._send = s.sent_msgs.append  # type: ignore[method-assign]
    return s


def test_live_correct_one_blocked_sends_once_and_flags(monkeypatch):
    s = _bare_session()
    monkeypatch.setattr(
        live, "correct_sentence",
        lambda text, opts: CorrectionResult(text=text, ok=False, blocked=True, balance=0.0),
    )
    s._correct_one(1, "câu bị chặn")
    assert s.credit_blocked is True
    assert s.sent_msgs == [{"type": "credit_blocked", "balance": 0.0}]


def test_live_correct_one_accumulates_credits(monkeypatch):
    s = _bare_session()
    monkeypatch.setattr(
        live, "correct_sentence",
        lambda text, opts: CorrectionResult(text="đã sửa " + text, ok=True, credits_spent=0.2),
    )
    s._correct_one(1, "câu một")
    s._correct_one(2, "câu hai")
    assert s.credits_spent == pytest.approx(0.4)
    assert [m["type"] for m in s.sent_msgs] == ["corrected", "corrected"]


# ── main.py — endpoints gate 503 khi CLOUD_BILLING off ──────────────────────
def test_endpoints_503_when_cloud_off(monkeypatch):
    monkeypatch.setattr(cloud, "CLOUD_BILLING", False)
    client = TestClient(app)  # không dùng `with` → không chạy lifespan/DB thật
    assert client.get("/api/wallet").status_code == 503
    assert client.get("/api/wallet/packages").status_code == 503
    assert client.post("/api/auth/login", json={"email": "a@x.vn", "password": "p"}).status_code == 503
    assert client.get("/api/auth/session").status_code == 503


def test_settings_rejects_bad_backend(monkeypatch, tmp_path):
    from app import db

    monkeypatch.setattr(db, "DATA", tmp_path)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    db.init()
    client = TestClient(app)
    assert client.put("/api/settings", json={"llm_backend": "xyz"}).status_code == 400
    r = client.put("/api/settings", json={"llm_backend": "cloud"})
    assert r.status_code == 200 and r.json()["llm_backend"] == "cloud"
