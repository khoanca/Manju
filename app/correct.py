"""Pass 2: gọi LLM sửa thuật ngữ tiếng Anh bị phiên âm sai.

Whisper hay phiên âm thuật ngữ tiếng Anh thành âm tiết Việt ("cu bơ nét" →
"Kubernetes"). LLM đọc lại text, chỉ sửa các cụm nghi vấn dựa trên glossary,
giữ nguyên phần còn lại.

Hai backend (FR-6):
- **ollama** — LLM local, miễn phí, mặc định.
- **cloud** — Edge Function llm-correct (Supabase), TRẢ CREDIT theo usage;
  bật khi CLOUD_BILLING on + user đăng nhập + chọn trong Settings.

Contract never-fail: transcription không bao giờ fail vì pass 2. Lỗi transient
(mạng, server tắt...) → trả text gốc, ok=False. Riêng 402 hết credit →
blocked=True — trạng thái RIÊNG, UI phải hiện, KHÔNG âm thầm fallback Ollama
(US-606).
"""
from __future__ import annotations

import difflib
import os
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass

import httpx

from app import cloud, db

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "gemma4:e4b")

# Tên model pass 2 phía cloud — chỉ để ghi metadata; model THẬT do server đặt
# (secret OPENROUTER_MODEL của Edge Function). Đổi server thì đổi env này theo.
CLOUD_LLM_MODEL = os.environ.get("CLOUD_LLM_MODEL", "anthropic/claude-haiku-4.5")

CHUNK_CHARS = 1800
TIMEOUT_S = 180
# Chunk sửa xong khác gốc quá mức này → coi là LLM "sửa quá tay", giữ bản gốc.
MIN_SIMILARITY = 0.6


def llm_backend() -> str:
    """'cloud' khi CLOUD_BILLING on + đã đăng nhập + user chọn trong Settings;
    ngược lại 'ollama' (mặc định, miễn phí)."""
    if (
        cloud.cloud_billing_enabled()
        and db.get_setting("llm_backend") == "cloud"
        and cloud.session_info() is not None
    ):
        return "cloud"
    return "ollama"


def llm_model_name() -> str:
    """Tên model pass 2 đang hiệu lực — ghi vào metadata transcript."""
    return CLOUD_LLM_MODEL if llm_backend() == "cloud" else OLLAMA_MODEL


@dataclass(frozen=True)
class LlmOpts:
    """Tham số 1 lượt gọi LLM sửa thuật ngữ. Mặc định cho full-text (upload);
    live mode dùng num_ctx nhỏ + timeout ngắn để kịp subtitle."""

    glossary: str = ""
    context: str = ""  # vài câu trước đó — chỉ backend cloud dùng
    num_ctx: int = 8192  # chỉ backend Ollama dùng
    timeout: float = TIMEOUT_S


@dataclass(frozen=True)
class CorrectionResult:
    """Kết quả pass 2. blocked=True = 402 hết credit (KHÔNG phải lỗi transient):
    text là bản gốc (hoặc sửa dở với full-text), UI phải báo + mời nạp."""

    text: str
    ok: bool
    blocked: bool = False
    credits_spent: float = 0.0  # đơn vị credit (wire milli-credit ÷ 1000)
    balance: float | None = None


_SYSTEM_PROMPT = (
    # Copy TS trong supabase/functions/llm-correct/handler.ts — sửa 1 nơi PHẢI
    # sửa nơi kia (backend cloud dùng bản server-side, Ollama dùng bản này).
    "Bạn là công cụ soát lỗi transcript cuộc họp tiếng Việt có pha thuật ngữ "
    "tiếng Anh. Nhiệm vụ DUY NHẤT: tìm những cụm từ bị phiên âm sai từ tiếng "
    "Anh sang âm tiết tiếng Việt và thay bằng đúng từ tiếng Anh gốc. "
    "Cụm nghi vấn đọc lên giống phát âm của thuật ngữ nào trong danh sách thì "
    "thay bằng đúng thuật ngữ đó (VD: 'cu bơ nét ét' → 'Kubernetes', 'đíp lôi' "
    "→ 'deploy'), không diễn đạt lại bằng từ tiếng Việt khác. "
    "Giữ nguyên toàn bộ nội dung khác: không tóm tắt, không thêm bớt câu, "
    "không sửa văn phong, không sửa chính tả tiếng Việt thông thường. "
    "Trả về đúng phần text đã sửa, không lời giải thích, không markdown."
)


def _prompt_for(chunk: str, glossary: str, context: str = "") -> str:
    parts = []
    if glossary:
        parts.append(f"Danh sách thuật ngữ / tên riêng cần nhận đúng: {glossary}")
    if context:
        parts.append(
            "Các câu ngay trước đó trong cuộc họp (chỉ để hiểu ngữ cảnh, "
            "KHÔNG đưa vào kết quả):\n" + context
        )
    parts.append("Text cần soát:\n" + chunk)
    return "\n\n".join(parts)


def _split_chunks(text: str, size: int = CHUNK_CHARS) -> list[str]:
    """Cắt theo ranh giới câu, mỗi chunk ~size ký tự."""
    sentences = re.split(r"(?<=[.!?…])\s+", text)
    chunks: list[str] = []
    buf = ""
    for s in sentences:
        if buf and len(buf) + len(s) + 1 > size:
            chunks.append(buf)
            buf = s
        else:
            buf = f"{buf} {s}".strip() if buf else s
    if buf:
        chunks.append(buf)
    return chunks


def _clean_output(raw: str) -> str:
    text = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL)
    text = text.strip()
    # Bỏ code fence nếu LLM lỡ bọc kết quả.
    m = re.fullmatch(r"```(?:\w+)?\n(.*?)\n?```", text, flags=re.DOTALL)
    if m:
        text = m.group(1).strip()
    return text


