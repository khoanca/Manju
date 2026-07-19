"""Tổng hợp slang/teencode MXH đang hot → nhập thư viện chờ duyệt (US-816).

Nguồn: LLM cloud (OpenRouter qua `correct.chat_once`) liệt kê từ lóng đang
thịnh hành dạng cặp (Whisper nghe nhầm → chính tả chuẩn). Mọi entry nhập với
source='trend', status='pending' — KHÔNG tự vào bias ASR/pass 2 cho tới khi
user duyệt trong Settings → Thư viện từ. Validate lỏng kiểu skip-not-raise
(khác `_validate_entries` chặt của nguồn remote có checksum): LLM trả entry
hỏng thì bỏ entry đó, đếm vào `skipped` cho user thấy.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from app import correct, db
from app.corrections import SLANG_TAG

TREND_SOURCE = "trend"
MAX_ENTRIES = 40   # chặn LLM trả danh sách dài bất thường
MAX_WORDS = 4      # khớp MAX_SPAN của corrections — dài hơn là câu, không phải cặp
MAX_CHARS = 40
LLM_TIMEOUT_S = 90.0

_EXTRACT_SYSTEM = (
    "Bạn là chuyên gia ngôn ngữ mạng xã hội Việt Nam, đang xây từ điển giúp "
    "công cụ ASR (Whisper) nhận đúng từ lóng khi người trẻ NÓI trong cuộc họp. "
    'Trả về DUY NHẤT một JSON array dạng [{"wrong": "...", "right": "..."}]: '
    "`right` = chính tả chuẩn của từ lóng/teencode được nói thành tiếng; "
    "`wrong` = cách Whisper nhiều khả năng phiên âm SAI khi nghe (âm tiết "
    "tiếng Việt gần giống cách đọc). CHỈ lấy từ lóng được NÓI thành tiếng — "
    "CẤM viết tắt chỉ dùng khi gõ phím (ko, j, đc, ntn, k...). CẤM cặp có "
    "`wrong` là từ/cụm tiếng Việt chuẩn thông dụng. Mỗi bên tối đa 4 từ. "
    "Tối đa 30 cặp. Không markdown, không lời giải thích."
)
_TREND_USER = (
    "Liệt kê từ lóng / teencode tiếng Việt đang thịnh hành trên TikTok, "
    "Facebook, X (giai đoạn 2024–2026) mà người trẻ có thể dùng khi nói "
    "chuyện, kèm cách Whisper dễ nghe nhầm."
)


@dataclass(frozen=True)
class TrendResult:
    """Kết quả 1 lần chạy tổng hợp trend — trả về UI để user biết có gì mới."""

    new_pending: int      # entry mới nhập, đang chờ duyệt
    skipped: int          # entry hỏng bị bỏ + cặp đã có sẵn trong thư viện
    sources_ok: int       # số nguồn lấy được dữ liệu
    sources_skipped: int  # số nguồn lỗi/bị chặn — bỏ qua, không sập


def _valid_pair(wrong: object, right: object) -> bool:
    if not (isinstance(wrong, str) and isinstance(right, str)):
        return False
    w, r = wrong.strip(), right.strip()
    if not w or not r or w.casefold() == r.casefold():
        return False
    if max(len(w.split()), len(r.split())) > MAX_WORDS:
        return False
    return max(len(w), len(r)) <= MAX_CHARS


def _parse_entries(raw: str) -> tuple[list[tuple[str, str]], int]:
    """Ép output LLM thành cặp (wrong, right) sạch. Payload không phải JSON
    array → ValueError (nguồn hỏng); entry lẻ hỏng/trùng wrong → skip + đếm."""
    data = json.loads(raw)  # ValueError nếu không phải JSON
    if not isinstance(data, list):
        raise ValueError("payload trend không phải danh sách")
    rows: list[tuple[str, str]] = []
    seen_wrong: set[str] = set()
    skipped = 0
    for e in data:
        wrong = e.get("wrong") if isinstance(e, dict) else None
        right = e.get("right") if isinstance(e, dict) else None
        if not _valid_pair(wrong, right):
            skipped += 1
            continue
        w, r = str(wrong).strip(), str(right).strip()
        if w.casefold() in seen_wrong or len(rows) >= MAX_ENTRIES:
            skipped += 1
            continue
        seen_wrong.add(w.casefold())
        rows.append((w, r))
    return rows, skipped


def llm_digest() -> tuple[list[tuple[str, str]], int]:
    """Nguồn 1: hỏi thẳng LLM trend đang hot. Lỗi mạng/JSON → raise, caller
    đếm nguồn skip (best-effort theo nguồn, không sập cả lượt chạy)."""
    raw = correct.chat_once(_EXTRACT_SYSTEM, _TREND_USER, timeout=LLM_TIMEOUT_S)
    return _parse_entries(raw)


def run_trend_update() -> TrendResult:
    """Chạy các nguồn trend, gộp + dedup, nhập DB pending. Từng nguồn hỏng chỉ
    bị skip; DB chỉ ghi sau khi gom xong (INSERT OR IGNORE — bấm lại an toàn)."""
    pairs: list[tuple[str, str]] = []
    skipped = ok = failed = 0
    try:
        rows, bad = llm_digest()
        pairs += rows
        skipped += bad
        ok += 1
    except Exception:  # noqa: BLE001 — nguồn trend hỏng không được sập endpoint
        failed += 1
    uniq: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for w, r in pairs:
        key = (w.casefold(), r.casefold())
        if key in seen:
            skipped += 1
            continue
        seen.add(key)
        uniq.append((w, r))
    added = db.add_corrections_ignore(
        [(w, r, SLANG_TAG) for w, r in uniq], source=TREND_SOURCE, status="pending"
    )
    skipped += len(uniq) - added  # cặp đã có trong thư viện (UNIQUE wrong,right)
    return TrendResult(added, skipped, ok, failed)
