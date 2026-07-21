from __future__ import annotations

from app.cleanup import review_and_fix


def test_review_and_fix_collapses_ngram_cycle():
    # live-1620: loop chu kỳ "tick là" — gom về 1 chu kỳ, giữ nội dung quanh nó.
    text = "ok " + "tick là " * 40 + "xong việc"
    r = review_and_fix(text)
    assert "tick là tick là" not in r.cleaned
    assert "ok" in r.cleaned and "xong việc" in r.cleaned
    assert r.chars_removed > 0
    assert r.original == text


def test_review_and_fix_collapses_intra_token():
    # live-1653: 1 token dính liền lặp bên trong.
    text = "H" + "bright" * 100
    r = review_and_fix(text)
    assert r.cleaned == "Hbright"
    assert r.chars_removed > 0


def test_review_and_fix_strips_hallucination_phrase():
    text = "nội dung thật hãy đăng kí cho kênh lalaschool nội dung tiếp"
    r = review_and_fix(text)
    assert r.dropped
    assert "nội dung thật" in r.cleaned and "nội dung tiếp" in r.cleaned


def test_review_and_fix_noop_on_clean_text():
    text = "đây là một câu hội thoại bình thường không có đoạn lặp"
    r = review_and_fix(text)
    assert r.cleaned == text
    assert r.chars_removed == 0
    assert r.dropped == []
