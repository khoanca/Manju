"""Wrapper faster-whisper + quản lý job transcribe trong background."""
from __future__ import annotations

import os
import re
import shutil
import threading
import uuid
import wave
from dataclasses import dataclass
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


@dataclass(frozen=True)
class JobSpec:
    """Tham số 1 job transcribe upload (từ form /api/transcribe)."""

    filename: str
    language: str
    model_name: str = DEFAULT_MODEL
    prompt: str = ""
    correct: bool = True


@dataclass(frozen=True)
class TranscriptDraft:
    """Transcript vừa xong, chờ xuất artifact + ghi DB (save_transcript)."""

    original_name: str
    language: str
    duration: float
    text: str
    model_name: str
    raw_text: str | None = None
    segments: list[dict] | None = None
    audio_path: Path | None = None


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


def _run(job_id: str, audio_path: Path, spec: JobSpec) -> None:
    try:
        _process(job_id, audio_path, spec)
    except Exception as exc:  # noqa: BLE001
        _update(job_id, status="error", error=str(exc))
        # Job lỗi → không lưu, dọn file tạm.
        try:
            audio_path.unlink(missing_ok=True)
        except Exception:  # noqa: BLE001
            pass


def _process(job_id: str, audio_path: Path, spec: JobSpec) -> None:
    _update(job_id, status="running")
    engine = engines.get_engine()
    # Tier GPU (mlx/cuda) dùng model mặc định của engine; tier CPU tôn
    # trọng lựa chọn size của user (YC-2).
    override = (
        spec.model_name
        if engine.info.tier == "cpu" and spec.model_name in ALLOWED_MODELS
        else None
    )
    result = engine.transcribe_file(
        audio_path,
        engines.DecodeSpec(spec.language, spec.prompt, override),
        lambda text, p: _update(job_id, text=text, progress=p),
    )
    full_text, corrected = _maybe_correct(job_id, result.text, spec)
    transcript_id = save_transcript(TranscriptDraft(
        original_name=spec.filename,
        language=spec.language,
        duration=result.duration,
        text=full_text,
        model_name=override or engine.info.model_name,
        raw_text=result.text if corrected and full_text != result.text else None,
        segments=result.segments or None,
        # Giữ lại file upload gốc để nghe/tải lại (save_transcript sẽ dời đi).
        audio_path=audio_path,
    ))
    _update(job_id, status="done", text=full_text, progress=1.0, transcript_id=transcript_id)


def _maybe_correct(job_id: str, text: str, spec: JobSpec) -> tuple[str, bool]:
    """Pass 2: LLM soát lại thuật ngữ tiếng Anh bị phiên âm sai."""
    if not (spec.correct and text):
        return text, False
    _update(job_id, status="correcting", progress=0.0)
    return correct_text(
        text, glossary=spec.prompt, on_progress=lambda p: _update(job_id, progress=p)
    )


def _store_audio(transcript_id: str, audio_path: Path | None) -> tuple[str | None, Path]:
    """Dời file ghi âm gốc vào thư mục audio user cấu hình (mặc định
    data/recordings/) để nghe/tải lại từ lịch sử. Trả (tên file, thư mục)."""
    audio_dir = db.get_audio_dir()
    if audio_path is None or not audio_path.exists():
        return None, audio_dir
    ext = audio_path.suffix.lower() or ".bin"
    audio_dir.mkdir(parents=True, exist_ok=True)
    dest = audio_dir / f"{transcript_id}{ext}"
    try:
        shutil.move(str(audio_path), dest)
    except Exception:  # noqa: BLE001 — lưu audio thất bại không được làm hỏng transcript
        return None, audio_dir
    return dest.name, audio_dir


def save_transcript(draft: TranscriptDraft) -> str:
    now = datetime.now(UTC).astimezone()
    transcript_id = f"{now:%Y%m%d-%H%M%S}-{_slugify(draft.original_name)}"
    # Artifact đọc nhanh/grep được; nguồn chân lý là SQLite (app/db.py).
    (TRANSCRIPTS / f"{transcript_id}.txt").write_text(draft.text, encoding="utf-8")
    audio_name, audio_dir = _store_audio(transcript_id, draft.audio_path)
    db.insert_transcript(db.TranscriptRecord(
        transcript_id=transcript_id,
        title=draft.original_name,
        language=draft.language,
        model=draft.model_name,
        duration=draft.duration,
        created_at=now.isoformat(),
        text=draft.text,
        raw_text=draft.raw_text,
        segments=draft.segments,
        llm_model=llm_model_name() if draft.raw_text is not None else None,
        audio_file=audio_name,
        audio_dir=str(audio_dir) if audio_name else None,
    ))
    return transcript_id


def start_transcription(file_bytes: bytes, spec: JobSpec) -> str:
    """Lưu file upload, khởi chạy job nền, trả job_id ngay."""
    job_id = _new_job()
    safe = f"{job_id}-{_slugify(spec.filename)}{Path(spec.filename).suffix}"
    audio_path = UPLOADS / safe
    audio_path.write_bytes(file_bytes)
    threading.Thread(target=_run, args=(job_id, audio_path, spec), daemon=True).start()
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
