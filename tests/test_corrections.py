"""API sửa transcript tay (US-801) + CRUD thư viện sửa lỗi (US-802)
+ build_bias/top_pairs mồi library vào ASR/pass 2 (US-803)
+ seed lexicon vùng miền & cập nhật remote (US-804) — DB tạm."""
from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from app import corrections, db, memory_filter, transcribe
from app.corrections import (
    BIAS_CAP_CHARS,
    LEXICON_DIR,
    PERSONAL_CAP_CHARS,
    SEED_REGIONS,
    _salient_terms,
    build_bias,
    extract_pairs,
    import_seed,
    mine_speaker_terms,
    remove_seed,
    top_pairs,
)


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


# ── build_bias mở rộng: personal + topic + regions ─────────────────────────
def _seed_tagged(wrong: str, right: str, tag: str, count: int = 1) -> None:
    row = db.upsert_correction(wrong, right, tag=tag)
    db.set_correction_status(row["id"], "approved")
    for _ in range(count - 1):
        db.upsert_correction(wrong, right)


def test_build_bias_empty_extras_identical_to_legacy(tmp_db):
    """Không personal/topic/regions → output byte-for-byte như hành vi cũ."""
    _seed_approved("gít háp", "GitHub", count=1)
    _seed_approved("cu bơ nét", "Kubernetes", count=3)
    legacy = build_bias("MyTerm")
    assert legacy == "MyTerm, Kubernetes, GitHub"
    assert build_bias("MyTerm", personal=(), topic="", regions=()) == legacy


def test_build_bias_region_beats_topic_beats_count(tmp_db):
    _seed_tagged("a sai", "CountTop", "", count=5)
    _seed_tagged("b sai", "TopicHit", "", count=2)
    _seed_tagged("c sai", "NamTerm", "nam", count=1)
    out = build_bias("", topic="bàn về topichit hôm nay", regions=("nam",))
    assert out == "NamTerm, TopicHit, CountTop"


def test_build_bias_topic_moves_matching_terms_up(tmp_db):
    _seed_approved("a sai", "Docker", count=5)
    _seed_approved("b sai", "GitHub", count=1)
    assert build_bias("", topic="review GitHub PR") == "GitHub, Docker"


def test_build_bias_personal_between_user_and_library_dedup(tmp_db):
    _seed_approved("a sai", "Docker", count=2)
    out = build_bias("MyTerm", personal=["Kafka", "myterm", "Redis"])
    # personal sau user, dedup casefold ("myterm" trùng glossary), library cuối
    assert out == "MyTerm, Kafka, Redis, Docker"


def test_build_bias_personal_sub_cap_240(tmp_db):
    personal = [f"p{i:02d}-" + "x" * 55 for i in range(6)]  # mỗi term 59 ký tự
    out = build_bias("", personal=personal)
    # 3 term đầu = 59+61+61 = 181 ≤ 240; term 4 đẩy lên 242 > cap → dừng
    assert out == ", ".join(personal[:3])
    assert "p03" not in out
    assert PERSONAL_CAP_CHARS == 240


def test_build_bias_db_error_keeps_user_and_personal(tmp_db, monkeypatch):
    def boom(**kw):
        raise RuntimeError("db nổ giả lập")

    monkeypatch.setattr(db, "list_corrections", boom)
    assert build_bias("MyTerm", personal=["Kafka"]) == "MyTerm, Kafka"


def test_top_pairs_regions_rank_first_before_cut(tmp_db):
    _seed_approved("a sai", "A", count=3)
    _seed_tagged("b sai", "B", "nam", count=1)
    _seed_tagged("c sai", "C", "bac", count=2)
    # nam lên đầu (stable), phần còn lại giữ count DESC; cắt limit SAU khi sort
    assert top_pairs(regions=("nam",)) == [("b sai", "B"), ("a sai", "A"), ("c sai", "C")]
    assert top_pairs(limit=1, regions=("nam",)) == [("b sai", "B")]


