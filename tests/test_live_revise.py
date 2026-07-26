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
        self.supports_revise = True
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

    def make(cfg: dict | None = None, engine: FakeEngine | None = None, real_trim: bool = False):
        eng = engine or FakeEngine()
        monkeypatch.setattr(engines, "get_engine", lambda: eng)
        base = {"store_audio": False}
        base.update(cfg or {})
        session = live.LiveSession(ws=SimpleNamespace(), loop=None, cfg=base)  # type: ignore[arg-type]
        sent: list[dict] = []
        session._send = sent.append  # type: ignore[method-assign]
        if not real_trim:
            # Test routing dùng audio zeros — VAD thật sẽ coi là im lặng và bỏ
            # decode (US-826). Patch trim để cô lập unit routing; hành vi trim
            # thật có test riêng bên dưới.
            session._trim_tail = lambda a: a  # type: ignore[method-assign]
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


def test_engine_without_revise_routes_straight_to_pass2(make_session):
    # mlx hiện tại (chưa có beam search): reroute qua revision_q chỉ tốn decode
    # lock + trễ pass 2 — supports_revise=False phải đi thẳng pass 2 như bản cũ.
    eng = FakeEngine([engines.DecodeResult(LONG, min_logprob=-0.9)])
    eng.supports_revise = False
    session, _, _ = make_session(engine=eng)
    session.utt_seq = 1

    session._finalize(AUDIO)

    assert session.revision_q.qsize() == 0
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


# ── Prompt live (US-826): CHỈ glossary user gõ tay, không bias tự động ──
# Đo 2026-07-26: bias tự động ("hình dung, kubernetes" từ thư viện) bị decoder
# echo lên subtitle khi im lặng/nhiễu và kéo no_speech_prob về 0. Thư viện chỉ
# còn phục vụ upload/reanalyze.


def test_prompt_is_user_glossary_verbatim(make_session):
    db.upsert_correction("cu bơ nét", "Kubernetes")
    db.upsert_correction("cu bơ nét", "Kubernetes")  # count=2 → auto-approve
    session, _, _ = make_session({"glossary": "Jira, OKR", "title": "họp về Grafana"})

    assert session.spec.glossary == "Jira, OKR"  # thư viện KHÔNG vào prompt live
    assert session.spec.glossary == session.glossary  # ASR và pass 2 cùng chuỗi


def test_prompt_stays_empty_when_no_user_glossary(make_session):
    # Glossary user rỗng → prompt RỖNG kể cả khi có title + thư viện có term
    # (chính là ca "kubernetes" bị echo khi prompt tự động còn bật).
    db.upsert_correction("cu bơ nét", "Kubernetes")
    db.upsert_correction("cu bơ nét", "Kubernetes")
    session, _, _ = make_session({"title": "họp sprint"})
    assert session.spec.glossary == ""


def test_silence_finalizes_empty_without_decode(make_session):
    # US-826: buffer toàn im lặng → không decode (đỡ 18-25s thang nhiệt lúc
    # Stop), final rỗng để client xoá dòng partial, không queue gì.
    eng = FakeEngine([engines.DecodeResult("bịa từ im lặng", min_logprob=-0.2)])
    session, _, sent = make_session(engine=eng, real_trim=True)
    session.utt_seq = 1

    session._finalize(AUDIO)  # AUDIO = zeros → VAD không thấy speech

    assert {"type": "final", "utt": 1, "text": ""} in sent
    assert session.correction_q.qsize() == 0
    assert session.revision_q.qsize() == 0
    assert eng.results  # decode KHÔNG được gọi — kết quả fake còn nguyên


def test_save_collapses_loop_across_utterance_boundary(make_session):
    # live-2341: mlx cắt chuỗi thoái hoá "NYE NYE NYE NYE" qua ranh giới 2
    # utterance → mỗi câu chỉ 2 NYE, lọt bộ lọc per-segment. _save gom lặp
    # toàn văn sau khi nối nên bản lưu không còn chuỗi lặp.
    session, _, _ = make_session()
    session.sentences = {1: "nhiều chủ đề khác nhau NYE NYE", 2: "NYE NYE Cái test mình"}
    session.raw_sentences = dict(session.sentences)

    tid = session._save()
    assert tid is not None
    row = db.read_transcript(tid)
    assert row is not None
    assert "NYE NYE NYE" not in row["text"]
    assert "NYE" in row["text"]  # giữ 1 lần, không drop nội dung thật quanh nó


def test_title_used_as_transcript_name(make_session):
    eng = FakeEngine([engines.DecodeResult(LONG, min_logprob=-0.2)])
    session, _, _ = make_session({"title": "Họp sprint 12"}, engine=eng)
    session.utt_seq = 1
    session._finalize(AUDIO)

    tid = session._save()
    assert tid is not None
    row = db.read_transcript(tid)
    assert row is not None and row["title"] == "Họp sprint 12"
