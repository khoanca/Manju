"""FastAPI app: upload file ghi âm → transcribe → text."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, Form, HTTPException, Response, UploadFile, WebSocket
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app import corrections, db, diarize, engines, live, org, subtitle, transcribe

logger = logging.getLogger(__name__)


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
    glossary: str | None = None


@app.get("/api/settings")
def api_settings():
    info = engines.get_engine().info
    return {
        "audio_dir": str(db.get_audio_dir()),
        "glossary": db.get_setting("glossary", "") or "",
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
    if body.glossary is not None:
        db.set_setting("glossary", body.glossary)
    return {
        "audio_dir": str(db.get_audio_dir()),
        "diarize_enabled": transcribe.diarize_enabled(),
        "glossary": db.get_setting("glossary", "") or "",
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


class EditedTextIn(BaseModel):
    """Body PATCH text — edited_text=None nghĩa là xoá bản sửa (quay về bản máy)."""

    edited_text: str | None
    base_text: str | None = None  # bản hiệu lực lúc mở editor — chống ghi đè chéo


@app.patch("/api/transcripts/{transcript_id}/text")
def api_edit_text(transcript_id: str, body: EditedTextIn):
    """Lưu bản user sửa tay (US-801). base_text lệch với bản hiệu lực hiện tại
    (edited_text nếu có, không thì text) → 409, UI báo reload."""
    data = transcribe.read_transcript(transcript_id)
    if data is None:
        raise HTTPException(404, "Không tìm thấy bản ghi")
    current = data.get("edited_text", data["text"])
    if body.base_text is not None and body.base_text != current:
        raise HTTPException(409, "Bản ghi đã thay đổi, tải lại trước khi lưu")
    db.set_edited_text(transcript_id, body.edited_text)
    # Hook T-006: trích cặp (sai → đúng) vào thư viện — never-fail,
    # lỗi trích cặp không được làm fail PATCH (US-802).
    pairs_extracted = 0
    if body.edited_text is not None and body.base_text is not None:
        try:
            pairs = corrections.extract_pairs(body.base_text, body.edited_text)
            for wrong, right in pairs:
                db.upsert_correction(wrong, right)
            pairs_extracted = len(pairs)
        except Exception:  # noqa: BLE001
            logger.warning("Trích cặp sửa lỗi thất bại: %s", transcript_id, exc_info=True)
    return {"ok": True, "edited_text": body.edited_text, "pairs_extracted": pairs_extracted}


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
    vps = diarize.to_np_voiceprints(db.load_voiceprints())
    smap = diarize.identify_clusters(audio, labeled, smap, vps)  # US-703: tự nhận diện
    db.update_speaker_layer(transcript_id, labeled, smap)
    return {
        "segments": labeled, "speaker_map": smap,
        "num_speakers": len(smap), "speakers": db.speaker_names(),
    }


class EnrollIn(BaseModel):
    transcript_id: str
    cluster: int


@app.post("/api/speakers/{speaker_id}/enroll")
def api_enroll(speaker_id: str, body: EnrollIn):
    """Ghi nhớ giọng: học voiceprint của 1 cụm trong 1 transcript cho speaker_id.
    Gọi lại nhiều lần → gộp thêm mẫu (centroid cập nhật, US-703 AC3)."""
    if not diarize.models_present():
        raise HTTPException(503, "Chưa tải model diarization")
    if speaker_id not in db.speaker_names():
        raise HTTPException(404, "Không tìm thấy người")
    data = transcribe.read_transcript(body.transcript_id)
    if data is None or not data.get("segments"):
        raise HTTPException(409, "Transcript không có segment đã tách giọng")
    audio = transcribe.transcript_audio_path(body.transcript_id)
    if audio is None:
        raise HTTPException(409, "Không còn file ghi âm để học giọng")
    spans = diarize.cluster_spans(data["segments"], body.cluster)
    if not spans:
        raise HTTPException(422, "Cụm giọng này không có đoạn nào trong bản ghi")
    vec = diarize.embed_spans(audio, spans)
    if vec is None:
        raise HTTPException(422, "Đoạn giọng quá ngắn để học (cần ≥ ~0.5s)")
    old = db.get_voiceprint(speaker_id)
    old_vec = diarize.to_np_voiceprints([("_", old[0])])[0][1] if old else None
    merged, count = diarize.merge_centroid(old_vec, old[1] if old else 0, vec)
    db.save_voiceprint(speaker_id, merged.tobytes(), merged.size, count, body.transcript_id)
    return {"ok": True, "sample_count": count}


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


# ── Thư viện sửa lỗi (US-802) — cặp (sai → đúng) trích từ chỉnh sửa ────────
@app.get("/api/corrections")
def api_corrections(
    status: str | None = None, source: str | None = None, tag: str | None = None
):
    return db.list_corrections(status=status, source=source, tag=tag)


class CorrectionIn(BaseModel):
    status: str | None = None  # pending|approved|rejected
    tag: str | None = None


@app.patch("/api/corrections/{correction_id}")
def api_update_correction(correction_id: str, body: CorrectionIn):
    if body.status is None and body.tag is None:
        raise HTTPException(400, "Không có gì để cập nhật (cần status hoặc tag)")
    if body.status is not None and body.status not in ("pending", "approved", "rejected"):
        raise HTTPException(400, "status phải là 'pending', 'approved' hoặc 'rejected'")
    found = True
    if body.status is not None:
        found = db.set_correction_status(correction_id, body.status)
    if body.tag is not None:
        found = db.set_correction_tag(correction_id, body.tag) and found
    if not found:
        raise HTTPException(404, "Không tìm thấy mục sửa lỗi")
    return {"ok": True}


@app.delete("/api/corrections/{correction_id}")
def api_delete_correction(correction_id: str):
    if not db.delete_correction(correction_id):
        raise HTTPException(404, "Không tìm thấy mục sửa lỗi")
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
