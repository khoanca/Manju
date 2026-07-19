"""EngineRegistry helpers — logic thuần, không load model Whisper."""
from __future__ import annotations

from types import SimpleNamespace

import numpy as np

from app import engines

AUDIO = np.zeros(16000, dtype=np.float32)


def make_mlx_engine(fake_transcribe) -> engines.MlxEngine:
    eng = engines.MlxEngine.__new__(engines.MlxEngine)
    eng._mlx = SimpleNamespace(transcribe=fake_transcribe)
    eng._repo = "fake/repo"
    eng.info = engines.EngineInfo("mlx", "fake (mlx)")
    return eng


def mlx_segment(text: str, avg_logprob: float, no_speech_prob: float = 0.1, words=None) -> dict:
    seg = {"text": text, "avg_logprob": avg_logprob, "no_speech_prob": no_speech_prob}
    if words is not None:
        seg["words"] = words
    return seg


def fw_segment(text: str, avg_logprob: float, no_speech_prob: float = 0.1, words=None):
    return SimpleNamespace(
        text=text, avg_logprob=avg_logprob, no_speech_prob=no_speech_prob, words=words
    )


class FakeFwModel:
    def __init__(self, segments, error: Exception | None = None):
        self.segments = segments
        self.error = error
        self.calls: list[dict] = []

    def transcribe(self, audio, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return iter(self.segments), SimpleNamespace(duration=1.0)


def make_cpu_engine(monkeypatch, model: FakeFwModel) -> tuple[engines.FwEngine, list[str]]:
    monkeypatch.setattr(engines, "_ram_gb", lambda: 8.0)  # live small / upload medium
    monkeypatch.delenv("WHISPER_MODEL", raising=False)
    eng = engines.FwEngine("cpu", "int8")
    requested: list[str] = []

    def fake_get_model(name: str) -> FakeFwModel:
        requested.append(name)
        return model

    monkeypatch.setattr(eng, "_get_model", fake_get_model)
    return eng, requested


def test_keep_segment_drops_hallucinated_outro():
    assert engines.keep_segment("Ghiền Mì Gõ", 0.1, -0.2) is False
    assert engines.keep_segment("Thanks for watching", 0.1, -0.2) is False
    assert engines.keep_segment("cảm ơn các bạn đã xem", 0.1, -0.2) is False


def test_keep_segment_drops_low_confidence_silence():
    # no_speech cao + logprob thấp = bịa lúc im lặng.
    assert engines.keep_segment("nội dung bình thường", 0.9, -1.5) is False


def test_keep_segment_keeps_real_speech():
    assert engines.keep_segment("triển khai Kubernetes", 0.1, -0.3) is True


def test_cpu_model_sizes_scales_with_hardware(monkeypatch):
    monkeypatch.setattr(engines, "_ram_gb", lambda: 32.0)
    monkeypatch.setattr(engines.os, "cpu_count", lambda: 12)
    assert engines.cpu_model_sizes() == ("medium", "large-v3-turbo")

    monkeypatch.setattr(engines, "_ram_gb", lambda: 8.0)
    monkeypatch.setattr(engines.os, "cpu_count", lambda: 4)
    assert engines.cpu_model_sizes() == ("small", "medium")

    monkeypatch.setattr(engines, "_ram_gb", lambda: 4.0)
    assert engines.cpu_model_sizes() == ("small", "small")


def test_probe_respects_forced_env(monkeypatch):
    monkeypatch.setattr(engines.os, "environ", {**engines.os.environ, "ASR_ENGINE": "cpu"})
    engine = engines._probe()
    assert engine.info.tier == "cpu"


def test_decode_scored_min_logprob_ignores_filtered_segments():
    segments = [
        mlx_segment("triển khai ", -0.5),
        mlx_segment("Kubernetes", -0.9),
        mlx_segment("Ghiền Mì Gõ", -2.0),  # hallucination → bị lọc, không tính min
    ]
    eng = make_mlx_engine(lambda audio, **kw: {"segments": segments})
    spec = engines.DecodeSpec(language="vi")

    result = eng.decode_scored(AUDIO, spec, final=True)

    assert result.text == "triển khai Kubernetes"
    assert result.min_logprob == -0.9
    assert result.words == ()


def test_decode_scored_defaults_when_no_segment_kept():
    eng = make_mlx_engine(lambda audio, **kw: {"segments": [mlx_segment("bịa", -1.5, 0.9)]})

    result = eng.decode_scored(AUDIO, engines.DecodeSpec(language="vi"), final=True)

    assert result == engines.DecodeResult(text="", min_logprob=0.0, words=())


def test_mlx_words_filled_only_when_flag_and_final():
    calls: list[dict] = []

    def fake_transcribe(audio, **kw):
        calls.append(kw)
        words = [{"word": " Kafka", "probability": 0.42, "start": 0.0, "end": 0.5}]
        return {"segments": [mlx_segment(" Kafka", -0.3, words=words)]}

    eng = make_mlx_engine(fake_transcribe)
    spec = engines.DecodeSpec(language="vi", flag_words=True)

    scored_final = eng.decode_scored(AUDIO, spec, final=True)
    scored_partial = eng.decode_scored(AUDIO, spec, final=False)
    scored_off = eng.decode_scored(AUDIO, engines.DecodeSpec(language="vi"), final=True)

    assert scored_final.words == ((" Kafka", 0.42),)
    assert calls[0]["word_timestamps"] is True
    assert scored_partial.words == ()
    assert calls[1]["word_timestamps"] is False
    assert scored_off.words == ()
    assert calls[2]["word_timestamps"] is False


def test_fw_words_filled_only_when_flag_and_final(monkeypatch):
    words = [SimpleNamespace(word=" GraphQL", probability=0.35, start=0.0, end=0.5)]
    model = FakeFwModel([fw_segment(" GraphQL", -0.4, words=words)])
    eng, _ = make_cpu_engine(monkeypatch, model)
    spec = engines.DecodeSpec(language="vi", flag_words=True)

    scored_final = eng.decode_scored(AUDIO, spec, final=True)
    model.segments = [fw_segment(" GraphQL", -0.4, words=None)]
    scored_partial = eng.decode_scored(AUDIO, spec, final=False)

    assert scored_final.words == ((" GraphQL", 0.35),)
    assert model.calls[0]["word_timestamps"] is True
    assert scored_partial.words == ()
    assert "word_timestamps" not in model.calls[1]


def test_decode_matches_decode_scored_text():
    segments = [mlx_segment(" xin chào", -0.4), mlx_segment(" thanks for watching", -0.2)]
    eng = make_mlx_engine(lambda audio, **kw: {"segments": segments})
    spec = engines.DecodeSpec(language="vi")

    assert eng.decode(AUDIO, spec, final=True) == eng.decode_scored(AUDIO, spec, final=True).text
    assert eng.decode(AUDIO, spec, final=True) == "xin chào"


def test_revise_returns_none_when_lock_busy(monkeypatch):
    model = FakeFwModel([fw_segment("bận rồi", -0.3)])
    eng, _ = make_cpu_engine(monkeypatch, model)

    engines._decode_lock.acquire()
    try:
        assert eng.revise(AUDIO, engines.DecodeSpec(language="vi")) is None
    finally:
        engines._decode_lock.release()
    assert model.calls == []


def test_revise_returns_none_on_exception(monkeypatch):
    model = FakeFwModel([], error=RuntimeError("model exploded"))
    eng, _ = make_cpu_engine(monkeypatch, model)

    assert eng.revise(AUDIO, engines.DecodeSpec(language="vi")) is None
    assert not engines._decode_lock.locked()  # lock phải được nhả sau lỗi


def test_cpu_revise_uses_bigger_upload_model(monkeypatch):
    model = FakeFwModel([fw_segment(" nâng cấp", -0.3)])
    eng, requested = make_cpu_engine(monkeypatch, model)

    text = eng.revise(AUDIO, engines.DecodeSpec(language="vi"))

    assert text == "nâng cấp"
    assert eng.live_model == "small"
    assert requested == ["medium"]  # model upload to hơn, không phải model live
    assert model.calls[0]["beam_size"] == 5


def test_cuda_revise_returns_none(monkeypatch):
    model = FakeFwModel([fw_segment("không dùng", -0.3)])
    eng = engines.FwEngine("cuda", "float16")
    monkeypatch.setattr(eng, "_get_model", lambda name: model)

    assert eng.revise(AUDIO, engines.DecodeSpec(language="vi")) is None
    assert model.calls == []


def test_mlx_revise_passes_beam_and_zero_temperature():
    calls: list[dict] = []

    def fake_transcribe(audio, **kw):
        calls.append(kw)
        return {"segments": [mlx_segment(" chốt lại", -0.3)]}

    eng = make_mlx_engine(fake_transcribe)

    text = eng.revise(AUDIO, engines.DecodeSpec(language="vi"))

    assert text == "chốt lại"
    assert calls[0]["beam_size"] == 5
    assert calls[0]["temperature"] == 0.0


def test_mlx_revise_returns_none_when_beam_unsupported():
    # mlx-whisper 0.4.3: beam search chưa implement → transcribe raise.
    def fake_transcribe(audio, **kw):
        raise NotImplementedError("Beam search decoder is not yet implemented")

    eng = make_mlx_engine(fake_transcribe)

    assert eng.revise(AUDIO, engines.DecodeSpec(language="vi")) is None
    assert not engines._decode_lock.locked()
