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
from collections.abc import Callable, Iterable
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
    segments: list[dict]  # [{start, end, text}] — cùng shape với segments của live


@dataclass(frozen=True)
class DecodeSpec:
    """Tham số decode cố định cho cả phiên live / cả file upload."""

    language: str
    glossary: str = ""
    model_override: str | None = None  # chỉ tier CPU dùng (user chọn size model)
    flag_words: bool = False  # final decode kèm (word, probability) để flag từ đáng ngờ


@dataclass(frozen=True)
class DecodeResult:
    text: str
    min_logprob: float = 0.0  # min avg_logprob trên các segment GIỮ LẠI; 0.0 nếu không có
    words: tuple[tuple[str, float], ...] = ()  # (word, probability), chỉ khi flag_words + final


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


def _is_token_loop(text: str) -> bool:
    """Hallucination lặp token ("J. J. J.", "ừ ừ ừ ừ"): cả segment chỉ là 1 từ
    ngắn lặp ≥3 lần. Câu thật không có dạng này — Whisper loop khi im lặng."""
    tokens = [t.strip(".,!?…-") .casefold() for t in text.split()]
    tokens = [t for t in tokens if t]
    if len(tokens) < 3:
        return False
    return len(set(tokens)) == 1 and len(tokens[0]) <= 4


def keep_segment(text: str, no_speech_prob: float, avg_logprob: float) -> bool:
    # Ngưỡng như logic nội bộ của Whisper: no_speech cao + logprob thấp = bịa.
    if no_speech_prob > 0.6 and avg_logprob < -1.0:
        return False
    if _is_token_loop(text):
        return False
    return not _HALLUCINATION_RE.search(text)


class Engine(ABC):
    info: EngineInfo
    # True khi revise() thật sự decode được bản tốt hơn. Live CHỈ reroute câu
    # low-confidence qua revision_q khi cờ này bật — engine không hỗ trợ mà vẫn
    # reroute thì chỉ tốn decode lock + trễ pass 2 (regression thực địa 2026-07-20).
    supports_revise: bool = False

    def decode(self, audio: np.ndarray, spec: DecodeSpec, *, final: bool) -> str:
        """Decode 1 utterance cho live. final=False có thể raise DecodeBusy."""
        return self.decode_scored(audio, spec, final=final).text

    def decode_scored(self, audio: np.ndarray, spec: DecodeSpec, *, final: bool) -> DecodeResult:
        """Như decode nhưng kèm điểm tin cậy. final=False có thể raise DecodeBusy."""
        if final:
            with _decode_lock:
                return self._decode(audio, spec, final=True)
        if not _decode_lock.acquire(blocking=False):
            raise DecodeBusy
        try:
            return self._decode(audio, spec, final=False)
        finally:
            _decode_lock.release()

    def revise(self, audio: np.ndarray, spec: DecodeSpec) -> str | None:
        """Re-decode nền với setting mạnh hơn. None = không hỗ trợ / đang bận."""
        return None

    @abstractmethod
    def _decode(self, audio: np.ndarray, spec: DecodeSpec, *, final: bool) -> DecodeResult: ...

    @abstractmethod
    def transcribe_file(
        self,
        path: Path,
        spec: DecodeSpec,
        on_progress: Callable[[str, float], None],
    ) -> FileResult: ...


def _mlx_scored(segments: list[dict], *, with_words: bool) -> DecodeResult:
    kept = [
        seg
        for seg in segments
        if keep_segment(seg["text"], seg.get("no_speech_prob", 0.0), seg.get("avg_logprob", 0.0))
    ]
    words: tuple[tuple[str, float], ...] = ()
    if with_words:
        words = tuple(
            (w["word"], float(w["probability"])) for seg in kept for w in seg.get("words", [])
        )
    return DecodeResult(
        "".join(seg["text"] for seg in kept).strip(),
        min((float(seg.get("avg_logprob", 0.0)) for seg in kept), default=0.0),
        words,
    )


