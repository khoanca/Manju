"""Đo độ chính xác transcript so với bản user sửa tay (US-820).

Bản chuẩn = `transcripts.edited_text` do user sửa (FR-6 đã thu thập sẵn), nên
không cần dataset riêng. WER/CER tự cài bằng Levenshtein thay vì thêm
dependency: dự án local-first, và cách chuẩn hoá tiếng Việt (bỏ dấu câu, gộp
hoa thường, giữ nguyên dấu thanh) phải nằm trong tầm kiểm soát — phiên âm sai
thanh điệu LÀ lỗi, không được chuẩn hoá cho biến mất.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# Bỏ mọi thứ không phải chữ/số/khoảng trắng. \w theo Unicode giữ nguyên chữ
# tiếng Việt có dấu ("để" không bị tách), chỉ gỡ dấu câu Whisper tự thêm.
_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)


def normalize(text: str) -> list[str]:
    """Text → list token đã chuẩn hoá để so khớp.

    Gộp hoa thường + bỏ dấu câu: Whisper chấm phẩy tuỳ hứng, tính vào WER sẽ
    nhiễu. KHÔNG bỏ dấu thanh và KHÔNG đổi số thành chữ — cả hai đều là nội
    dung thật, sai là phải tính lỗi.
    """
    return _PUNCT_RE.sub(" ", text.casefold()).split()


@dataclass(frozen=True)
class EditCounts:
    """Số phép sửa để biến hyp thành ref — soi được lỗi thiên về loại nào."""

    hits: int = 0
    subs: int = 0
    dels: int = 0  # có trong ref, thiếu ở hyp (ASR nuốt chữ)
    ins: int = 0  # hyp thừa ra (ASR bịa thêm — loop hallucination rơi vào đây)

    @property
    def ref_len(self) -> int:
        return self.hits + self.subs + self.dels

    @property
    def rate(self) -> float:
        """(sub + del + ins) / len(ref). >1.0 khi hyp bịa quá nhiều — không cắt
        trần, vì chính con số vượt 1.0 tố cáo segment loop."""
        if self.ref_len == 0:
            return 0.0 if self.ins == 0 else 1.0
        return (self.subs + self.dels + self.ins) / self.ref_len


def _distance_matrix(ref: list[str], hyp: list[str]) -> list[list[int]]:
    rows, cols = len(ref) + 1, len(hyp) + 1
    d = [[0] * cols for _ in range(rows)]
    for i in range(rows):
        d[i][0] = i
    for j in range(cols):
        d[0][j] = j
    for i in range(1, rows):
        for j in range(1, cols):
            if ref[i - 1] == hyp[j - 1]:
                d[i][j] = d[i - 1][j - 1]
            else:
                d[i][j] = 1 + min(d[i - 1][j - 1], d[i - 1][j], d[i][j - 1])
    return d


def edit_counts(ref: list[str], hyp: list[str]) -> EditCounts:
    """Levenshtein + backtrace để tách sub/del/ins (không chỉ tổng khoảng cách)."""
    if not ref and not hyp:
        return EditCounts()
    if not ref:
        return EditCounts(ins=len(hyp))
    if not hyp:
        return EditCounts(dels=len(ref))
    d = _distance_matrix(ref, hyp)
    hits = subs = dels = ins = 0
    i, j = len(ref), len(hyp)
    while i > 0 or j > 0:
        if i > 0 and j > 0 and ref[i - 1] == hyp[j - 1] and d[i][j] == d[i - 1][j - 1]:
            hits += 1
            i, j = i - 1, j - 1
        elif i > 0 and j > 0 and d[i][j] == d[i - 1][j - 1] + 1:
            subs += 1
            i, j = i - 1, j - 1
        elif i > 0 and d[i][j] == d[i - 1][j] + 1:
            dels += 1
            i -= 1
        else:
            ins += 1
            j -= 1
    return EditCounts(hits=hits, subs=subs, dels=dels, ins=ins)


def wer(reference: str, hypothesis: str) -> float:
    """Word Error Rate — 0.0 là khớp hoàn toàn. Xem EditCounts.rate về >1.0."""
    return edit_counts(normalize(reference), normalize(hypothesis)).rate


def cer(reference: str, hypothesis: str) -> float:
    """Character Error Rate — nhạy hơn WER với lỗi sai một âm tiết."""
    ref = list(" ".join(normalize(reference)))
    hyp = list(" ".join(normalize(hypothesis)))
    return edit_counts(ref, hyp).rate


def cross_segment_repeat(first: str, second: str) -> int:
    """Số token bị lặp ở ranh giới 2 segment liền nhau (US-822).

    Đệm đuôi utterance nuốt sang audio câu kế tiếp → cùng một cụm bị phiên âm
    vào CẢ hai segment. Trả độ dài đoạn chồng lấn dài nhất (đuôi `first` trùng
    đầu `second`); 0 là sạch. Đây là cái giá phải theo dõi khi tăng đệm biên.
    """
    a, b = normalize(first), normalize(second)
    for n in range(min(len(a), len(b)), 0, -1):
        if a[-n:] == b[:n]:
            return n
    return 0


def max_cross_repeat(segments: list[str]) -> int:
    """Chồng lấn lớn nhất trên mọi cặp segment liền kề của một bản ghi."""
    return max(
        (cross_segment_repeat(a, b) for a, b in zip(segments, segments[1:], strict=False)),
        default=0,
    )
