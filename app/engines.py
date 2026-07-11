"""EngineRegistry: dò năng lực máy → chọn engine ASR tốt nhất (PRD FR-1).

Thứ tự probe: mlx (Mac Apple Silicon, GPU Metal) → cuda (GPU NVIDIA) → cpu.
Env `ASR_ENGINE=mlx|cuda|cpu` ép tier, bỏ qua probe. Chỗ dành sẵn cho tier
"native" (ANE — chờ Apple thêm vi_VN vào SpeechTranscriber, probe bằng
`native/bin/native-asr`, xem BRD mục 4) và tier "remote" (Đợt 3).

Mọi decode đi qua một lock toàn cục: Metal (mlx) không chịu được decode song
song từ nhiều thread; final chờ lock, partial bận thì raise DecodeBusy để
phiên live bỏ tick đó thay vì xếp hàng dồn trễ.
"""
from __future__ import annotations

import os
import platform
import re
import sys
import threading
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np


@dataclass(frozen=True)
class EngineInfo:
    tier: str  # "mlx" | "cuda" | "cpu"
    model_name: str  # tên hiển thị / ghi vào metadata transcript


@dataclass
class FileResult:
    text: str
    duration: float
    segments: list[dict]  # [{start, text}] — cùng shape với segments của live


class DecodeBusy(Exception):
    """Partial decode bị bỏ vì engine đang bận phiên khác."""


_decode_lock = threading.Lock()

# Whisper train trên phụ đề YouTube → gặp im lặng/noise hay "đoán bừa" ra mấy
# câu outro quen thuộc. Dùng chung cho mọi backend (live tắt vad_filter nên dễ dính).
_HALLUCINATION_RE = re.compile(
    r"ghiền mì gõ|subscribe|đăng ký kênh|like và chia sẻ"
    r"|cảm ơn các bạn đã (xem|theo dõi|lắng nghe)|hẹn gặp lại các bạn"
    r"|thanks for watching|please like and",
    re.IGNORECASE,
)


def keep_segment(text: str, no_speech_prob: float, avg_logprob: float) -> bool:
    # Ngưỡng như logic nội bộ của Whisper: no_speech cao + logprob thấp = bịa.
    if no_speech_prob > 0.6 and avg_logprob < -1.0:
        return False
    return not _HALLUCINATION_RE.search(text)


class Engine(ABC):
    info: EngineInfo

    def decode(
        self,
        audio: np.ndarray,
        *,
        language: str,
        glossary: str,
        final: bool,
        model_override: str | None = None,
    ) -> str:
        """Decode 1 utterance cho live. final=False có thể raise DecodeBusy."""
        if final:
            with _decode_lock:
                return self._decode(audio, language, glossary, True, model_override)
        if not _decode_lock.acquire(blocking=False):
            raise DecodeBusy
        try:
            return self._decode(audio, language, glossary, False, model_override)
        finally:
            _decode_lock.release()

    @abstractmethod
    def _decode(
        self, audio: np.ndarray, language: str, glossary: str, final: bool,
        model_override: str | None,
    ) -> str: ...

    @abstractmethod
    def transcribe_file(
        self,
        path: Path,
        *,
        language: str,
        glossary: str,
        on_progress: Callable[[str, float], None],
        model_override: str | None = None,
    ) -> FileResult: ...


class MlxEngine(Engine):
    """mlx-whisper trên GPU Metal — large-v3-turbo 8.4x realtime trên M1,
    thuật ngữ Anh xen tiếng Việt chính xác hơn mọi lựa chọn CPU (benchmark
    2026-07-03). Không có beam search / hotwords; utterance ≤28s = 1 cửa sổ
    decode nên initial_prompt phủ đủ glossary. Bỏ qua model_override."""

    def __init__(self):
        import mlx_whisper

        self._mlx = mlx_whisper
        self._repo = os.environ.get("LIVE_MLX_MODEL", "mlx-community/whisper-large-v3-turbo")
        self.info = EngineInfo("mlx", f"{self._repo.rsplit('/', 1)[-1]} (mlx)")

    def _transcribe(self, audio, language: str, glossary: str, final: bool) -> dict:
        return self._mlx.transcribe(
            audio,
            path_or_hf_repo=self._repo,
            language=language,
            condition_on_previous_text=False,
            initial_prompt=glossary or None,
            # final: cho phép fallback nhiệt độ khi đoạn "khó"; partial: 1 lần cho nhanh.
            temperature=(0.0, 0.2, 0.4, 0.6, 0.8, 1.0) if final else 0.0,
            verbose=None,
        )

    def _decode(self, audio, language, glossary, final, model_override):
        result = self._transcribe(audio, language, glossary, final)
        return "".join(
            seg["text"]
            for seg in result["segments"]
            if keep_segment(seg["text"], seg.get("no_speech_prob", 0.0), seg.get("avg_logprob", 0.0))
        ).strip()

    def transcribe_file(self, path, *, language, glossary, on_progress, model_override=None):
        # Giữ lock suốt file: Metal không share được với decode live. Upload
        # dài sẽ chặn partial của phiên live đang mở — chấp nhận (turbo nhanh).
        with _decode_lock:
            result = self._transcribe(str(path), language, glossary, final=True)
        segs = [
            {"start": round(float(seg["start"]), 2), "text": seg["text"].strip()}
            for seg in result["segments"]
            if keep_segment(seg["text"], seg.get("no_speech_prob", 0.0), seg.get("avg_logprob", 0.0))
        ]
        text = " ".join(s["text"] for s in segs).strip()
        # mlx không trả duration file — lấy mốc cuối segment (đủ cho metadata).
        duration = float(result["segments"][-1]["end"]) if result["segments"] else 0.0
        on_progress(text, 1.0)
        return FileResult(text, duration, segs)


