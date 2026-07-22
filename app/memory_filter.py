"""Lớp A — bộ lọc từ ký ức bản dịch cũ (deterministic, trước pass 2).

Các cặp (sai → đúng) đã duyệt trong thư viện là "ký ức" những đoạn dịch cũ. Thay
vì chỉ nhồi chúng vào prompt LLM (few-shot bị cắt khi thư viện lớn), lớp này thay
thẳng các cụm phiên-âm-sai ĐÃ BIẾT bằng dạng chuẩn ngay trên text thô: nhanh, RẺ
(không tốn lượt LLM) và NHẤT QUÁN giữa các lần (cùng một lỗi luôn ra cùng kết
quả). Phần còn lại vẫn để pass 2 lo. Never-fail: lỗi DB → không đụng text.

Chỉ áp cặp `approved` (user đã xác nhận hoặc seed đã tuyển) — không đụng cặp
pending/trend chờ duyệt, tránh ký ức chưa kiểm chứng sửa bừa.
"""
from __future__ import annotations

import re
from collections.abc import Iterable

from app import corrections, db

# Cụm `wrong` ngắn hơn ngưỡng dễ trùng âm tiết Việt thường ("re", "đi") → bỏ,
# nhường pass 2 (có ngữ cảnh) xử. Chỉ thay khi cụm đủ đặc trưng.
MIN_WRONG_CHARS = 3

# Một entry ký ức đã biên dịch: regex khớp cụm `wrong` + chuỗi thay `right`.
Memory = list[tuple[re.Pattern[str], str]]


def _compile(wrong: str, right: str) -> tuple[re.Pattern[str], str] | None:
    """Biên dịch 1 cặp thành (pattern, right). Trả None nếu cặp không đáng thay
    (rỗng, quá ngắn, hoặc hai bên chỉ khác hoa-thường)."""
    wrong, right = wrong.strip(), right.strip()
    if len(wrong) < MIN_WRONG_CHARS or not right:
        return None
    if wrong.casefold() == right.casefold():
        return None
    # Khớp cả cụm nhiều từ, cho phép khoảng trắng co giãn giữa các từ; ranh giới
    # không-phải-chữ hai đầu để "in voi" không dính trong "in voice".
    tokens = [re.escape(t) for t in wrong.split()]
    body = r"\s+".join(tokens)
    return re.compile(rf"(?<!\w){body}(?!\w)", re.IGNORECASE), right


def build_memory(pairs: Iterable[tuple[str, str]]) -> Memory:
    """Biên dịch danh sách cặp thành Memory, cụm DÀI trước (khớp "cu bơ nét ét"
    trước "cu bơ nét" — tránh thay dở dang). Bỏ cặp không đáng thay."""
    compiled: Memory = []
    for wrong, right in sorted(pairs, key=lambda p: len(p[0]), reverse=True):
        entry = _compile(wrong, right)
        if entry:
            compiled.append(entry)
    return compiled


def apply_memory(text: str, memory: Memory) -> tuple[str, int]:
    """Thay mọi cụm đã biết trong text; trả (text mới, số lần thay). Áp tuần tự
    theo thứ tự đã sắp (dài → ngắn). Dạng `right` là tiếng Anh chuẩn nên pattern
    sau (cụm phiên âm Việt) không khớp lại phần vừa thay."""
    if not text:
        return text, 0
    total = 0
    for pattern, right in memory:
        text, n = pattern.subn(right, text)
        total += n
    return text, total


def from_library() -> Memory:
    """Ký ức = mọi cặp approved trong thư viện + base lexicon AI/tech (luôn bật).
    Thư viện đặt TRƯỚC base: build_memory giữ thứ tự này ở các cặp cùng độ dài
    `wrong`, apply_memory thay tuần tự nên cặp thư viện (user/seed đã duyệt) thắng
    base tĩnh khi trùng `wrong`. Lỗi DB → chỉ base (never-fail)."""
    try:
        rows = db.list_corrections(status="approved")
        pairs = [(r["wrong"], r["right"]) for r in rows]
    except Exception:  # noqa: BLE001 — ký ức hỏng không được chặn transcribe
        pairs = []
    pairs += corrections.base_pairs()
    return build_memory(pairs)


def correct_from_memory(text: str) -> tuple[str, int]:
    """Tiện ích 1 phát: dựng ký ức từ thư viện rồi áp lên text."""
    return apply_memory(text, from_library())
