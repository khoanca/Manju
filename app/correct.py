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
import json
import os
import re
from collections.abc import Callable
from dataclasses import dataclass

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


@dataclass(frozen=True)
class LlmOpts:
    """Tham số 1 lượt gọi LLM sửa thuật ngữ. Mặc định cho full-text (upload);
    live mode dùng num_ctx nhỏ + timeout ngắn để kịp subtitle."""

    glossary: str = ""
    context: str = ""  # ngữ cảnh đang diễn ra (topic + câu gần nhất) — cả 2 backend dùng (US-805)
    num_ctx: int = 8192  # chỉ backend Ollama dùng
    timeout: float = TIMEOUT_S
    pairs: tuple[tuple[str, str], ...] = ()  # few-shot (sai → đúng) từ thư viện (US-803)
    uncertain: tuple[str, ...] = ()  # cụm word-confidence thấp từ ASR (US-812)

_SYSTEM_PROMPT = (
    "Bạn là công cụ soát lỗi transcript cuộc họp tiếng Việt có pha thuật ngữ "
    "tiếng Anh. Nhiệm vụ DUY NHẤT: tìm những cụm từ bị phiên âm sai từ tiếng "
    "Anh sang âm tiết tiếng Việt và thay bằng đúng từ tiếng Anh gốc. "
    "Cụm nghi vấn đọc lên giống phát âm của thuật ngữ nào trong danh sách thì "
    "thay bằng đúng thuật ngữ đó (VD: 'cu bơ nét ét' → 'Kubernetes', 'đíp lôi' "
    "→ 'deploy'), không diễn đạt lại bằng từ tiếng Việt khác. "
    "CHỈ thay khi cụm gốc PHÁT ÂM gần giống thuật ngữ. Nếu không chắc chắn về "
    "mặt phát âm, GIỮ NGUYÊN cụm gốc — tuyệt đối không đoán một thuật ngữ chỉ vì "
    "nó hợp ngữ cảnh (thà để cụm khó đọc còn hơn thay bằng từ sai nghĩa). "
    "Giữ nguyên toàn bộ nội dung khác: không tóm tắt, không thêm bớt câu, "
    "không sửa văn phong, không sửa chính tả tiếng Việt thông thường. "
    "Trả về đúng phần text đã sửa, không lời giải thích, không markdown."
)


def _prompt_for(
    chunk: str,
    glossary: str,
    context: str = "",
    pairs: tuple[tuple[str, str], ...] = (),
    uncertain: tuple[str, ...] = (),
) -> str:
    parts = []
    if pairs:
        # Few-shot từ thư viện đã duyệt: LLM sửa nhất quán các lỗi hay gặp.
        listing = "\n".join(f"- {w} → {r}" for w, r in pairs)
        parts.append("Các cặp đã biết (sai → đúng):\n" + listing)
    if glossary:
        parts.append(f"Danh sách thuật ngữ / tên riêng cần nhận đúng: {glossary}")
    if context:
        parts.append(
            "Các câu ngay trước đó trong cuộc họp (chỉ để hiểu ngữ cảnh, "
            "KHÔNG đưa vào kết quả):\n" + context
        )
    if uncertain:
        # US-812: ASR tự báo chỗ nghe không chắc — LLM soát mạnh tay đúng chỗ.
        parts.append("Các cụm nghe không rõ, ưu tiên soát kỹ: " + ", ".join(uncertain))
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


def _chat_ollama(client: httpx.Client, system: str, user: str, opts: LlmOpts) -> str:
    """1 lượt chat Ollama, trả text đã clean. Dùng chung cho sửa câu + tóm tắt topic."""
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
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        },
        timeout=opts.timeout,
    )
    resp.raise_for_status()
    return _clean_output(resp.json()["message"]["content"])


def _chat_openrouter(client: httpx.Client, system: str, user: str, opts: LlmOpts) -> str:
    """1 lượt chat OpenRouter, trả text đã clean.

    Không set temperature: các model Claude đời mới từ chối sampling params.
    """
    resp = client.post(
        f"{OPENROUTER_URL}/chat/completions",
        headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}"},
        json={
            "model": OPENROUTER_MODEL,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        },
        timeout=opts.timeout,
    )
    resp.raise_for_status()
    return _clean_output(resp.json()["choices"][0]["message"]["content"])


