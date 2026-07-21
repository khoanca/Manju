"""transcribe.py helpers — slugify + đóng gói WAV, không load model."""
from __future__ import annotations

import wave
from types import SimpleNamespace

from app import corrections, db, engines, transcribe


def test_slugify_sanitizes_and_truncates():
    assert transcribe._slugify("My Meeting!.mp3") == "My-Meeting"
    assert transcribe._slugify("!!!.mp3") == "meeting"  # sạch còn rỗng → fallback
    assert len(transcribe._slugify("x" * 200)) == 60


def test_transcript_audio_path_rejects_traversal():
    assert transcribe.transcript_audio_path("../etc/passwd") is None
    assert transcribe.transcript_audio_path("a/b") is None


def test_pcm_to_wav_roundtrip(tmp_path):
    pcm = (1234).to_bytes(2, "little", signed=True) * 8000  # 0.5s @16kHz mono
    out = tmp_path / "clip.wav"
    transcribe.pcm_to_wav(pcm, out, sample_rate=16000)
    with wave.open(str(out), "rb") as w:
        assert w.getnchannels() == 1
        assert w.getsampwidth() == 2
        assert w.getframerate() == 16000
        assert w.getnframes() == 8000


def test_process_wires_flag_words_setting_into_spec(tmp_path, monkeypatch):
    """US-812: khi setting flag_words bật, upload path phải dựng DecodeSpec kèm
    flag_words và giữ segment words (kèm p) xuống DB qua TranscriptDraft."""
    captured: dict = {}
    seg_words = [{"w": "Kafka", "p": 0.31}]

    def fake_transcribe_file(path, spec, on_progress):
        captured["spec"] = spec
        return engines.FileResult(
            "triển khai Kafka", 1.2,
            [{"start": 0.0, "end": 1.2, "text": "triển khai Kafka", "words": seg_words}],
        )

    fake_engine = SimpleNamespace(
        info=SimpleNamespace(tier="mlx", model_name="fake (mlx)"),
        transcribe_file=fake_transcribe_file,
    )
    monkeypatch.setattr(engines, "get_engine", lambda: fake_engine)
    monkeypatch.setattr(corrections, "build_bias", lambda *a, **k: "")
    monkeypatch.setattr(db, "get_setting",
                        lambda key, default=None: "1" if key == "flag_words" else default)
    monkeypatch.setattr(transcribe, "save_transcript", lambda draft: captured.setdefault("draft", draft) or "tid-x")

    audio = tmp_path / "clip.wav"
    audio.write_bytes(b"\x00")
    transcribe._process(
        transcribe._new_job(), audio,
        transcribe.JobSpec(filename="clip.wav", language="vi", correct=False),
    )

    assert captured["spec"].flag_words is True
    assert captured["draft"].segments[0]["words"] == seg_words
