"""Lớp C — lexicon từ vựng ngành trong corrections: import/remove seed ngành,
ensure_domains idempotent, build_bias/top_pairs ưu tiên tag ngành active."""
from __future__ import annotations

import pytest

from app import corrections, db, transcribe
from app.corrections import (
    DOMAINS,
    build_bias,
    domain_tag,
    ensure_domains,
    import_domain_seed,
    is_domain_tag,
    remove_domain_seed,
    top_pairs,
)


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


def test_domain_tag_prefixed_and_detected():
    assert domain_tag("devops") == "dom:devops"
    assert is_domain_tag(domain_tag("finance"))
    assert not is_domain_tag("bac")


def test_every_domain_has_loadable_seed():
    for dom in DOMAINS:
        pairs = corrections.domain_seed_pairs(dom)
        assert pairs, f"{dom} thiếu seed"
        assert all(w and r for w, r in pairs)


def test_import_domain_seed_idempotent(tmp_db):
    n1 = import_domain_seed("devops")
    assert n1 > 0
    # Chạy lại: INSERT OR IGNORE không thêm mới.
    assert import_domain_seed("devops") == 0
    rows = db.list_corrections(tag=domain_tag("devops"))
    assert len(rows) == n1
    assert all(r["source"] == "seed" and r["status"] == "approved" for r in rows)


def test_remove_domain_seed(tmp_db):
    import_domain_seed("legal")
    assert db.list_corrections(tag=domain_tag("legal"))
    removed = remove_domain_seed("legal")
    assert removed > 0
    assert db.list_corrections(tag=domain_tag("legal")) == []


def test_ensure_domains_skips_unknown(tmp_db):
    added = ensure_domains(["devops", "khong-ton-tai"])
    assert added > 0
    assert db.list_corrections(tag=domain_tag("devops"))
    assert db.list_corrections(tag="dom:khong-ton-tai") == []


def test_build_bias_promotes_active_domain_terms(tmp_db):
    import_domain_seed("finance")
    import_domain_seed("devops")
    # Không truyền domains → mọi tag đồng hạng (thứ tự count/updated_at DB).
    plain = build_bias("")
    # Truyền domains=finance → term finance phải nổi lên đầu chuỗi bias.
    biased = build_bias("", domains=["finance"])
    assert "revenue" in biased
    fin_terms = {r["right"] for r in db.list_corrections(tag=domain_tag("finance"))}
    first_term = biased.split(",")[0].strip()
    assert first_term in fin_terms
    assert plain != biased  # thứ hạng đổi khi có ngành active


def test_top_pairs_prioritizes_domain_tag(tmp_db):
    import_domain_seed("medical")
    import_domain_seed("marketing")
    pairs = top_pairs(limit=5, domains=["medical"])
    med_wrongs = {r["wrong"] for r in db.list_corrections(tag=domain_tag("medical"))}
    # Ít nhất cặp đầu tiên phải thuộc ngành active.
    assert pairs[0][0] in med_wrongs