def chat_once(system: str, user: str, timeout: float = TIMEOUT_S) -> str:
    """1 lượt chat OpenRouter cho tác vụ ngoài pass 2 (US-816 trend digest).

    KHÔNG fallback Ollama — tác vụ cần kiến thức trend/thế giới mà model 4B
    local không có. Lỗi mạng/API raise cho caller xử lý (khác contract
    never-fail của pass 2); caller phải check `openrouter_enabled()` trước.
    """
    opts = LlmOpts(timeout=timeout)
    with httpx.Client() as client:
        return _chat_openrouter(client, system, user, opts)


_SUGGEST_SYSTEM_PROMPT = (
    "Bạn giúp đoán lại một từ mà ASR nghe không rõ trong transcript cuộc họp kỹ "
    "thuật tiếng Việt pha thuật ngữ tiếng Anh. Dựa vào ngữ cảnh câu, đưa các cách "
    "hiểu khả dĩ nhất của từ đó — giữ đúng thuật ngữ tiếng Anh xen tiếng Việt "
    '(VD: nghe "cu bơ nết" trong ngữ cảnh hạ tầng → "kubernetes"). CHỈ trả về một '
    'mảng JSON các chuỗi, ví dụ ["kubernetes","cu bơ nết"]. '
    "Không giải thích, không markdown."
)


def _parse_suggestions(raw: str, n: int) -> list[str]:
    """Ép output LLM về list chuỗi: json.loads thẳng, fail thì trích mảng `[...]`
    đầu tiên bằng regex, fail nữa → []. Giữ tối đa n chuỗi non-empty."""
    text = raw.strip()
    data: object
    try:
        data = json.loads(text)
    except ValueError:
        m = re.search(r"\[.*?\]", text, flags=re.DOTALL)
        if m is None:
            return []
        try:
            data = json.loads(m.group(0))
        except ValueError:
            return []
    if not isinstance(data, list):
        return []
    out: list[str] = []
    for item in data:
        if isinstance(item, str) and item.strip():
            out.append(item.strip())
        if len(out) >= n:
            break
    return out


def suggest_alternatives(
    word: str, context: str, language: str = "vi", n: int = 3
) -> list[str]:
    """Tối đa n cách hiểu khả dĩ cho 1 từ ASR nghe không rõ trong `context`,
    sinh bởi LLM (giữ thuật ngữ Anh xen Việt đúng ngữ cảnh họp kỹ thuật). Trả
    list str; MỌI lỗi (LLM tắt/timeout/API/parse hỏng) → [] (never-fail như
    pass 2)."""
    word = word.strip()
    if not word:
        return []
    user = (
        f"Ngôn ngữ chính: {language}\n"
        f"Câu chứa từ: {context}\n"
        f"Từ nghe không rõ: {word}\n"
        f"Đưa tối đa {n} cách hiểu khả dĩ dưới dạng mảng JSON các chuỗi."
    )
    try:
        raw = chat_once(_SUGGEST_SYSTEM_PROMPT, user)
    except Exception:  # noqa: BLE001 — LLM lỗi thì không gợi ý, không raise
        return []
    return _parse_suggestions(raw, n)


def _correct_chunk(client: httpx.Client, chunk: str, opts: LlmOpts) -> str:
    # US-805: Ollama cũng nhận context như OpenRouter — cùng 1 chỗ build prompt.
    user = _prompt_for(chunk, opts.glossary, opts.context, opts.pairs, opts.uncertain)
    return _guard(chunk, _chat_ollama(client, _SYSTEM_PROMPT, user, opts))


def _correct_chunk_openrouter(client: httpx.Client, chunk: str, opts: LlmOpts) -> str:
    user = _prompt_for(chunk, opts.glossary, opts.context, opts.pairs, opts.uncertain)
    return _guard(chunk, _chat_openrouter(client, _SYSTEM_PROMPT, user, opts))