def _fw_scored(segments: Iterable[object], *, with_words: bool) -> DecodeResult:
    # getattr thay vì attribute: faster_whisper không ship type stubs (Segment/Word).
    kept = [
        seg
        for seg in segments
        if keep_segment(
            str(getattr(seg, "text", "")),
            getattr(seg, "no_speech_prob", 0.0),
            getattr(seg, "avg_logprob", 0.0),
        )
    ]
    words: tuple[tuple[str, float], ...] = ()
    if with_words:
        words = tuple(
            (str(getattr(w, "word", "")), float(getattr(w, "probability", 0.0)))
            for seg in kept
            for w in (getattr(seg, "words", None) or [])
        )
    return DecodeResult(
        "".join(str(getattr(seg, "text", "")) for seg in kept).strip(),
        min((float(getattr(seg, "avg_logprob", 0.0)) for seg in kept), default=0.0),
        words,
    )


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

    def _transcribe(self, audio, spec: DecodeSpec, final: bool, *, word_timestamps: bool = False) -> dict:
        return self._mlx.transcribe(
            audio,
            path_or_hf_repo=self._repo,
            language=spec.language,
            condition_on_previous_text=False,
            initial_prompt=spec.glossary or None,
            # final: cho phép fallback nhiệt độ khi đoạn "khó"; partial: 1 lần cho nhanh.
            temperature=(0.0, 0.2, 0.4, 0.6, 0.8, 1.0) if final else 0.0,
            word_timestamps=word_timestamps,
            verbose=None,
        )

    def _decode(self, audio, spec, *, final):
        with_words = spec.flag_words and final
        result = self._transcribe(audio, spec, final, word_timestamps=with_words)
        return _mlx_scored(result["segments"], with_words=with_words)

    def revise(self, audio: np.ndarray, spec: DecodeSpec) -> str | None:
        # mlx-whisper 0.4.3 raise NotImplementedError với beam_size (chưa có beam
        # search) → except trả None; khi upstream bổ sung: bật supports_revise=True. temperature phải
        # 0.0: DecodingOptions cấm trộn beam + sampling.
        if not _decode_lock.acquire(blocking=False):
            return None
        try:
            result = self._mlx.transcribe(
                audio,
                path_or_hf_repo=self._repo,
                language=spec.language,
                condition_on_previous_text=False,
                initial_prompt=spec.glossary or None,
                temperature=0.0,
                beam_size=5,
                verbose=None,
            )
            return _mlx_scored(result["segments"], with_words=False).text
        except Exception:  # noqa: BLE001 — revise chạy nền, never-fail
            return None
        finally:
            _decode_lock.release()

    def transcribe_file(self, path, spec, on_progress):
        # Giữ lock suốt file: Metal không share được với decode live. Upload
        # dài sẽ chặn partial của phiên live đang mở — chấp nhận (turbo nhanh).
        with _decode_lock:
            result = self._transcribe(str(path), spec, final=True)
        segs = [
            {
                "start": round(float(seg["start"]), 2),
                "end": round(float(seg["end"]), 2),
                "text": seg["text"].strip(),
            }
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
        # cpu: revise bằng model upload to hơn; cuda: final đã turbo beam-5.
        self.supports_revise = device == "cpu"
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

    def _decode(self, audio, spec, *, final):
        model = self._get_model(spec.model_override or self.live_model)
        common = dict(
            language=spec.language,
            condition_on_previous_text=False,
            initial_prompt=spec.glossary or None,
            hotwords=spec.glossary or None,
        )
        with_words = spec.flag_words and final
        if final:
            segments, _ = model.transcribe(
                audio,
                beam_size=5,
                vad_filter=True,
                temperature=[0.0, 0.2, 0.4, 0.6, 0.8, 1.0],
                word_timestamps=with_words,
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
        return _fw_scored(segments, with_words=with_words)

    def revise(self, audio: np.ndarray, spec: DecodeSpec) -> str | None:
        if self.device != "cpu":
            return None  # cuda: final đã large-v3-turbo beam-5, không có nấc mạnh hơn
        try:
            # Lấy model upload (to hơn model live) TRƯỚC khi thử lock: lần load
            # đầu ~10s không được giữ lock chặn partial của phiên live.
            model = self._get_model(self.upload_model)
        except Exception:  # noqa: BLE001 — revise chạy nền, never-fail
            return None
        if not _decode_lock.acquire(blocking=False):
            return None
        try:
            segments, _ = model.transcribe(
                audio,
                language=spec.language,
                beam_size=5,
                vad_filter=True,
                temperature=[0.0, 0.2, 0.4, 0.6, 0.8, 1.0],
                condition_on_previous_text=False,
                initial_prompt=spec.glossary or None,
                hotwords=spec.glossary or None,
            )
            return _fw_scored(segments, with_words=False).text
        except Exception:  # noqa: BLE001 — revise chạy nền, never-fail
            return None
        finally:
            _decode_lock.release()

    def transcribe_file(self, path, spec, on_progress):
        # CPU/CUDA decode song song được với live → không cần giữ _decode_lock.
        model = self._get_model(spec.model_override or self.upload_model)
        segments, info = model.transcribe(
            str(path),
            language=spec.language,
            vad_filter=True,
            beam_size=5,
            initial_prompt=spec.glossary or None,
            hotwords=spec.glossary or None,
            condition_on_previous_text=False,
            temperature=[0.0, 0.2, 0.4, 0.6, 0.8, 1.0],
        )
        duration = float(getattr(info, "duration", 0) or 0)
        parts: list[str] = []
        segs: list[dict] = []
        for seg in segments:
            parts.append(seg.text)
            segs.append(
                {
                    "start": round(float(seg.start), 2),
                    "end": round(float(seg.end), 2),
                    "text": seg.text.strip(),
                }
            )
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
