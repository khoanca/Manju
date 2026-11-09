"""FR-12 — endpoint nối Manju Enterprise với Messup Enterprise (xem app/messup.py).

Mọi route ở đây đã qua `enterprise_gate` (đăng nhập + active); các route
`/api/transcripts/{id}/…` còn được gate lọc theo chủ bản ghi.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app import db, edition, messup, transcribe

router = APIRouter()


class LinkIn(BaseModel):
    login: str
    password: str


class SpeakerIn(BaseModel):
    label: str
    personId: int | None = None
    newName: str | None = None


class SendIn(BaseModel):
    projectId: int | None = None
    name: str = ""
    speakers: list[SpeakerIn] = []


def _user(request: Request) -> dict:
    if not edition.is_enterprise():
        raise HTTPException(404, "Chỉ có ở bản enterprise")
    return request.state.user


def _http(exc: messup.MessupError) -> HTTPException:
    if exc.status == 401:
        return HTTPException(409, "Messup không nhận token nữa — hãy liên kết lại tài khoản")
    if exc.status in (400, 404, 409, 413, 503):
        return HTTPException(exc.status if exc.status != 404 else 400, str(exc))
    return HTTPException(502, str(exc))


def _token(user: dict) -> str:
    link = db.get_messup_link(user["id"])
    if link is None:
        raise HTTPException(409, "Chưa liên kết tài khoản Messup")
    return link["token"]


@router.get("/api/messup/status")
def status(request: Request):
    user = _user(request)
    link = db.get_messup_link(user["id"])
    return {
        "configured": messup.configured(),
        "linked": link is not None,
        "login": link["login"] if link else None,
        "name": link["name"] if link else None,
    }


@router.post("/api/messup/link")
def link_account(body: LinkIn, request: Request):
    user = _user(request)
    try:
        res = messup.login(body.login.strip(), body.password)
    except messup.MessupError as exc:
        if exc.status == 401:
            raise HTTPException(400, "Sai tài khoản hoặc mật khẩu Messup") from exc
        raise _http(exc) from exc
    remote = res.get("user") or {}
    db.set_messup_link(user["id"], res["token"], remote.get("login") or body.login.strip(),
                       remote.get("name") or "")
    return {"linked": True, "login": remote.get("login"), "name": remote.get("name")}


@router.delete("/api/messup/link")
def unlink_account(request: Request):
    db.delete_messup_link(_user(request)["id"])
    return {"linked": False}


@router.get("/api/messup/projects")
def list_projects(request: Request):
    try:
        return messup.projects(_token(_user(request)))
    except messup.MessupError as exc:
        raise _http(exc) from exc


@router.get("/api/messup/people")
def list_people(request: Request, q: str = ""):
    try:
        return messup.people(_token(_user(request)), q)
    except messup.MessupError as exc:
        raise _http(exc) from exc


def _transcript(transcript_id: str) -> dict:
    transcript = transcribe.read_transcript(transcript_id)
    if transcript is None:
        raise HTTPException(404, "Không tìm thấy transcript")
    return transcript


@router.get("/api/transcripts/{transcript_id}/messup")
def sync_state(transcript_id: str, request: Request):
    _user(request)
    transcript = _transcript(transcript_id)
    return {
        "labels": messup.speaker_labels(transcript),
        "title": transcript["title"],
        "sync": db.get_messup_sync(transcript_id),
    }


@router.post("/api/transcripts/{transcript_id}/messup")
def send(transcript_id: str, body: SendIn, request: Request):
    user = _user(request)
    transcript = _transcript(transcript_id)
    req = messup.PushRequest(body.projectId, body.name.strip(),
                             [s.model_dump(exclude_none=True) for s in body.speakers])
    try:
        return messup.push(transcript, user["id"], req)
    except messup.MessupError as exc:
        if exc.retriable:
            # Đã lưu vào hàng chờ: vòng nền sẽ tự gửi lại, người dùng không cần làm gì.
            return db.get_messup_sync(transcript_id)
        raise _http(exc) from exc
