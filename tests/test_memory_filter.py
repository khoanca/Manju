"""Lớp A — bộ lọc từ ký ức bản dịch cũ (memory_filter): thay xác định cụm đã biết
trước pass 2, chỉ từ cặp approved, never-fail khi DB hỏng."""
from __future__ import annotations

from app import db, memory_filter, transcribe


def test_apply_replaces_longest_match_first():
    # "cu bơ nét ét" phải thắng "cu bơ nét" (tránh thay dở dang thành "Kubernetes ét").
    mem = memory_filter.build_memory(
        [("cu bơ nét", "Kubernetes"), ("cu bơ nét ét", "Kubernetes")]
    )
    out, hits = memory_filter.apply_memory("triển khai cu bơ nét ét ngay", mem)
    assert out == "triển khai Kubernetes ngay"
    assert hits == 1


def test_apply_counts_all_hits_case_insensitive():
    mem = memory_filter.build_memory([("đíp lôi", "deploy")])
    out, hits = memory_filter.apply_memory("Đíp Lôi rồi đíp lôi lại", mem)
    assert out == "deploy rồi deploy lại"
    assert hits == 2


def test_compile_skips_risky_common_word_pair():
    # Cặp `wrong` toàn từ phổ thông ("thằng"→"từng") bị bỏ khi biên dịch ký ức →
    # không băm câu thật, dù cặp lỡ nằm trong DB approved.
    mem = memory_filter.build_memory([("thằng", "từng"), ("cuba nết", "kubernetes")])
    out, hits = memory_filter.apply_memory("thằng đó xài cuba nết", mem)
    assert out == "thằng đó xài kubernetes"
    assert hits == 1


def test_boundary_avoids_partial_word_match():
    # "voi" không được dính bên trong "voiceover".
    mem = memory_filter.build_memory([("voi", "voice")])
    out, hits = memory_filter.apply_memory("con voi và voiceover", mem)
    assert out == "con voice và voiceover"
    assert hits == 1


def test_build_skips_noise_pairs():
    # Quá ngắn, chỉ khác hoa-thường, hay right rỗng → không biên dịch.
    mem = memory_filter.build_memory(
        [("ab", "X"), ("Deploy", "deploy"), ("x", ""), ("có", "có")]
    )
    assert mem == []


def test_empty_text_is_noop():
    mem = memory_filter.build_memory([("đíp lôi", "deploy")])
    assert memory_filter.apply_memory("", mem) == ("", 0)


def test_from_library_only_approved(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DATA", tmp_path)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(db, "DEFAULT_RECORDINGS", tmp_path / "rec")
    monkeypatch.setattr(transcribe, "TRANSCRIPTS", tmp_path / "tx")
    (tmp_path / "tx").mkdir()
    (tmp_path / "rec").mkdir()
    db.init()
    db.add_corrections_ignore([("cu bơ nét", "Kubernetes", "")], source="user", status="approved")
    db.add_corrections_ignore([("đíp lôi", "deploy", "")], source="trend", status="pending")

    out, hits = memory_filter.correct_from_memory("cu bơ nét và đíp lôi")
    # Chỉ cặp approved được áp; cặp pending giữ nguyên.
    assert out == "Kubernetes và đíp lôi"
    assert hits == 1


def test_from_library_never_fails_on_db_error(monkeypatch):
    # Lỗi DB không raise; base lexicon (luôn bật, không đọc DB) vẫn dùng được.
    monkeypatch.setattr(db, "list_corrections", lambda **_: (_ for _ in ()).throw(RuntimeError()))
    mem = memory_filter.from_library()
    assert mem  # không rỗng — còn base
    fixed, hits = memory_filter.apply_memory("cái mô độ", mem)
    assert fixed == "cái model" and hits == 1
