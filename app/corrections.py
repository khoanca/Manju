"""Trích cặp (sai → đúng) từ diff bản máy vs bản user sửa (US-802)."""
from __future__ import annotations

import string
from difflib import SequenceMatcher

# Ngưỡng lọc chống cặp văn phong (US-802 AC3):
# - MAX_SPAN: mỗi bên ≤4 từ — dài hơn coi là user viết lại câu, không phải sửa lỗi;
# - MIN_RATIO: hai bên lệch số từ thì cụm phải giống nhau ≥0.3 (ký tự, lowercase).
#   Chốt 0.3 vì cặp phiên âm chuẩn "cu bơ nét"→"Kubernetes" đạt 0.42 (qua rộng rãi);
#   lưu ý dấu tiếng Việt kéo ratio xuống ("rét đít"→"redis" chỉ 0.17 — chấp nhận
#   bỏ sót còn hơn nhận nhầm văn phong).
MAX_SPAN = 4
MIN_RATIO = 0.3


def _clean(tokens: list[str]) -> str:
    """Ghép span thành cụm: strip dấu câu mép ngoài từng từ (giữ dấu trong từ
    như "sherpa-onnx"), bỏ từ rỗng — whitespace tự gọn khi join."""
    words = (t.strip(string.punctuation) for t in tokens)
    return " ".join(w for w in words if w)


def _is_noise(wrong: str, right: str) -> bool:
    """True nếu cặp là văn phong/vô nghĩa, không đáng vào thư viện (AC3)."""
    if not wrong or not right:
        return True  # một bên rỗng sau khi strip dấu câu
    if wrong.casefold() == right.casefold():
        return True  # chỉ khác hoa-thường hoặc dấu câu
    if max(len(wrong.split()), len(right.split())) > MAX_SPAN:
        return True  # span quá dài — user viết lại câu
    if len(wrong.split()) == len(right.split()):
        return False  # thay thế 1-1 theo từ — giữ
    # Lệch số từ: chỉ giữ khi hai cụm đủ giống nhau (phiên âm sai thuật ngữ)
    return SequenceMatcher(None, wrong.lower(), right.lower()).ratio() < MIN_RATIO


def extract_pairs(machine: str, edited: str) -> list[tuple[str, str]]:
    """Trích cặp (sai → đúng) từ diff 2 bản text, token hoá theo từ.

    Chỉ lấy op `replace` của SequenceMatcher rồi lọc văn phong bằng `_is_noise`.
    Trả list cặp đã chuẩn hoá (strip dấu câu mép ngoài, whitespace gọn).
    """
    machine_tokens, edited_tokens = machine.split(), edited.split()
    matcher = SequenceMatcher(None, machine_tokens, edited_tokens, autojunk=False)
    pairs: list[tuple[str, str]] = []
    for op, i1, i2, j1, j2 in matcher.get_opcodes():
        if op != "replace":
            continue
        wrong = _clean(machine_tokens[i1:i2])
        right = _clean(edited_tokens[j1:j2])
        if not _is_noise(wrong, right):
            pairs.append((wrong, right))
    return pairs
