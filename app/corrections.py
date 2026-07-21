"""Trích cặp (sai → đúng) từ diff bản máy vs bản user sửa (US-802)
+ build bias mồi ASR / few-shot pass 2 từ entry approved (US-803)
+ seed lexicon vùng miền & cập nhật remote opt-in (US-804)."""
from __future__ import annotations

import hashlib
import json
import string
from collections import Counter
from collections.abc import Collection, Sequence
from difflib import SequenceMatcher
from pathlib import Path

import httpx

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
# Sub-cap riêng cho term cá nhân (mine theo người nói) — chừa chỗ cho thư viện.
PERSONAL_CAP_CHARS = 240


def _add_personal(out: str, seen: set[str], personal: Sequence[str]) -> str:
    """Nối term cá nhân sau glossary user: dedup casefold với phần đã có,
    dừng khi phần cá nhân vượt PERSONAL_CAP_CHARS hoặc tổng vượt BIAS_CAP_CHARS."""
    used = 0
    for raw in personal:
        term = raw.strip()
        if not term or term.casefold() in seen:
            continue
        added = len(term) + (2 if out else 0)
        if used + added > PERSONAL_CAP_CHARS or len(out) + added > BIAS_CAP_CHARS:
            break
        out = f"{out}, {term}" if out else term
        used += added
        seen.add(term.casefold())
    return out


# Tag của entry slang/teencode (US-815) — không phải vùng miền: khi lọc theo
# vùng đang active, slang được coi là "khớp mọi vùng" (region-neutral), nếu
# không seed vùng (372-586 ký tự/vùng) sẽ chèn hết cap 800 trước khi tới slang.
SLANG_TAG = "slang"


def _eligible_tags(regions: Collection[str], domains: Collection[str] = ()) -> frozenset[str]:
    """Tag xếp tier ưu tiên khi có vùng/ngành active; regions=() và domains=()
    → rỗng như cũ (mọi tag đồng hạng, slang KHÔNG được nhảy lên trên cặp user
    count cao). Ngành đưa vào dưới dạng tag đã prefix (`domain_tag`)."""
    dom_tags = {domain_tag(d) for d in domains}
    if not regions and not dom_tags:
        return frozenset()
    return frozenset({*regions, *dom_tags, SLANG_TAG})


def _rank_rows(
    rows: list[dict], topic: str, regions: Collection[str], domains: Collection[str] = ()
) -> list[dict]:
    """Stable sort: tag khớp vùng miền/ngành (slang tính là khớp) trước, rồi
    term xuất hiện trong topic; còn lại giữ nguyên count DESC/updated_at DESC."""
    topic_cf = topic.casefold()
    eligible = _eligible_tags(regions, domains)
    return sorted(rows, key=lambda r: (
        r["tag"] not in eligible,
        not (topic_cf and r["right"].strip().casefold() in topic_cf),
    ))


def build_bias(
    user_glossary: str,
    personal: Sequence[str] = (),
    topic: str = "",
    regions: Collection[str] = (),
    domains: Collection[str] = (),
) -> str:
    """Glossary hiệu lực cho ASR: (1) glossary user giữ nguyên đứng trước —
    KHÔNG bao giờ bị cắt; (2) term cá nhân theo người nói (sub-cap
    PERSONAL_CAP_CHARS); (3) term `right` approved từ thư viện, re-rank
    vùng miền/ngành > topic > count (`_rank_rows`), dừng khi vượt
    BIAS_CAP_CHARS. `domains` = ngành nghề dự đoán (Lớp B) → term ngành nổi lên
    đầu bias. Dedup casefold xuyên suốt. Lỗi DB → user + personal (never-fail).
    """
    out = user_glossary.strip()
    seen = {t.strip().casefold() for t in out.split(",") if t.strip()}
    out = _add_personal(out, seen, personal)
    try:
        rows = db.list_corrections(status="approved")
    except Exception:  # noqa: BLE001 — thư viện hỏng không được chặn transcribe
        return out
    for row in _rank_rows(rows, topic, regions, domains):
        term = row["right"].strip()
        if not term or term.casefold() in seen:
            continue
        merged = f"{out}, {term}" if out else term
        if len(merged) > BIAS_CAP_CHARS:
            break
        out = merged
        seen.add(term.casefold())
    return out


