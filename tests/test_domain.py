"""Lớp B — dự đoán ngành nghề (domain): overlap TF-IDF trên seed, chọn ngành
active để nạp lexicon, never-fail khi không khớp gì."""
from __future__ import annotations

from app import corrections, domain


def test_predict_devops_from_mixed_terms():
    txt = "Team bàn đíp lôi lên cu bơ nét, đo lây ten xi rồi rôn bách pipeline."
    scores = domain.predict_domain(txt)
    assert scores
    assert scores[0].domain == "devops"
    assert scores[0].confidence > 0.5


def test_predict_matches_corrected_english_form_too():
    # Vốn từ gồm cả `right` → khớp cả text đã sửa (tiếng Anh chuẩn).
    txt = "We will deploy to Kubernetes and watch latency on the load balancer."
    assert domain.predict_domain(txt)[0].domain == "devops"


def test_predict_finance_distinct_from_devops():
    txt = "Chốt revenue, soát invoice, chạy audit và forecast margin quý này."
    assert domain.predict_domain(txt)[0].domain == "finance"


def test_no_match_returns_empty():
    assert domain.predict_domain("hôm nay trời đẹp, cả nhà đi chơi công viên") == []
    assert domain.predict_domain("") == []


def test_active_domains_respects_threshold_and_cap():
    txt = "đíp lôi cu bơ nét pipeline rôn bách container monitoring staging"
    active = domain.active_domains(txt, min_confidence=0.9, max_active=2)
    assert active == ("devops",)
    # Ngưỡng quá cao → không ngành nào đủ tin cậy.
    assert domain.active_domains("chỉ có một chữ deploy thôi", min_confidence=0.99) == ()


def test_active_domains_are_known_domains():
    txt = "campaign conversion funnel engagement ROI branding influencer"
    for d in domain.active_domains(txt):
        assert d in corrections.DOMAINS
