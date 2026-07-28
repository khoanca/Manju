"""FR-10 T-008: LiveSession nhánh online — token cloud → protocol, fallback, re-listen.

Transport fake (kịch bản token), engine fake — không model/mạng thật. Fixture
db/paths theo pattern test_live_revise.
"""
from __future__ import annotations

import json
import threading
import time
from dataclasses import replace
from types import SimpleNamespace

import pytest

from app import cloud_stt, db, engines, live, transcribe
from tests.test_cloud_stt import FakeWS, tok


class FakeEngine:
    def __init__(self):
        self.info = SimpleNamespace(tier="mlx", model_name="fake-mlx")
        self.supports_revise = False

    def decode(self, audio, spec, *, final):
        return ""

    def decode_scored(self, audio, spec, *, final):
        return engines.DecodeResult("", 0.0, ())


@pytest.fixture
def make_online(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DATA", tmp_path)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(db, "DEFAULT_RECORDINGS", tmp_path / "rec")
    monkeypatch.setattr(transcribe, "TRANSCRIPTS", tmp_path / "tx")
    monkeypatch.setattr(transcribe, "UPLOADS", tmp_path / "up")
    for d in ("tx", "rec", "up"):
        (tmp_path / d).mkdir()
    db.init()
    monkeypatch.setenv("SONIOX_API_KEY", "test-key")
    monkeypatch.setattr(engines, "get_engine", lambda: FakeEngine())

    def make(script: list[dict], cfg: dict | None = None):
        ws = FakeWS(script)
        real_cls = cloud_stt.SonioxLive
        monkeypatch.setattr(
            cloud_stt, "SonioxLive",
            lambda spec: real_cls(replace(spec, connect=lambda: ws)),
        )
        base = {"store_audio": False, "mode": "online"}
        base.update(cfg or {})
        session = live.LiveSession(ws=SimpleNamespace(), loop=None, cfg=base)  # type: ignore[arg-type]
        sent: list[dict] = []
        session._send = sent.append  # type: ignore[method-assign]
        session.start()
        return session, ws, sent

    return make


def _wait(pred, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(0.02)
    return False


def test_online_tokens_flow_to_protocol_and_save(make_online):
    session, _, sent = make_online([
        {"tokens": [tok("xin ", 0, 200, speaker="1"), tok("ch", 200, 300, final=False)]},
        {"tokens": [tok("deploy model", 1300, 1900)]},  # gap 1100ms → utt mới
    ])
    assert session.mode == "online"
    assert session.model_name == cloud_stt.RT_MODEL
    assert _wait(lambda: any(m.get("type") == "final" for m in sent))

    tid = session.shutdown()  # flush utt cuối + lưu

    assert {"type": "final", "utt": 1, "text": "xin"} in sent
    assert {"type": "final", "utt": 2, "text": "deploy model"} in sent
    assert any(m == {"type": "partial", "utt": 1, "text": "xin ch"} for m in sent)
    row = db.read_transcript(tid)
    assert row is not None and row["text"] == "xin deploy model"
    raw_segments = row["raw_segments"]  # read_transcript đã parse JSON
    assert raw_segments[0]["engine"] == cloud_stt.RT_MODEL
    assert raw_segments[0]["speaker"] == "1"


def test_cloud_cleanup_gates_pass2(make_online, monkeypatch):
    # Mặc định (cloud_cleanup tắt): transcript cloud là nguồn chân lý, không LLM.
    session, _, _ = make_online([])
    assert session.correct_enabled is False
    session.shutdown()
    # Bật cloud_cleanup → pass 2 chạy như nhánh local.
    db.set_setting("cloud_cleanup", "1")
    session2, _, _ = make_online([])
    assert session2.correct_enabled is True
    session2.shutdown()


def test_provider_error_falls_back_to_local(make_online):
    session, ws, sent = make_online([])
    # Thứ tự thật: audio feed TRƯỚC, token/error mô tả audio đã gửi về SAU.
    session.feed(b"\x00\x00" * 32000)  # 2s
    ws.incoming.put(json.dumps({"tokens": [tok("đã chốt", 0, 1000)]}))
    ws.incoming.put(json.dumps(
        {"error_code": 500, "error_type": "service_unavailable", "error_message": "sập"}
    ))
    assert _wait(lambda: session.mode == "offline")

    assert _wait(lambda: session._decode_thread.is_alive())
    assert session.cloud is None
    # Trim tới token final cuối (1000ms = 16000 samples) — local không decode lại.
    assert session.consumed_samples == 16000
    assert any(m.get("type") == "mode" and m.get("mode") == "offline" for m in sent)
    # Câu cloud đã chốt vẫn trong transcript sau khi lưu.
    tid = session.shutdown()
    row = db.read_transcript(tid)
    assert row is not None and "đã chốt" in row["text"]


def test_error_after_stop_does_not_fallback(make_online):
    session, ws, _ = make_online([{"tokens": [tok("câu cuối", 0, 400)]}])
    session.shutdown()
    # Sau Stop, lỗi từ rx (server đóng WS...) không được lật mode/bật decode loop.
    assert session.mode == "online"
    assert not session._decode_thread.is_alive()


def test_relisten_replaces_text_keeps_live_raw(make_online, monkeypatch):
    session, _, _ = make_online(
        [{"tokens": [tok("bản live", 0, 500)]}], cfg={"store_audio": True}
    )
    session.feed(b"\x00\x00" * 16000)
    calls: list = []

    def fake_async(path, spec, client=None):
        calls.append((path, spec.language))
        return "bản async chuẩn hơn"

    monkeypatch.setattr(cloud_stt, "transcribe_file_async", fake_async)
    tid = session.shutdown()
    assert tid is not None
    assert _wait(lambda: bool(calls))
    assert _wait(
        lambda: (db.read_transcript(tid) or {}).get("text") == "bản async chuẩn hơn"
    )
    row = db.read_transcript(tid)
    assert row is not None and row["raw_text"] == "bản live"
    assert (transcribe.TRANSCRIPTS / f"{tid}.txt").read_text() == "bản async chuẩn hơn"


def test_relisten_respects_toggle_off(make_online, monkeypatch):
    db.set_setting("cloud_relisten", "0")
    session, _, _ = make_online(
        [{"tokens": [tok("bản live", 0, 500)]}], cfg={"store_audio": True}
    )
    session.feed(b"\x00\x00" * 16000)
    called = threading.Event()
    monkeypatch.setattr(session, "_relisten", lambda tid: called.set())
    session.shutdown()
    assert not called.wait(0.3)


def test_online_request_without_key_runs_offline(tmp_path, monkeypatch):
    monkeypatch.delenv("SONIOX_API_KEY", raising=False)
    monkeypatch.setattr(engines, "get_engine", lambda: FakeEngine())
    session = live.LiveSession(
        ws=SimpleNamespace(), loop=None,  # type: ignore[arg-type]
        cfg={"store_audio": False, "mode": "online"},
    )
    assert session.mode == "offline"
    assert session.correct_enabled is True  # nhánh local giữ pass 2 như cũ
