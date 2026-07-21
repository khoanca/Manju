"""Tests for app.denoise — streaming noise reduction helper."""
from __future__ import annotations

import wave

import numpy as np
import pytest

from app import denoise
from app.denoise import DenoiseParams, StreamDenoiser

SR = 16000
TONE_HZ = 440.0


def _noisy_sine(n: int, noise: float = 0.05, seed: int = 42) -> np.ndarray:
    rng = np.random.default_rng(seed)
    t = np.arange(n) / SR
    sig = 0.5 * np.sin(2 * np.pi * TONE_HZ * t) + noise * rng.standard_normal(n)
    return sig.astype(np.float32)


def _tone(freq: float, n: int, amp: float = 0.5) -> np.ndarray:
    t = np.arange(n) / SR
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def _band_rms(sig: np.ndarray, lo: float, hi: float) -> float:
    spec = np.fft.rfft(sig)
    freqs = np.fft.rfftfreq(len(sig), 1 / SR)
    band = (freqs >= lo) & (freqs <= hi)
    return float(np.sqrt(np.mean(np.abs(spec[band]) ** 2)))


@pytest.mark.parametrize("n", [100, 12800, 19200])
def test_first_call_same_length_and_dtype(n: int) -> None:
    d = StreamDenoiser()

    out = d.process(_noisy_sine(n))

    assert out.dtype == np.float32
    assert len(out) == n


def test_sequential_chunks_same_length_and_dtype() -> None:
    d = StreamDenoiser()
    x = _noisy_sine(48000)

    sizes = [100, 12800, 19200, 1, 7900]
    pos = 0
    for n in sizes:
        out = d.process(x[pos : pos + n])
        assert out.dtype == np.float32
        assert len(out) == n
        pos += n


def test_stateful_halves_close_to_single_call() -> None:
    x = _noisy_sine(25600)
    half = len(x) // 2

    full = StreamDenoiser().process(x)
    d = StreamDenoiser()
    d.process(x[:half])
    second = d.process(x[half:])

    ref = full[half:]
    assert len(second) == half
    assert np.corrcoef(second, ref)[0, 1] > 0.99
    assert np.allclose(second, ref, atol=0.05)


def test_denoise_lowers_noise_floor_and_improves_snr() -> None:
    x = _noisy_sine(25600)

    out = StreamDenoiser().process(x)

    assert not np.allclose(out, x)
    noise_ratio = _band_rms(out, 2000, 8000) / _band_rms(x, 2000, 8000)
    assert noise_ratio < 0.5
    snr_in = _band_rms(x, 400, 480) / _band_rms(x, 2000, 8000)
    snr_out = _band_rms(out, 400, 480) / _band_rms(out, 2000, 8000)
    assert snr_out > snr_in


def test_reduce_noise_failure_returns_input_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    nr = pytest.importorskip("noisereduce")

    def boom(**_kwargs: object) -> np.ndarray:
        raise RuntimeError("boom")

    monkeypatch.setattr(nr, "reduce_noise", boom)
    d = StreamDenoiser()
    x = _noisy_sine(12800)

    out = d.process(x)

    assert out.dtype == np.float32
    assert np.array_equal(out, x)


def test_unavailable_is_passthrough(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(denoise, "_available", False)
    d = StreamDenoiser()
    x = _noisy_sine(12800)

    out = d.process(x)

    assert denoise.available() is False
    assert out.dtype == np.float32
    assert np.array_equal(out, x)


def test_lower_prop_decrease_leaves_more_noise() -> None:
    pytest.importorskip("noisereduce")
    x = _noisy_sine(25600)

    soft = StreamDenoiser(params=DenoiseParams(prop_decrease=0.3)).process(x)
    hard = StreamDenoiser(params=DenoiseParams(prop_decrease=1.0)).process(x)

    # Khử nhẹ (0.3) để lại nhiều năng lượng nền hơn khử mạnh (1.0).
    assert _band_rms(soft, 2000, 8000) > _band_rms(hard, 2000, 8000)


def test_non_stationary_same_length_dtype_and_changes_signal() -> None:
    pytest.importorskip("noisereduce")
    x = _noisy_sine(25600)

    out = StreamDenoiser(params=DenoiseParams(stationary=False)).process(x)

    assert out.dtype == np.float32
    assert len(out) == len(x)
    assert not np.array_equal(out, x)


def test_highpass_cuts_low_band_keeps_voice_band() -> None:
    pytest.importorskip("scipy")
    n = 16000
    y = _tone(60.0, n) + _tone(1000.0, n)

    out = denoise._prefilter(y, SR, highpass_hz=200.0, hum_hz=0.0)

    assert out.dtype == np.float32
    assert _band_rms(out, 0, 120) < 0.3 * _band_rms(y, 0, 120)
    assert _band_rms(out, 900, 1100) > 0.8 * _band_rms(y, 900, 1100)


@pytest.mark.parametrize("hum", [50.0, 60.0])
def test_notch_removes_hum_keeps_voice_band(hum: float) -> None:
    pytest.importorskip("scipy")
    n = 16000
    y = _tone(hum, n) + _tone(440.0, n)

    out = denoise._prefilter(y, SR, highpass_hz=0.0, hum_hz=hum)

    assert out.dtype == np.float32
    # Notch hạ mạnh đúng bin hum; band thoại 440Hz phần lớn giữ lại (một hài
    # của hum rơi gần 440 nên không thể kỳ vọng giữ 100%).
    assert _band_rms(out, hum - 5, hum + 5) < 0.3 * _band_rms(y, hum - 5, hum + 5)
    assert _band_rms(out, 420, 460) > 0.6 * _band_rms(y, 420, 460)


def test_reduce_file_writes_16k_mono_wav(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("noisereduce")
    monkeypatch.setattr(denoise, "_load_audio", lambda _p, _sr: _noisy_sine(25600))

    out_path = denoise.reduce_file(
        tmp_path / "in.wav", DenoiseParams(), sample_rate=16000
    )

    assert out_path is not None
    assert out_path.exists()
    try:
        with wave.open(str(out_path), "rb") as w:
            assert w.getnchannels() == 1
            assert w.getframerate() == 16000
            assert w.getnframes() > 0
    finally:
        out_path.unlink()


def test_reduce_file_returns_none_when_load_fails(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("noisereduce")

    def boom(_p: object, _sr: object) -> np.ndarray:
        raise RuntimeError("ffmpeg missing")

    monkeypatch.setattr(denoise, "_load_audio", boom)

    assert denoise.reduce_file(tmp_path / "in.wav", DenoiseParams()) is None


def test_reduce_file_returns_none_when_unavailable(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(denoise, "_available", False)

    assert denoise.reduce_file(tmp_path / "in.wav", DenoiseParams()) is None


def test_params_from_settings_maps_all_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    from app import db

    values = {
        "denoise_strength": "50",
        "denoise_mode": "nonstationary",
        "denoise_highpass": "80",
        "denoise_hum": "60",
    }
    monkeypatch.setattr(db, "get_setting", lambda key, default=None: values.get(key, default))

    params = denoise.params_from_settings()

    assert params.prop_decrease == pytest.approx(0.5)
    assert params.stationary is False
    assert params.highpass_hz == pytest.approx(80.0)
    assert params.hum_hz == pytest.approx(60.0)


def test_params_from_settings_defaults_when_keys_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app import db

    monkeypatch.setattr(db, "get_setting", lambda _key, default=None: default)

    assert denoise.params_from_settings() == DenoiseParams()
