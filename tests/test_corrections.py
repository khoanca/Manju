"""API sửa transcript tay (US-801) + CRUD thư viện sửa lỗi (US-802)
+ build_bias/top_pairs mồi library vào ASR/pass 2 (US-803) — DB tạm."""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import db, transcribe
from app.corrections import BIAS_CAP_CHARS, build_bias, extract_pairs, top_pairs


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """DB tạm, không cần app — cho test thuần build_bias/top_pairs/wiring."""
    monkeypatch.setattr(db, "DATA", tmp_path)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(db, "DEFAULT_RECORDINGS", tmp_path / "rec")
    monkeypatch.setattr(transcribe, "TRANSCRIPTS", tmp_path / "tx")
    (tmp_path / "tx").mkdir()
    (tmp_path / "rec").mkdir()
    db.init()
    return tmp_path


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DATA", tmp_path)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(db, "DEFAULT_RECORDINGS", tmp_path / "rec")
    monkeypatch.setattr(transcribe, "TRANSCRIPTS", tmp_path / "tx")
    monkeypatch.setattr(transcribe, "RECORDINGS", tmp_path / "rec")
    (tmp_path / "tx").mkdir()
    (tmp_path / "rec").mkdir()
    from app.main import app
    with TestClient(app) as c:
        yield c


def _seed_transcript(tid="20260101-000000-hop", text="triển khai Kubernetes"):
    db.insert_transcript(db.TranscriptRecord(
        transcript_id=tid, title="Họp", language="vi", model="m", duration=6.0,
        created_at="2026-01-01T00:00:00+07:00", text=text,
        raw_text="triển khai cu bơ nét", segments=None,
        llm_model="gemma4:e4b", audio_file=None, audio_dir=None,
    ))


# ── PATCH /api/transcripts/{id}/text ───────────────────────────────────────
def test_patch_text_saves_and_detail_returns_edited(client):
    _seed_transcript()
    r = client.patch("/api/transcripts/20260101-000000-hop/text",
                     json={"edited_text": "triển khai K8s"})
    assert r.status_code == 200
    # Không gửi base_text → hook trích cặp không chạy, pairs_extracted = 0
    assert r.json() == {"ok": True, "edited_text": "triển khai K8s", "pairs_extracted": 0}
    detail = client.get("/api/transcripts/20260101-000000-hop").json()
    # Đủ 3 bản để UI toggle: raw / pass-2 / user sửa (US-801 AC2)
    assert detail["edited_text"] == "triển khai K8s"
    assert detail["text"] == "triển khai Kubernetes"
    assert detail["raw_text"] == "triển khai cu bơ nét"


def test_patch_text_none_clears_edit(client):
    _seed_transcript()
    client.patch("/api/transcripts/20260101-000000-hop/text",
                 json={"edited_text": "bản sửa"})
    r = client.patch("/api/transcripts/20260101-000000-hop/text",
                     json={"edited_text": None})
    assert r.status_code == 200
    assert "edited_text" not in client.get("/api/transcripts/20260101-000000-hop").json()


def test_patch_text_missing_404(client):
    r = client.patch("/api/transcripts/khong-co/text", json={"edited_text": "x"})
    assert r.status_code == 404


def test_patch_text_stale_base_409(client):
    _seed_transcript()
    r = client.patch(
        "/api/transcripts/20260101-000000-hop/text",
        json={"edited_text": "bản mới", "base_text": "bản đã cũ (lệch)"},
    )
    assert r.status_code == 409
    # Không ghi đè khi lệch
    assert "edited_text" not in client.get("/api/transcripts/20260101-000000-hop").json()


def test_patch_text_base_matches_effective_text(client):
    """base_text so với bản hiệu lực: edited_text nếu có, không thì text."""
    _seed_transcript()
    r1 = client.patch(
        "/api/transcripts/20260101-000000-hop/text",
        json={"edited_text": "sửa lần 1", "base_text": "triển khai Kubernetes"},
    )
    assert r1.status_code == 200
    # Lần 2 base_text phải là bản sửa hiện tại, không phải bản máy nữa
    r2 = client.patch(
        "/api/transcripts/20260101-000000-hop/text",
        json={"edited_text": "sửa lần 2", "base_text": "sửa lần 1"},
    )
    assert r2.status_code == 200
    r3 = client.patch(
        "/api/transcripts/20260101-000000-hop/text",
        json={"edited_text": "x", "base_text": "triển khai Kubernetes"},
    )
    assert r3.status_code == 409