def _guard(chunk: str, fixed: str) -> str:
    """LLM trả rỗng hoặc khác gốc quá mức ("sửa quá tay") → giữ bản gốc."""
    if not fixed:
        return chunk
    if difflib.SequenceMatcher(None, chunk, fixed).ratio() < MIN_SIMILARITY:
        return chunk
    return fixed


def correct_sentence(text: str, opts: LlmOpts) -> tuple[str, bool]:
    """Sửa 1 câu (live mode) — caller đặt num_ctx nhỏ + timeout ngắn cho kịp subtitle.

    `opts.context` = ngữ cảnh đang diễn ra (topic + các câu gần nhất) — LLM dựa
    vào mạch cuộc họp để đoán đúng thuật ngữ; cả 2 backend đều dùng (US-805).
    Trả (câu đã sửa, True) nếu chạy trót lọt, ngược lại (câu gốc, False). Cùng
    contract never-fail như correct_text.
    """
    text = text.strip()
    if not text:
        return text, False
    try:
        with httpx.Client() as client:
            if openrouter_enabled():
                try:
                    return _correct_chunk_openrouter(client, text, opts), True
                except Exception:  # noqa: BLE001 — mạng rớt/hết credit → thử LLM local
                    pass
            return _correct_chunk(client, text, opts), True
    except Exception:  # noqa: BLE001 — Ollama tắt/timeout → dùng bản gốc
        return text, False


_TOPIC_SYSTEM_PROMPT = (
    "Bạn theo dõi một cuộc họp tiếng Việt có pha thuật ngữ tiếng Anh. "
    "Tóm tắt 1-2 câu chủ đề đang được bàn (tên dự án, thuật ngữ, chủ đề chính). "
    "Trả về đúng phần tóm tắt, không lời giải thích, không markdown."
)


def summarize_topic(old_topic: str, sentences: str, opts: LlmOpts) -> str | None:
    """Tóm tắt chủ đề đang bàn (live, US-805) — cùng backend selection với pass 2.

    Trả topic mới, hoặc None khi LLM lỗi/timeout/trả rỗng — caller giữ topic cũ
    (never-fail, không bao giờ raise).
    """
    parts = []
    if old_topic:
        parts.append("Chủ đề trước đó: " + old_topic)
    parts.append("Các câu mới trong cuộc họp:\n" + sentences)
    user = "\n\n".join(parts)
    try:
        with httpx.Client() as client:
            if openrouter_enabled():
                try:
                    return _chat_openrouter(client, _TOPIC_SYSTEM_PROMPT, user, opts) or None
                except Exception:  # noqa: BLE001 — mạng rớt/hết credit → thử LLM local
                    pass
            return _chat_ollama(client, _TOPIC_SYSTEM_PROMPT, user, opts) or None
    except Exception:  # noqa: BLE001 — Ollama tắt/timeout → giữ topic cũ
        return None


def correct_text(
    text: str,
    glossary: str = "",
    on_progress: Callable[[float], None] | None = None,
    pairs: tuple[tuple[str, str], ...] = (),
) -> tuple[str, bool]:
    """Trả (text đã sửa, True) nếu pass 2 chạy trót lọt, ngược lại (text gốc, False)."""
    text = text.strip()
    if not text:
        return text, False
    chunks = _split_chunks(text)
    fixed_parts: list[str] = []
    opts = LlmOpts(glossary=glossary, pairs=pairs)
    try:
        with httpx.Client() as client:
            for i, chunk in enumerate(chunks):
                if openrouter_enabled():
                    try:
                        fixed = _correct_chunk_openrouter(client, chunk, opts)
                    except Exception:  # noqa: BLE001 — rơi về LLM local
                        fixed = _correct_chunk(client, chunk, opts)
                else:
                    fixed = _correct_chunk(client, chunk, opts)
                fixed_parts.append(fixed)
                if on_progress:
                    on_progress((i + 1) / len(chunks))
    except Exception:  # noqa: BLE001 — Ollama tắt/timeout → dùng bản gốc
        return text, False
    return " ".join(fixed_parts).strip(), True
