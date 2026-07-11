"""EngineRegistry helpers — logic thuần, không load model Whisper."""
from __future__ import annotations

from app import engines


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
