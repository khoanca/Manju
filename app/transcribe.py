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

from app import corrections, db, diarize, domain, engines, memory_filter
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
    speaker_map: dict | None = None  # pass 3: {cụm local: speaker_id | null}
    domain: str | None = None  # Lớp B: ngành nghề dự đoán


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
    # US-803: glossary hiệu lực = glossary user + term approved từ thư viện
    # (build_bias never-fail) — tính 1 lần, dùng cho cả mồi Whisper lẫn pass 2.
    glossary = corrections.build_bias(spec.prompt)
    result = engine.transcribe_file(
        audio_path,
        engines.DecodeSpec(spec.language, glossary, override),
        lambda text, p: _update(job_id, text=text, progress=p),
    )
    # Lớp B: đoán ngành từ bản thô để nạp lexicon ngành cho pass 2 (Lớp C).
    # Tắt được qua setting domain_auto (mặc định bật) — họp không rõ ngành thì
    # tránh ép lexicon lạ.
    domain_on = spec.correct and db.get_setting("domain_auto", "1") == "1"
    domains = domain.active_domains(result.text) if domain_on else ()
    full_text, corrected = _maybe_correct(job_id, result.text, spec, domains)
    segments, speaker_map = _maybe_diarize(job_id, audio_path, result.segments)
    transcript_id = save_transcript(TranscriptDraft(
        original_name=spec.filename,
        language=spec.language,
        duration=result.duration,
        text=full_text,
        model_name=override or engine.info.model_name,
        raw_text=result.text if corrected and full_text != result.text else None,
        segments=segments or None,
        speaker_map=speaker_map,
        # Giữ lại file upload gốc để nghe/tải lại (save_transcript sẽ dời đi).
        audio_path=audio_path,
        domain=domains[0] if domains else None,
    ))
    _update(job_id, status="done", text=full_text, progress=1.0, transcript_id=transcript_id)


def _maybe_correct(
    job_id: str, text: str, spec: JobSpec, domains: tuple[str, ...]
) -> tuple[str, bool]:
    """Sửa thuật ngữ theo 2 lớp: (A) ký ức bản dịch cũ thay xác định các cụm đã
    biết, rồi (Pass 2) LLM soát phần còn lại. `domains` = ngành dự đoán → nạp
    lexicon ngành vào glossary + few-shot. Trả (text, đã-đổi-so-với-thô)."""
    if not (spec.correct and text):
        return text, False
    # Lớp A: ký ức bản dịch cũ — thay xác định trước, rẻ + nhất quán.
    text, mem_hits = memory_filter.correct_from_memory(text)
    # Lớp C: nạp lexicon các ngành vừa đoán vào thư viện (idempotent, never-fail).
    corrections.ensure_domains(domains)
    # Glossary/few-shot hiệu lực = thư viện (build_bias never-fail) + term ngành.
    glossary = corrections.build_bias(spec.prompt, domains=domains)
    _update(job_id, status="correcting", progress=0.0)
    fixed, ok = correct_text(
        text,
        glossary=glossary,
        on_progress=lambda p: _update(job_id, progress=p),
        # Few-shot cặp (sai → đúng) đã duyệt, ưu tiên ngành — never-fail (US-803).
        pairs=tuple(corrections.top_pairs(domains=domains)),
    )
    # "Đã đổi" nếu pass 2 chạy được HOẶC ký ức đã thay gì đó — để lưu raw_text.
    return fixed, ok or mem_hits > 0


def diarize_enabled() -> bool:
    return db.get_setting("diarize_enabled") == "1"


def _maybe_diarize(
    job_id: str, audio_path: Path | None, segments: list[dict] | None
) -> tuple[list[dict] | None, dict | None]:
    """Pass 3: gán giọng cho từng segment. Never-fail — lỗi/thiếu model →
    trả segment gốc, transcript vẫn lưu (US-701 AC2)."""
    if not (segments and audio_path and diarize_enabled() and diarize.models_present()):
        return segments, None
    _update(job_id, status="diarizing", progress=0.0)
    try:
        spans = diarize.diarize_file(audio_path)
    except Exception:  # noqa: BLE001 — pass 3 không được làm hỏng transcript
        return segments, None
    if not spans:  # audio ngắn / 1 giọng
        return segments, None
    labeled = diarize.assign_speakers(segments, spans)
    smap = diarize.initial_speaker_map(labeled)
    # US-703: tự gán tên cho cụm khớp voiceprint đã enroll.
    vps = diarize.to_np_voiceprints(db.load_voiceprints())
    smap = diarize.identify_clusters(audio_path, labeled, smap, vps)
    return labeled, smap


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
        speaker_map=draft.speaker_map,
        domain=draft.domain,
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
