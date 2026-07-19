"""US-814: nhận diện người nói realtime theo utterance final.

Không load model sherpa thật: monkeypatch models_present/embed_utterance/
best_match; DB tạm cho voiceprints + personal_terms.
"""
from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from app import db, diarize, engines, live, transcribe


class FakeEngine:
    def __init__(self) -> None:
        self.info = SimpleNamespace(tier="mlx", model_name="fake")

    def decode(self, audio, spec, *, final):
        return ""

    def decode_scored(self, audio, spec, *, final):
        return engines.DecodeResult("", 0.0, ())

    def revise(self, audio, spec):
        return None


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DATA", tmp_path)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(db, "DEFAULT_RECORDINGS", tmp_path / "rec")
    monkeypatch.setattr(transcribe, "TRANSCRIPTS", tmp_path / "tx")
    (tmp_path / "tx").mkdir()
    (tmp_path / "rec").mkdir()
    db.init()
    monkeypatch.setattr(engines, "get_engine", lambda: FakeEngine())
    return monkeypatch


def _make(cfg: dict | None = None):
    base = {"store_audio": False}
    base.update(cfg or {})
    session = live.LiveSession(ws=SimpleNamespace(), loop=None, cfg=base)  # type: ignore[arg-type]
    sent: list[dict] = []
    session._send = sent.append  # type: ignore[method-assign]
    return session, sent


def _enroll(env, name: str = "An") -> str:
    sid = db.find_or_create_speaker(name)
    vec = np.ones(4, dtype=np.float32)
    db.save_voiceprint(sid, vec.tobytes(), 4, 1, None)
    env.setattr(diarize, "models_present", lambda: True)
    return sid


LONG_AUDIO = np.zeros(int(live.SAMPLE_RATE * 2.0), dtype=np.float32)
SHORT_AUDIO = np.zeros(int(live.SAMPLE_RATE * 0.5), dtype=np.float32)


def test_no_voiceprints_disables_ident_thread(env):
    env.setattr(diarize, "models_present", lambda: True)
    session, _ = _make()
    assert session._ident_thread is None

    session._queue_ident(1, LONG_AUDIO)  # không nổ, không queue
    assert session.ident_q.qsize() == 0


def test_missing_models_disables_ident_thread(env):
    env.setattr(diarize, "models_present", lambda: False)
    session, _ = _make()
    assert session._ident_thread is None


def test_queue_ident_gates_short_audio_and_backlog(env):
    _enroll(env)
    session, _ = _make()
    assert session._ident_thread is not None

    session._queue_ident(1, SHORT_AUDIO)  # < LIVE_ID_MIN_S → bỏ
    assert session.ident_q.qsize() == 0
    for i in range(live.IDENT_BACKLOG_MAX + 2):
        session._queue_ident(i, LONG_AUDIO)
    assert session.ident_q.qsize() == live.IDENT_BACKLOG_MAX


def test_match_sends_speaker_msg_and_swaps_bias_once_per_change(env):
    sid = _enroll(env, "An")
    db.replace_speaker_terms("t1", [(sid, "Kubernetes", 5)])
    session, sent = _make()
    env.setattr(diarize, "embed_utterance", lambda a: np.ones(4, dtype=np.float32))
    env.setattr(diarize, "best_match", lambda v, vps, th: sid)
    swaps: list[str] = []
    orig = session._bias_speaker
    session._bias_speaker = lambda s: swaps.append(s) or orig(s)  # type: ignore[method-assign]

    session.ident_q.put((1, LONG_AUDIO))
    session.ident_q.put((2, LONG_AUDIO))  # cùng người → không swap lần 2
    session.ident_q.put(None)
    session._ident_loop()

    assert {"type": "speaker", "utt": 1, "name": "An"} in sent
    assert {"type": "speaker", "utt": 2, "name": "An"} in sent
    assert swaps == [sid]
    assert session._active_spk == sid
    assert "Kubernetes" in session._personal_now
    assert "Kubernetes" in session.spec.glossary


def test_below_threshold_no_msg_no_swap(env):
    _enroll(env)
    session, sent = _make()
    env.setattr(diarize, "embed_utterance", lambda a: np.ones(4, dtype=np.float32))
    env.setattr(diarize, "best_match", lambda v, vps, th: None)

    session.ident_q.put((1, LONG_AUDIO))
    session.ident_q.put(None)
    session._ident_loop()

    assert sent == []
    assert session._active_spk is None


def test_embed_error_never_fails(env):
    _enroll(env)
    session, sent = _make()

    def boom(a):
        raise RuntimeError("onnx hỏng")

    env.setattr(diarize, "embed_utterance", boom)
    session.ident_q.put((1, LONG_AUDIO))
    session.ident_q.put(None)
    session._ident_loop()

    assert sent == []


def test_active_speaker_terms_ranked_before_participants(env):
    sid_a = _enroll(env, "An")
    sid_b = db.find_or_create_speaker("Bình")
    db.replace_speaker_terms("t1", [(sid_a, "Terraform", 9), (sid_b, "Grafana", 9)])
    session, _ = _make({"participants": [sid_a, sid_b]})

    session._bias_speaker(sid_b)

    terms = list(session._personal_now)
    assert terms.index("Grafana") < terms.index("Terraform")


def test_embed_utterance_guards_short_audio(env):
    env.setattr(diarize, "models_present", lambda: True)
    assert diarize.embed_utterance(SHORT_AUDIO) is None
    env.setattr(diarize, "models_present", lambda: False)
    assert diarize.embed_utterance(LONG_AUDIO) is None