# ── _salient_terms + mine_speaker_terms (từ vựng cá nhân) ──────────────────
def test_salient_terms_keeps_terms_drops_noise():
    text = "Triển khai Kubernetes với redis và CI ngay 123 ok không nhé Kubernetes"
    c = _salient_terms(text, known=set())
    assert c["Kubernetes"] == 2
    assert c["redis"] == 1
    assert c["CI"] == 1
    for dropped in ("không", "với", "và", "nhé", "123", "ok"):
        assert dropped not in c


def test_salient_terms_known_kept_at_count_one():
    c = _salient_terms("dùng k8s đi nhé", known={"k8s"})
    assert c["k8s"] == 1
    assert "nhé" not in c


def test_salient_terms_counts_casefold_keeps_first_casing():
    c = _salient_terms("Redis rồi redis nữa REDIS", known=set())
    assert c == {"Redis": 3}


def _seed_spk_transcript(tid: str, speaker_map: dict) -> None:
    db.insert_transcript(db.TranscriptRecord(
        transcript_id=tid, title="Họp", language="vi", model="m", duration=9.0,
        created_at="2026-01-01T00:00:00+07:00", text="x", raw_text=None,
        segments=[
            {"start": 0.0, "end": 3.0, "text": "triển khai Kubernetes với Kubernetes", "spk": 0},
            {"start": 3.0, "end": 6.0, "text": "dùng redis cache và redis queue", "spk": 1},
            {"start": 6.0, "end": 9.0, "text": "GitHub GitHub GitHub", "spk": 2},
        ],
        llm_model=None, audio_file=None, audio_dir=None, speaker_map=speaker_map,
    ))


def test_mine_speaker_terms_groups_by_speaker_skips_unmapped(tmp_db):
    a = db.find_or_create_speaker("An")
    b = db.find_or_create_speaker("Bình")
    _seed_spk_transcript("t-mine", {"0": a, "1": b, "2": None})
    # count≥2 mới giữ: Kubernetes(2)/redis(2) vào, cache/queue(1) rớt;
    # cụm 2 (GitHub×3) chưa gán tên → bỏ qua
    assert mine_speaker_terms("t-mine") == 2
    assert db.personal_terms([a]) == ["Kubernetes"]
    assert db.personal_terms([b]) == ["redis"]


def test_mine_speaker_terms_known_kept_at_count_one(tmp_db):
    a = db.find_or_create_speaker("An")
    _seed_approved("két", "cache")
    _seed_spk_transcript("t-mine", {"0": None, "1": a, "2": None})
    assert mine_speaker_terms("t-mine") == 2  # redis(2) + cache(1, trong known)
    assert set(db.personal_terms([a])) == {"redis", "cache"}


def test_mine_speaker_terms_drops_pass2_invention(tmp_db):
    # segments (bản sau pass 2) có 'budget' ×2, nhưng raw_text (Whisper thật)
    # chỉ có 'redis' — 'budget' là LLM bịa, không được vào bias phiên sau.
    a = db.find_or_create_speaker("An")
    db.insert_transcript(db.TranscriptRecord(
        transcript_id="t-inv", title="Họp", language="vi", model="m", duration=6.0,
        created_at="2026-01-01T00:00:00+07:00", text="x",
        raw_text="dùng redis rồi redis nữa còn doanh thu thì doanh thu sau",
        segments=[
            {"start": 0.0, "end": 3.0, "text": "dùng redis rồi redis nữa", "spk": 0},
            {"start": 3.0, "end": 6.0, "text": "còn budget thì budget sau", "spk": 0},
        ],
        llm_model=None, audio_file=None, audio_dir=None, speaker_map={"0": a},
    ))
    assert mine_speaker_terms("t-inv") == 1
    assert db.personal_terms([a]) == ["redis"]


def test_mine_speaker_terms_missing_or_no_segments_returns_zero(tmp_db):
    assert mine_speaker_terms("khong-ton-tai") == 0
    db.insert_transcript(db.TranscriptRecord(
        transcript_id="t-empty", title="Họp", language="vi", model="m", duration=1.0,
        created_at="2026-01-01T00:00:00+07:00", text="x", raw_text=None,
        segments=None, llm_model=None, audio_file=None, audio_dir=None,
    ))
    assert mine_speaker_terms("t-empty") == 0


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
    # Library chốt lúc start phiên: vào cả spec ASR lẫn pairs pass 2.
    # ASR và pass 2 dùng CÙNG chuỗi term user-first (hotfix 2026-07-20: bỏ
    # tiêm topic + bỏ đảo thứ tự — prompt văn xuôi bị Whisper nhại vào subtitle).
    assert session.spec.glossary == "MyTerm, Kubernetes"
    assert session.glossary == "MyTerm, Kubernetes"
    assert session.pairs == (("cu bơ nét", "Kubernetes"),)


