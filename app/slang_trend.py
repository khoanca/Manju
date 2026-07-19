"""Tổng hợp slang/teencode MXH đang hot → nhập thư viện chờ duyệt (US-816/817).

Hai loại nguồn, cùng một đường trích xuất LLM:
1. LLM cloud (OpenRouter qua `correct.chat_once`) tự liệt kê trend (US-816);
2. Trang web PUBLIC tổng hợp trend MXH (US-817) — chỉ trang không cần đăng
   nhập, tôn trọng robots.txt, KHÔNG né anti-bot: bị chặn/lỗi thì skip nguồn.
   TikTok/FB/X trực tiếp nằm ngoài phạm vi (API đóng/tường đăng nhập).

Mọi entry nhập với source='trend', status='pending' — KHÔNG tự vào bias
ASR/pass 2 cho tới khi user duyệt trong Settings → Thư viện từ. Validate lỏng
kiểu skip-not-raise (khác `_validate_entries` chặt của nguồn remote có
checksum): entry hỏng thì bỏ, đếm vào `skipped` cho user thấy.
"""
from __future__ import annotations

import json
import urllib.parse
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib import robotparser

import httpx

from app import correct, db
from app.corrections import SLANG_TAG

TREND_SOURCE = "trend"
MAX_ENTRIES = 40   # chặn LLM trả danh sách dài bất thường
MAX_WORDS = 4      # khớp MAX_SPAN của corrections — dài hơn là câu, không phải cặp
MAX_CHARS = 40
LLM_TIMEOUT_S = 90.0
FETCH_TIMEOUT_S = 10.0
MAX_PAGE_CHARS = 6000  # đủ cho LLM đọc phần chính bài viết, không tràn context
USER_AGENT = "ManjuLexiconBot/1.0 (local meeting transcriber; slang lexicon updater)"
# Trang public không cần đăng nhập (đã verify 200 lúc implement 2026-07-20);
# user thêm bài báo trend qua setting `slang_sources` (URL cách nhau khoảng trắng).
DEFAULT_SOURCES = ("https://vi.wikipedia.org/wiki/Tiếng_lóng",)

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


# ── US-817: web adapter best-effort ────────────────────────────────────────
class _TextExtractor(HTMLParser):
    """Rút text thô từ HTML bằng stdlib — đủ cho LLM đọc, không cần DOM chuẩn."""

    _SKIP = frozenset({"script", "style", "noscript"})

    def __init__(self) -> None:
        super().__init__()
        self.chunks: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._SKIP:
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if not self._skip_depth and data.strip():
            self.chunks.append(data.strip())


def _html_to_text(html: str) -> str:
    parser = _TextExtractor()
    parser.feed(html)
    return " ".join(parser.chunks)[:MAX_PAGE_CHARS]


def _robots_ok(url: str) -> bool:
    """robots.txt cho phép UA của app đọc url? Disallow rõ ràng → skip nguồn
    (không né). Không tải được robots.txt → coi như cho phép (chuẩn de-facto)."""
    parts = urllib.parse.urlsplit(url)
    rp = robotparser.RobotFileParser(f"{parts.scheme}://{parts.netloc}/robots.txt")
    try:
        rp.read()
    except OSError:
        return True
    return rp.can_fetch(USER_AGENT, url)


def _fetch_page(url: str) -> str | None:
    """GET public — không cookie, không đăng nhập, không retry. Non-200 /
    timeout / lỗi mạng → None (nguồn không muốn bot đọc thì tôn trọng)."""
    try:
        resp = httpx.get(url, timeout=FETCH_TIMEOUT_S, follow_redirects=True,
                         headers={"User-Agent": USER_AGENT})
    except Exception:  # noqa: BLE001 — mọi lỗi mạng đều chỉ là skip nguồn
        return None
    if resp.status_code != 200:
        return None
    return _html_to_text(resp.text)


def _sources() -> tuple[str, ...]:
    """URL nguồn web: setting `slang_sources` (cách nhau khoảng trắng/xuống
    dòng) — rỗng thì dùng DEFAULT_SOURCES."""
    try:
        raw = db.get_setting("slang_sources", "") or ""
    except Exception:  # noqa: BLE001 — DB hỏng thì dùng mặc định
        raw = ""
    return tuple(raw.split()) or DEFAULT_SOURCES


def web_digest() -> tuple[list[tuple[str, str]], int, int, int]:
    """Nguồn 2: đọc từng trang public → cùng prompt trích xuất như llm_digest.
    Trả (pairs, entry_skipped, sources_ok, sources_failed)."""
    pairs: list[tuple[str, str]] = []
    skipped = ok = failed = 0
    for url in _sources():
        try:
            page = _fetch_page(url) if _robots_ok(url) else None
            if not page:
                failed += 1
                continue
            raw = correct.chat_once(
                _EXTRACT_SYSTEM, _page_prompt(page), timeout=LLM_TIMEOUT_S
            )
            rows, bad = _parse_entries(raw)
            pairs += rows
            skipped += bad
            ok += 1
        except Exception:  # noqa: BLE001 — nguồn hỏng chỉ là skip
            failed += 1
    return pairs, skipped, ok, failed


def _page_prompt(page_text: str) -> str:
    return (
        "Trích các cặp từ lóng/teencode đang thịnh hành từ nội dung bài viết "
        "sau (chỉ lấy từ được NÓI thành tiếng):\n\n" + page_text
    )


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
    w_pairs, w_skip, w_ok, w_fail = web_digest()
    pairs += w_pairs
    skipped += w_skip
    ok += w_ok
    failed += w_fail
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