# ── extract_pairs (T-005) ──────────────────────────────────────────────────
def test_extract_pairs_transliteration():
    """Cặp phiên âm lệch số từ ("cu bơ nét" 3 từ → "Kubernetes" 1 từ) phải qua
    ngưỡng ratio 0.3 (thực đo 0.42) — đây là ca chốt ngưỡng."""
    pairs = extract_pairs(
        "triển khai cu bơ nét cho dự án", "triển khai Kubernetes cho dự án"
    )
    assert pairs == [("cu bơ nét", "Kubernetes")]


def test_extract_pairs_full_rewrite_returns_nothing():
    # Viết lại toàn bộ câu (văn phong) → span dài/khác hẳn → 0 cặp (AC3)
    pairs = extract_pairs(
        "hôm nay chúng ta bàn về vấn đề ngân sách quý tới",
        "cuộc họp tập trung thảo luận kế hoạch chi tiêu sắp tới",
    )
    assert pairs == []


def test_extract_pairs_reorder_long_phrase_returns_nothing():
    # Đảo cụm dài đầu-cuối câu → chỉ insert/delete, không phải cặp sửa lỗi
    pairs = extract_pairs(
        "chúng ta sẽ triển khai hệ thống mới vào tuần sau",
        "vào tuần sau chúng ta sẽ triển khai hệ thống mới",
    )
    assert pairs == []


def test_extract_pairs_case_and_punctuation_only():
    pairs = extract_pairs(
        "triển khai kubernetes ngay.", "Triển khai Kubernetes ngay!"
    )
    assert pairs == []


def test_extract_pairs_multiple_pairs_one_text():
    pairs = extract_pairs(
        "dùng cu bơ nét và gít háp để deploy",
        "dùng Kubernetes và GitHub để deploy",
    )
    assert pairs == [("cu bơ nét", "Kubernetes"), ("gít háp", "GitHub")]


def test_extract_pairs_equal_text():
    assert extract_pairs("không đổi gì cả", "không đổi gì cả") == []


def test_extract_pairs_keeps_vietnamese_diacritics():
    # Sửa sai dấu 1-1 theo từ: giữ nguyên dấu tiếng Việt hai phía
    pairs = extract_pairs(
        "anh Hùng phụ trách phần triễn khai", "anh Hùng phụ trách phần triển khai"
    )
    assert pairs == [("triễn", "triển")]


# ── Hook PATCH text → thư viện (T-006) ─────────────────────────────────────
def test_patch_text_hook_extracts_pending_pair(client):
    _seed_transcript(text="triển khai cu bơ nét trên server")
    r = client.patch(
        "/api/transcripts/20260101-000000-hop/text",
        json={
            "edited_text": "triển khai Kubernetes trên server",
            "base_text": "triển khai cu bơ nét trên server",
        },
    )
    assert r.status_code == 200
    assert r.json()["pairs_extracted"] == 1
    rows = client.get("/api/corrections").json()
    assert len(rows) == 1
    row = rows[0]
    assert (row["wrong"], row["right"]) == ("cu bơ nét", "Kubernetes")
    assert (row["status"], row["source"], row["count"]) == ("pending", "user", 1)


def test_patch_text_hook_repeat_pair_auto_approves(client):
    # Cùng cặp lặp lại ở transcript thứ 2 → count=2, tự approved (US-802 AC2)
    _seed_transcript(tid="20260101-000000-hop", text="triển khai cu bơ nét ngay")
    _seed_transcript(tid="20260102-000000-hop", text="học lại cu bơ nét từ đầu")
    client.patch(
        "/api/transcripts/20260101-000000-hop/text",
        json={"edited_text": "triển khai Kubernetes ngay",
              "base_text": "triển khai cu bơ nét ngay"},
    )
    r = client.patch(
        "/api/transcripts/20260102-000000-hop/text",
        json={"edited_text": "học lại Kubernetes từ đầu",
              "base_text": "học lại cu bơ nét từ đầu"},
    )
    assert r.status_code == 200
    rows = client.get("/api/corrections").json()
    assert len(rows) == 1
    assert (rows[0]["count"], rows[0]["status"]) == (2, "approved")


