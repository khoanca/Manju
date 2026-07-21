"""Lớp B — dự đoán ngành nghề của cuộc họp để nạp đúng lexicon ngành (Lớp C).

Local-first, không bắt buộc mạng: chấm điểm bằng độ trùng term có trọng số TF-IDF
giữa transcript và vốn từ seed từng ngành (`corrections.domain_seed_pairs`). Vốn
từ gồm CẢ dạng Whisper-phiên-sai (`wrong`) lẫn dạng chuẩn (`right`) nên khớp được
transcript thô lẫn bản đã sửa. Có OpenRouter thì `predict_domain_llm` cho lượt
zero-shot chính xác hơn, nhưng không phải điều kiện bắt buộc.
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from functools import lru_cache

from app import correct, corrections

# Ngưỡng confidence tối thiểu để coi 1 ngành là "active" và nạp lexicon của nó:
# thấp hơn thì tín hiệu quá yếu, ép ngành sai hại hơn không có (giống ngưỡng
# duyệt thư viện — thà bỏ sót còn hơn nạp nhầm term ngành lạ vào bias).
MIN_CONFIDENCE = 0.35
MAX_ACTIVE = 2  # tối đa số ngành nạp cùng lúc — nhiều hơn thì bias loãng
# Sàn bằng chứng tuyệt đối: confidence là TỶ TRỌNG giữa các ngành khớp nên 1
# term lạ duy nhất vẫn ra 1.0. Đòi ≥2 tín hiệu KHÁC NHAU mới kích hoạt lexicon,
# tránh 1 từ tiếng Anh vu vơ lật cả ngành.
MIN_HITS = 2


@dataclass(frozen=True)
class DomainScore:
    domain: str
    score: float        # điểm TF-IDF thô
    confidence: float   # điểm chuẩn hoá trên tổng (0..1)
    hits: int           # số tín hiệu KHÁC NHAU khớp (bằng chứng tuyệt đối)


def _norm(text: str) -> str:
    """Về lowercase + gộp khoảng trắng để so khớp cụm (kể cả cụm nhiều từ)."""
    return re.sub(r"\s+", " ", text.casefold()).strip()


@lru_cache(maxsize=1)
def _domain_vocab() -> dict[str, tuple[str, ...]]:
    """Vốn từ mỗi ngành: mọi tín hiệu (wrong+right) đã chuẩn hoá, bỏ trùng.
    Cache 1 lần — seed tĩnh, không đổi trong 1 process."""
    vocab: dict[str, tuple[str, ...]] = {}
    for dom in corrections.DOMAINS:
        signals: list[str] = []
        seen: set[str] = set()
        for wrong, right in corrections.domain_seed_pairs(dom):
            for sig in (_norm(wrong), _norm(right)):
                if sig and sig not in seen:
                    seen.add(sig)
                    signals.append(sig)
        vocab[dom] = tuple(signals)
    return vocab


@lru_cache(maxsize=1)
def _idf() -> dict[str, float]:
    """IDF theo số NGÀNH chứa mỗi tín hiệu: term đặc trưng 1 ngành có trọng số
    cao, term dùng chung nhiều ngành gần 0. idf = log((1+N)/(1+df)) + 1."""
    vocab = _domain_vocab()
    n = len(vocab)
    df: dict[str, int] = {}
    for signals in vocab.values():
        for sig in set(signals):
            df[sig] = df.get(sig, 0) + 1
    return {sig: math.log((1 + n) / (1 + d)) + 1.0 for sig, d in df.items()}


def _count(signal: str, haystack: str) -> int:
    """Số lần `signal` (đã norm) xuất hiện như cụm nguyên trong text đã norm.
    Dùng ranh giới không-phải-chữ để "in voi" không dính trong "in voice"."""
    pat = re.compile(rf"(?<!\w){re.escape(signal)}(?!\w)")
    return len(pat.findall(haystack))


def predict_domain(text: str, top_k: int = 3) -> list[DomainScore]:
    """Chấm điểm mọi ngành theo độ trùng TF-IDF, trả top_k điểm > 0 (giảm dần).
    Text rỗng / không khớp gì → [] (caller giữ hành vi mặc định, never-fail)."""
    hay = _norm(text)
    if not hay:
        return []
    idf = _idf()
    raw: list[tuple[str, float, int]] = []
    for dom, signals in _domain_vocab().items():
        score = 0.0
        hits = 0
        for sig in signals:
            n = _count(sig, hay)
            if n:
                score += n * idf.get(sig, 1.0)
                hits += 1
        if score > 0:
            raw.append((dom, score, hits))
    total = sum(s for _, s, _ in raw)
    if total <= 0:
        return []
    scored = [DomainScore(d, s, s / total, h) for d, s, h in raw]
    scored.sort(key=lambda x: x.score, reverse=True)
    return scored[:top_k]


def active_domains(
    text: str, min_confidence: float = MIN_CONFIDENCE, max_active: int = MAX_ACTIVE
) -> tuple[str, ...]:
    """Ngành đủ tin cậy để nạp lexicon: confidence ≥ ngưỡng VÀ ≥ MIN_HITS tín
    hiệu (bằng chứng tuyệt đối), tối đa `max_active`. Đây là đầu ra dùng cho
    `corrections.build_bias(domains=...)`/`top_pairs`."""
    picks = [
        d.domain
        for d in predict_domain(text)
        if d.confidence >= min_confidence and d.hits >= MIN_HITS
    ]
    return tuple(picks[:max_active])


_LLM_SYSTEM = (
    "Bạn phân loại ngành nghề của một cuộc họp tiếng Việt xen thuật ngữ tiếng "
    "Anh. Chỉ chọn trong danh sách cho sẵn. Trả về DUY NHẤT một JSON object "
    '{"domain": "<tên ngành hoặc null>", "confidence": <0..1>}. '
    "Không chắc chắn thì domain = null. Không giải thích, không markdown."
)


def predict_domain_llm(text: str, timeout: float = 30.0) -> DomainScore | None:
    """Lượt zero-shot bằng LLM cloud (chính xác hơn overlap khi thuật ngữ ít).
    Cần OpenRouter — caller phải check `correct.openrouter_enabled()` trước.
    Lỗi mạng/JSON/ngoài danh sách → None (caller rơi về `predict_domain`)."""
    user = (
        "Danh sách ngành: " + ", ".join(corrections.DOMAINS) + "\n\n"
        "Nội dung cuộc họp:\n" + text[:4000]
    )
    try:
        raw = correct.chat_once(_LLM_SYSTEM, user, timeout=timeout)
        data = json.loads(raw)
    except Exception:  # noqa: BLE001 — mạng/parse lỗi → không có kết quả LLM
        return None
    dom = data.get("domain") if isinstance(data, dict) else None
    if dom not in corrections.DOMAINS:
        return None
    try:
        conf = float(data.get("confidence", 0.0))
    except (TypeError, ValueError):
        conf = 0.0
    conf = max(0.0, min(1.0, conf))
    # LLM tự khẳng định là bằng chứng riêng → hits = MIN_HITS (không bị sàn loại).
    return DomainScore(dom, conf, conf, MIN_HITS)
