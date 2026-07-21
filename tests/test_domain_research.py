"""Lớp C giai đoạn 2 — research thuật ngữ ngành bằng LLM → nhập pending, và
endpoint /api/lexicon/domain-research + /api/domains."""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app import correct, corrections, db, transcribe
from app.main import app
from app.slang_trend import run_domain_research

_GOOD = [
    {"wrong": "com plai ần", "right": "compliance"},
    {"wrong": "li ti ghê sần", "right": "litigation"},
]


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DATA", tmp_path)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(db, "DEFAULT_RECORDINGS", tmp_path / "rec")
    monkeypatch.setattr(transcribe, "TRANSCRIPTS", tmp_path / "tx")
    (tmp_path / "tx").mkdir()
    (tmp_path / "rec").mkdir()
    db.init()
    return tmp_path


@pytest.fixture
def client(tmp_db, monkeypatch):
    with TestClient(app) as c:
        yield c


def test_run_domain_research_imports_pending(tmp_db, monkeypatch):
    monkeypatch.setattr(correct, "chat_once", lambda s, u, timeout=0: json.dumps(_GOOD))
    res = run_domain_research("legal")
    assert res.new_pending == 2
    rows = db.list_corrections(tag=corrections.domain_tag("legal"))
    assert {r["status"] for r in rows} == {"pending"}
    assert {r["source"] for r in rows} == {"trend"}
    # Bấm lại: đã có → không nhập mới.
    assert run_domain_research("legal").new_pending == 0


def test_domain_research_pending_excluded_from_bias(tmp_db, monkeypatch):
    monkeypatch.setattr(correct, "chat_once", lambda s, u, timeout=0: json.dumps(_GOOD))
    run_domain_research("legal")
    # Chưa duyệt → không lọt vào bias dù truyền domains=legal.
    assert "compliance" not in corrections.build_bias("", domains=["legal"])


def test_api_domains_lists_known(client):
    r = client.get("/api/domains")
    assert r.status_code == 200
    assert r.json()["domains"] == list(corrections.DOMAINS)


def test_api_domain_research_rejects_unknown(client):
    r = client.post("/api/lexicon/domain-research", json={"domain": "khong-co"})
    assert r.status_code == 400


def test_api_domain_research_needs_openrouter(client, monkeypatch):
    monkeypatch.setattr(correct, "openrouter_enabled", lambda: False)
    r = client.post("/api/lexicon/domain-research", json={"domain": "devops"})
    assert r.status_code == 503


def test_api_domain_research_ok(client, monkeypatch):
    monkeypatch.setattr(correct, "openrouter_enabled", lambda: True)
    monkeypatch.setattr(correct, "chat_once", lambda s, u, timeout=0: json.dumps(_GOOD))
    r = client.post("/api/lexicon/domain-research", json={"domain": "legal"})
    assert r.status_code == 200
    assert r.json()["new_pending"] == 2


def test_api_settings_domain_auto_toggle(client):
    assert client.get("/api/settings").json()["domain_auto"] is True
    client.put("/api/settings", json={"domain_auto": False})
    assert client.get("/api/settings").json()["domain_auto"] is False
