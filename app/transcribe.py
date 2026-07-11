"""Wrapper faster-whisper + quản lý job transcribe trong background."""
from __future__ import annotations

import os
import re
import shutil
import threading
import uuid
import wave
from datetime import UTC, datetime
from pathlib import Path

from app import db, engines
from app.correct import correct_text, llm_model_name

# ── Đường dẫn dữ liệu (chung với MCP server) ──────────────────────────────
ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
UPLOADS = DATA / "uploads"
TRANSCRIPTS = DATA / "transcripts"
RECORDINGS = DATA / "recordings"  # file ghi âm gốc (record + upload), giữ để nghe lại
UPLOADS.mkdir(parents=True, exist_ok=True)
TRANSCRIPTS.mkdir(parents=True, exist_ok=True)
RECORDINGS.mkdir(parents=True, exist_ok=True)

DEFAULT_MODEL = os.environ.get("WHISPER_MODEL", "large-v3-turbo")
ALLOWED_MODELS = {"small", "medium", "large-v3-turbo", "large-v3"}


# ── Quản lý job (in-memory) ───────────────────────────────────────────────
_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()


def _new_job() -> str:
    job_id = uuid.uuid4().hex[:12]
    with _jobs_lock:
        _jobs[job_id] = {
            "status": "queued",  # queued | running | correcting | done | error
            "text": "",
            "progress": 0.0,
            "error": None,
            "transcript_id": None,
        }
    return job_id


def get_job(job_id: str) -> dict | None:
    with _jobs_lock:
        job = _jobs.get(job_id)
        return dict(job) if job else None


def _update(job_id: str, **kw) -> None:
    with _jobs_lock:
        if job_id in _jobs:
            _jobs[job_id].update(kw)


def pcm_to_wav(pcm_data: bytes, wav_path: Path, sample_rate: int = 16000) -> None:
    """Đóng gói PCM16 mono thô thành file WAV nghe/tải lại được."""
    with wave.open(str(wav_path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm_data)


def _slugify(name: str) -> str:
    stem = Path(name).stem
    stem = re.sub(r"[^\w\-]+", "-", stem, flags=re.UNICODE).strip("-")
    return stem[:60] or "meeting"


def _run(
    job_id: str,
    audio_path: Path,
    language: str,
    original_name: str,
    model_name: str,
    prompt: str,
    correct: bool,
) -> None:
    try:
        _update(job_id, status="running")
        engine = engines.get_engine()
        # Tier GPU (mlx/cuda) dùng model mặc định của engine; tier CPU tôn
        # trọng lựa chọn size của user (YC-2).
        override = model_name if engine.info.tier == "cpu" and model_name in ALLOWED_MODELS else None
        result = engine.transcribe_file(
            audio_path,
            language=language,
            glossary=prompt,
            on_progress=lambda text, p: _update(job_id, text=text, progress=p),
            model_override=override,
        )
        duration = result.duration
        full_text = result.text
        used_model = override or engine.info.model_name

        # Pass 2: LLM local soát lại thuật ngữ tiếng Anh bị phiên âm sai.
        raw_text = full_text
        corrected = False
        if correct and full_text:
            _update(job_id, status="correcting", progress=0.0)
            full_text, corrected = correct_text(
                full_text,
                glossary=prompt,
                on_progress=lambda p: _update(job_id, progress=p),
            )

        transcript_id = save_transcript(
            original_name,
            language,
            duration,
            full_text,
            used_model,
            raw_text=raw_text if corrected and full_text != raw_text else None,
            segments=result.segments or None,
            # Giữ lại file upload gốc để nghe/tải lại (save_transcript sẽ dời đi).
            audio_path=audio_path,
        )
        _update(
            job_id,
            status="done",
            text=full_text,
            progress=1.0,
            transcript_id=transcript_id,
        )
    except Exception as exc:  # noqa: BLE001
        _update(job_id, status="error", error=str(exc))
        # Job lỗi → không lưu, dọn file tạm.
        try:
            audio_path.unlink(missing_ok=True)
        except Exception:  # noqa: BLE001
            pass


def save_transcript(
    original_name: str,
    language: str,
    duration: float,
    text: str,
    model_name: str,
    raw_text: str | None = None,
    segments: list[dict] | None = None,
    audio_path: Path | None = None,
) -> str:
    now = datetime.now(UTC).astimezone()
    transcript_id = f"{now:%Y%m%d-%H%M%S}-{_slugify(original_name)}"
    # Artifact đọc nhanh/grep được; nguồn chân lý là SQLite (app/db.py).
    (TRANSCRIPTS / f"{transcript_id}.txt").write_text(text, encoding="utf-8")
    # File ghi âm gốc → dời vào thư mục audio user cấu hình (mặc định
    # data/recordings/) để nghe/tải lại từ lịch sử.
    audio_name: str | None = None
    audio_dir = db.get_audio_dir()
    if audio_path is not None and audio_path.exists():
        ext = audio_path.suffix.lower() or ".bin"
        audio_dir.mkdir(parents=True, exist_ok=True)
        dest = audio_dir / f"{transcript_id}{ext}"
        try:
            shutil.move(str(audio_path), dest)
            audio_name = dest.name
        except Exception:  # noqa: BLE001 — lưu audio thất bại không được làm hỏng transcript
            audio_name = None
    db.insert_transcript(
        transcript_id=transcript_id,
        title=original_name,
        language=language,
        model=model_name,
        duration=duration,
        created_at=now.isoformat(),
        text=text,
        raw_text=raw_text,
        segments=segments,
        llm_model=llm_model_name() if raw_text is not None else None,
        audio_file=audio_name,
        audio_dir=str(audio_dir) if audio_name else None,
    )
    return transcript_id


def start_transcription(
    file_bytes: bytes,
    filename: str,
    language: str,
    model_name: str = DEFAULT_MODEL,
    prompt: str = "",
    correct: bool = True,
) -> str:
    """Lưu file upload, khởi chạy job nền, trả job_id ngay."""
    job_id = _new_job()
    safe = f"{job_id}-{_slugify(filename)}{Path(filename).suffix}"
    audio_path = UPLOADS / safe
    audio_path.write_bytes(file_bytes)
    threading.Thread(
        target=_run,
        args=(job_id, audio_path, language, filename, model_name, prompt, correct),
        daemon=True,
    ).start()
    return job_id


# ── Đọc transcript đã lưu (dùng cho UI lịch sử) — delegate sang SQLite ────
def list_transcripts() -> list[dict]:
    return db.list_transcripts()


def transcript_audio_path(transcript_id: str) -> Path | None:
    """Đường dẫn file ghi âm của 1 transcript (None nếu không có)."""
    if "/" in transcript_id or "\\" in transcript_id or ".." in transcript_id:
        return None
    return db.transcript_audio_path(transcript_id)


def read_transcript(transcript_id: str) -> dict | None:
    return db.read_transcript(transcript_id)