# ── Seed lexicon vùng miền (T-012, US-804) ─────────────────────────────────
def _lexicon_entries(region: str) -> list[dict]:
    return json.loads((LEXICON_DIR / f"{region}.json").read_text(encoding="utf-8"))


def _base_entries() -> list[dict]:
    return json.loads((LEXICON_DIR / "base.json").read_text(encoding="utf-8"))


def test_base_lexicon_schema_and_distinct():
    """base.json: schema hợp lệ, không cặp trùng — vì memory_filter thay xác định
    lên MỌI transcript, trùng/rác sẽ hại rộng."""
    entries = _base_entries()
    assert isinstance(entries, list) and entries
    seen: set[tuple[str, str]] = set()
    for e in entries:
        assert set(e) == {"wrong", "right"}, e
        assert e["wrong"].strip() and e["right"].strip(), e
        pair = (e["wrong"], e["right"])
        assert pair not in seen, f"cặp trùng: {pair}"
        seen.add(pair)


def test_base_pairs_loaded_from_json():
    pairs = corrections.base_pairs()
    assert ("mô độ", "model") in pairs
    assert len(pairs) == len(_base_entries())


def test_base_lexicon_fixes_model_via_memory(tmp_db):
    """"mô độ"→model, "con claw"→Claude được memory_filter thay xác định — KHÔNG
    cần seed DB (base nạp tại chỗ đọc). Đúng ca live-1738 pass 2 bỏ sót."""
    fixed, hits = memory_filter.correct_from_memory("cái mô độ của con claw nó mạnh hơn")
    assert "model" in fixed and "Claude" in fixed
    assert hits >= 2


def test_base_lexicon_not_in_corrections_table(tmp_db):
    """Base KHÔNG nhồi vào bảng corrections → thư viện user sạch, đếm không lệch."""
    assert db.list_corrections() == []
    memory_filter.correct_from_memory("cái mô độ")  # dùng base nhưng không ghi DB
    assert db.list_corrections() == []


def test_library_pair_overrides_base_on_conflict(tmp_db):
    """Cặp thư viện approved trùng `wrong` với base thì thắng (đặt sau, đã duyệt)."""
    db.add_corrections_ignore([("mô độ", "mô-đen", "user")], source="user")
    fixed, _ = memory_filter.correct_from_memory("cái mô độ")
    assert "mô-đen" in fixed


def test_lexicon_files_parse_and_schema():
    """4 file seed: JSON hợp lệ, wrong/right non-empty, không cặp trùng
    trong file lẫn giữa các file (toggle vùng phải độc lập)."""
    seen_all: set[tuple[str, str]] = set()
    for region in SEED_REGIONS:
        entries = _lexicon_entries(region)
        assert isinstance(entries, list) and entries, region
        for e in entries:
            assert set(e) == {"wrong", "right"}, (region, e)
            assert isinstance(e["wrong"], str) and e["wrong"].strip(), (region, e)
            assert isinstance(e["right"], str) and e["right"].strip(), (region, e)
            pair = (e["wrong"], e["right"])
            assert pair not in seen_all, f"cặp trùng: {region} {pair}"
            seen_all.add(pair)


def test_import_seed_idempotent(tmp_db):
    n1 = import_seed("trung")
    assert n1 == len(_lexicon_entries("trung"))
    assert import_seed("trung") == 0  # chạy lại không nhân đôi
    rows = db.list_corrections(source="seed", tag="trung")
    assert len(rows) == n1
    assert {(r["status"], r["count"]) for r in rows} == {("approved", 1)}


