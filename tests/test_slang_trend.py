"""US-816: LLM trend digest slang → nhập pending chờ duyệt
+ US-817: web adapter best-effort — mock toàn bộ boundary mạng/LLM."""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app import correct, db, slang_trend, transcribe
from app.corrections import build_bias
from app.slang_trend import _parse_entries, run_trend_update


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


_GOOD = [
    {"wrong": "ghét gô mới", "right": "gét gô mới"},
    {"wrong": "phờ lếch xxx", "right": "flex xxx"},
]


def _mock_llm(monkeypatch, payload: str):
    monkeypatch.setattr(correct, "chat_once", lambda system, user, timeout=0: payload)


def _mock_web(monkeypatch, result=([], 0, 0, 0)):
    """Chặn adapter web (US-817) trong test US-816 — không gọi mạng thật."""
    monkeypatch.setattr(slang_trend, "web_digest", lambda: result)


def _no_apify(monkeypatch):
    """Tắt adapter Apify (US-818) — máy dev có thể có APIFY_TOKEN thật."""
    monkeypatch.setattr(slang_trend, "APIFY_TOKEN", "")


# ── _parse_entries: validate lỏng, skip-not-raise ──────────────────────────
def test_parse_entries_skips_garbage():
    data = _GOOD + [
        {"wrong": "", "right": "x"},                      # rỗng
        {"wrong": "trùng hoa thường", "right": "TRÙNG HOA THƯỜNG"},  # chỉ khác case
        {"wrong": "một hai ba bốn năm", "right": "x"},    # >4 từ
        {"right": "thiếu wrong"},                          # thiếu key
        "không phải dict",
        {"wrong": "ghét gô mới", "right": "khác"},        # trùng wrong trong batch
    ]
    rows, skipped = _parse_entries(json.dumps(data, ensure_ascii=False))
    assert rows == [(e["wrong"], e["right"]) for e in _GOOD]
    assert skipped == 6


def test_parse_entries_raises_on_bad_payload():
    with pytest.raises(ValueError):
        _parse_entries("LLM trả văn xuôi, không phải JSON")
    with pytest.raises(ValueError):
        _parse_entries('{"wrong": "không phải", "right": "danh sách"}')


def test_parse_entries_caps_max_entries():
    data = [{"wrong": f"sai {i} x", "right": f"đúng {i} x"} for i in range(50)]
    rows, skipped = _parse_entries(json.dumps(data, ensure_ascii=False))
    assert len(rows) == slang_trend.MAX_ENTRIES
    assert skipped == 50 - slang_trend.MAX_ENTRIES


# ── run_trend_update: nhập pending, never-fail theo nguồn ──────────────────
def test_run_trend_update_imports_pending(tmp_db, monkeypatch):
    _mock_llm(monkeypatch, json.dumps(_GOOD, ensure_ascii=False))
    _mock_web(monkeypatch)
    _no_apify(monkeypatch)
    res = run_trend_update()
    assert (res.new_pending, res.sources_ok, res.sources_skipped) == (2, 1, 0)
    rows = db.list_corrections(source="trend")
    assert {(r["tag"], r["status"]) for r in rows} == {("slang", "pending")}
    # Bấm lại: cặp đã có → INSERT OR IGNORE, đếm vào skipped
    res2 = run_trend_update()
    assert res2.new_pending == 0
    assert res2.skipped == 2


def test_run_trend_update_llm_down_returns_zero_sources(tmp_db, monkeypatch):
    def boom(system, user, timeout=0):
        raise RuntimeError("mạng rớt")

    monkeypatch.setattr(correct, "chat_once", boom)
    _mock_web(monkeypatch)
    _no_apify(monkeypatch)
    res = run_trend_update()
    assert (res.sources_ok, res.sources_skipped, res.new_pending) == (0, 1, 0)
    assert db.list_corrections(source="trend") == []


def test_pending_trend_excluded_from_bias_until_approved(tmp_db, monkeypatch):
    _mock_llm(monkeypatch, json.dumps(_GOOD, ensure_ascii=False))
    _mock_web(monkeypatch)
    _no_apify(monkeypatch)
    run_trend_update()
    assert "gét gô mới" not in build_bias("")
    row = next(r for r in db.list_corrections(source="trend") if r["right"] == "gét gô mới")
    db.set_correction_status(row["id"], "approved")
    assert "gét gô mới" in build_bias("")


