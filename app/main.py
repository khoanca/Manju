"""FastAPI app: upload file ghi âm → transcribe → text."""
from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, Form, HTTPException, Response, UploadFile, WebSocket
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app import db, diarize, engines, live, org, subtitle, transcribe


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
    diarize_enabled: bool | None = None


@app.get("/api/settings")
def api_settings():
    info = engines.get_engine().info
    return {
        "audio_dir": str(db.get_audio_dir()),
        "engine": {"tier": info.tier, "model": info.model_name},
        "max_live_sessions": live.MAX_LIVE_SESSIONS,
        "diarize": {
            "enabled": transcribe.diarize_enabled(),
            "models_present": diarize.models_present(),
        },
    }


@app.put("/api/settings")
def api_settings_put(body: SettingsIn):
    if body.audio_dir is not None:
        try:
            db.set_audio_dir(body.audio_dir)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
    if body.diarize_enabled is not None:
        db.set_setting("diarize_enabled", "1" if body.diarize_enabled else "0")
    return {
        "audio_dir": str(db.get_audio_dir()),
        "diarize_enabled": transcribe.diarize_enabled(),
    }


@app.get("/api/transcripts")
def api_transcripts():
    return transcribe.list_transcripts()


@app.get("/api/transcripts/{transcript_id}")
def api_transcript(transcript_id: str):
    data = transcribe.read_transcript(transcript_id)
    if data is None:
        raise HTTPException(404, "Không tìm thấy transcript")
    return JSONResponse(data)


def _speaker_labels(speaker_map: dict | None) -> dict[int, str]:
    """{local_cluster_idx: speaker_id} + bảng speakers → {idx: tên} cho phụ đề."""
    if not speaker_map:
        return {}
    names = db.speaker_names()
    return {int(k): names[v] for k, v in speaker_map.items() if v and v in names}


@app.get("/api/transcripts/{transcript_id}/subtitle")
def api_subtitle(transcript_id: str, format: str = "srt"):
    if format not in ("srt", "vtt"):
        raise HTTPException(400, "format phải là 'srt' hoặc 'vtt'")
    data = transcribe.read_transcript(transcript_id)
    if data is None:
        raise HTTPException(404, "Không tìm thấy transcript")
    segments = data.get("segments")
    if not segments:
        raise HTTPException(409, "Transcript chưa có timestamp/segment để xuất phụ đề")
    text = subtitle.render(
        segments, _speaker_labels(data.get("speaker_map")), vtt=(format == "vtt")
    )
    media = "text/vtt" if format == "vtt" else "application/x-subrip"
    return Response(
        text,
        media_type=media,
        headers={"Content-Disposition": f'attachment; filename="{transcript_id}.{format}"'},
    )


@app.get("/api/transcripts/{transcript_id}/audio")
def api_transcript_audio(transcript_id: str):
    path = transcribe.transcript_audio_path(transcript_id)
    if path is None:
        raise HTTPException(404, "Không có file ghi âm")
    # FileResponse tự đoán media type theo đuôi file (wav/mp3/m4a/…).
    return FileResponse(path, filename=path.name)


# ── Lớp speaker: tách giọng lại + gán tên cụm + quản lý người ──────────────
@app.post("/api/transcripts/{transcript_id}/diarize")
def api_diarize(transcript_id: str, num_speakers: int = -1):
    """Chạy/chạy lại pass 3 trên file ghi âm đã lưu (kể cả recording live).
    Ghi đè speaker_map cũ (UI cảnh báo trước khi gọi)."""
    if not diarize.models_present():
        raise HTTPException(503, "Chưa tải model diarization (scripts/fetch_diarize_models.py)")
    data = transcribe.read_transcript(transcript_id)
    if data is None:
        raise HTTPException(404, "Không tìm thấy transcript")
    segments = data.get("segments")
    if not segments:
        raise HTTPException(409, "Transcript không có timestamp/segment để tách giọng")
    audio = transcribe.transcript_audio_path(transcript_id)
    if audio is None:
        raise HTTPException(409, "Không còn file ghi âm để tách giọng")
    spans = diarize.diarize_file(audio, num_speakers)
    if not spans:
        raise HTTPException(422, "Không tách được giọng (audio quá ngắn hoặc chỉ 1 người)")
    labeled = diarize.assign_speakers(segments, spans)
    smap = diarize.initial_speaker_map(labeled)
    db.update_speaker_layer(transcript_id, labeled, smap)
    return {"segments": labeled, "speaker_map": smap, "num_speakers": len(smap)}


class ClusterAssign(BaseModel):
    cluster: int
    name: str | None = None        # tên → tìm/tạo speaker
    speaker_id: str | None = None  # hoặc gán id sẵn có; cả hai None = bỏ gán cụm


@app.put("/api/transcripts/{transcript_id}/speaker-map")
def api_assign_cluster(transcript_id: str, body: ClusterAssign):
    sid = body.speaker_id
    if body.name and body.name.strip():
        sid = db.find_or_create_speaker(body.name)
    try:
        smap = db.set_transcript_cluster(transcript_id, body.cluster, sid)
    except KeyError as exc:
        raise HTTPException(404, "Không tìm thấy transcript") from exc
    return {"speaker_map": smap, "speakers": db.speaker_names()}


@app.get("/api/speakers")
def api_speakers():
    return db.list_speakers()


class SpeakerIn(BaseModel):
    name: str


@app.patch("/api/speakers/{speaker_id}")
def api_rename_speaker(speaker_id: str, body: SpeakerIn):
    if not body.name.strip():
        raise HTTPException(400, "Tên rỗng")
    db.rename_speaker(speaker_id, body.name)
    return {"ok": True}


@app.delete("/api/speakers/{speaker_id}")
def api_delete_speaker(speaker_id: str):
    db.delete_speaker(speaker_id)
    return {"ok": True}


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
