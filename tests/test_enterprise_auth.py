"""FR-11 T-011: auth SĐT+mật khẩu, duyệt user, scope theo chủ bản ghi, API token,
gate edition. TestClient thật, DB tmp, không mạng (Soniox mock ở test khác)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import auth, db, transcribe
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
    monkeypatch.delenv("SONIOX_API_KEY", raising=False)
    auth.reset_rate_limit()
    db.init()
    return TestClient(app)


def _client(ent: TestClient) -> TestClient:
    return TestClient(app)  # cookie jar riêng cho từng user


def register(c: TestClient, phone: str, name: str = "Nguyen A", password: str = "secret1") -> dict:
    r = c.post("/api/auth/register", json={"phone": phone, "name": name, "password": password})
    assert r.status_code == 200, r.text
    return r.json()


def test_first_user_is_admin_active_then_members_pending(ent):
    admin = register(ent, "0901111111")
    assert (admin["role"], admin["status"]) == ("admin", "active")
    c2 = _client(ent)
    member = register(c2, "0902222222", "Tran B")
    assert (member["role"], member["status"]) == ("member", "pending")
    # pending: đăng nhập được (me) nhưng API khác 403 chờ duyệt
    assert c2.get("/api/auth/me").status_code == 200
    r = c2.get("/api/transcripts")
    assert r.status_code == 403 and "duyệt" in r.json()["detail"]


def test_unauthenticated_api_401_and_static_open(ent):
    assert ent.get("/api/transcripts").status_code == 401
    assert ent.get("/").status_code == 200


def test_app_shell_is_always_revalidated_so_a_deploy_shows_up(ent):
    for path in ("/", "/sw.js", "/static/enterprise.js"):
        assert ent.get(path).headers["cache-control"] == "no-cache", path
    assert "cache-control" not in ent.get("/api/auth/me").headers


def test_login_gate_has_real_forms_and_an_error_slot_per_form(ent):
    html = ent.get("/").text
    for needle in ('<form id="authLogin"', '<form id="authRegister"', 'id="authErr"', 'id="regErr"',
                   'for="authPhone"', 'for="regPass"', 'id="authPendingRetry"'):
        assert needle in html, needle


def test_register_validation_and_duplicate(ent):
    r = ent.post("/api/auth/register", json={"phone": "abc", "name": "X Y", "password": "secret1"})
    assert r.status_code == 400
    r = ent.post("/api/auth/register", json={"phone": "0901111111", "name": "X Y", "password": "123"})
    assert r.status_code == 400
    register(ent, "0901111111")
    r = _client(ent).post("/api/auth/register", json={"phone": "090 111 1111", "name": "X Y", "password": "secret1"})
    assert r.status_code == 409  # chuẩn hoá SĐT rồi mới so


def test_login_wrong_password_rate_limited(ent):
    register(ent, "0901111111", password="secret1")
    c = _client(ent)
    for _ in range(auth.MAX_FAILS):
        assert c.post("/api/auth/login", json={"phone": "0901111111", "password": "x"}).status_code == 401
    r = c.post("/api/auth/login", json={"phone": "0901111111", "password": "secret1"})
    assert r.status_code == 429  # đúng mật khẩu nhưng đang bị khoá
    auth.reset_rate_limit()
    assert c.post("/api/auth/login", json={"phone": "0901111111", "password": "secret1"}).status_code == 200


def test_admin_approves_and_blocks_member(ent):
    register(ent, "0901111111")  # admin (ent giữ cookie admin)
    c2 = _client(ent)
    member = register(c2, "0902222222", "Tran B")
    # member không được gọi /api/users
    assert c2.get("/api/users").status_code == 403
    r = ent.patch(f"/api/users/{member['id']}", json={"status": "active"})
    assert r.status_code == 200 and r.json()["status"] == "active"
    assert c2.get("/api/transcripts").status_code == 200
    r = ent.patch(f"/api/users/{member['id']}", json={"status": "blocked"})
    assert r.status_code == 200
    assert c2.get("/api/transcripts").status_code == 403
    assert c2.post("/api/auth/login", json={"phone": "0902222222", "password": "secret1"}).status_code == 403


def test_cannot_demote_last_admin(ent):
    admin = register(ent, "0901111111")
    r = ent.patch(f"/api/users/{admin['id']}", json={"role": "member"})
    assert r.status_code == 400
    r = ent.patch(f"/api/users/{admin['id']}", json={"status": "blocked"})
    assert r.status_code == 400


def _save(owner_id: str | None, title: str) -> str:
    return transcribe.save_transcript(transcribe.TranscriptDraft(
        original_name=title, language="vi", duration=10.0, text=f"nội dung {title}",
        model_name="stt-rt-v6", owner_id=owner_id,
    ))


def test_member_sees_only_own_transcripts_admin_sees_all(ent):
    admin = register(ent, "0901111111")
    c2 = _client(ent)
    member = register(c2, "0902222222", "Tran B")
    ent.patch(f"/api/users/{member['id']}", json={"status": "active"})
    t_admin = _save(admin["id"], "hop-admin")
    t_member = _save(member["id"], "hop-member")

    ids = {t["id"] for t in c2.get("/api/transcripts").json()}
    assert ids == {t_member}
    assert c2.get(f"/api/transcripts/{t_member}").status_code == 200
    assert c2.get(f"/api/transcripts/{t_admin}").status_code == 404  # không lộ tồn tại
    assert c2.get(f"/api/transcripts/{t_admin}/subtitle").status_code == 404

    ids = {t["id"] for t in ent.get("/api/transcripts").json()}
    assert ids == {t_admin, t_member}
    # search q khớp title/text, vẫn trong scope
    assert [t["id"] for t in c2.get("/api/transcripts", params={"q": "MEMBER"}).json()] == [t_member]
    assert c2.get("/api/transcripts", params={"q": "admin"}).json() == []


def test_settings_put_admin_only_and_edition_exposed(ent):
    register(ent, "0901111111")
    s = ent.get("/api/settings").json()
    assert s["edition"] == "enterprise"
    assert s["engine"]["tier"] == "cloud"
    assert s["cloud"]["available"] is False  # chưa có key
    c2 = _client(ent)
    member = register(c2, "0902222222", "Tran B")
    ent.patch(f"/api/users/{member['id']}", json={"status": "active"})
    assert c2.put("/api/settings", json={"glossary": "x"}).status_code == 403
    assert ent.put("/api/settings", json={"glossary": "Claude, Kubernetes"}).status_code == 200
    assert c2.get("/api/settings").json()["glossary"] == "Claude, Kubernetes"


def test_api_token_bearer_auth_for_mcp(ent):
    register(ent, "0901111111")
    tid = _save(ent.get("/api/auth/me").json()["id"], "hop")
    r = ent.post("/api/auth/tokens", json={"label": "claude"})
    assert r.status_code == 200
    token = r.json()["token"]
    assert token.startswith("mj_")
    # list không lộ token
    assert "token" not in ent.get("/api/auth/tokens").json()[0]
    bare = _client(ent)
    r = bare.get("/api/transcripts", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200 and [t["id"] for t in r.json()] == [tid]
    assert bare.get("/api/transcripts", headers={"Authorization": "Bearer mj_sai"}).status_code == 401
    ent.delete(f"/api/auth/tokens/{r.json() and ent.get('/api/auth/tokens').json()[0]['id']}")
    assert bare.get("/api/transcripts", headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_logout_ends_session(ent):
    register(ent, "0901111111")
    assert ent.get("/api/transcripts").status_code == 200
    ent.post("/api/auth/logout")
    assert ent.get("/api/transcripts").status_code == 401


def test_upload_requires_soniox_key_and_never_touches_disk(ent, tmp_path):
    register(ent, "0901111111")
    r = ent.post("/api/transcribe", files={"file": ("a.wav", b"RIFF....", "audio/wav")})
    assert r.status_code == 503
    assert list((tmp_path / "up").iterdir()) == []


def test_local_edition_unchanged(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DATA", tmp_path)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    monkeypatch.delenv("MANJU_EDITION", raising=False)
    db.init()
    c = TestClient(app)
    assert c.get("/api/transcripts").status_code == 200  # không cần đăng nhập
    assert c.post("/api/auth/register", json={"phone": "0901111111", "name": "A B", "password": "secret1"}).status_code == 404
