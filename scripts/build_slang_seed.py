#!/usr/bin/env python3
"""Sinh seed lexicon slang/teencode `app/data/lexicon/slang.json` (US-815) — dev-time.

Nguồn chính: CURATED bên dưới (tự soạn — wrong = Whisper nghe nhầm khả dĩ,
right = chính tả chuẩn của từ lóng ĐANG NÓI trong họp/MXH). Dataset teencode
công khai (Vinorm, teencode4 — khảo sát 2026-07-20) toàn viết tắt CHAT
("bme"→"bố mẹ") mà Whisper không bao giờ output, license NOASSERTION → chỉ
tải khi truyền --include-datasets và lọc qua `_plausible_spoken`; kết quả
mặc định commit vào repo KHÔNG chứa dữ liệu dataset.

Chạy: `uv run python scripts/build_slang_seed.py [--include-datasets]`
Ràng buộc output (test_lexicon_files_parse_and_schema): entry đúng 2 key
{wrong, right}; không trùng cặp trong file lẫn với 4 file vùng hiện có.
"""
from __future__ import annotations

import argparse
import json
import sys
import unicodedata
import urllib.request
from pathlib import Path

LEXICON_DIR = Path(__file__).resolve().parent.parent / "app" / "data" / "lexicon"
OUT_FILE = LEXICON_DIR / "slang.json"
REGION_FILES = ("bac", "trung", "nam", "en_accent")
MAX_WORDS = 4   # khớp MAX_SPAN của corrections._is_noise — dài hơn là câu, không phải cặp
MAX_CHARS = 40
FETCH_TIMEOUT_S = 15

# (tên, URL raw đã verify 2026-07-20, ký tự tách wrong/right mỗi dòng)
DATASETS = (
    ("vinorm-teencode",
     "https://raw.githubusercontent.com/v-nhandt21/Vinorm/master/vinorm/Mapping/Teencode.txt",
     "#"),
    ("teencode4",
     "https://raw.githubusercontent.com/FlynnBui399/vietnamese-emoji-emotion-recognition/main/docs/teencode4.txt",
     "\t"),
)

# Slang nói 2024–2026 (MXH VN: TikTok/FB/X) — KHÔNG đưa cặp có `wrong` là
# từ/cụm chuẩn thông dụng (không bao giờ "không"→"khum") để pass 2 khỏi sửa
# bậy lời nói bình thường; teencode chỉ-viết (ko, j, đc) cũng loại vì Whisper
# không output dạng đó.
CURATED: tuple[tuple[str, str], ...] = (
    # Gốc Anh — Whisper phiên thành âm tiết Việt
    ("phờ lếch", "flex"),
    ("phơ lếch", "flex"),
    ("tóc xích", "toxic"),
    ("tóc xít", "toxic"),
    ("chiu", "chill"),
    ("rét phờ lác", "red flag"),
    ("grin phờ lác", "green flag"),
    ("vai bờ", "vibe"),
    ("vai bơ", "vibe"),
    ("hiu linh", "healing"),
    ("sờ lây", "slay"),
    ("xì lây", "slay"),
    ("phờ lóp", "flop"),
    ("cờ rin giơ", "cringe"),
    ("cờ rớt", "crush"),
    ("sim bờ", "simp"),
    ("bét sờ ti", "bestie"),
    ("pích mi", "pick me"),
    ("tú ét đây", "Tuesday"),
    ("nét ti dừn", "netizen"),
    ("đờ ra ma", "drama"),
    ("tờ rôn", "troll"),
    ("tờ ren", "trend"),
    ("bắt trén", "bắt trend"),
    ("bắt tren", "bắt trend"),
    ("đu tren", "đu trend"),
    ("đu ai đồ", "đu idol"),
    ("gen dét", "Gen Z"),
    ("gien dét", "Gen Z"),
    ("bo đi sê ming", "body shaming"),
    ("sô lô", "solo"),
    ("quen cha na", "gwenchana"),
    ("xỉu áp xỉu đao", "xỉu up xỉu down"),
    # Meme giữ nguyên chính tả meme (sửa về bản chuẩn của CHÍNH từ lóng)
    ("ơ mây din", "ơ mây zing"),
    ("gút chọp", "gút chóp"),
    ("mai đét ti ni", "mai đẹt ti ni"),
    ("tốp tốp", "tóp tóp"),
    # Gốc Việt — garble dấu/phụ âm về đúng chính tả từ lóng
    ("ghét gô", "gét gô"),
    ("gét gâu", "gét gô"),
    ("ét ô ét", "ét o ét"),
    ("ét ơ ét", "ét o ét"),
    ("su cà na", "xu cà na"),
    ("xu cà la", "xu cà na"),
    ("mai keo", "mãi keo"),
    ("mãi kèo", "mãi keo"),
    ("mãi mặn", "mãi mận"),
    ("ô rề", "ô dề"),
    ("ô giề", "ô dề"),
    ("chằm zen", "chằm Zn"),
    ("chằm dét en", "chằm Zn"),
    ("u la trời", "u là trời"),
    ("ra rẻ", "ra dẻ"),
    ("cà nhín", "cà nhính"),
    ("phông bạc", "phông bạt"),
    ("cà khỉa", "cà khịa"),
    ("xịt kêu", "xịt keo"),
    ("ao chình", "ao trình"),
    ("đỉnh lóc kịch trần", "đỉnh nóc kịch trần"),
    ("mờ lem", "mlem"),
    ("bóc phót", "bóc phốt"),
    ("ăn nói sà lơ", "ăn nói xà lơ"),
)