def test_import_seed_keeps_user_entry(tmp_db):
    # User đã có sẵn cặp trùng với seed → giữ nguyên của user, không đè
    entries = _lexicon_entries("nam")
    wrong, right = entries[0]["wrong"], entries[0]["right"]
    db.upsert_correction(wrong, right)
    assert import_seed("nam") == len(entries) - 1
    row = next(r for r in db.list_corrections() if (r["wrong"], r["right"]) == (wrong, right))
    assert (row["source"], row["status"], row["count"]) == ("user", "pending", 1)


def test_remove_seed_keeps_user_and_other_regions(tmp_db):
    import_seed("bac")
    import_seed("trung")
    db.upsert_correction("sai user", "đúng user")
    assert remove_seed("bac") == len(_lexicon_entries("bac"))
    assert db.list_corrections(source="seed", tag="bac") == []
    assert len(db.list_corrections(source="seed", tag="trung")) > 0
    assert len(db.list_corrections(source="user")) == 1


def test_settings_toggle_imports_and_removes_seed(client):
    r = client.put("/api/settings", json={"lexicon_trung": True})
    assert r.status_code == 200
    assert r.json()["lexicon"]["trung"] is True
    assert db.get_setting("lexicon_trung") == "1"
    assert len(client.get("/api/corrections",
                          params={"source": "seed", "tag": "trung"}).json()) > 0

    r = client.put("/api/settings", json={"lexicon_trung": False})
    assert r.status_code == 200
    assert r.json()["lexicon"]["trung"] is False
    assert client.get("/api/corrections", params={"source": "seed"}).json() == []


def test_settings_toggle_en_maps_to_en_accent(client):
    # Field lexicon_en ứng với file/tag en_accent
    client.put("/api/settings", json={"lexicon_en": True})
    rows = client.get("/api/corrections", params={"source": "seed", "tag": "en_accent"}).json()
    assert len(rows) == len(_lexicon_entries("en_accent"))


def test_settings_get_returns_lexicon_state(client, monkeypatch):
    from app import main

    monkeypatch.setattr(
        main.engines, "get_engine",
        lambda: SimpleNamespace(info=SimpleNamespace(tier="mlx", model_name="m")),
    )
    s = client.get("/api/settings").json()
    assert s["lexicon"] == {
        "bac": False, "trung": False, "nam": False, "en": False,
        "slang": False, "url": "",
    }


# ── Seed slang/teencode MXH (US-815) ───────────────────────────────────────
def test_import_seed_slang_idempotent(tmp_db):
    n1 = import_seed("slang")
    assert n1 == len(_lexicon_entries("slang"))
    assert import_seed("slang") == 0
    rows = db.list_corrections(source="seed", tag="slang")
    assert {(r["status"], r["count"]) for r in rows} == {("approved", 1)}


def test_settings_toggle_slang_imports_and_removes(client):
    db.upsert_correction("sai user", "đúng user")
    r = client.put("/api/settings", json={"lexicon_slang": True})
    assert r.status_code == 200
    assert r.json()["lexicon"]["slang"] is True
    assert len(client.get("/api/corrections",
                          params={"source": "seed", "tag": "slang"}).json()) > 0

    r = client.put("/api/settings", json={"lexicon_slang": False})
    assert r.json()["lexicon"]["slang"] is False
    assert client.get("/api/corrections", params={"source": "seed", "tag": "slang"}).json() == []
    # Entry user không bị toggle slang đụng tới
    assert len(client.get("/api/corrections", params={"source": "user"}).json()) == 1


def test_rank_slang_shares_tier_with_active_regions(tmp_db):
    # Không vùng nào active thì slang KHÔNG được ưu tiên; có vùng active thì
    # slang đứng cùng tier với vùng, trên entry không tag (US-815).
    db.add_corrections_ignore([("nàm x", "làm x", "bac")], source="seed")
    db.add_corrections_ignore([("ghét gô x", "gét gô x", "slang")], source="seed")
    db.add_corrections_ignore([("sai chung", "đúng chung", "")], source="seed")
    bias = build_bias("", regions=("bac",))
    assert bias.index("gét gô x") < bias.index("đúng chung")
    assert bias.index("làm x") < bias.index("đúng chung")
    pairs = top_pairs(2, regions=("bac",))
    assert set(pairs) == {("nàm x", "làm x"), ("ghét gô x", "gét gô x")}


