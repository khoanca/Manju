"""FR-6 — CloudSession: đăng nhập Supabase Auth + client ví credit.

App local single-user → giữ MỘT session Supabase phía server Python (token cần
trong thread live/_correct_loop và transcribe, không dùng supabase-js ở PWA).
refresh_token persist trong SQLite settings (cùng mức tin cậy với `.env` hiện
nay — app local); access_token in-memory, tự refresh trước khi hết hạn.

Gate: env CLOUD_BILLING=on + SUPABASE_URL/ANON_KEY. Off → mọi hàm auth/ví
raise CloudNotConfigured; app chạy offline nguyên trạng với Ollama.
"""
from __future__ import annotations

import os
import threading
import time

import httpx

from . import db

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_ANON_KEY = os.environ.get("SUPABASE_ANON_KEY", "")
CLOUD_BILLING = os.environ.get("CLOUD_BILLING", "off").lower() in ("1", "on", "true")

AUTH_TIMEOUT_S = 30.0
_REFRESH_MARGIN_S = 60.0  # refresh sớm 1 phút trước khi access_token hết hạn

_SETTING_REFRESH = "supabase_refresh_token"
_SETTING_EMAIL = "supabase_email"


class CloudError(Exception):
    """Lỗi gọi cloud (mạng, HTTP...)."""


class CloudNotConfigured(CloudError):
    """CLOUD_BILLING off hoặc thiếu SUPABASE_URL / SUPABASE_ANON_KEY."""


class CloudAuthError(CloudError):
    """Chưa đăng nhập / refresh token hết hiệu lực."""


class InsufficientCredits(CloudError):
    """402 — ví hết credit; tính năng trả phí bị chặn (US-606)."""

    def __init__(self, balance: float):
        super().__init__("Ví hết credit")
        self.balance = balance  # đơn vị credit (đã chia 1000 từ wire)


_lock = threading.Lock()
_state: dict = {"access_token": "", "expires_at": 0.0, "user_id": ""}


def cloud_billing_enabled() -> bool:
    return CLOUD_BILLING and bool(SUPABASE_URL and SUPABASE_ANON_KEY)


def _require_enabled() -> None:
    if not cloud_billing_enabled():
        raise CloudNotConfigured("CLOUD_BILLING off hoặc thiếu cấu hình Supabase")


def _auth_request(grant: str, payload: dict) -> dict:
    try:
        resp = httpx.post(
            f"{SUPABASE_URL}/auth/v1/token",
            params={"grant_type": grant},
            headers={"apikey": SUPABASE_ANON_KEY},
            json=payload,
            timeout=AUTH_TIMEOUT_S,
        )
    except httpx.HTTPError as exc:
        raise CloudError(f"Không kết nối được Supabase Auth: {exc}") from exc
    if resp.status_code >= 400:
        raise CloudAuthError(f"Auth từ chối (HTTP {resp.status_code}): {resp.text[:200]}")
    return resp.json()


def _store_tokens(data: dict) -> None:
    """Cập nhật state in-memory + persist refresh_token (rotate mỗi lần refresh)."""
    _state["access_token"] = data["access_token"]
    _state["expires_at"] = time.time() + float(data.get("expires_in", 3600)) - _REFRESH_MARGIN_S
    _state["user_id"] = (data.get("user") or {}).get("id", _state["user_id"])
    db.set_setting(_SETTING_REFRESH, data.get("refresh_token", ""))


def login(email: str, password: str) -> dict:
    """Đăng nhập Supabase Auth; giữ session cho mọi call cloud sau đó."""
    _require_enabled()
    data = _auth_request("password", {"email": email, "password": password})
    with _lock:
        _store_tokens(data)
        db.set_setting(_SETTING_EMAIL, email)
    return {"email": email, "user_id": _state["user_id"]}


def logout() -> None:
    with _lock:
        _state.update(access_token="", expires_at=0.0, user_id="")
        db.set_setting(_SETTING_REFRESH, "")
        db.set_setting(_SETTING_EMAIL, "")


def session_info() -> dict | None:
    """{email} nếu có session lưu (chưa chắc còn hiệu lực — biết khi gọi thật)."""
    email = db.get_setting(_SETTING_EMAIL)
    return {"email": email} if email and db.get_setting(_SETTING_REFRESH) else None


