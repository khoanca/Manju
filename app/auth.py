"""FR-11 Enterprise — đăng ký/đăng nhập SĐT + mật khẩu, session cookie, API token.

Không OTP (user chốt): người đầu tiên đăng ký = admin active; người sau =
member `pending` tới khi admin duyệt. Mật khẩu hash scrypt (stdlib, không thêm
dep). Session = token ngẫu nhiên trong cookie HttpOnly, hạn SESSION_DAYS.
API token (cho MCP cá nhân): hiện 1 lần, DB chỉ giữ sha256.

Rate-limit đăng nhập: 5 lần sai / SĐT → khoá LOCK_S (in-memory — restart xoá,
chấp nhận cho nội bộ công ty).
"""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import threading
import time
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException, Request, Response, WebSocket

from app import db

COOKIE = "manju_session"
SESSION_DAYS = 30
MAX_FAILS = 5
LOCK_S = 15 * 60
_PHONE_RE = re.compile(r"^\+?\d{8,15}$")

_fails: dict[str, tuple[int, float]] = {}  # phone → (count, locked_until)
_fails_lock = threading.Lock()


class AuthError(HTTPException):
    def __init__(self, status: int, detail: str):
        super().__init__(status, detail)


# ── Mật khẩu ───────────────────────────────────────────────────────────────
def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)
    return f"scrypt${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, salt_hex, digest_hex = stored.split("$")
        if algo != "scrypt":
            return False
        digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt_hex), n=2**14, r=8, p=1)
        return hmac.compare_digest(digest.hex(), digest_hex)
    except Exception:  # noqa: BLE001
        return False


def normalize_phone(phone: str) -> str:
    p = re.sub(r"[\s.\-()]", "", phone or "")
    if not _PHONE_RE.match(p):
        raise AuthError(400, "Số điện thoại không hợp lệ (8–15 chữ số)")
    return p


# ── Đăng ký / đăng nhập ────────────────────────────────────────────────────
def register(phone: str, name: str, password: str) -> dict:
    phone = normalize_phone(phone)
    name = (name or "").strip()
    if len(name) < 2:
        raise AuthError(400, "Tên quá ngắn")
    if len(password) < 6:
        raise AuthError(400, "Mật khẩu tối thiểu 6 ký tự")
    if db.get_user_auth(phone) is not None:
        raise AuthError(409, "Số điện thoại đã đăng ký")
    first = db.count_users() == 0
    return db.create_user(
        phone, name, hash_password(password),
        role="admin" if first else "member",
        status="active" if first else "pending",
    )


def _locked(phone: str) -> float:
    with _fails_lock:
        count, until = _fails.get(phone, (0, 0.0))
    return until if count >= MAX_FAILS and until > time.monotonic() else 0.0


def _record_fail(phone: str) -> None:
    with _fails_lock:
        count, _ = _fails.get(phone, (0, 0.0))
        count += 1
        _fails[phone] = (count, time.monotonic() + LOCK_S if count >= MAX_FAILS else 0.0)


def login(phone: str, password: str) -> dict:
    phone = normalize_phone(phone)
    until = _locked(phone)
    if until:
        raise AuthError(429, f"Sai quá {MAX_FAILS} lần — thử lại sau {int((until - time.monotonic()) / 60) + 1} phút")
    found = db.get_user_auth(phone)
    if found is None or not verify_password(password, found[1]):
        _record_fail(phone)
        raise AuthError(401, "Sai số điện thoại hoặc mật khẩu")
    with _fails_lock:
        _fails.pop(phone, None)
    user = found[0]
    if user["status"] == "blocked":
        raise AuthError(403, "Tài khoản đã bị khoá")
    return user


def reset_rate_limit() -> None:
    """Test dùng — xoá bộ đếm sai mật khẩu."""
    with _fails_lock:
        _fails.clear()


# ── Session cookie ─────────────────────────────────────────────────────────
def start_session(response: Response, user: dict, *, secure: bool) -> None:
    token = secrets.token_urlsafe(32)
    expires = datetime.now(UTC) + timedelta(days=SESSION_DAYS)
    db.create_session(user["id"], token, expires.isoformat())
    response.set_cookie(
        COOKIE, token, max_age=SESSION_DAYS * 86400, httponly=True,
        samesite="lax", secure=secure, path="/",
    )


def end_session(request: Request, response: Response) -> None:
    token = request.cookies.get(COOKIE)
    if token:
        db.delete_session(token)
    response.delete_cookie(COOKIE, path="/")


def _bearer_user(authorization: str | None) -> dict | None:
    if not authorization or not authorization.lower().startswith("bearer "):
        return None
    token = authorization[7:].strip()
    return db.api_token_user(hashlib.sha256(token.encode()).hexdigest()) if token else None


def user_from(cookies: dict, authorization: str | None) -> dict | None:
    """Session cookie (browser) hoặc `Authorization: Bearer <api token>` (MCP)."""
    token = cookies.get(COOKIE)
    user = db.session_user(token) if token else None
    return user or _bearer_user(authorization)


def current_user(request: Request) -> dict:
    """Dependency: user đã đăng nhập và active. 401 chưa đăng nhập, 403 chờ duyệt/khoá."""
    user = user_from(request.cookies, request.headers.get("authorization"))
    return require_active(user)


def require_active(user: dict | None) -> dict:
    if user is None:
        raise AuthError(401, "Chưa đăng nhập")
    if user["status"] == "pending":
        raise AuthError(403, "Tài khoản chờ admin duyệt")
    if user["status"] != "active":
        raise AuthError(403, "Tài khoản đã bị khoá")
    return user


def require_admin(user: dict) -> dict:
    if user["role"] != "admin":
        raise AuthError(403, "Chỉ admin")
    return user


def ws_user(ws: WebSocket) -> dict | None:
    return user_from(ws.cookies, ws.headers.get("authorization"))


# ── API token (MCP cá nhân) ────────────────────────────────────────────────
def issue_api_token(user: dict, label: str) -> dict:
    token = "mj_" + secrets.token_urlsafe(32)
    rec = db.create_api_token(user["id"], hashlib.sha256(token.encode()).hexdigest(), label.strip()[:60])
    return {**rec, "token": token}  # token chỉ trả về đúng 1 lần


# ── Quản lý user (admin) ───────────────────────────────────────────────────
def admin_update_user(admin: dict, user_id: str, *, role: str | None, status: str | None) -> dict:
    if role is not None and role not in ("member", "admin"):
        raise AuthError(400, "role phải là member|admin")
    if status is not None and status not in ("pending", "active", "blocked"):
        raise AuthError(400, "status không hợp lệ")
    target = db.get_user(user_id)
    if target is None:
        raise AuthError(404, "Không có user")
    demote = (role == "member") or (status in ("blocked", "pending"))
    if demote and target["role"] == "admin" and target["status"] == "active" and db.count_active_admins() <= 1:
        raise AuthError(400, "Không thể hạ/khoá admin cuối cùng")
    updated = db.update_user(user_id, role=role, status=status)
    assert updated is not None
    return updated
