"""FR-12: liên kết tài khoản Messup, gửi transcript theo hợp đồng, báo bản ghi
trùng, hàng chờ gửi lại. Messup giả bằng httpx.MockTransport (biên ngoài duy nhất)."""
from __future__ import annotations

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app import auth, db, messup, transcribe
from app.main import app

MESSUP = "https://messup.test"


class FakeMessup:
    """Trả lời đúng hình dạng docs/manju-contract.md của messup-enterprise."""

    def __init__(self) -> None:
        self.meetings: list[dict] = []
        self.down = False
        self.duplicate_of: int | None = None
        self.by_hash: dict[str, int] = {}

    def handler(self, request: httpx.Request) -> httpx.Response:
        if self.down:
            raise httpx.ConnectError("no route", request=request)
        path, body = request.url.path, json.loads(request.content or b"{}")
        if path == "/api/login":
            if body["password"] != "messup-pass":
                return httpx.Response(401, json={"error": "Sai tài khoản hoặc mật khẩu", "code": "UNAUTHORIZED"})
            role = "employee" if body["login"] == "member" else "admin"
            return httpx.Response(200, json={"token": "mcp_tok", "user": {"id": 7, "login": body["login"], "name": "Lan", "role": role}})
        if request.headers.get("authorization") == "Bearer mcp_botkey" and path == "/api/me":
            return httpx.Response(200, json={"id": 1, "login": "admin", "name": "", "role": "admin", "projectIds": [1],
                                             "bot": {"id": 3, "name": "bot-kho", "projectId": 1}})
        if request.headers.get("authorization") == "Bearer mcp_botkey":
            request.headers["authorization"] = "Bearer mcp_tok"  # the fake answers a bot key like a token
        if path == "/api/me" and request.headers.get("authorization") == "Bearer mcp_tok":
            return httpx.Response(200, json={"id": 7, "login": "lan", "name": "Lan", "role": "employee", "projectIds": [1], "bot": None})
        if request.headers.get("authorization") != "Bearer mcp_tok":
            return httpx.Response(401, json={"error": "Chưa đăng nhập", "code": "UNAUTHORIZED"})
        if path == "/api/projects":
            return httpx.Response(200, json={"projects": [{"id": 1, "name": "ACME Q4", "status": "active"}]})
        if path == "/api/people":
            return httpx.Response(200, json={"people": [{"id": 12, "name": "Hùng", "title": "", "hasVoice": False}]})
        if path == "/api/meetings":
            if body.get("projectId") == 99:
                return httpx.Response(404, json={"error": "Không tìm thấy", "code": "NOT_FOUND"})
            if body["audioHash"] in self.by_hash:
                return httpx.Response(200, json={"sourceId": self.by_hash[body["audioHash"]], "created": False, "duplicateOf": self.duplicate_of})
            self.meetings.append(body)
            source_id = 40 + len(self.meetings)
            self.by_hash[body["audioHash"]] = source_id
            return httpx.Response(201, json={"sourceId": source_id, "created": True, "events": len(body["segments"]), "duplicateOf": self.duplicate_of})
        return httpx.Response(404, json={"error": "Không tìm thấy"})


@pytest.fixture
def fake(monkeypatch) -> FakeMessup:
    fake = FakeMessup()
    monkeypatch.setenv("MESSUP_URL", MESSUP)
    monkeypatch.setattr(
        messup, "_client",
        lambda: httpx.Client(base_url=MESSUP, transport=httpx.MockTransport(fake.handler)),
    )
    return fake