# ── POST /api/lexicon/slang-trend ──────────────────────────────────────────
def test_endpoint_503_without_key(client, monkeypatch):
    monkeypatch.setattr(correct, "OPENROUTER_API_KEY", "")
    r = client.post("/api/lexicon/slang-trend")
    assert r.status_code == 503
    assert client.get("/api/corrections").json() == []


def test_endpoint_502_when_all_sources_fail(client, monkeypatch):
    monkeypatch.setattr(correct, "OPENROUTER_API_KEY", "k")

    def boom(system, user, timeout=0):
        raise RuntimeError("mạng rớt")

    monkeypatch.setattr(correct, "chat_once", boom)
    _mock_web(monkeypatch, ([], 0, 0, 1))
    _no_apify(monkeypatch)
    assert client.post("/api/lexicon/slang-trend").status_code == 502


def test_endpoint_imports_and_returns_counts(client, monkeypatch):
    monkeypatch.setattr(correct, "OPENROUTER_API_KEY", "k")
    _mock_llm(monkeypatch, json.dumps(_GOOD + [{"wrong": "x", "right": "x"}], ensure_ascii=False))
    _mock_web(monkeypatch)
    _no_apify(monkeypatch)
    r = client.post("/api/lexicon/slang-trend")
    assert r.status_code == 200
    d = r.json()
    assert (d["new_pending"], d["skipped"], d["sources_ok"]) == (2, 1, 1)
    rows = client.get("/api/corrections", params={"status": "pending", "source": "trend"}).json()
    assert len(rows) == 2


# ── US-817: web adapter best-effort ────────────────────────────────────────
def test_html_to_text_strips_markup():
    html = (
        "<html><head><style>.x{color:red}</style><script>var a=1;</script></head>"
        "<body><h1>Từ lóng 2026</h1><p>gét gô nghĩa là <b>đi thôi</b></p></body></html>"
    )
    text = slang_trend._html_to_text(html)
    assert "Từ lóng 2026" in text and "gét gô" in text
    assert "color:red" not in text and "var a=1" not in text


def test_robots_disallow_skips_source_without_fetch(tmp_db, monkeypatch):
    db.set_setting("slang_sources", "http://x.test/a")
    monkeypatch.setattr(slang_trend, "_robots_ok", lambda url: False)
    monkeypatch.setattr(
        slang_trend, "_fetch_page",
        lambda url: pytest.fail("robots cấm thì không được fetch"),
    )
    assert slang_trend.web_digest() == ([], 0, 0, 1)


def test_fetch_page_returns_none_on_error_or_non200(monkeypatch):
    def boom(url, **kw):
        raise OSError("timeout")

    monkeypatch.setattr(slang_trend.httpx, "get", boom)
    assert slang_trend._fetch_page("http://x.test/a") is None

    class _Resp:
        status_code = 403
        text = "bị chặn"

    monkeypatch.setattr(slang_trend.httpx, "get", lambda url, **kw: _Resp())
    assert slang_trend._fetch_page("http://x.test/a") is None


def test_web_digest_feeds_shared_extraction(tmp_db, monkeypatch):
    db.set_setting("slang_sources", "http://x.test/ok http://y.test/die")
    monkeypatch.setattr(slang_trend, "_robots_ok", lambda url: True)
    monkeypatch.setattr(
        slang_trend, "_fetch_page",
        lambda url: "bài viết về slang" if "x.test" in url else None,
    )
    _mock_llm(monkeypatch, json.dumps(_GOOD, ensure_ascii=False))
    pairs, skipped, ok, failed = slang_trend.web_digest()
    assert pairs == [(e["wrong"], e["right"]) for e in _GOOD]
    assert (skipped, ok, failed) == (0, 1, 1)


def test_run_trend_update_web_survives_llm_down(tmp_db, monkeypatch):
    # LLM digest rớt nhưng nguồn web sống → vẫn nhập được pending (best-effort).
    db.set_setting("slang_sources", "http://x.test/ok")
    monkeypatch.setattr(slang_trend, "_robots_ok", lambda url: True)
    monkeypatch.setattr(slang_trend, "_fetch_page", lambda url: "bài viết")

    def picky(system, user, timeout=0):
        if "bài viết" in user:
            return json.dumps(_GOOD, ensure_ascii=False)
        raise RuntimeError("digest trực tiếp rớt")

    monkeypatch.setattr(correct, "chat_once", picky)
    _no_apify(monkeypatch)
    res = run_trend_update()
    assert (res.new_pending, res.sources_ok, res.sources_skipped) == (2, 1, 1)
    assert len(db.list_corrections(source="trend", status="pending")) == 2