_VOWELS = frozenset("aeiouy")


def _has_vowel(token: str) -> bool:
    """Token 'phát âm được' — có nguyên âm (bỏ dấu tiếng Việt trước khi so)."""
    stripped = unicodedata.normalize("NFD", token.casefold())
    return any(c in _VOWELS for c in stripped)


def _plausible_spoken(wrong: str, right: str) -> bool:
    """Lọc cặp dataset: loại viết tắt chat (token không nguyên âm như 'bme',
    'ntn'), cặp quá dài, cặp chỉ khác hoa-thường — giữ cặp Whisper CÓ THỂ
    output khi nghe người nói."""
    if not wrong or not right or wrong.casefold() == right.casefold():
        return False
    if max(len(wrong.split()), len(right.split())) > MAX_WORDS:
        return False
    if max(len(wrong), len(right)) > MAX_CHARS:
        return False
    return all(len(t) >= 2 and _has_vowel(t) for t in wrong.split())


def _fetch_dataset(name: str, url: str, sep: str) -> list[tuple[str, str]]:
    try:
        with urllib.request.urlopen(url, timeout=FETCH_TIMEOUT_S) as resp:
            text = resp.read().decode("utf-8", errors="replace")
    except OSError as exc:
        print(f"  {name}: bỏ qua (lỗi tải: {exc})")
        return []
    pairs = []
    for line in text.splitlines():
        parts = [p.strip() for p in line.split(sep)]
        if len(parts) == 2 and all(parts):
            pairs.append((parts[0], parts[1]))
    kept = [p for p in pairs if _plausible_spoken(*p)]
    print(f"  {name}: {len(pairs)} dòng → giữ {len(kept)} sau lọc phát âm")
    return kept


def _existing_pairs() -> set[tuple[str, str]]:
    seen: set[tuple[str, str]] = set()
    for region in REGION_FILES:
        for e in json.loads((LEXICON_DIR / f"{region}.json").read_text(encoding="utf-8")):
            seen.add((e["wrong"], e["right"]))
    return seen


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--include-datasets", action="store_true",
                    help="tải thêm dataset teencode công khai (license NOASSERTION)")
    args = ap.parse_args()

    candidates = list(CURATED)
    if args.include_datasets:
        print("Tải dataset công khai (opt-in):")
        for name, url, sep in DATASETS:
            candidates += _fetch_dataset(name, url, sep)

    taken = _existing_pairs()
    wrong_seen: set[str] = set()
    entries: list[dict[str, str]] = []
    for wrong, right in candidates:
        pair = (wrong, right)
        # 1 wrong chỉ map 1 right (mâu thuẫn thì bản CURATED đứng trước thắng)
        if pair in taken or wrong.casefold() in wrong_seen:
            continue
        taken.add(pair)
        wrong_seen.add(wrong.casefold())
        entries.append({"wrong": wrong, "right": right})

    lines = ",\n".join("  " + json.dumps(e, ensure_ascii=False) for e in entries)
    OUT_FILE.write_text(f"[\n{lines}\n]\n", encoding="utf-8")
    print(f"Đã ghi {len(entries)} entry → {OUT_FILE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
