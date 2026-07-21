"""Noise reduction for live audio chunks and uploaded files.

Wraps noisereduce (spectral gating) + scipy prefilters (high-pass rumble cut,
notch mains hum). Streaming path keeps a rolling raw-audio tail so each
incremental chunk gets denoised/filtered with context. Batch path (reduce_file)
loads a file via ffmpeg, cleans it, writes a temp WAV. Both never fail — any
error returns the input unchanged / None so ASR always has audio to work with.

Params are carried in DenoiseParams; params_from_settings() reads the user's
settings once so live.py and transcribe.py map identically. Defaults preserve
the original US-813 behaviour (full-strength stationary gating, no prefilter).
"""
from __future__ import annotations

import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np

OVERLAP_S = 0.25
SAMPLE_RATE = 16000

_available: bool | None = None


@dataclass(frozen=True)
class DenoiseParams:
    """Tuning shared by streaming + batch. Defaults = original US-813 behaviour."""

    prop_decrease: float = 1.0  # 0..1 — cường độ khử (1 = mạnh nhất)
    stationary: bool = True  # False = non-stationary (nền thay đổi)
    highpass_hz: float = 0.0  # 0 = tắt; cắt rumble dưới ngưỡng
    hum_hz: float = 0.0  # 0 = tắt; 50/60 = notch điện lưới + sóng hài


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


def params_from_settings() -> DenoiseParams:
    """Đọc setting → DenoiseParams (một nguồn duy nhất cho live + upload).

    Never-fail: thiếu key / giá trị lạ → default (giữ hành vi cũ).
    """
    try:
        from app import db

        strength = int(db.get_setting("denoise_strength", "100") or "100")
        mode = db.get_setting("denoise_mode", "stationary")
        highpass = float(db.get_setting("denoise_highpass", "0") or "0")
        hum_raw = db.get_setting("denoise_hum", "off") or "off"
        hum = float(hum_raw) if hum_raw not in ("", "off") else 0.0
        return DenoiseParams(
            prop_decrease=max(0.0, min(1.0, strength / 100.0)),
            stationary=(mode != "nonstationary"),
            highpass_hz=max(0.0, highpass),
            hum_hz=hum,
        )
    except Exception:  # noqa: BLE001 — cấu hình hỏng → mặc định an toàn
        return DenoiseParams()


def _prefilter(y: np.ndarray, sr: int, highpass_hz: float, hum_hz: float) -> np.ndarray:
    """High-pass cắt rumble + notch hum (kèm sóng hài). No-op khi tắt/quá Nyquist."""
    if not (highpass_hz or hum_hz):
        return y
    from scipy import signal

    nyq = sr / 2.0
    out = np.asarray(y, dtype=np.float64)
    if 0.0 < highpass_hz < nyq:
        sos = signal.butter(4, highpass_hz, btype="highpass", fs=sr, output="sos")
        out = signal.sosfilt(sos, out)
    if 0.0 < hum_hz < nyq:
        f = hum_hz
        while f < nyq:  # điện lưới + hài: 50/100/150… hoặc 60/120/180…
            b, a = signal.iirnotch(f, Q=30.0, fs=sr)
            out = signal.lfilter(b, a, out)
            f += hum_hz
    return out.astype(np.float32)


def _reduce(y: np.ndarray, sr: int, params: DenoiseParams) -> np.ndarray:
    import noisereduce

    filtered = _prefilter(y, sr, params.highpass_hz, params.hum_hz)
    cleaned = noisereduce.reduce_noise(
        y=filtered,
        sr=sr,
        stationary=params.stationary,
        prop_decrease=params.prop_decrease,
    )
    return np.asarray(cleaned, dtype=np.float32)


def _load_audio(path: Path, sr: int) -> np.ndarray:
    """Decode bất kỳ định dạng nào → float32 mono @ sr qua ffmpeg (whisper vốn cần)."""
    import subprocess

    cmd = [
        "ffmpeg", "-nostdin", "-threads", "0", "-i", str(path),
        "-f", "s16le", "-ac", "1", "-acodec", "pcm_s16le", "-ar", str(sr), "-",
    ]
    raw = subprocess.run(cmd, capture_output=True, check=True).stdout
    return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0


def _write_wav(path: Path, y: np.ndarray, sr: int) -> None:
    pcm16 = (np.clip(y, -1.0, 1.0) * 32767.0).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm16.tobytes())


def reduce_file(
    in_path: Path, params: DenoiseParams, *, sample_rate: int = SAMPLE_RATE
) -> Path | None:
    """Khử ồn file upload → WAV 16k tạm cạnh file gốc. Never-fail → None.

    None nghĩa: transcribe file gốc (ffmpeg thiếu / codec lạ / lỗi nội bộ).
    Caller chịu trách nhiệm xoá file trả về.
    """
    if not available():
        return None
    try:
        y = _load_audio(in_path, sample_rate)
        if y.size == 0:
            return None
        cleaned = _reduce(y, sample_rate, params)
        out_path = in_path.with_suffix(in_path.suffix + ".denoised.wav")
        _write_wav(out_path, cleaned, sample_rate)
        return out_path
    except Exception:  # noqa: BLE001 — khử ồn hỏng không được chặn transcribe
        return None


class StreamDenoiser:
    def __init__(
        self, sample_rate: int = SAMPLE_RATE, params: DenoiseParams | None = None
    ) -> None:
        self.sample_rate = sample_rate
        self.params = params or DenoiseParams()
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
        except Exception:  # noqa: BLE001
            out = raw
        self._tail = np.concatenate([self._tail, raw])[-self._tail_len :]
        return out

    def _denoise(self, raw: np.ndarray) -> np.ndarray:
        ctx = len(self._tail)
        padded = np.concatenate([self._tail, raw]) if ctx else raw
        cleaned = _reduce(padded, self.sample_rate, self.params)
        out = np.asarray(cleaned[ctx : ctx + len(raw)], dtype=np.float32)
        if out.shape != raw.shape:
            raise ValueError("denoised length mismatch")
        return out