def test_patch_text_hook_never_fails_patch(client, monkeypatch):
    # extract_pairs nổ → PATCH vẫn 200, bản sửa vẫn lưu (never-fail)
    from app import corrections

    def boom(machine, edited):
        raise RuntimeError("nổ giả lập")

    monkeypatch.setattr(corrections, "extract_pairs", boom)
    _seed_transcript(text="triển khai cu bơ nét")
    r = client.patch(
        "/api/transcripts/20260101-000000-hop/text",
        json={"edited_text": "triển khai Kubernetes",
              "base_text": "triển khai cu bơ nét"},
    )
    assert r.status_code == 200
    assert r.json()["pairs_extracted"] == 0
    detail = client.get("/api/transcripts/20260101-000000-hop").json()
    assert detail["edited_text"] == "triển khai Kubernetes"
    assert client.get("/api/corrections").json() == []


# ── CRUD /api/corrections ──────────────────────────────────────────────────
def test_corrections_list_filter_patch_delete(client):
    cid = db.upsert_correction("cu bơ nét", "Kubernetes")["id"]
    db.upsert_correction("mô có", "mô khó", tag="trung", source="seed")

    rows = client.get("/api/corrections").json()
    assert len(rows) == 2
    assert client.get("/api/corrections", params={"source": "seed"}).json()[0]["tag"] == "trung"
    assert client.get("/api/corrections", params={"status": "rejected"}).json() == []

    r = client.patch(f"/api/corrections/{cid}", json={"status": "approved", "tag": "bac"})
    assert r.status_code == 200
    row = client.get("/api/corrections", params={"status": "approved"}).json()[0]
    assert (row["id"], row["tag"]) == (cid, "bac")

    assert client.delete(f"/api/corrections/{cid}").status_code == 200
    assert len(client.get("/api/corrections").json()) == 1


def test_corrections_patch_validates(client):
    cid = db.upsert_correction("gít", "git")["id"]
    assert client.patch(f"/api/corrections/{cid}", json={}).status_code == 400
    assert client.patch(f"/api/corrections/{cid}", json={"status": "sai"}).status_code == 400


def test_corrections_missing_404(client):
    assert client.patch("/api/corrections/cor-khongco",
                        json={"status": "approved"}).status_code == 404
    assert client.patch("/api/corrections/cor-khongco",
                        json={"tag": "bac"}).status_code == 404
    assert client.delete("/api/corrections/cor-khongco").status_code == 404


# ── build_bias / top_pairs (T-008, US-803) ─────────────────────────────────
def _seed_approved(wrong: str, right: str, count: int = 1) -> None:
    """Seed 1 cặp approved với count mong muốn (upsert lặp để cộng count)."""
    row = db.upsert_correction(wrong, right)
    db.set_correction_status(row["id"], "approved")
    for _ in range(count - 1):
        db.upsert_correction(wrong, right)


def test_build_bias_ranks_by_count(tmp_db):
    _seed_approved("gít háp", "GitHub", count=1)
    _seed_approved("cu bơ nét", "Kubernetes", count=3)
    _seed_approved("đóc cơ", "Docker", count=2)
    # User glossary đứng trước, library nối sau theo count DESC
    assert build_bias("MyTerm") == "MyTerm, Kubernetes, Docker, GitHub"


def test_build_bias_empty_library_returns_user_glossary(tmp_db):
    assert build_bias("Kubernetes, deploy") == "Kubernetes, deploy"
    assert build_bias("") == ""


def test_build_bias_ignores_pending(tmp_db):
    db.upsert_correction("gít", "git")  # pending — chưa được mồi
    assert build_bias("MyTerm") == "MyTerm"


def test_build_bias_dedupes_against_user_glossary_casefold(tmp_db):
    _seed_approved("cu bơ nét", "kubernetes", count=3)  # trùng user (khác hoa-thường)
    _seed_approved("gít háp", "GitHub", count=2)
    assert build_bias("Kubernetes, deploy") == "Kubernetes, deploy, GitHub"