def suggest_from_library(word: str, limit: int = 5) -> list[str]:
    """Phương án `right` từ thư viện approved cho 1 từ khả nghi. Khớp `wrong`
    (casefold): exact `wrong == word` ưu tiên trên chứa/được-chứa. Trả list
    `right` unique, giữ thứ tự count giảm dần (list_corrections đã sort count
    DESC — sorted theo tier ổn định nên không phá thứ tự đó). Lỗi DB → []
    (never-fail)."""
    key = word.strip().casefold()
    if not key:
        return []
    try:
        rows = db.list_corrections(status="approved")
    except Exception:  # noqa: BLE001 — thư viện hỏng không được chặn gợi ý
        return []
    scored: list[tuple[int, str]] = []
    for row in rows:
        wrong = row["wrong"].strip().casefold()
        if wrong == key:
            tier = 0
        elif key in wrong or wrong in key:
            tier = 1
        else:
            continue
        scored.append((tier, row["right"].strip()))
    seen: set[str] = set()
    out: list[str] = []
    for _, right in sorted(scored, key=lambda s: s[0]):
        cf = right.casefold()
        if not right or cf in seen:
            continue
        seen.add(cf)
        out.append(right)
        if len(out) >= limit:
            break
    return out


def top_pairs(
    limit: int = 20, regions: Collection[str] = (), domains: Collection[str] = ()
) -> list[tuple[str, str]]:
    """Cặp (sai → đúng) approved gặp nhiều nhất — few-shot cho prompt pass 2.
    Tag khớp `regions`/`domains` xếp trước (stable) RỒI mới cắt limit. Lỗi DB →
    [] (never-fail, US-803 AC2)."""
    try:
        rows = db.list_corrections(status="approved")
    except Exception:  # noqa: BLE001
        return []
    eligible = _eligible_tags(regions, domains)
    ranked = sorted(rows, key=lambda r: r["tag"] not in eligible)
    return [(r["wrong"], r["right"]) for r in ranked[:limit]]


# ── Mine từ vựng cá nhân theo người nói (bias ASR phiên sau) ───────────────
# Từ đệm/filler hay gặp trong họp — không đáng đưa vào bias.
STOPWORDS = frozenset({
    "ok", "oke", "okay", "okie", "uh", "um", "ah", "eh", "hmm", "vs",
    "ko", "ntn", "thanks", "thank", "hello", "hi", "yeah", "yes", "no",
    "the", "and", "for",
})
MIN_TERM_COUNT = 2
MAX_TERMS_PER_SPEAKER = 30


def _salient_terms(text: str, known: set[str]) -> Counter[str]:
    """Đếm term đáng bias: từ ASCII thuần ≥3 ký tự (thuật ngữ Anh không dấu),
    từ Viết Hoa ≥2 ký tự, hoặc từ đã có trong thư viện approved (`known`,
    casefold — thắng cả stoplist). Token strip dấu câu mép ngoài như `_clean`.
    Đếm theo casefold để gộp biến thể hoa-thường; key trả về là casing gặp
    đầu tiên (đơn giản, đủ ổn định cho bias)."""
    counts: Counter[str] = Counter()
    first_seen: dict[str, str] = {}
    for raw in text.split():
        tok = raw.strip(string.punctuation)
        cf = tok.casefold()
        if not tok or tok.isnumeric():
            continue
        if cf not in known:
            if cf in STOPWORDS:
                continue
            ascii_term = tok.isascii() and tok.isalpha() and len(tok) >= 3
            capitalized = len(tok) >= 2 and tok[0].isupper()
            if not (ascii_term or capitalized):
                continue
        first_seen.setdefault(cf, tok)
        counts[cf] += 1
    return Counter({first_seen[cf]: n for cf, n in counts.items()})


def _attested_vocab(data: dict) -> set[str] | None:
    """Tập token (casefold) được CHỨNG THỰC: có trong `raw_text` (Whisper thật
    sự nghe được) hoặc `edited_text` (user sửa tay). `segments` lưu bản SAU pass
    2 — LLM đôi khi bịa thuật ngữ ở chỗ nghe không rõ (VD 'doanh thu'→'budget'),
    không được để cụm bịa đó bootstrap vào bias phiên sau (vòng tự khuếch đại).
    Trả None nếu không có nguồn chứng thực nào → caller không lọc (giữ hành vi cũ,
    never-fail cho transcript cũ thiếu raw_text)."""
    sources = [str(data[k]) for k in ("raw_text", "edited_text") if data.get(k)]
    if not sources:
        return None
    return {tok.casefold() for src in sources for tok in _salient_terms(src, set())}