class FwEngine(Engine):
    """faster-whisper trên CUDA (float16) hoặc CPU (int8)."""

    def __init__(self, device: str, compute_type: str):
        self.device = device
        self.compute_type = compute_type
        self._models: dict[str, object] = {}
        self._lock = threading.Lock()
        if device == "cpu":
            self.live_model, upload_default = cpu_model_sizes()
        else:
            self.live_model, upload_default = "large-v3-turbo", "large-v3-turbo"
        self.upload_model = os.environ.get("WHISPER_MODEL", upload_default)
        self.info = EngineInfo(device, f"{self.live_model} (faster-whisper {device})")

    def _get_model(self, name: str):
        with self._lock:
            model = self._models.get(name)
            if model is None:
                from faster_whisper import WhisperModel

                model = WhisperModel(name, device=self.device, compute_type=self.compute_type)
                self._models[name] = model
            return model

    def _decode(self, audio, language, glossary, final, model_override):
        model = self._get_model(model_override or self.live_model)
        common = dict(
            language=language,
            condition_on_previous_text=False,
            initial_prompt=glossary or None,
            hotwords=glossary or None,
        )
        if final:
            segments, _ = model.transcribe(
                audio,
                beam_size=5,
                vad_filter=True,
                temperature=[0.0, 0.2, 0.4, 0.6, 0.8, 1.0],
                **common,
            )
        else:
            segments, _ = model.transcribe(
                audio,
                beam_size=1,
                temperature=0.0,
                vad_filter=False,  # buffer đã được gate; vad_filter còn cắt mất nửa từ cuối
                without_timestamps=True,
                **common,
            )
        return "".join(
            seg.text
            for seg in segments
            if keep_segment(seg.text, getattr(seg, "no_speech_prob", 0.0), getattr(seg, "avg_logprob", 0.0))
        ).strip()

    def transcribe_file(self, path, *, language, glossary, on_progress, model_override=None):
        # CPU/CUDA decode song song được với live → không cần giữ _decode_lock.
        model = self._get_model(model_override or self.upload_model)
        segments, info = model.transcribe(
            str(path),
            language=language,
            vad_filter=True,
            beam_size=5,
            initial_prompt=glossary or None,
            hotwords=glossary or None,
            condition_on_previous_text=False,
            temperature=[0.0, 0.2, 0.4, 0.6, 0.8, 1.0],
        )
        duration = float(getattr(info, "duration", 0) or 0)
        parts: list[str] = []
        segs: list[dict] = []
        for seg in segments:
            parts.append(seg.text)
            segs.append({"start": round(float(seg.start), 2), "text": seg.text.strip()})
            progress = min(seg.end / duration, 0.99) if duration else 0.0
            on_progress("".join(parts).strip(), progress)
        return FileResult("".join(parts).strip(), duration, segs)


def _ram_gb() -> float:
    try:
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 2**30
    except (ValueError, OSError, AttributeError):
        return 8.0


def cpu_model_sizes() -> tuple[str, str]:
    """(model live, model upload) cho tier CPU theo RAM/core — ngưỡng bảo thủ
    vì live phải theo kịp realtime."""
    ram, cores = _ram_gb(), os.cpu_count() or 4
    if ram >= 16 and cores >= 10:
        return "medium", "large-v3-turbo"
    if ram >= 8:
        return "small", "medium"
    return "small", "small"


_engine: Engine | None = None
_engine_lock = threading.Lock()


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        with _engine_lock:
            if _engine is None:
                _engine = _probe()
    return _engine


def _probe() -> Engine:
    forced = os.environ.get("ASR_ENGINE", "").strip().lower()
    if forced == "mlx":
        return MlxEngine()
    if forced == "cuda":
        return FwEngine("cuda", "float16")
    if forced == "cpu":
        return FwEngine("cpu", "int8")

    if sys.platform == "darwin" and platform.machine() == "arm64":
        try:
            return MlxEngine()
        except ImportError:
            pass
    # (Đợt 3) tier native ANE: chạy `native/bin/native-asr` để kiểm tra
    # SpeechTranscriber đã hỗ trợ vi_VN chưa — hiện chưa (benchmark 2026-07-08).
    try:
        import ctranslate2

        if ctranslate2.get_cuda_device_count() > 0:
            return FwEngine("cuda", "float16")
    except Exception:  # noqa: BLE001 — thiếu lib/driver → coi như không có CUDA
        pass
    return FwEngine("cpu", "int8")