def _guard(chunk: str, fixed: str) -> str:
    """LLM trả rỗng hoặc khác gốc quá mức ("sửa quá tay") → giữ bản gốc."""
    if not fixed:
        return chunk
    if difflib.SequenceMatcher(None, chunk, fixed).ratio() < MIN_SIMILARITY:
        return chunk
    return fixed


def _correct_chunk(client: httpx.Client, chunk: str, opts: LlmOpts) -> str:
    resp = client.post(
        f"{OLLAMA_URL}/api/chat",
        json={
            "model": OLLAMA_MODEL,
            "stream": False,
            "think": False,
            # num_ctx cố định: không kế thừa OLLAMA_CONTEXT_LENGTH của server
            # (context quá lớn làm KV cache phình, model tràn RAM → cực chậm).
            "options": {"temperature": 0.1, "num_ctx": opts.num_ctx},
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": _prompt_for(chunk, opts.glossary)},
            ],
        },
        timeout=opts.timeout,
    )
    resp.raise_for_status()
    return _guard(chunk, _clean_output(resp.json()["message"]["content"]))


def _correct_chunk_cloud(chunk: str, opts: LlmOpts) -> tuple[str, float, float]:
    """1 chunk qua Edge Function. Trả (text đã guard, credit đã trừ, số dư) —
    đơn vị credit. Raise cloud.InsufficientCredits khi 402 (caller xử lý)."""
    data = cloud.llm_correct(
        {
            "text": chunk,
            "glossary": opts.glossary,
            "context": opts.context,
            "requestId": str(uuid.uuid4()),  # server idempotent theo id này
        },
        timeout=opts.timeout,
    )
    credits = data.get("credits", {})
    return (
        _guard(chunk, _clean_output(str(data.get("text", "")))),
        float(credits.get("spentCredits", 0)) / 1000,
        float(credits.get("balanceCredits", 0)) / 1000,
    )


def correct_sentence(text: str, opts: LlmOpts) -> CorrectionResult:
    """Sửa 1 câu (live mode) — caller đặt num_ctx nhỏ + timeout ngắn cho kịp
    subtitle. Backend theo llm_backend(); cloud 402 → blocked=True, KHÔNG
    fallback Ollama (user đã chọn trả phí — âm thầm đổi chất lượng là lừa)."""
    text = text.strip()
    if not text:
        return CorrectionResult(text=text, ok=False)
    if llm_backend() == "cloud":
        return _sentence_cloud(text, opts)
    try:
        with httpx.Client() as client:
            return CorrectionResult(text=_correct_chunk(client, text, opts), ok=True)
    except Exception:  # noqa: BLE001 — Ollama tắt/timeout → dùng bản gốc
        return CorrectionResult(text=text, ok=False)


def _sentence_cloud(text: str, opts: LlmOpts) -> CorrectionResult:
    try:
        fixed, spent, balance = _correct_chunk_cloud(text, opts)
        return CorrectionResult(text=fixed, ok=True, credits_spent=spent, balance=balance)
    except cloud.InsufficientCredits as exc:
        return CorrectionResult(text=text, ok=False, blocked=True, balance=exc.balance)
    except Exception:  # noqa: BLE001 — transient (mạng/502) → bản gốc, không blocked
        return CorrectionResult(text=text, ok=False)


def correct_text(
    text: str,
    glossary: str = "",
    on_progress: Callable[[float], None] | None = None,
) -> CorrectionResult:
    """Pass 2 full-text (upload). ok=True khi chạy trót lọt; blocked=True khi
    hết credit giữa chừng (text = phần đã sửa + phần còn lại nguyên bản)."""
    text = text.strip()
    if not text:
        return CorrectionResult(text=text, ok=False)
    chunks = _split_chunks(text)
    opts = LlmOpts(glossary=glossary)
    if llm_backend() == "cloud":
        return _text_cloud(chunks, opts, on_progress)
    fixed_parts: list[str] = []
    try:
        with httpx.Client() as client:
            for i, chunk in enumerate(chunks):
                fixed_parts.append(_correct_chunk(client, chunk, opts))
                if on_progress:
                    on_progress((i + 1) / len(chunks))
    except Exception:  # noqa: BLE001 — Ollama tắt/timeout → dùng bản gốc
        return CorrectionResult(text=text, ok=False)
    return CorrectionResult(text=" ".join(fixed_parts).strip(), ok=True)


def _text_cloud(
    chunks: list[str],
    opts: LlmOpts,
    on_progress: Callable[[float], None] | None,
) -> CorrectionResult:
    """Loop chunk qua cloud, cộng dồn credit. 402 giữa chừng → dừng ngay các
    chunk còn lại (khỏi tốn latency), giữ phần đã sửa + phần còn lại nguyên."""
    fixed_parts: list[str] = []
    spent_total = 0.0
    balance: float | None = None
    for i, chunk in enumerate(chunks):
        try:
            fixed, spent, balance = _correct_chunk_cloud(chunk, opts)
        except cloud.InsufficientCredits as exc:
            text = " ".join([*fixed_parts, *chunks[i:]]).strip()
            return CorrectionResult(
                text=text, ok=False, blocked=True,
                credits_spent=spent_total, balance=exc.balance,
            )
        except Exception:  # noqa: BLE001 — transient → giữ nguyên chunk này
            fixed, spent = chunk, 0.0
        fixed_parts.append(fixed)
        spent_total += spent
        if on_progress:
            on_progress((i + 1) / len(chunks))
    return CorrectionResult(
        text=" ".join(fixed_parts).strip(), ok=True,
        credits_spent=spent_total, balance=balance,
    )
