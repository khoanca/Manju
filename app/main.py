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

from app import (
    correct,
    corrections,
    db,
    diarize,
    engines,
    live,
    org,
    slang_trend,
    subtitle,
    transcribe,
)

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
    # US-804: bật/tắt seed lexicon theo vùng + URL nguồn cập nhật online (opt-in)
    lexicon_bac: bool | None = None
    lexicon_trung: bool | None = None
    lexicon_nam: bool | None = None
    lexicon_en: bool | None = None
    # US-815: seed slang/teencode MXH — mặc định tắt (họp trang trọng không nhiễm slang)
    lexicon_slang: bool | None = None
    lexicon_url: str | None = None
    # US-817: URL trang public tổng hợp trend (cách nhau khoảng trắng) — rỗng dùng mặc định
    slang_sources: str | None = None
    # US-818: hashtag TikTok quét qua Apify (cần APIFY_TOKEN) — rỗng dùng mặc định
    slang_hashtags: str | None = None
    # Toggle bổ trợ live: đánh dấu từ nghi sai + khử nhiễu đầu vào ("0"/"1")
    flag_words: bool | None = None
    live_ident: bool | None = None
    denoise_enabled: bool | None = None
    # Lớp B: tự đoán ngành + nạp lexicon ngành khi transcribe (mặc định bật)
    domain_auto: bool | None = None


# Field SettingsIn / key settings → vùng seed lexicon (app/data/lexicon/{region}.json)
_LEXICON_REGIONS = {
    "lexicon_bac": "bac",
    "lexicon_trung": "trung",
    "lexicon_nam": "nam",
    "lexicon_en": "en_accent",
    "lexicon_slang": "slang",
}


