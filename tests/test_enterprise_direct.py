"""FR-11 T-011: live direct (server chỉ nhận text), temp key Soniox, upload bytes
không chạm đĩa, MCP client. Soniox mock bằng httpx.MockTransport; WS qua TestClient."""
from __future__ import annotations

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app import auth, cloud_stt, db, live_direct, transcribe
from app.main import app


@pytest.fixture
def ent(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DATA", tmp_path)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(db, "DEFAULT_RECORDINGS", tmp_path / "rec")
    monkeypatch.setattr(transcribe, "TRANSCRIPTS", tmp_path / "tx")
    monkeypatch.setattr(transcribe, "UPLOADS", tmp_path / "up")
    for d in ("tx", "rec", "up"):
        (tmp_path / d).mkdir()
    monkeypatch.setenv("MANJU_EDITION", "enterprise")
    monkeypatch.setenv("SONIOX_API_KEY", "real-key")
    auth.reset_rate_limit()
    db.init()
    c = TestClient(app)
    r = c.post("/api/auth/register", json={"phone": "0901111111", "name": "Admin A", "password": "secret1"})
    assert r.status_code == 200
    return c


# ── DirectSession ──────────────────────────────────────────────────────────
def test_direct_session_groups_utts_and_saves_without_audio(ent):
    s = live_direct.DirectSession({"language": "vi", "title": "Họp sprint"}, owner_id="u-1")
    assert s.add_utt({"utt": 1, "text": "xin chào ", "start_ms": 0, "end_ms": 900, "speaker": "1"}) == 1
    assert s.add_utt({"utt": 2, "text": "deploy model", "start_ms": 1500, "end_ms": 2400, "speaker": "2"}) == 2
    assert s.add_utt({"utt": 2, "text": "deploy model", "start_ms": 1500, "end_ms": 2400, "speaker": "2"}) == 2  # replay
    assert s.add_utt({"text": "thiếu utt"}) is None
    tid = s.shutdown()
    assert tid is not None and s.shutdown() == tid  # idempotent
    data = db.read_transcript(tid)
    assert data is not None
    assert data["text"] == "xin chào deploy model"
    assert data["owner_id"] == "u-1"
    assert data["audio"] is None
    assert data["duration"] == 2.4
    assert data["segments"] == [
        {"start": 0.0, "end": 0.9, "text": "xin chào", "speaker": "1"},
        {"start": 1.5, "end": 2.4, "text": "deploy model", "speaker": "2"},
    ]
    assert data["model"] == "stt-rt-v6"


def test_direct_session_empty_saves_nothing(ent):
    s = live_direct.DirectSession({}, owner_id=None)
    assert s.shutdown() is None
    assert db.list_transcripts() == []


# ── WS /ws/live mode direct qua TestClient ─────────────────────────────────
def test_ws_direct_flow_saves_text_for_owner(ent):
    me = ent.get("/api/auth/me").json()
    with ent.websocket_connect("/ws/live") as ws:
        ws.send_text(json.dumps({"type": "start", "mode": "direct", "language": "vi", "title": "WS họp"}))
        assert ws.receive_json()["type"] == "session"
        assert ws.receive_json()["type"] == "ready"
        ws.send_text(json.dumps({"type": "utt", "utt": 1, "text": "câu một", "start_ms": 0, "end_ms": 800}))
        assert ws.receive_json() == {"type": "ack_utt", "utt": 1}
        ws.send_bytes(b"\x00\x00")  # PCM lạc → bỏ qua, không lỗi
        ws.send_text(json.dumps({"type": "stop"}))
        saved = ws.receive_json()
    assert saved["type"] == "saved" and saved["transcript_id"]
    data = db.read_transcript(saved["transcript_id"])
    assert data is not None and data["text"] == "câu một" and data["owner_id"] == me["id"]


def test_ws_rejects_non_direct_and_unauthenticated(ent):
    with ent.websocket_connect("/ws/live") as ws:
        ws.send_text(json.dumps({"type": "start", "mode": "online"}))
        msg = ws.receive_json()
        assert msg["type"] == "error" and "direct" in msg["message"]
    bare = TestClient(app)
    with bare.websocket_connect("/ws/live") as ws:
        msg = ws.receive_json()
        assert msg["type"] == "error" and "đăng nhập" in msg["message"]


def test_ws_direct_resume_replays_idempotent(ent, monkeypatch):
    monkeypatch.setattr(live_direct, "RESUME_GRACE_S", 30.0)
    with ent.websocket_connect("/ws/live") as ws:
        ws.send_text(json.dumps({"type": "start", "mode": "direct"}))
        token = ws.receive_json()["token"]
        ws.receive_json()  # ready
        ws.send_text(json.dumps({"type": "utt", "utt": 1, "text": "a", "start_ms": 0, "end_ms": 500}))
        ws.receive_json()
    # rớt WS (không stop) → park; nối lại bằng token, replay utt 1 + thêm utt 2
    with ent.websocket_connect("/ws/live") as ws:
        ws.send_text(json.dumps({"type": "resume", "token": token}))
        msg = ws.receive_json()
        assert msg["type"] == "resumed" and msg["utts"] == [1]
        ws.send_text(json.dumps({"type": "utt", "utt": 1, "text": "a", "start_ms": 0, "end_ms": 500}))
        ws.receive_json()
        ws.send_text(json.dumps({"type": "utt", "utt": 2, "text": "b", "start_ms": 1500, "end_ms": 2000}))
        ws.receive_json()
        ws.send_text(json.dumps({"type": "stop"}))
        tid = ws.receive_json()["transcript_id"]
    data = db.read_transcript(tid)
    assert data is not None and data["text"] == "a b"


# ── Temp key + /api/cloud/session ──────────────────────────────────────────
def _soniox_mock(seen: dict):
    def handler(req: httpx.Request) -> httpx.Response:
        seen["auth"] = req.headers.get("authorization")
        if req.method == "DELETE":
            seen.setdefault("deleted", []).append(req.url.path)
            return httpx.Response(204)
        if req.url.path.endswith("/auth/temporary-api-key"):
            seen["body"] = json.loads(req.content)
            return httpx.Response(200, json={"api_key": "snx_temp_abc", "expires_at": "2026-11-09T10:00:00Z"})
        if req.url.path.endswith("/files"):
            seen["upload_name"] = req.headers.get("content-type", "")
            return httpx.Response(200, json={"id": "f1"})
        if req.url.path.endswith("/transcriptions") and req.method == "POST":
            seen["job"] = json.loads(req.content)
            return httpx.Response(200, json={"id": "t1"})
        if req.url.path.endswith("/transcriptions/t1"):
            return httpx.Response(200, json={"status": "completed"})
        if req.url.path.endswith("/transcript"):
            return httpx.Response(200, json={"text": "nội dung file"})
        return httpx.Response(404)

    return httpx.Client(base_url=cloud_stt.API_BASE, transport=httpx.MockTransport(handler))


def test_create_temporary_key_uses_real_key_server_side(monkeypatch):
    monkeypatch.setenv("SONIOX_API_KEY", "real-key")
    seen: dict = {}
    out = cloud_stt.create_temporary_key("manju:u-1", client=_soniox_mock(seen))
    assert out == {"api_key": "snx_temp_abc", "expires_at": "2026-11-09T10:00:00Z"}
    assert seen["auth"] == "Bearer real-key"
    assert seen["body"]["usage_type"] == "transcribe_websocket"
    assert seen["body"]["client_reference_id"] == "manju:u-1"


def test_cloud_session_endpoint_returns_temp_key_and_config(ent, monkeypatch):
    seen: dict = {}
    mock = _soniox_mock(seen)
    monkeypatch.setattr(cloud_stt, "_client", lambda c: (mock, False))
    ent.put("/api/settings", json={"glossary": "Kubernetes, Claude"})
    r = ent.post("/api/cloud/session", json={"language": "vi", "glossary": "sprint"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["api_key"] == "snx_temp_abc"
    assert body["ws_url"] == cloud_stt.RT_URL
    cfg = body["config"]
    assert cfg["model"] == "stt-rt-v6" and "api_key" not in cfg
    assert cfg["language_hints"] == ["vi", "en"]
    assert set(cfg["context"]["terms"]) >= {"Kubernetes", "Claude", "sprint"}
    assert "real-key" not in r.text  # key thật không rời server


def test_cloud_session_without_key_503(ent, monkeypatch):
    monkeypatch.delenv("SONIOX_API_KEY")
    assert ent.post("/api/cloud/session", json={}).status_code == 503


# ── Upload bytes trong RAM ─────────────────────────────────────────────────
def test_upload_enterprise_in_memory_saves_owner_no_disk(ent, tmp_path, monkeypatch):
    seen: dict = {}
    mock = _soniox_mock(seen)
    monkeypatch.setattr(cloud_stt, "_client", lambda c: (mock, False))
    me = ent.get("/api/auth/me").json()
    r = ent.post("/api/transcribe", files={"file": ("hop.m4a", b"\x00" * 64, "audio/mp4")},
                 data={"language": "vi", "correct": "false"})
    assert r.status_code == 200, r.text
    job_id = r.json()["job_id"]
    import time
    for _ in range(100):
        job = ent.get(f"/api/jobs/{job_id}").json()
        if job["status"] in ("done", "error"):
            break
        time.sleep(0.02)
    assert job["status"] == "done", job
    data = db.read_transcript(job["transcript_id"])
    assert data is not None and data["text"] == "nội dung file"
    assert data["owner_id"] == me["id"] and data["audio"] is None
    assert list((tmp_path / "up").iterdir()) == []  # không ghi đĩa
    assert set(seen["deleted"]) == {"/v1/transcriptions/t1", "/v1/files/f1"}  # dọn phía Soniox
    assert seen["job"]["model"] == "stt-async-v6"


# ── MCP client enterprise ──────────────────────────────────────────────────
def test_mcp_enterprise_client_uses_token_and_scope(ent, monkeypatch):
    from mcp_server import enterprise as mcp_ent

    me = ent.get("/api/auth/me").json()
    tid = transcribe.save_transcript(transcribe.TranscriptDraft(
        original_name="hop-mcp", language="vi", duration=5.0, text="bàn về kubernetes",
        model_name="stt-rt-v6", owner_id=me["id"],
    ))
    db.set_edited_text(tid, "bàn về Kubernetes")
    token = ent.post("/api/auth/tokens", json={"label": "t"}).json()["token"]
    monkeypatch.setenv("MANJU_URL", "http://testserver")
    monkeypatch.setenv("MANJU_TOKEN", token)
    # TestClient là httpx.Client đồng bộ chạy ASGI in-process — thay _client thật.
    monkeypatch.setattr(
        mcp_ent, "_client", lambda: TestClient(app, headers={"Authorization": f"Bearer {token}"})
    )
    assert [t["id"] for t in mcp_ent.list_transcripts()] == [tid]
    assert [t["id"] for t in mcp_ent.search_transcripts("KUBER")] == [tid]
    assert mcp_ent.search_transcripts("không có") == []
    assert mcp_ent.read_transcript(tid)["text"] == "bàn về Kubernetes"  # ưu tiên bản sửa tay
