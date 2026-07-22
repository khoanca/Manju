"""Công cụ chạy lại biến thể (app.reanalyze) — lõi tất định, không load model.

Kiểm phần ASR-cleanup theo phiên bản (_cleanup) + hai khác biệt cũ↔mới có hiệu
lực: collapse_loops mới (bắt thêm loop chu-kỳ ≥2 token + lặp trong-token) và regex
hallucination mở rộng trong keep_segment. Phần model LLM cần mạng nên không test ở
đây (model="none" là tất định).
"""
from __future__ import annotations

import pytest

from app import db, engines, reanalyze, transcribe


def _seg(text: str, nsp: float = 0.0, alp: float = 0.0) -> dict:
    return {"text": text, "no_speech_prob": nsp, "avg_logprob": alp}


def test_cycle_loop_only_collapsed_by_new():
    """Loop chu-kỳ 2 token lặp nhiều lần: bản mới gom về 1 chu kỳ, bản cũ giữ."""
    segs = [_seg("tick là " * 8 + "xong")]
    assert reanalyze._cleanup(segs, "new") == "tick là xong"
    assert reanalyze._cleanup(segs, "old").count("tick là") == 8  # cũ chỉ bắt token-đơn


def test_intra_token_loop_only_collapsed_by_new():
    segs = [_seg("Hbrightbrightbrightbright ok")]
    assert reanalyze._cleanup(segs, "new") == "Hbright ok"
    assert reanalyze._cleanup(segs, "old") == "Hbrightbrightbrightbright ok"


def test_expanded_hallucination_regex_drops_more():
    """Cụm "đăng ký ... kênh" xen chữ: keep_segment mới bỏ, bản cũ giữ."""
    segs = [_seg("nhớ đăng ký cho mình cái kênh nhé")]
    assert reanalyze._cleanup(segs, "new") == ""   # bản mới coi là outro → bỏ
    assert reanalyze._cleanup(segs, "old") != ""   # bản cũ regex hẹp → giữ


def test_raw_version_skips_keep_and_collapse():
    """Phiên bản 'raw': segment thô nối lại, KHÔNG keep_segment/collapse."""
    segs = [_seg("để " * 10 + "bán"), _seg("đăng ký kênh")]
    raw = reanalyze._cleanup(segs, "raw")
    assert raw.count("để") == 10        # không collapse
    assert "đăng ký kênh" in raw        # không bị keep_segment loại


def test_real_repetition_kept_by_both():
    segs = [_seg("không không được đâu")]
    assert reanalyze._cleanup(segs, "new") == "không không được đâu"
    assert reanalyze._cleanup(segs, "old") == "không không được đâu"


def test_period1_token_loop_collapsed_by_both():
    segs = [_seg("để " * 10 + "bán")]
    assert reanalyze._cleanup(segs, "new") == "để bán"
    assert reanalyze._cleanup(segs, "old") == "để bán"


def test_correct_none_is_passthrough():
    """model='none' → không sửa, trả nguyên cleanup (tất định, không gọi LLM)."""
    assert reanalyze._correct("cái mô độ", "none") == "cái mô độ"


def test_options_lists_versions_and_models():
    opt = reanalyze.options()
    assert {v["key"] for v in opt["versions"]} == {"new", "old", "raw"}
    keys = {m["key"] for m in opt["models"]}
    assert {"none", "haiku", "gemma"} <= keys  # tối thiểu; thêm model không phá test
    none = next(m for m in opt["models"] if m["key"] == "none")
    assert none["available"] is True  # 'không sửa' luôn dùng được
    gemma = next(m for m in opt["models"] if m["key"] == "gemma")
    assert gemma["available"] is True  # model local luôn khả dụng


def test_start_rejects_bad_keys():
    with pytest.raises(ValueError, match="Phiên bản"):
        reanalyze.start("x", "bogus", "none")
    with pytest.raises(ValueError, match="Model"):
        reanalyze.start("x", "new", "bogus")


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DATA", tmp_path)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(db, "DEFAULT_RECORDINGS", tmp_path / "rec")
    monkeypatch.setattr(transcribe, "TRANSCRIPTS", tmp_path / "tx")
    (tmp_path / "tx").mkdir()
    (tmp_path / "rec").mkdir()
    db.init()
    return tmp_path


def test_prefers_stored_live_segments_over_decode(tmp_db):
    """Bản ghi live có raw_segments đã lưu → dùng thẳng (from_live=True), KHÔNG
    batch-decode lại (không cần audio)."""
    db.insert_transcript(db.TranscriptRecord(
        transcript_id="20260101-000000-live-0000", title="live", language="vi",
        model="m", duration=3.0, created_at="2026-01-01T00:00:00+07:00",
        text="cái model", raw_text="cái mô độ", segments=None,
        llm_model=None, audio_file=None, audio_dir=None,
        raw_segments=[{"text": "cái mô độ", "no_speech_prob": 0.0, "avg_logprob": -0.3}],
    ))
    segs, from_live = reanalyze._raw_segments("20260101-000000-live-0000")
    assert from_live is True
    assert reanalyze._cleanup(segs, "new") == "cái mô độ"  # cleanup không đụng


def test_raw_segments_missing_transcript_raises(tmp_db):
    with pytest.raises(ValueError, match="Không tìm thấy"):
        reanalyze._raw_segments("khong-ton-tai")


def test_legacy_helpers_match_pre_ffe38d2_shape():
    """keep_segment_legacy chỉ khác bản mới ở regex; token/digit-loop y hệt."""
    assert engines.keep_segment_legacy("đăng ký kênh", 0.0, 0.0) is False
    assert engines.keep_segment_legacy("nội dung thật", 0.0, 0.0) is True