# ── US-818: Apify adapter (opt-in APIFY_TOKEN) ─────────────────────────────
class _ApifyResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def test_apify_hashtags_from_setting(tmp_db):
    assert slang_trend._apify_hashtags() == ["xuhuong"]
    db.set_setting("slang_hashtags", "  #genz  tiktokvietnam ")
    assert slang_trend._apify_hashtags() == ["genz", "tiktokvietnam"]


def test_harvest_text_prefers_known_keys():
    items = [
        {"text": "caption một"},
        {"desc": "mô tả hai", "id": 5},
        {"noise": 123},
        {"text": "  "},
    ]
    assert slang_trend._harvest_text(items) == "caption một\nmô tả hai"


def test_apify_digest_runs_actor_and_parses(tmp_db, monkeypatch):
    monkeypatch.setattr(slang_trend, "APIFY_TOKEN", "tok")
    db.set_setting("slang_hashtags", "genz")
    captured = {}

    def fake_post(url, params=None, json=None, timeout=None):
        captured.update(url=url, params=params, input=json)
        return _ApifyResp([{"text": "caption chứa slang"}])

    monkeypatch.setattr(slang_trend.httpx, "post", fake_post)
    _mock_llm(monkeypatch, json.dumps(_GOOD, ensure_ascii=False))
    rows, skipped = slang_trend.apify_digest()
    assert rows == [(e["wrong"], e["right"]) for e in _GOOD]
    assert "clockworks~tiktok-scraper/run-sync-get-dataset-items" in captured["url"]
    assert captured["params"] == {"token": "tok"}
    assert captured["input"]["hashtags"] == ["genz"]


def test_apify_digest_raises_when_no_text(tmp_db, monkeypatch):
    monkeypatch.setattr(slang_trend, "APIFY_TOKEN", "tok")
    monkeypatch.setattr(
        slang_trend.httpx, "post",
        lambda url, **kw: _ApifyResp([{"id": 1}, "lạ"]),
    )
    with pytest.raises(ValueError):
        slang_trend.apify_digest()


def test_run_trend_update_counts_apify_source(tmp_db, monkeypatch):
    # LLM digest rớt, web trống, Apify sống → vẫn nhập pending từ caption tươi.
    monkeypatch.setattr(slang_trend, "APIFY_TOKEN", "tok")
    _mock_web(monkeypatch)
    monkeypatch.setattr(
        slang_trend.httpx, "post",
        lambda url, **kw: _ApifyResp([{"text": "caption chứa slang"}]),
    )

    def picky(system, user, timeout=0):
        if "caption chứa slang" in user:
            return json.dumps(_GOOD, ensure_ascii=False)
        raise RuntimeError("digest trực tiếp rớt")

    monkeypatch.setattr(correct, "chat_once", picky)
    res = run_trend_update()
    assert (res.new_pending, res.sources_ok, res.sources_skipped) == (2, 1, 1)


def test_run_trend_update_apify_error_is_skip(tmp_db, monkeypatch):
    monkeypatch.setattr(slang_trend, "APIFY_TOKEN", "tok")
    _mock_web(monkeypatch)

    def boom_post(url, **kw):
        raise OSError("hết quota")

    monkeypatch.setattr(slang_trend.httpx, "post", boom_post)
    _mock_llm(monkeypatch, json.dumps(_GOOD, ensure_ascii=False))
    res = run_trend_update()
    assert (res.new_pending, res.sources_ok, res.sources_skipped) == (2, 1, 1)


def test_apify_disabled_never_called(tmp_db, monkeypatch):
    _no_apify(monkeypatch)
    _mock_web(monkeypatch)
    monkeypatch.setattr(
        slang_trend, "_apify_items",
        lambda: pytest.fail("APIFY_TOKEN rỗng thì không được gọi Apify"),
    )
    _mock_llm(monkeypatch, json.dumps(_GOOD, ensure_ascii=False))
    assert run_trend_update().sources_ok == 1
