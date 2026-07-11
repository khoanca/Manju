"""transcribe.py helpers — slugify + đóng gói WAV, không load model."""
from __future__ import annotations

import wave

from app import transcribe


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
