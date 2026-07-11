"""FastAPI app: upload file ghi âm → transcribe → text."""
from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, Form, HTTPException, UploadFile, WebSocket
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app import db, engines, live, org, transcribe


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.init()
    # Đưa transcript dạng file (trước khi có SQLite) vào DB — idempotent.
    db.migrate_from_files(transcribe.TRANSCRIPTS, transcribe.RECORDINGS)
    yield


app = FastAPI(title="Meeting Transcriber", lifespan=lifespan)

STATIC = Path(__file__).resolve().parent / "static"
# AudioWorklet phải fetch module JS qua URL → cần serve cả thư mục static.
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/sw.js")
def sw():
    # Service worker phải serve từ gốc để scope phủ cả app (PWA).
    return FileResponse(STATIC / "sw.js", media_type="application/javascript")


@app.websocket("/ws/live")
async def ws_live(ws: WebSocket):
    await live.handle(ws)


class TranscribeForm(BaseModel):
    """Toàn bộ form multipart của /api/transcribe — wire format không đổi."""

    file: UploadFile
    language: str = "vi"
    model: str = transcribe.DEFAULT_MODEL
    prompt: str = ""
    correct: bool = True


@app.post("/api/transcribe")
async def api_transcribe(form: Annotated[TranscribeForm, Form()]):
    if form.language not in ("vi", "en"):
        raise HTTPException(400, "language phải là 'vi' hoặc 'en'")
    if form.model not in transcribe.ALLOWED_MODELS:
        raise HTTPException(400, "model không hợp lệ")
    data = await form.file.read()
    if not data:
        raise HTTPException(400, "File rỗng")
    spec = transcribe.JobSpec(
        filename=form.file.filename or "meeting",
        language=form.language,
        model_name=form.model,
        prompt=form.prompt.strip(),
        correct=form.correct,
    )
    return {"job_id": transcribe.start_transcription(data, spec)}


@app.get("/api/jobs/{job_id}")
def api_job(job_id: str):
    job = transcribe.get_job(job_id)
    if job is None:
        raise HTTPException(404, "Không tìm thấy job")
    return job


class SettingsIn(BaseModel):
    audio_dir: str | None = None


@app.get("/api/settings")
def api_settings():
    info = engines.get_engine().info
    return {
        "audio_dir": str(db.get_audio_dir()),
        "engine": {"tier": info.tier, "model": info.model_name},
        "max_live_sessions": live.MAX_LIVE_SESSIONS,
    }


@app.put("/api/settings")
def api_settings_put(body: SettingsIn):
    if body.audio_dir is not None:
        try:
            db.set_audio_dir(body.audio_dir)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
    return {"audio_dir": str(db.get_audio_dir())}


@app.get("/api/transcripts")
def api_transcripts():
    return transcribe.list_transcripts()


@app.get("/api/transcripts/{transcript_id}")
def api_transcript(transcript_id: str):
    data = transcribe.read_transcript(transcript_id)
    if data is None:
        raise HTTPException(404, "Không tìm thấy transcript")
    return JSONResponse(data)


@app.get("/api/transcripts/{transcript_id}/audio")
def api_transcript_audio(transcript_id: str):
    path = transcribe.transcript_audio_path(transcript_id)
    if path is None:
        raise HTTPException(404, "Không có file ghi âm")
    # FileResponse tự đoán media type theo đuôi file (wav/mp3/m4a/…).
    return FileResponse(path, filename=path.name)


# ── Đợt 2: đẩy TEXT bản ghi lên org cloud (không audio) ────────────────────
class PushIn(BaseModel):
    org_id: str
    access_token: str  # JWT Supabase Auth của user — RLS trên Postgres áp quyền


@app.post("/api/transcripts/{transcript_id}/push")
def api_push(transcript_id: str, body: PushIn):
    transcript = transcribe.read_transcript(transcript_id)
    if transcript is None:
        raise HTTPException(404, "Không tìm thấy transcript")
    try:
        remote = org.push_transcript(body.org_id, body.access_token, transcript)
    except org.OrgNotConfigured as exc:
        raise HTTPException(503, str(exc)) from exc
    except org.OrgError as exc:
        raise HTTPException(502, str(exc)) from exc
    db.set_sync_state(transcript_id, db.SyncState(
        org_id=body.org_id,
        remote_id=remote.get("id"),
        pushed_at=datetime.now(UTC).astimezone().isoformat(),
    ))
    return {"status": "pushed", "remote_id": remote.get("id")}


@app.get("/api/transcripts/{transcript_id}/sync")
def api_sync_state(transcript_id: str):
    return db.get_sync_state(transcript_id) or {"status": "pending"}
