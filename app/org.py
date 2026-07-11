"""Đợt 2 — đẩy TEXT bản ghi lên org cloud (Supabase Postgres qua PostgREST).

Chỉ đẩy text + metadata, KHÔNG audio (PRD FR-5, mục Ngoài phạm vi). Request đi
kèm access_token của user (Supabase Auth) → PostgREST áp RLS trên Postgres;
server local KHÔNG dùng service_role, không tự quyết quyền. Push lại = upsert
theo (org_id, owner_id, local_id); owner_id do DB gán = auth.uid().

Chưa cấu hình Supabase (thiếu env) → OrgNotConfigured; thiếu token → OrgError.
"""
from __future__ import annotations

import os

import httpx

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_ANON_KEY = os.environ.get("SUPABASE_ANON_KEY", "")

PUSH_TIMEOUT_S = 30.0


class OrgError(Exception):
    """Push thất bại (thiếu token, lỗi mạng, RLS từ chối...)."""


class OrgNotConfigured(OrgError):
    """Chưa set SUPABASE_URL / SUPABASE_ANON_KEY."""


def org_configured() -> bool:
    return bool(SUPABASE_URL and SUPABASE_ANON_KEY)


def build_payload(org_id: str, transcript: dict) -> dict:
    """Map dict từ db.read_transcript → cột transcripts_text.

    Không gửi owner_id: DB mặc định auth.uid() (người đăng nhập) và RLS chặn
    ghi thay người khác. Không gửi audio.
    """
    return {
        "org_id": org_id,
        "local_id": transcript["id"],
        "title": transcript["title"],
        "language": transcript["language"],
        "model": transcript.get("model"),
        "duration": transcript.get("duration"),
        "text": transcript["text"],
        "raw_text": transcript.get("raw_text"),
        "corrected": bool(transcript.get("corrected")),
        "llm_model": transcript.get("llm_model"),
    }


def push_transcript(
    org_id: str,
    access_token: str,
    transcript: dict,
    *,
    timeout: float = PUSH_TIMEOUT_S,
) -> dict:
    """Upsert 1 bản text lên org. Trả row remote (có id) khi thành công."""
    if not org_configured():
        raise OrgNotConfigured("Chưa cấu hình SUPABASE_URL / SUPABASE_ANON_KEY")
    if not access_token:
        raise OrgError("Thiếu access_token — cần đăng nhập tổ chức trước khi đẩy")
    payload = build_payload(org_id, transcript)
    try:
        resp = httpx.post(
            f"{SUPABASE_URL}/rest/v1/transcripts_text",
            params={"on_conflict": "org_id,owner_id,local_id"},
            headers={
                "apikey": SUPABASE_ANON_KEY,
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
                # merge-duplicates = upsert; return=representation để lấy remote id.
                "Prefer": "resolution=merge-duplicates,return=representation",
            },
            json=payload,
            timeout=timeout,
        )
    except httpx.HTTPError as exc:
        raise OrgError(f"Không kết nối được org cloud: {exc}") from exc
    if resp.status_code >= 400:
        raise OrgError(f"Org từ chối (HTTP {resp.status_code}): {resp.text[:200]}")
    rows = resp.json()
    return rows[0] if isinstance(rows, list) and rows else {}
