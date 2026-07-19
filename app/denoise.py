"""Streaming noise reduction for live audio chunks.

Wraps noisereduce (spectral gating) with a rolling raw-audio tail so each
incremental chunk gets denoised with context; never fails — any error returns
the input unchanged.
"""
from __future__ import annotations

import numpy as np

OVERLAP_S = 0.25

_available: bool | None = None


def available() -> bool:
    """True if noisereduce importable (import guard, cached)."""
    global _available
    if _available is None:
        try:
            import noisereduce  # noqa: F401

            _available = True
        except ImportError:
            _available = False
    return _available


class StreamDenoiser:
    def __init__(self, sample_rate: int = 16000) -> None:
        self.sample_rate = sample_rate
        self._tail_len = int(OVERLAP_S * sample_rate)
        self._tail: np.ndarray = np.zeros(0, dtype=np.float32)

    def process(self, new_audio: np.ndarray) -> np.ndarray:
        """Denoise new_audio using the retained tail as context.

        Returns float32 with the exact length of new_audio; on any failure
        (noisereduce missing, chunk too short, internal error) returns the
        input unchanged. The raw (not denoised) tail is always retained.
        """
        raw = np.asarray(new_audio, dtype=np.float32)
        try:
            out = self._denoise(raw) if available() else raw
        except Exception:
            out = raw
        self._tail = np.concatenate([self._tail, raw])[-self._tail_len :]
        return out

    def _denoise(self, raw: np.ndarray) -> np.ndarray:
        import noisereduce

        ctx = len(self._tail)
        padded = np.concatenate([self._tail, raw]) if ctx else raw
        cleaned = noisereduce.reduce_noise(y=padded, sr=self.sample_rate, stationary=True)
        out = np.asarray(cleaned[ctx : ctx + len(raw)], dtype=np.float32)
        if out.shape != raw.shape:
            raise ValueError("denoised length mismatch")
        return out
