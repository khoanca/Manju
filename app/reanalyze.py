"""Chạy lại phân tích 1 bản ghi theo BIẾN THỂ chọn được trên UI: (1) phiên bản
pipeline ASR-cleanup và (2) model LLM sửa thuật ngữ. Vừa để test (đổi dropdown so
kết quả), vừa cho user "Dùng bản này" lưu lại.

Giải mã file ghi âm gốc MỘT LẦN rồi cache segment thô theo transcript — đổi phiên
bản/model chỉ chạy lại phần hậu-xử-lý (rẻ), không giải mã lại.

Phiên bản pipeline (tất định, không LLM):
  • new — hiện tại: keep_segment + collapse_loops mới (bắt loop chu-kỳ + trong-token)
  • old — trước ffe38d2: keep_segment_legacy + collapse_loops_legacy (chỉ token-đơn)
  • raw — segment thô, chưa keep/collapse

Model sửa (pass 2, chạy sau cleanup; memory_filter base-lexicon áp trước như production):
  • none  — không sửa, giữ bản cleanup
  • haiku — anthropic/claude-haiku-4.5 qua OpenRouter (cần key)
  • gemma — gemma4:e4b qua Ollama local
"""
from __future__ import annotations

import threading
import uuid
from pathlib import Path

from app import correct, corrections, db, engines, memory_filter, transcribe

# key → nhãn hiển thị (UI đổ vào dropdown phiên bản).
PIPELINE_VERSIONS: dict[str, str] = {
    "new": "Bản mới (hiện tại)",
    "old": "Bản cũ (trước ffe38d2)",
    "raw": "Bản gốc thô (chưa cleanup)",
}
# key → (nhãn, backend correct.correct_text, tên model). backend "" = không sửa,
# "openrouter" = cloud (cần key), "ollama" = local. Thêm model = thêm 1 dòng.
CORRECTION_MODELS: dict[str, tuple[str, str, str]] = {
    "none": ("Không sửa (bản thô)", "", ""),
    "haiku": ("Haiku 4.5 (cloud)", "openrouter", "anthropic/claude-haiku-4.5"),
    "sonnet": ("Sonnet 5 (cloud)", "openrouter", "anthropic/claude-sonnet-5"),
    "gemma": ("Gemma e4b (local)", "ollama", "gemma4:e4b"),
    "qwen": ("Qwen3 4b (local)", "ollama", "qwen3:4b"),
}

_jobs: dict[str, dict] = {}
_raw_cache: dict[str, list[dict]] = {}  # transcript_id → segment thô (giải mã 1 lần)
_lock = threading.Lock()


def options() -> dict:
    """Danh sách phiên bản + model cho UI. Model cloud chỉ 'available' khi có key
    OpenRouter (không thì dropdown vô hiệu hoá lựa chọn đó)."""
    return {
        "versions": [{"key": k, "label": v} for k, v in PIPELINE_VERSIONS.items()],
        "models": [
            {
                "key": k,
                "label": label,
                "available": backend != "openrouter" or correct.openrouter_enabled(),
            }
            for k, (label, backend, _model) in CORRECTION_MODELS.items()
        ],
    }


def _cleanup(segments: list[dict], version: str) -> str:
    """Áp phiên bản ASR-cleanup lên segment thô → text."""
    if version == "raw":
        return " ".join(s["text"].strip() for s in segments).strip()
    legacy = version == "old"
    keep = engines.keep_segment_legacy if legacy else engines.keep_segment
    collapse = engines.collapse_loops_legacy if legacy else engines.collapse_loops
    kept = [s for s in segments if keep(s["text"], s["no_speech_prob"], s["avg_logprob"])]
    return " ".join(collapse(s["text"]).strip() for s in kept).strip()


def _correct(text: str, model_key: str) -> str:
    """Sửa thuật ngữ bằng model chọn. none → giữ nguyên. Áp memory_filter (base
    lexicon: mô độ→model...) trước rồi pass 2, giống production. Never-fail."""
    label, backend, model = CORRECTION_MODELS[model_key]
    if not backend or not text:
        return text
    text, _ = memory_filter.correct_from_memory(text)
    glossary = corrections.build_bias(db.get_setting("glossary", "") or "")
    fixed, ok = correct.correct_text(
        text, glossary=glossary, pairs=tuple(corrections.top_pairs()),
        backend=backend, model=model,
    )
    return fixed if ok else text


def _raw_segments(transcript_id: str) -> tuple[list[dict], bool]:
    """Segment thô của transcript → (segments, từ_live). Ưu tiên segment ASR THÔ
    đã lưu của phiên live (phản ánh đúng cái live nghe); không có thì batch-decode
    lại file ghi âm gốc (cache theo id). Thiếu transcript/audio → ValueError."""
    meta = db.read_transcript(transcript_id)
    if meta is None:
        raise ValueError("Không tìm thấy transcript")
    stored = meta.get("raw_segments")
    if stored:  # phiên live đã lưu segment thô → dùng thẳng, không decode lại
        return stored, True
    with _lock:
        cached = _raw_cache.get(transcript_id)
    if cached is not None:
        return cached, False
    audio = transcribe.transcript_audio_path(transcript_id)
    if audio is None or not audio.exists():
        raise ValueError("Transcript không có file ghi âm để phân tích lại")
    spec = engines.DecodeSpec(
        str(meta.get("language") or "vi"),
        corrections.build_bias(db.get_setting("glossary", "") or ""),
        None,
    )
    raw = engines.get_engine().raw_file_segments(Path(audio), spec)
    with _lock:
        _raw_cache[transcript_id] = raw
    return raw, False


def get(job_id: str) -> dict | None:
    with _lock:
        job = _jobs.get(job_id)
        return dict(job) if job else None


def _set(job_id: str, **kw) -> None:
    with _lock:
        if job_id in _jobs:
            _jobs[job_id].update(kw)


def _run(job_id: str, transcript_id: str, version: str, model_key: str) -> None:
    try:
        _set(job_id, status="decoding")
        segs, from_live = _raw_segments(transcript_id)
        cleaned = _cleanup(segs, version)
        if CORRECTION_MODELS[model_key][1]:
            _set(job_id, status="correcting")
        _set(
            job_id, status="done", text=_correct(cleaned, model_key),
            segments=len(segs), from_live=from_live,
        )
    except Exception as exc:  # noqa: BLE001 — công cụ chẩn đoán, báo lỗi về UI
        _set(job_id, status="error", error=str(exc))


def start(transcript_id: str, version: str, model_key: str) -> str:
    """Khởi chạy 1 biến thể trong luồng nền, trả job_id. Key lạ → ValueError."""
    if version not in PIPELINE_VERSIONS:
        raise ValueError(f"Phiên bản không hợp lệ: {version}")
    if model_key not in CORRECTION_MODELS:
        raise ValueError(f"Model không hợp lệ: {model_key}")
    job_id = uuid.uuid4().hex[:12]
    with _lock:
        _jobs[job_id] = {
            "status": "queued", "text": "", "segments": 0, "from_live": False, "error": None,
        }
    threading.Thread(
        target=_run, args=(job_id, transcript_id, version, model_key), daemon=True
    ).start()
    return job_id
