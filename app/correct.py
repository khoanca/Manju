"""Pass 2: gọi LLM sửa thuật ngữ tiếng Anh bị phiên âm sai.

Whisper hay phiên âm thuật ngữ tiếng Anh thành âm tiết Việt ("cu bơ nét" →
"Kubernetes"). LLM đọc lại text, chỉ sửa các cụm nghi vấn dựa trên glossary,
giữ nguyên phần còn lại. Mọi lỗi (server không chạy, timeout...) đều trả về
text gốc — pass 2 không bao giờ làm fail job.

Hai backend: có OPENROUTER_API_KEY thì dùng Claude qua OpenRouter (hiểu ngữ
cảnh tốt hơn hẳn model 4B local — sửa được cả câu nát mà gemma bó tay), lỗi
hoặc không có key thì rơi về Ollama local.
"""
from __future__ import annotations

import difflib
import os
import re
from collections.abc import Callable

import httpx

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "gemma4:e4b")

OPENROUTER_URL = os.environ.get("OPENROUTER_URL", "https://openrouter.ai/api/v1")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
# Haiku 4.5: nhanh + rẻ ($1/$5 per MTok), đủ cho việc sửa thuật ngữ có ngữ cảnh.
OPENROUTER_MODEL = os.environ.get("OPENROUTER_MODEL", "anthropic/claude-haiku-4.5")


def openrouter_enabled() -> bool:
    return bool(OPENROUTER_API_KEY)


def llm_model_name() -> str:
    """Tên model pass 2 đang hiệu lực — ghi vào metadata transcript."""
    return OPENROUTER_MODEL if openrouter_enabled() else OLLAMA_MODEL

CHUNK_CHARS = 1800
TIMEOUT_S = 180
# Chunk sửa xong khác gốc quá mức này → coi là LLM "sửa quá tay", giữ bản gốc.
MIN_SIMILARITY = 0.6

_SYSTEM_PROMPT = (
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


def _correct_chunk(
    client: httpx.Client,
    chunk: str,
    glossary: str,
    num_ctx: int = 8192,
    timeout: float = TIMEOUT_S,
) -> str:
    resp = client.post(
        f"{OLLAMA_URL}/api/chat",
        json={
            "model": OLLAMA_MODEL,
            "stream": False,
            "think": False,
            # num_ctx cố định: không kế thừa OLLAMA_CONTEXT_LENGTH của server
            # (context quá lớn làm KV cache phình, model tràn RAM → cực chậm).
            "options": {"temperature": 0.1, "num_ctx": num_ctx},
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": _prompt_for(chunk, glossary)},
            ],
        },
        timeout=timeout,
    )
    resp.raise_for_status()
    return _guard(chunk, _clean_output(resp.json()["message"]["content"]))


def _correct_chunk_openrouter(
    client: httpx.Client,
    chunk: str,
    glossary: str,
    context: str = "",
    timeout: float = 30.0,
) -> str:
    # Không set temperature: các model Claude đời mới từ chối sampling params.
    resp = client.post(
        f"{OPENROUTER_URL}/chat/completions",
        headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}"},
        json={
            "model": OPENROUTER_MODEL,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": _prompt_for(chunk, glossary, context)},
            ],
        },
        timeout=timeout,
    )
    resp.raise_for_status()
    return _guard(chunk, _clean_output(resp.json()["choices"][0]["message"]["content"]))


def _guard(chunk: str, fixed: str) -> str:
    """LLM trả rỗng hoặc khác gốc quá mức ("sửa quá tay") → giữ bản gốc."""
    if not fixed:
        return chunk
    if difflib.SequenceMatcher(None, chunk, fixed).ratio() < MIN_SIMILARITY:
        return chunk
    return fixed


def correct_sentence(
    text: str,
    glossary: str = "",
    context: str = "",
    num_ctx: int = 2048,
    timeout: float = 20.0,
) -> tuple[str, bool]:
    """Sửa 1 câu (live mode): timeout ngắn để kịp subtitle.

    `context` = vài câu trước đó — Claude dựa vào mạch cuộc họp để đoán đúng
    thuật ngữ (Ollama local bỏ qua để đỡ tốn ctx). Trả (câu đã sửa, True) nếu
    chạy trót lọt, ngược lại (câu gốc, False). Cùng contract never-fail như
    correct_text.
    """
    text = text.strip()
    if not text:
        return text, False
    try:
        with httpx.Client() as client:
            if openrouter_enabled():
                try:
                    return _correct_chunk_openrouter(client, text, glossary, context, timeout), True
                except Exception:  # noqa: BLE001 — mạng rớt/hết credit → thử LLM local
                    pass
            return _correct_chunk(client, text, glossary, num_ctx, timeout), True
    except Exception:  # noqa: BLE001 — Ollama tắt/timeout → dùng bản gốc
        return text, False


def correct_text(
    text: str,
    glossary: str = "",
    on_progress: Callable[[float], None] | None = None,
) -> tuple[str, bool]:
    """Trả (text đã sửa, True) nếu pass 2 chạy trót lọt, ngược lại (text gốc, False)."""
    text = text.strip()
    if not text:
        return text, False
    chunks = _split_chunks(text)
    fixed_parts: list[str] = []
    try:
        with httpx.Client() as client:
            for i, chunk in enumerate(chunks):
                if openrouter_enabled():
                    try:
                        fixed = _correct_chunk_openrouter(client, chunk, glossary, timeout=TIMEOUT_S)
                    except Exception:  # noqa: BLE001 — rơi về LLM local
                        fixed = _correct_chunk(client, chunk, glossary)
                else:
                    fixed = _correct_chunk(client, chunk, glossary)
                fixed_parts.append(fixed)
                if on_progress:
                    on_progress((i + 1) / len(chunks))
    except Exception:  # noqa: BLE001 — Ollama tắt/timeout → dùng bản gốc
        return text, False
    return " ".join(fixed_parts).strip(), True