def test_build_bias_never_cuts_user_glossary(tmp_db):
    user = "u" * 900  # user glossary dài hơn cả cap — vẫn giữ nguyên vẹn
    _seed_approved("sai", "right-term", count=2)
    assert build_bias(user) == user  # thêm term nào cũng vượt cap → dừng


def test_build_bias_caps_total_length(tmp_db):
    # 10 term ~85 ký tự, count giảm dần — chỉ nạp được tới cap rồi dừng
    for i in range(10):
        _seed_approved(f"sai {i}", f"right-{i:02d}-" + "x" * 76, count=11 - i)
    out = build_bias("MyTerm")
    assert out.startswith("MyTerm, right-00-")
    assert len(out) <= BIAS_CAP_CHARS
    assert "right-09-" not in out  # term rank thấp nhất bị cắt


def test_build_bias_db_error_returns_user_glossary(tmp_db, monkeypatch):
    def boom(**kw):
        raise RuntimeError("db nổ giả lập")

    monkeypatch.setattr(db, "list_corrections", boom)
    assert build_bias("MyTerm") == "MyTerm"  # never-fail (AC2)


def test_top_pairs_sorts_and_limits(tmp_db):
    _seed_approved("gít háp", "GitHub", count=1)
    _seed_approved("cu bơ nét", "Kubernetes", count=3)
    _seed_approved("đóc cơ", "Docker", count=2)
    db.upsert_correction("pen đinh", "pending-x")  # pending — không lấy
    assert top_pairs() == [
        ("cu bơ nét", "Kubernetes"), ("đóc cơ", "Docker"), ("gít háp", "GitHub"),
    ]
    assert top_pairs(limit=2) == [("cu bơ nét", "Kubernetes"), ("đóc cơ", "Docker")]


def test_top_pairs_db_error_returns_empty(tmp_db, monkeypatch):
    def boom(**kw):
        raise RuntimeError("db nổ giả lập")

    monkeypatch.setattr(db, "list_corrections", boom)
    assert top_pairs() == []


# ── Wiring upload + live (T-010, T-011) ────────────────────────────────────
def test_process_wires_build_bias_into_asr_and_pass2(tmp_db, monkeypatch, tmp_path):
    _seed_approved("cu bơ nét", "Kubernetes", count=2)
    captured = {}

    class FakeEngine:
        info = SimpleNamespace(tier="mlx", model_name="large-v3-turbo")

        def transcribe_file(self, path, spec, on_progress):
            captured["asr_glossary"] = spec.glossary
            return SimpleNamespace(text="triển khai cu bơ nét", segments=[], duration=1.0)

    def fake_correct(text, glossary="", on_progress=None, pairs=()):
        captured["llm_glossary"] = glossary
        captured["pairs"] = pairs
        return text, True

    monkeypatch.setattr(transcribe.engines, "get_engine", lambda: FakeEngine())
    monkeypatch.setattr(transcribe, "correct_text", fake_correct)
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"\x00\x00")
    job_id = transcribe._new_job()
    transcribe._process(
        job_id, audio, transcribe.JobSpec(filename="a.wav", language="vi", prompt="MyTerm")
    )
    # Cùng 1 glossary đã merge library cho cả mồi Whisper lẫn pass 2 (AC1)
    assert captured["asr_glossary"] == "MyTerm, Kubernetes"
    assert captured["llm_glossary"] == "MyTerm, Kubernetes"
    assert captured["pairs"] == (("cu bơ nét", "Kubernetes"),)
    assert transcribe.get_job(job_id)["status"] == "done"


def test_live_session_snapshots_bias_at_start(tmp_db, monkeypatch):
    from app import live

    _seed_approved("cu bơ nét", "Kubernetes", count=2)
    monkeypatch.setattr(
        live.engines,
        "get_engine",
        lambda: SimpleNamespace(info=SimpleNamespace(tier="mlx", model_name="large-v3-turbo")),
    )
    session = live.LiveSession(
        ws=None, loop=None, cfg={"glossary": "MyTerm", "store_audio": False}
    )
    # Library chốt lúc start phiên: vào cả spec ASR lẫn pairs pass 2
    assert session.spec.glossary == "MyTerm, Kubernetes"
    assert session.pairs == (("cu bơ nét", "Kubernetes"),)