def mine_speaker_terms(transcript_id: str) -> int:
    """Mine term đặc trưng theo người nói từ 1 transcript đã gán tên: gom text
    segment theo speaker_map (bỏ cụm chưa gán), CHỈ giữ term được chứng thực
    trong raw_text/edited_text (chặn term pass 2 bịa — xem `_attested_vocab`),
    count ≥ MIN_TERM_COUNT hoặc đã có trong thư viện approved, top
    MAX_TERMS_PER_SPEAKER mỗi người, ghi đè qua db.replace_speaker_terms. Trả
    tổng số hàng ghi; transcript không có/không segment → 0. Never-fail (hook
    không được chặn API)."""
    try:
        data = db.read_transcript(transcript_id)
        if not data or not data.get("segments"):
            return 0
        smap = {k: v for k, v in (data.get("speaker_map") or {}).items() if v}
        texts: dict[str, list[str]] = {}
        for seg in data["segments"]:
            sid = smap.get(str(seg.get("spk")))
            if sid:
                texts.setdefault(sid, []).append(seg.get("text", ""))
        known = {
            r["right"].strip().casefold() for r in db.list_corrections(status="approved")
        }
        attested = _attested_vocab(data)
        rows: list[tuple[str, str, int]] = []
        for sid, chunks in texts.items():
            counts = _salient_terms(" ".join(chunks), known)
            kept = [(term, n) for term, n in counts.most_common()
                    if (n >= MIN_TERM_COUNT or term.casefold() in known)
                    and (attested is None or term.casefold() in attested)]
            rows += [(sid, term, n) for term, n in kept[:MAX_TERMS_PER_SPEAKER]]
        return db.replace_speaker_terms(transcript_id, rows)
    except Exception:  # noqa: BLE001
        return 0


# ── Seed lexicon vùng miền + slang + cập nhật remote opt-in (US-804/815) ───
LEXICON_DIR = Path(__file__).resolve().parent / "data" / "lexicon"
SEED_REGIONS = ("bac", "trung", "nam", "en_accent", SLANG_TAG)
FETCH_TIMEOUT_S = 15.0


class LexiconError(Exception):
    """Nguồn lexicon remote lỗi (mạng/checksum/format) — DB chưa bị ghi gì."""


def _validate_entries(data: object) -> list[dict]:
    """Ép schema [{"wrong","right","tag"?}] — wrong/right phải là str non-empty."""
    if not isinstance(data, list):
        raise LexiconError("Format thư viện sai: cần danh sách các cặp {wrong, right}")
    for e in data:
        wrong = e.get("wrong") if isinstance(e, dict) else None
        right = e.get("right") if isinstance(e, dict) else None
        if not (isinstance(wrong, str) and wrong.strip()
                and isinstance(right, str) and right.strip()):
            raise LexiconError(f"Entry lexicon hỏng (thiếu wrong/right): {e!r:.80}")
    return data


def import_seed(region: str) -> int:
    """Nạp lexicon vùng `region` vào corrections (source='seed', tag=region,
    status='approved', count=1). INSERT OR IGNORE — không đè/cộng count entry
    user trùng cặp; chạy lại không nhân đôi. Trả số entry thêm mới."""
    raw = (LEXICON_DIR / f"{region}.json").read_text(encoding="utf-8")
    entries = _validate_entries(json.loads(raw))
    return db.add_corrections_ignore(
        [(e["wrong"].strip(), e["right"].strip(), region) for e in entries], source="seed"
    )


def remove_seed(region: str) -> int:
    """Gỡ toàn bộ entry seed của 1 vùng — không đụng entry user/remote."""
    return db.delete_corrections(source="seed", tag=region)


