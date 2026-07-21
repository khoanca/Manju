"""Rà & sửa lặp/nhiễu toàn văn cho bản ghi đã lưu (hậu kỳ).

Live lọc từng segment lúc thu (engines.keep_segment / collapse_loops), nhưng
loop thoái hoá có thể vắt qua nhiều segment hoặc lọt qua nếu decode ra một khối
dính liền. Bước này chạy lại bộ gom lặp trên TOÀN văn bản đã lưu + cắt cụm
hallucination — tất định, không gọi LLM (pass 2 là bước tuỳ chọn riêng).
"""
from __future__ import annotations

from dataclasses import dataclass

from app import engines


@dataclass(frozen=True)
class ReviewResult:
    cleaned: str  # văn bản sau khi gom lặp + cắt cụm hallucination
    original: str  # nguyên văn đầu vào
    chars_removed: int  # số ký tự bị cắt (>= 0)
    dropped: list[str]  # các cụm hallucination đã cắt (để UI liệt kê)


def review_and_fix(text: str) -> ReviewResult:
    """Gom mọi dạng loop (token đơn / chu kỳ n-gram / lặp trong-token) trên toàn
    văn bản rồi cắt cụm outro YouTube. Không đổi gì → cleaned == original."""
    collapsed = engines.collapse_loops(text)
    cleaned, dropped = engines.strip_hallucination_phrases(collapsed)
    return ReviewResult(
        cleaned=cleaned,
        original=text,
        chars_removed=max(0, len(text) - len(cleaned)),
        dropped=dropped,
    )
