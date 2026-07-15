"""Trích cặp (sai → đúng) từ diff bản máy vs bản user sửa (US-802)
+ build bias mồi ASR / few-shot pass 2 từ entry approved (US-803)."""
from __future__ import annotations

import string
from difflib import SequenceMatcher

from app import db

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


# Cap chuỗi bias mồi Whisper: initial_prompt thực tế chỉ ăn ~224 token
# (engines.py — Whisper tự bỏ phần đuôi thừa), ~800 ký tự là ngưỡng an toàn.
# Chỉ cap phần nối từ thư viện — phần user KHÔNG bao giờ bị cắt.
BIAS_CAP_CHARS = 800


def build_bias(user_glossary: str) -> str:
    """Glossary hiệu lực cho ASR: glossary user giữ nguyên đứng trước, nối
    thêm các term `right` approved từ thư viện (db đã sort count DESC,
    updated_at DESC), bỏ term đã có (so casefold), dừng khi vượt
    BIAS_CAP_CHARS. Lỗi DB → trả nguyên user_glossary (never-fail, US-803 AC2).
    """
    out = user_glossary.strip()
    try:
        rows = db.list_corrections(status="approved")
    except Exception:  # noqa: BLE001 — thư viện hỏng không được chặn transcribe
        return out
    seen = {t.strip().casefold() for t in out.split(",") if t.strip()}
    for row in rows:
        term = row["right"].strip()
        if not term or term.casefold() in seen:
            continue
        merged = f"{out}, {term}" if out else term
        if len(merged) > BIAS_CAP_CHARS:
            break
        out = merged
        seen.add(term.casefold())
    return out


def top_pairs(limit: int = 20) -> list[tuple[str, str]]:
    """Cặp (sai → đúng) approved gặp nhiều nhất — few-shot cho prompt pass 2.
    Lỗi DB → [] (never-fail, US-803 AC2)."""
    try:
        rows = db.list_corrections(status="approved")
    except Exception:  # noqa: BLE001
        return []
    return [(r["wrong"], r["right"]) for r in rows[:limit]]