def test_rank_slang_neutral_without_regions(tmp_db):
    # regions=() → hành vi cũ giữ nguyên: cặp user count cao vẫn đứng trước slang.
    db.add_corrections_ignore([("ghét gô x", "gét gô x", "slang")], source="seed")
    for _ in range(3):
        db.upsert_correction("cu bơ nét", "Kubernetes")  # count 3 → approved
    bias = build_bias("")
    assert bias.index("Kubernetes") < bias.index("gét gô x")
    assert top_pairs(1) == [("cu bơ nét", "Kubernetes")]


# ── Cập nhật lexicon online opt-in (T-013, US-804 AC2) ─────────────────────
class _FakeResp:
    def __init__(self, content: bytes):
        self.content = content
        self.text = content.decode("utf-8")

    def raise_for_status(self):
        pass


def _remote_payload() -> tuple[bytes, str]:
    data = json.dumps([
        {"wrong": "đét lai", "right": "deadline", "tag": "en_accent"},
        {"wrong": "sai x", "right": "đúng x"},
    ]).encode("utf-8")
    return data, hashlib.sha256(data).hexdigest()


def _mock_lexicon_source(monkeypatch, data: bytes, digest: str):
    def fake_get(url, **kw):
        return _FakeResp(digest.encode() if url.endswith(".sha256") else data)

    monkeypatch.setattr(corrections.httpx, "get", fake_get)


def test_lexicon_update_without_url_400(client):
    r = client.post("/api/lexicon/update")
    assert r.status_code == 400
    assert "Chưa cấu hình" in r.json()["detail"]


def test_lexicon_update_verified_imports(client, monkeypatch):
    data, digest = _remote_payload()
    _mock_lexicon_source(monkeypatch, data, digest)
    db.set_setting("lexicon_url", "https://example.com/lexicon.json")
    r = client.post("/api/lexicon/update")
    assert r.status_code == 200
    assert r.json() == {"ok": True, "imported": 2}
    rows = db.list_corrections(source="remote")
    assert {(x["wrong"], x["right"], x["tag"], x["status"]) for x in rows} == {
        ("đét lai", "deadline", "en_accent", "approved"),
        ("sai x", "đúng x", "", "approved"),
    }
    # Gọi lại → INSERT OR IGNORE, không nhân đôi
    assert client.post("/api/lexicon/update").json()["imported"] == 0


def test_lexicon_update_bad_checksum_502_table_unchanged(client, monkeypatch):
    data, _ = _remote_payload()
    _mock_lexicon_source(monkeypatch, data, "0" * 64)
    db.set_setting("lexicon_url", "https://example.com/lexicon.json")
    r = client.post("/api/lexicon/update")
    assert r.status_code == 502
    assert "Checksum" in r.json()["detail"]
    assert client.get("/api/corrections").json() == []


def test_lexicon_update_network_error_502(client, monkeypatch):
    def boom(url, **kw):
        raise httpx.ConnectError("mạng rớt giả lập")

    monkeypatch.setattr(corrections.httpx, "get", boom)
    db.set_setting("lexicon_url", "https://example.com/lexicon.json")
    r = client.post("/api/lexicon/update")
    assert r.status_code == 502
    assert client.get("/api/corrections").json() == []


def test_lexicon_update_invalid_json_502(client, monkeypatch):
    data = b"khong phai json"
    _mock_lexicon_source(monkeypatch, data, hashlib.sha256(data).hexdigest())
    db.set_setting("lexicon_url", "https://example.com/lexicon.json")
    r = client.post("/api/lexicon/update")
    assert r.status_code == 502
    assert client.get("/api/corrections").json() == []


def test_lexicon_update_bad_schema_502(client, monkeypatch):
    data = json.dumps([{"wrong": "", "right": "x"}]).encode()
    _mock_lexicon_source(monkeypatch, data, hashlib.sha256(data).hexdigest())
    db.set_setting("lexicon_url", "https://example.com/lexicon.json")
    r = client.post("/api/lexicon/update")
    assert r.status_code == 502
    assert client.get("/api/corrections").json() == []