@pytest.fixture
def ent(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setattr(db, "DATA", tmp_path)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(db, "DEFAULT_RECORDINGS", tmp_path / "rec")
    monkeypatch.setattr(transcribe, "TRANSCRIPTS", tmp_path / "tx")
    monkeypatch.setattr(transcribe, "UPLOADS", tmp_path / "up")
    for d in ("tx", "rec", "up"):
        (tmp_path / d).mkdir()
    monkeypatch.setenv("MANJU_EDITION", "enterprise")
    monkeypatch.delenv("MESSUP_URL", raising=False)
    auth.reset_rate_limit()
    db.init()
    client = TestClient(app)
    r = client.post("/api/auth/register", json={"phone": "0901111111", "name": "Nguyen A", "password": "secret1"})
    assert r.status_code == 200, r.text
    return client


def make_transcript(owner_id: str, *, title: str = "Họp giao hàng") -> str:
    return transcribe.save_transcript(transcribe.TranscriptDraft(
        original_name=title,
        language="vi",
        duration=120.0,
        text="Chào mọi người. Chốt giao ngày 20/11.",
        model_name="stt-rt",
        raw_text=None,
        segments=[
            {"start": 0.0, "end": 3.2, "text": "Chào mọi người", "speaker": "1"},
            {"start": 4.0, "end": 9.5, "text": "Chốt giao ngày 20/11", "speaker": "2"},
            {"start": 10.0, "end": 10.5, "text": "  "},
        ],
        audio_path=None,
        owner_id=owner_id,
    ))


def me(client: TestClient) -> str:
    return client.get("/api/auth/me").json()["id"]


def link(client: TestClient) -> None:
    r = client.post("/api/messup/link", json={"login": "lan", "password": "messup-pass"})
    assert r.status_code == 200, r.text


def test_feature_is_off_until_messup_url_is_set(ent):
    assert ent.get("/api/messup/status").json() == {"configured": False, "linked": False, "login": None, "name": None}
    r = ent.post("/api/messup/link", json={"login": "lan", "password": "messup-pass"})
    assert r.status_code == 503


def test_link_stores_token_not_password_and_rejects_bad_password(ent, fake):
    bad = ent.post("/api/messup/link", json={"login": "lan", "password": "wrong"})
    assert bad.status_code == 400 and "Sai tài khoản" in bad.json()["detail"]
    assert ent.get("/api/messup/status").json()["linked"] is False

    # Tài khoản thành viên của Messup chỉ đọc: không liên kết được, token không được lưu.
    member = ent.post("/api/messup/link", json={"login": "member", "password": "messup-pass"})
    assert member.status_code == 400 and "chỉ đọc" in member.json()["detail"]
    assert ent.get("/api/messup/status").json()["linked"] is False

    link(ent)
    status = ent.get("/api/messup/status").json()
    assert status == {"configured": True, "linked": True, "login": "lan", "name": "Lan"}
    stored = db.get_messup_link(me(ent))
    assert stored["token"] == "mcp_tok" and "messup-pass" not in json.dumps(stored)
    assert ent.get("/api/messup/projects").json() == [{"id": 1, "name": "ACME Q4", "status": "active"}]
    assert ent.get("/api/messup/people").json()[0]["name"] == "Hùng"

    assert ent.delete("/api/messup/link").json() == {"linked": False}
    assert ent.get("/api/messup/projects").status_code == 409


def test_link_with_a_bot_key_sends_meetings_with_that_key(ent, fake):
    wrong = ent.post("/api/messup/link", json={"key": "mcp_nope"})
    assert wrong.status_code == 400 and "không nhận khoá" in wrong.json()["detail"]
    person = ent.post("/api/messup/link", json={"key": "mcp_tok"})
    assert person.status_code == 400 and "không phải khoá Manju của bot" in person.json()["detail"]
    assert ent.post("/api/messup/link", json={}).status_code == 400

    r = ent.post("/api/messup/link", json={"key": "mcp_botkey"})
    assert r.status_code == 200, r.text
    assert ent.get("/api/messup/status").json() == {"configured": True, "linked": True, "login": "bot:bot-kho", "name": "bot-kho"}
    assert db.get_messup_link(me(ent))["token"] == "mcp_botkey"

    tid = make_transcript(me(ent))
    sent = ent.post(f"/api/transcripts/{tid}/messup", json={"speakers": []})
    assert sent.status_code == 200, sent.text
    assert len(fake.meetings) == 1 and "projectId" not in fake.meetings[0]


def test_send_maps_segments_and_speakers_to_the_contract(ent, fake):
    link(ent)
    tid = make_transcript(me(ent))
    assert ent.get(f"/api/transcripts/{tid}/messup").json()["labels"] == ["Speaker 1", "Speaker 2", "Speaker"]

    r = ent.post(f"/api/transcripts/{tid}/messup", json={
        "projectId": 1,
        "speakers": [{"label": "Speaker 1", "personId": 12}, {"label": "Speaker 2", "newName": "Khách mới"},
                     {"label": "Speaker 9", "personId": 3}],
    })
    assert r.status_code == 200, r.text
    assert (r.json()["status"], r.json()["source_id"], r.json()["duplicate_of"]) == ("sent", 41, None)

    sent = fake.meetings[0]
    assert sent["projectId"] == 1
    assert sent["name"] == "Họp giao hàng"
    assert sent["audioHash"] == messup.audio_hash(tid) and 8 <= len(sent["audioHash"]) <= 128
    assert sent["segments"] == [
        {"index": 0, "speaker": "Speaker 1", "startMs": 0, "endMs": 3200, "text": "Chào mọi người"},
        {"index": 1, "speaker": "Speaker 2", "startMs": 4000, "endMs": 9500, "text": "Chốt giao ngày 20/11"},
    ]
    assert sent["speakers"] == [
        {"label": "Speaker 1", "personId": 12},
        {"label": "Speaker 2", "newPerson": {"name": "Khách mới"}},
    ]
    # startedAt = lúc lưu lùi lại đúng thời lượng phiên
    created = db.read_transcript(tid)["created_at"]
    from datetime import datetime, timedelta
    assert datetime.fromisoformat(sent["startedAt"]) == datetime.fromisoformat(created) - timedelta(seconds=120)


def test_project_is_optional_and_resending_does_not_duplicate(ent, fake):
    link(ent)
    tid = make_transcript(me(ent))
    first = ent.post(f"/api/transcripts/{tid}/messup", json={}).json()
    again = ent.post(f"/api/transcripts/{tid}/messup", json={}).json()
    assert "projectId" not in fake.meetings[0]
    assert len(fake.meetings) == 1
    assert first["source_id"] == again["source_id"] == 41


def test_duplicate_recording_is_reported(ent, fake):
    link(ent)
    fake.duplicate_of = 5
    tid = make_transcript(me(ent))
    r = ent.post(f"/api/transcripts/{tid}/messup", json={"projectId": 1})
    assert r.json()["duplicate_of"] == 5
    assert ent.get(f"/api/transcripts/{tid}/messup").json()["sync"]["duplicate_of"] == 5


def test_network_failure_is_queued_and_retried(ent, fake):
    link(ent)
    tid = make_transcript(me(ent))
    fake.down = True
    r = ent.post(f"/api/transcripts/{tid}/messup", json={"projectId": 1, "speakers": [{"label": "Speaker 1", "personId": 12}]})
    assert r.status_code == 200 and r.json()["status"] == "failed"
    assert messup.retry_failed() == 0  # vẫn mất mạng

    fake.down = False
    assert messup.retry_failed() == 1
    sync = db.get_messup_sync(tid)
    assert (sync["status"], sync["source_id"], sync["attempts"]) == ("sent", 41, 3)
    assert fake.meetings[0]["projectId"] == 1
    assert fake.meetings[0]["speakers"] == [{"label": "Speaker 1", "personId": 12}]
    assert messup.retry_failed() == 0  # không gửi lại bản đã tới


def test_rejection_is_shown_and_not_retried(ent, fake):
    link(ent)
    tid = make_transcript(me(ent))
    r = ent.post(f"/api/transcripts/{tid}/messup", json={"projectId": 99})
    assert r.status_code == 400
    assert db.get_messup_sync(tid)["status"] == "rejected"
    assert messup.retry_failed() == 0 and fake.meetings == []


def test_sending_needs_a_link_and_only_the_owner_can_send(ent, fake):
    tid = make_transcript(me(ent))
    assert ent.post(f"/api/transcripts/{tid}/messup", json={}).status_code == 409

    other = TestClient(app)
    other.post("/api/auth/register", json={"phone": "0902222222", "name": "Tran B", "password": "secret1"})
    db.update_user(other.get("/api/auth/me").json()["id"], status="active")
    assert other.post(f"/api/transcripts/{tid}/messup", json={}).status_code == 404
    assert other.get(f"/api/transcripts/{tid}/messup").status_code == 404


def test_local_edition_has_no_messup_routes(ent, fake, monkeypatch):
    monkeypatch.setenv("MANJU_EDITION", "local")
    assert TestClient(app).get("/api/messup/status").status_code == 404