def _lexicon_settings() -> dict:
    """Trạng thái 4 toggle vùng (default tắt) + URL nguồn cập nhật cho UI."""
    out: dict = {
        field.removeprefix("lexicon_"): db.get_setting(field, "0") == "1"
        for field in _LEXICON_REGIONS
    }
    out["url"] = db.get_setting("lexicon_url", "") or ""
    return out


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
        "lexicon": _lexicon_settings(),
        "slang_sources": db.get_setting("slang_sources", "") or "",
        "slang_hashtags": db.get_setting("slang_hashtags", "") or "",
        "apify_enabled": slang_trend.apify_enabled(),
        "flag_words": db.get_setting("flag_words", "0") == "1",
        "live_ident": db.get_setting("live_ident", "0") == "1",
        "denoise_enabled": db.get_setting("denoise_enabled", "0") == "1",
        "domain_auto": db.get_setting("domain_auto", "1") == "1",
        "domains": list(corrections.DOMAINS),
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
    # US-804: bật vùng → nạp seed vào corrections; tắt → chỉ gỡ entry seed vùng đó
    for field, region in _LEXICON_REGIONS.items():
        enabled = getattr(body, field)
        if enabled is None:
            continue
        db.set_setting(field, "1" if enabled else "0")
        if enabled:
            corrections.import_seed(region)
        else:
            corrections.remove_seed(region)
    if body.lexicon_url is not None:
        db.set_setting("lexicon_url", body.lexicon_url.strip())
    if body.slang_sources is not None:
        db.set_setting("slang_sources", body.slang_sources.strip())
    if body.slang_hashtags is not None:
        db.set_setting("slang_hashtags", body.slang_hashtags.strip())
    if body.flag_words is not None:
        db.set_setting("flag_words", "1" if body.flag_words else "0")
    if body.live_ident is not None:
        db.set_setting("live_ident", "1" if body.live_ident else "0")
    if body.denoise_enabled is not None:
        db.set_setting("denoise_enabled", "1" if body.denoise_enabled else "0")
    if body.domain_auto is not None:
        db.set_setting("domain_auto", "1" if body.domain_auto else "0")
    return {
        "audio_dir": str(db.get_audio_dir()),
        "diarize_enabled": transcribe.diarize_enabled(),
        "glossary": db.get_setting("glossary", "") or "",
        "lexicon": _lexicon_settings(),
        "flag_words": db.get_setting("flag_words", "0") == "1",
        "live_ident": db.get_setting("live_ident", "0") == "1",
        "denoise_enabled": db.get_setting("denoise_enabled", "0") == "1",
        "domain_auto": db.get_setting("domain_auto", "1") == "1",
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


class GoldenIn(BaseModel):
    """Body PATCH golden — đánh dấu bản sửa tay làm chuẩn đo (US-819)."""

    golden: bool


@app.patch("/api/transcripts/{transcript_id}/golden")
def api_set_golden(transcript_id: str, body: GoldenIn):
    """Bật/tắt cờ dùng bản ghi này làm chuẩn đo độ chính xác.

    Yêu cầu đã có bản sửa tay: cờ golden trên bản máy chưa ai soát thì bộ đo
    sẽ chấm điểm chính output của máy — số đẹp giả, vô nghĩa.
    """
    data = transcribe.read_transcript(transcript_id)
    if data is None:
        raise HTTPException(404, "Không tìm thấy bản ghi")
    if body.golden and not (data.get("edited_text") or "").strip():
        raise HTTPException(400, "Cần sửa tay bản ghi trước khi dùng làm chuẩn đo")
    db.set_golden(transcript_id, body.golden)
    return {"ok": True, "golden": body.golden}


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
def _mine_terms_hook(transcript_id: str) -> None:
    """Hook mine từ vựng cá nhân theo người nói — never-fail, lỗi mine không
    được làm fail diarize/gán tên (mirror hook trích cặp US-802)."""
    try:
        corrections.mine_speaker_terms(transcript_id)
    except Exception:  # noqa: BLE001
        logger.warning("Mine từ vựng theo người thất bại: %s", transcript_id, exc_info=True)


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
    _mine_terms_hook(transcript_id)
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
    _mine_terms_hook(transcript_id)
    return {"speaker_map": smap, "speakers": db.speaker_names()}


@app.get("/api/speakers")
def api_speakers():
    return db.list_speakers()


class SpeakerIn(BaseModel):
    name: str
    region: str | None = None  # 'bac'|'trung'|'nam'|"" (rỗng = bỏ gán)|None (giữ nguyên)


@app.patch("/api/speakers/{speaker_id}")
def api_rename_speaker(speaker_id: str, body: SpeakerIn):
    if not body.name.strip():
        raise HTTPException(400, "Tên rỗng")
    if body.region is not None and body.region not in ("", "bac", "trung", "nam"):
        raise HTTPException(400, "region phải là 'bac', 'trung', 'nam' hoặc rỗng")
    db.rename_speaker(speaker_id, body.name)
    if body.region is not None:
        db.set_speaker_region(speaker_id, body.region or None)
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


@app.post("/api/lexicon/update")
def api_lexicon_update():
    """Tải thư viện từ nguồn remote (opt-in, US-804 AC2) — verify sha256 xong
    mới nhập (INSERT OR IGNORE, source='remote'); lỗi → bảng không đổi."""
    url = (db.get_setting("lexicon_url", "") or "").strip()
    if not url:
        raise HTTPException(400, "Chưa cấu hình nguồn cập nhật thư viện")
    try:
        entries = corrections.fetch_remote_lexicon(url)
    except corrections.LexiconError as exc:
        raise HTTPException(502, str(exc)) from exc
    return {"ok": True, "imported": corrections.import_remote(entries)}


@app.get("/api/domains")
def api_domains():
    """Danh sách ngành có seed lexicon (Lớp C) — UI hiện nút research/nạp."""
    return {"domains": list(corrections.DOMAINS)}


class DomainResearchIn(BaseModel):
    domain: str


@app.post("/api/lexicon/domain-research")
def api_domain_research(body: DomainResearchIn):
    """Lớp C: LLM research thuật ngữ 1 ngành → nhập pending (tag 'dom:{domain}',
    source 'trend') chờ user duyệt — không tự vào bias."""
    if body.domain not in corrections.DOMAINS:
        raise HTTPException(400, f"Ngành không hợp lệ (chọn trong {corrections.DOMAINS})")
    if not correct.openrouter_enabled():
        raise HTTPException(
            503, "Chưa cấu hình OPENROUTER_API_KEY — research ngành cần LLM cloud"
        )
    try:
        res = slang_trend.run_domain_research(body.domain)
    except Exception as exc:  # noqa: BLE001 — mạng/LLM lỗi → 502 cho client biết
        raise HTTPException(502, f"Không research được ngành: {exc}") from exc
    return {"ok": True, "new_pending": res.new_pending, "skipped": res.skipped}


@app.post("/api/lexicon/slang-trend")
def api_slang_trend():
    """US-816: LLM tổng hợp slang MXH đang hot → nhập pending (tag 'slang',
    source 'trend') chờ user duyệt — không tự vào bias ASR/pass 2."""
    if not correct.openrouter_enabled():
        raise HTTPException(
            503, "Chưa cấu hình OPENROUTER_API_KEY — tổng hợp trend cần LLM cloud"
        )
    res = slang_trend.run_trend_update()
    if res.sources_ok == 0:
        raise HTTPException(502, "Không lấy được dữ liệu xu hướng từ nguồn nào")
    return {
        "ok": True,
        "new_pending": res.new_pending,
        "skipped": res.skipped,
        "sources_ok": res.sources_ok,
        "sources_skipped": res.sources_skipped,
    }


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