def access_token() -> str:
    """Token hiện hành, tự refresh khi sắp hết hạn. Raise CloudAuthError nếu
    chưa đăng nhập hoặc refresh token bị thu hồi."""
    _require_enabled()
    with _lock:
        if _state["access_token"] and time.time() < _state["expires_at"]:
            return str(_state["access_token"])
        refresh = db.get_setting(_SETTING_REFRESH)
        if not refresh:
            raise CloudAuthError("Chưa đăng nhập")
        data = _auth_request("refresh_token", {"refresh_token": refresh})
        _store_tokens(data)
        return str(_state["access_token"])


def _rest_get(path: str) -> list[dict]:
    """PostgREST GET bằng token USER (RLS enforced — chỉ thấy dữ liệu của mình)."""
    try:
        resp = httpx.get(
            f"{SUPABASE_URL}/rest/v1/{path}",
            headers={
                "apikey": SUPABASE_ANON_KEY,
                "Authorization": f"Bearer {access_token()}",
            },
            timeout=AUTH_TIMEOUT_S,
        )
    except httpx.HTTPError as exc:
        raise CloudError(f"Không kết nối được cloud: {exc}") from exc
    if resp.status_code >= 400:
        raise CloudError(f"Cloud từ chối (HTTP {resp.status_code}): {resp.text[:200]}")
    rows = resp.json()
    return rows if isinstance(rows, list) else []


def get_wallet() -> dict:
    """Số dư (milli-credit) + 10 giao dịch gần nhất."""
    wallets = _rest_get("wallets?select=balance_credits")
    ledger = _rest_get(
        "credit_ledger?select=delta_credits,reason,model,prompt_tokens,"
        "completion_tokens,created_at&order=created_at.desc&limit=10"
    )
    balance = wallets[0]["balance_credits"] if wallets else 0
    return {"balance_credits": balance, "ledger": ledger}


def get_packages() -> list[dict]:
    return _rest_get(
        "topup_packages?select=code,amount_vnd,credits&active=is.true&order=amount_vnd"
    )


def get_order(order_id: str) -> dict | None:
    rows = _rest_get(
        f"topup_orders?id=eq.{order_id}&select=id,status,amount_vnd,credits,paid_at"
    )
    return rows[0] if rows else None


def llm_correct(payload: dict, timeout: float) -> dict:
    """Gọi Edge Function llm-correct (payload {text, glossary, context, requestId}
    hoặc {ping: true}). Raise InsufficientCredits khi 402; CloudError khi khác."""
    try:
        resp = httpx.post(
            f"{SUPABASE_URL}/functions/v1/llm-correct",
            headers={
                "apikey": SUPABASE_ANON_KEY,
                "Authorization": f"Bearer {access_token()}",
            },
            json=payload,
            timeout=timeout,
        )
    except httpx.HTTPError as exc:
        raise CloudError(f"Không kết nối được cloud: {exc}") from exc
    if resp.status_code == 402:
        try:
            balance = float(resp.json().get("balanceCredits", 0)) / 1000
        except Exception:  # noqa: BLE001
            balance = 0.0
        raise InsufficientCredits(balance)
    if resp.status_code >= 400:
        raise CloudError(f"llm-correct lỗi (HTTP {resp.status_code}): {resp.text[:200]}")
    return resp.json()


def create_topup(package_code: str) -> dict:
    """Gọi Edge Function payos-order → {orderId, checkoutUrl, qrCode, ...}."""
    try:
        resp = httpx.post(
            f"{SUPABASE_URL}/functions/v1/payos-order",
            headers={
                "apikey": SUPABASE_ANON_KEY,
                "Authorization": f"Bearer {access_token()}",
            },
            json={"packageCode": package_code},
            timeout=AUTH_TIMEOUT_S,
        )
    except httpx.HTTPError as exc:
        raise CloudError(f"Không kết nối được cloud: {exc}") from exc
    if resp.status_code >= 400:
        raise CloudError(f"Tạo đơn nạp thất bại (HTTP {resp.status_code}): {resp.text[:200]}")
    return resp.json()
