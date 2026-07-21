"""Roundtrip + validation cho settings khử ồn qua /api/settings (DB tạm)."""
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


def _denoise(client) -> dict:
    return client.get("/api/settings").json()["denoise"]


def test_denoise_settings_roundtrip(client):
    r = client.put("/api/settings", json={
        "denoise_strength": 60,
        "denoise_mode": "nonstationary",
        "denoise_highpass": 80,
        "denoise_hum": "50",
        "denoise_upload_enabled": True,
    })
    assert r.status_code == 200

    d = _denoise(client)
    assert d["strength"] == 60
    assert d["mode"] == "nonstationary"
    assert d["highpass"] == 80
    assert d["hum"] == "50"
    assert d["upload_enabled"] is True


def test_denoise_mode_lung_tung_ve_stationary(client):
    client.put("/api/settings", json={"denoise_mode": "banana"})
    assert _denoise(client)["mode"] == "stationary"


def test_denoise_hum_lung_tung_ve_off(client):
    client.put("/api/settings", json={"denoise_hum": "42"})
    assert _denoise(client)["hum"] == "off"


def test_denoise_strength_bi_kep_trong_0_100(client):
    client.put("/api/settings", json={"denoise_strength": 500})
    assert _denoise(client)["strength"] == 100

    client.put("/api/settings", json={"denoise_strength": -20})
    assert _denoise(client)["strength"] == 0
