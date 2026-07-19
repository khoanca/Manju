"""Đợt 2 — push text lên org: logic thuần + gating (không gọi mạng thật)."""
from __future__ import annotations

import pytest

from app import org


def test_org_configured_reflects_env(monkeypatch):
    monkeypatch.setattr(org, "SUPABASE_URL", "")
    monkeypatch.setattr(org, "SUPABASE_ANON_KEY", "")
    assert org.org_configured() is False
    monkeypatch.setattr(org, "SUPABASE_URL", "https://x.supabase.co")
    monkeypatch.setattr(org, "SUPABASE_ANON_KEY", "anon-key")
    assert org.org_configured() is True


def test_build_payload_maps_columns_and_omits_audio():
    transcript = {
        "id": "20260101-000000-hop",
        "title": "Họp sprint",
        "language": "vi",
        "model": "large-v3-turbo",
        "duration": 42.4,
        "text": "triển khai Kubernetes",
        "raw_text": "triển khai cu bơ nét",
        "corrected": True,
        "llm_model": "gemma4:e4b",
        "audio": "20260101-000000-hop.wav",
    }
    payload = org.build_payload("org-123", transcript)
    assert payload["org_id"] == "org-123"
    assert payload["local_id"] == "20260101-000000-hop"
    assert payload["text"] == "triển khai Kubernetes"
    assert payload["corrected"] is True
    # Không đẩy audio, không tự gán owner_id (DB gán = auth.uid()).
    assert "audio" not in payload
    assert "owner_id" not in payload


def test_build_payload_prefers_edited_text():
    """Bản user sửa tay là bản chính khi push (US-801 AC2)."""
    transcript = {
        "id": "t1", "title": "Họp", "language": "vi",
        "text": "bản máy", "edited_text": "bản user sửa",
    }
    assert org.build_payload("org-1", transcript)["text"] == "bản user sửa"


def test_push_raises_when_not_configured(monkeypatch):
    monkeypatch.setattr(org, "SUPABASE_URL", "")
    monkeypatch.setattr(org, "SUPABASE_ANON_KEY", "")
    with pytest.raises(org.OrgNotConfigured):
        org.push_transcript("org-1", "token", {"id": "x", "title": "t", "language": "vi", "text": "hi"})


def test_push_requires_access_token(monkeypatch):
    monkeypatch.setattr(org, "SUPABASE_URL", "https://x.supabase.co")
    monkeypatch.setattr(org, "SUPABASE_ANON_KEY", "anon-key")
    with pytest.raises(org.OrgError):
        org.push_transcript("org-1", "", {"id": "x", "title": "t", "language": "vi", "text": "hi"})