def fetch_remote_lexicon(url: str) -> list[dict]:
    """Tải lexicon remote theo thiết kế 2 file: GET {url} (data JSON, raw bytes)
    + GET {url}.sha256 (hex sha256 của raw bytes). Verify xong mới trả entries —
    lỗi mạng/checksum/format → LexiconError, DB không bị ghi dở dang."""
    try:
        data = httpx.get(url, timeout=FETCH_TIMEOUT_S, follow_redirects=True)
        digest = httpx.get(f"{url}.sha256", timeout=FETCH_TIMEOUT_S, follow_redirects=True)
        data.raise_for_status()
        digest.raise_for_status()
    except httpx.HTTPError as exc:
        raise LexiconError(f"Không tải được thư viện từ nguồn: {exc}") from exc
    parts = digest.text.split()  # chấp nhận cả format `<hex>  <tên file>` của sha256sum
    expected = parts[0].lower() if parts else ""
    if hashlib.sha256(data.content).hexdigest() != expected:
        raise LexiconError("Checksum không khớp — file nguồn có thể hỏng hoặc bị thay đổi")
    try:
        parsed = json.loads(data.content)
    except ValueError as exc:
        raise LexiconError("File thư viện không phải JSON hợp lệ") from exc
    return _validate_entries(parsed)


def import_remote(entries: list[dict]) -> int:
    """Nạp entries remote đã verify (source='remote', approved). Trả số thêm mới."""
    rows = [
        (e["wrong"].strip(), e["right"].strip(), str(e.get("tag") or "").strip())
        for e in entries
    ]
    return db.add_corrections_ignore(rows, source="remote")


# ── Lexicon từ vựng NGÀNH (Lớp C) ──────────────────────────────────────────
# Tag ngành prefix "dom:" tách bạch hẳn tag vùng miền (bac|trung|nam|...) trong
# cùng cột `corrections.tag`: một cặp có thể trùng term vùng miền lẫn ngành mà
# không lẫn tier ưu tiên. Seed đặt ở data/lexicon/domain/{domain}.json.
DOMAIN_DIR = LEXICON_DIR / "domain"
DOMAIN_TAG_PREFIX = "dom:"
# Ngành hỗ trợ sẵn seed — Lớp B chỉ dự đoán trong tập này; research (Lớp C giai
# đoạn 2) bổ sung term mới vào chính các tag này.
DOMAINS = ("devops", "finance", "medical", "legal", "marketing")


def domain_tag(domain: str) -> str:
    """Tên tag lưu DB cho 1 ngành (prefix để không lẫn tag vùng miền)."""
    return f"{DOMAIN_TAG_PREFIX}{domain.strip()}"


def is_domain_tag(tag: str) -> bool:
    return tag.startswith(DOMAIN_TAG_PREFIX)


def import_domain_seed(domain: str) -> int:
    """Nạp lexicon ngành `domain` vào corrections (source='seed',
    tag='dom:{domain}', approved). INSERT OR IGNORE như import_seed vùng miền —
    chạy lại không nhân đôi. Trả số entry thêm mới."""
    raw = (DOMAIN_DIR / f"{domain}.json").read_text(encoding="utf-8")
    entries = _validate_entries(json.loads(raw))
    return db.add_corrections_ignore(
        [(e["wrong"].strip(), e["right"].strip(), domain_tag(domain)) for e in entries],
        source="seed",
    )


def remove_domain_seed(domain: str) -> int:
    """Gỡ toàn bộ entry seed của 1 ngành — không đụng entry user/remote/trend."""
    return db.delete_corrections(source="seed", tag=domain_tag(domain))


def ensure_domains(domains: Collection[str]) -> int:
    """Nạp seed lexicon cho các ngành đang active (idempotent, INSERT OR IGNORE).
    Gọi khi Lớp B đoán ra ngành → term ngành có mặt trong thư viện để build_bias/
    top_pairs dùng. Bỏ ngành lạ/file hỏng. Never-fail. Trả tổng entry thêm mới."""
    total = 0
    for d in domains:
        if d not in DOMAINS:
            continue
        try:
            total += import_domain_seed(d)
        except (OSError, ValueError, LexiconError):
            continue
    return total


def domain_seed_pairs(domain: str) -> list[tuple[str, str]]:
    """Cặp (wrong, right) trong seed 1 ngành — Lớp B dùng cả 2 dạng làm tín hiệu
    (khớp được text thô Whisper-phiên-sai LẪN text đã sửa). File thiếu/hỏng → []
    (never-fail, dự đoán ngành không được sập)."""
    try:
        raw = (DOMAIN_DIR / f"{domain}.json").read_text(encoding="utf-8")
        entries = _validate_entries(json.loads(raw))
    except (OSError, ValueError, LexiconError):
        return []
    return [(e["wrong"].strip(), e["right"].strip()) for e in entries]
