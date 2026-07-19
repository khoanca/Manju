"""Tests for app.denoise — streaming noise reduction helper."""
from __future__ import annotations

import numpy as np
import pytest

from app import denoise
from app.denoise import StreamDenoiser

SR = 16000
TONE_HZ = 440.0


def _noisy_sine(n: int, noise: float = 0.05, seed: int = 42) -> np.ndarray:
    rng = np.random.default_rng(seed)
    t = np.arange(n) / SR
    sig = 0.5 * np.sin(2 * np.pi * TONE_HZ * t) + noise * rng.standard_normal(n)
    return sig.astype(np.float32)


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
