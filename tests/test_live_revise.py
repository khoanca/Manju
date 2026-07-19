"""US-811/812: re-decode câu confidence thấp + flag từ nghe không rõ cho pass 2.

Không load model/LLM thật: FakeEngine trả DecodeResult định sẵn; không sleep —
đồng bộ bằng chạy loop đồng bộ tới sentinel.
"""
from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from app import db, engines, live, transcribe


class FakeEngine:
    def __init__(self, results: list | None = None, revised: str | None = None):
        self.info = SimpleNamespace(tier="mlx", model_name="fake")
        self.results = list(results or [])
        self.revised = revised
        self.revise_calls: list[np.ndarray] = []

    def decode(self, audio, spec, *, final):
        return self.decode_scored(audio, spec, final=final).text

    def decode_scored(self, audio, spec, *, final):
        if self.results:
            return self.results.pop(0)
        return engines.DecodeResult("", 0.0, ())

    def revise(self, audio, spec):
        self.revise_calls.append(audio)
        return self.revised


@pytest.fixture
def make_session(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DATA", tmp_path)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(db, "DEFAULT_RECORDINGS", tmp_path / "rec")
    monkeypatch.setattr(transcribe, "TRANSCRIPTS", tmp_path / "tx")
    (tmp_path / "tx").mkdir()
    (tmp_path / "rec").mkdir()
    db.init()

    def make(cfg: dict | None = None, engine: FakeEngine | None = None):
        eng = engine or FakeEngine()
        monkeypatch.setattr(engines, "get_engine", lambda: eng)
        base = {"store_audio": False}
        base.update(cfg or {})
        session = live.LiveSession(ws=SimpleNamespace(), loop=None, cfg=base)  # type: ignore[arg-type]
        sent: list[dict] = []
        session._send = sent.append  # type: ignore[method-assign]
        return session, eng, sent

    return make


AUDIO = np.zeros(live.SAMPLE_RATE, dtype=np.float32)
LONG = "một câu đủ dài để qua ngưỡng pass hai"


def test_low_confidence_routes_to_revision_queue(make_session):
    eng = FakeEngine([engines.DecodeResult(LONG, min_logprob=-0.9)])
    session, _, sent = make_session(engine=eng)
    session.utt_seq = 1

    session._finalize(AUDIO)

    assert session.revision_q.qsize() == 1
    assert session.correction_q.qsize() == 0
    assert {"type": "final", "utt": 1, "text": LONG} in sent


def test_confident_final_routes_to_correction_queue(make_session):
    eng = FakeEngine([engines.DecodeResult(LONG, min_logprob=-0.2)])
    session, _, _ = make_session(engine=eng)
    session.utt_seq = 1

    session._finalize(AUDIO)

    assert session.revision_q.qsize() == 0
    assert session.correction_q.get_nowait() == (1, LONG, ())


def test_revise_success_sends_revise_then_queues_pass2(make_session):
    revised = "một câu đã được decode lại rõ ràng hơn"
    session, eng, sent = make_session(engine=FakeEngine(revised=revised))
    session.revision_q.put((3, AUDIO, LONG, ("mờ",)))
    session.revision_q.put(None)

    session._revise_loop()

    assert {"type": "revise", "utt": 3, "text": revised} in sent
    assert session.sentences[3] == revised
    assert session.raw_sentences[3] == revised
    # Pass 2 nhận bản revise (text tốt nhất) kèm uncertain giữ nguyên.
    assert session.correction_q.get_nowait() == (3, revised, ("mờ",))


def test_revise_failure_keeps_original_for_pass2(make_session):
    session, eng, sent = make_session(engine=FakeEngine(revised=None))
    session.stop_event.set()  # bỏ retry chờ 0.5s — test không sleep
    session.revision_q.put((3, AUDIO, LONG, ()))
    session.revision_q.put(None)

    session._revise_loop()

    assert all(m["type"] != "revise" for m in sent)
    assert session.correction_q.get_nowait() == (3, LONG, ())


def test_revise_unchanged_text_sends_no_revise_msg(make_session):
    session, _, sent = make_session(engine=FakeEngine(revised=LONG))
    session.revision_q.put((3, AUDIO, LONG, ()))
    session.revision_q.put(None)

    session._revise_loop()

    assert all(m["type"] != "revise" for m in sent)
    assert session.correction_q.get_nowait() == (3, LONG, ())


def test_revision_backlog_full_falls_through_to_pass2(make_session):
    eng = FakeEngine([engines.DecodeResult(LONG, min_logprob=-0.9)])
    session, _, _ = make_session(engine=eng)
    session.utt_seq = 1
    for _ in range(live.REVISION_BACKLOG_MAX):
        session.revision_q.put((0, AUDIO, "x", ()))

    session._finalize(AUDIO)

    assert session.revision_q.qsize() == live.REVISION_BACKLOG_MAX
    assert session.correction_q.get_nowait() == (1, LONG, ())


def test_revise_disabled_by_env_routes_to_pass2(make_session, monkeypatch):
    monkeypatch.setattr(live, "REVISE_ENABLED", False)
    eng = FakeEngine([engines.DecodeResult(LONG, min_logprob=-0.9)])
    session, _, _ = make_session(engine=eng)
    session.utt_seq = 1

    session._finalize(AUDIO)

    assert session.revision_q.qsize() == 0
    assert session.correction_q.get_nowait() == (1, LONG, ())


def test_uncertain_words_flow_into_correction_item(make_session):
    db.set_setting("flag_words", "1")
    words = ((" Kubernetes", 0.3), (" họp", 0.95))
    eng = FakeEngine([engines.DecodeResult(LONG, min_logprob=-0.2, words=words)])
    session, _, _ = make_session(engine=eng)
    assert session.spec.flag_words is True
    session.utt_seq = 1

    session._finalize(AUDIO)

    assert session.correction_q.get_nowait() == (1, LONG, ("Kubernetes",))


def test_uncertain_capped_at_max(make_session):
    words = tuple((f"w{i}", 0.1) for i in range(live.UNCERTAIN_MAX + 4))
    eng = FakeEngine([engines.DecodeResult(LONG, min_logprob=-0.2, words=words)])
    session, _, _ = make_session(engine=eng)
    session.utt_seq = 1

    session._finalize(AUDIO)

    _, _, uncertain = session.correction_q.get_nowait()
    assert len(uncertain) == live.UNCERTAIN_MAX


# ── _asr_prompt + _refresh_bias (US-806/809): topic vào prompt, swap spec ──


def test_asr_prompt_topic_first_user_glossary_last(make_session):
    from app import corrections

    db.upsert_correction("cu bơ nét", "Kubernetes")
    db.upsert_correction("cu bơ nét", "Kubernetes")  # count=2 → auto-approve
    session, _, _ = make_session({"glossary": "Jira, OKR", "title": "họp hạ tầng"})

    prompt = session.spec.glossary
    assert prompt.startswith("Chủ đề: họp hạ tầng.")
    assert prompt.endswith("Jira, OKR")  # glossary user cuối — Whisper giữ đuôi
    assert "Kubernetes" in prompt
    assert corrections.build_bias("Jira, OKR").startswith("Jira, OKR")  # pass 2 vẫn user-first


def test_asr_prompt_without_topic_is_plain_bias(make_session):
    session, _, _ = make_session({"glossary": "Jira"})
    assert "Chủ đề" not in session.spec.glossary
    assert session.spec.glossary.endswith("Jira")


def test_refresh_bias_swaps_spec_atomically(make_session):
    session, _, _ = make_session({"glossary": "Jira"})
    old_spec = session.spec

    session._refresh_bias("bàn về Kubernetes")

    assert session.spec is not old_spec
    assert session.spec.glossary.startswith("Chủ đề: bàn về Kubernetes.")
    assert "Chủ đề" not in old_spec.glossary  # object cũ không bị sửa (frozen)


def test_refresh_bias_error_keeps_old_spec(make_session, monkeypatch):
    from app import corrections

    session, _, _ = make_session({"glossary": "Jira"})
    old_spec, old_gloss = session.spec, session.glossary

    def boom(*a, **k):
        raise RuntimeError("db hỏng")

    monkeypatch.setattr(corrections, "build_bias", boom)
    session._refresh_bias("topic mới")

    assert session.spec is old_spec
    assert session.glossary == old_gloss


def test_topic_truncated_in_prompt(make_session):
    session, _, _ = make_session({"glossary": "Jira"})
    session._refresh_bias("x" * 500)
    assert f"Chủ đề: {'x' * live.TOPIC_PROMPT_CHARS}." in session.spec.glossary


def test_title_used_as_transcript_name(make_session):
    eng = FakeEngine([engines.DecodeResult(LONG, min_logprob=-0.2)])
    session, _, _ = make_session({"title": "Họp sprint 12"}, engine=eng)
    session.utt_seq = 1
    session._finalize(AUDIO)

    tid = session._save()
    assert tid is not None
    row = db.read_transcript(tid)
    assert row is not None and row["title"] == "Họp sprint 12"
