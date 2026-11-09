"""FR-12 — gửi transcript sang Messup Enterprise (kho chung chat + họp của công ty).

Hợp đồng: `docs/manju-contract.md` của repo messup-enterprise. Chỉ gửi TEXT theo
segment; không có đường nào gửi audio. Mỗi user Manju liên kết một lần với tài
khoản Messup của mình (Messup cấp token), vì quyền project ở Messup gán theo người.

`MESSUP_URL` chưa đặt → tính năng tắt hẳn (UI ẩn).
"""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from datetime import datetime, timedelta

import httpx

from app import db

TIMEOUT_S = 30.0
MAX_ATTEMPTS = 10


class MessupError(Exception):
    """Messup từ chối hoặc không tới được. `retriable` = nên tự gửi lại sau."""

    def __init__(self, message: str, *, status: int = 0, retriable: bool = False):
        super().__init__(message)
        self.status = status
        self.retriable = retriable


def base_url() -> str:
    return os.environ.get("MESSUP_URL", "").rstrip("/")


def configured() -> bool:
    return bool(base_url())


def _client() -> httpx.Client:
    return httpx.Client(base_url=base_url(), timeout=TIMEOUT_S)


def _call(method: str, path: str, *, token: str | None = None, body: dict | None = None,
          params: dict | None = None) -> dict:
    if not configured():
        raise MessupError("Admin chưa cấu hình MESSUP_URL", status=503)
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        with _client() as client:
            resp = client.request(method, path, json=body, params=params, headers=headers)
    except httpx.HTTPError as exc:
        raise MessupError(f"Không kết nối được Messup: {exc}", retriable=True) from exc
    if resp.status_code >= 400:
        try:
            detail = resp.json().get("error") or resp.text[:200]
        except ValueError:
            detail = resp.text[:200]
        raise MessupError(str(detail), status=resp.status_code, retriable=resp.status_code >= 500)
    return resp.json()


def login(login_name: str, password: str) -> dict:
    """Đổi tài khoản/mật khẩu Messup lấy token agent. Mật khẩu không được lưu."""
    return _call("POST", "/api/login", body={"login": login_name, "password": password,
                                             "device": "manju"})


def projects(token: str) -> list[dict]:
    return _call("GET", "/api/projects", token=token).get("projects", [])


def people(token: str, q: str = "") -> list[dict]:
    return _call("GET", "/api/people", token=token, params={"q": q} if q else None).get("people", [])


# ── Dựng payload ───────────────────────────────────────────────────────────
@dataclass(frozen=True)
class PushRequest:
    project_id: int | None
    name: str
    speakers: list[dict]  # [{label, personId?} | {label, newName?}]


def audio_hash(transcript_id: str) -> str:
    """Khoá chống gửi trùng. Phiên live không có file audio trên server nên băm
    mã transcript: cùng một bản ghi luôn ra cùng khoá, gửi lại là an toàn."""
    return "manju-" + hashlib.sha256(transcript_id.encode()).hexdigest()


def speaker_label(raw: object) -> str:
    return f"Speaker {raw}" if raw not in (None, "") else "Speaker"


def speaker_labels(transcript: dict) -> list[str]:
    seen: list[str] = []
    for seg in transcript.get("segments") or [{}]:
        label = speaker_label(seg.get("speaker"))
        if label not in seen:
            seen.append(label)
    return seen


def _started_at(transcript: dict) -> str:
    # created_at là lúc lưu (cuối phiên) → lùi lại bằng thời lượng để ra lúc bắt đầu.
    created = datetime.fromisoformat(transcript["created_at"])
    return (created - timedelta(seconds=float(transcript.get("duration") or 0))).isoformat()


def build_payload(transcript: dict, req: PushRequest) -> dict:
    segments = [
        {
            "index": i,
            "speaker": speaker_label(seg.get("speaker")),
            "startMs": int(float(seg.get("start") or 0) * 1000),
            "endMs": int(float(seg.get("end") or seg.get("start") or 0) * 1000),
            "text": seg["text"],
        }
        for i, seg in enumerate(transcript.get("segments") or [])
        if (seg.get("text") or "").strip()
    ]
    if not segments:  # bản cũ không có segment: gửi nguyên văn làm một đoạn
        text = transcript.get("edited_text") or transcript["text"]
        segments = [{"index": 0, "speaker": speaker_label(None), "startMs": 0, "text": text}]
    labels = {s["speaker"] for s in segments}
    speakers = []
    for sp in req.speakers:
        if sp.get("label") not in labels:
            continue
        if sp.get("personId"):
            speakers.append({"label": sp["label"], "personId": int(sp["personId"])})
        elif (sp.get("newName") or "").strip():
            speakers.append({"label": sp["label"], "newPerson": {"name": sp["newName"].strip()}})
    payload: dict = {
        "audioHash": audio_hash(transcript["id"]),
        "startedAt": _started_at(transcript),
        "name": (req.name or transcript["title"] or "Cuộc họp")[:200],
        "segments": segments,
        "speakers": speakers,
    }
    if req.project_id is not None:
        payload["projectId"] = req.project_id
    return payload


# ── Gửi + ghi trạng thái ───────────────────────────────────────────────────
def push(transcript: dict, user_id: str, req: PushRequest) -> dict:
    """Gửi một transcript bằng liên kết của `user_id`, ghi kết quả vào messup_sync.

    Lỗi mạng/5xx → trạng thái `failed` (tự gửi lại ở `retry_failed`); Messup từ
    chối (4xx) → `rejected`, cần người sửa rồi gửi lại.
    """
    link = db.get_messup_link(user_id)
    if link is None:
        raise MessupError("Chưa liên kết tài khoản Messup", status=409)
    saved = {"project_id": req.project_id, "name": req.name, "speakers": req.speakers}
    try:
        result = _call("POST", "/api/meetings", token=link["token"],
                       body=build_payload(transcript, req))
    except MessupError as exc:
        db.record_messup_attempt(transcript["id"], user_id, saved,
                                 status="failed" if exc.retriable else "rejected", error=str(exc))
        raise
    db.record_messup_attempt(transcript["id"], user_id, saved, status="sent",
                             source_id=result.get("sourceId"),
                             duplicate_of=result.get("duplicateOf"))
    return db.get_messup_sync(transcript["id"]) or {}


def retry_failed() -> int:
    """Gửi lại các bản lỗi mạng. Trả số bản đã gửi được."""
    sent = 0
    for row in db.failed_messup_syncs(MAX_ATTEMPTS):
        transcript = db.read_transcript(row["transcript_id"])
        if transcript is None:
            continue
        saved = row["request"]
        try:
            push(transcript, row["user_id"],
                 PushRequest(saved.get("project_id"), saved.get("name") or "", saved.get("speakers") or []))
            sent += 1
        except MessupError:
            continue
    return sent
