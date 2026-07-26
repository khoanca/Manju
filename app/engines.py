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
    segments: list[dict]  # [{start, end, text, words?}] — cùng shape với segments của live
    # words (optional, chỉ khi spec.flag_words): [{"w": <từ>, "p": <probability>}] để flag từ khả nghi


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
    # Telemetry (US-828) — max trên TẤT CẢ segment (kể cả bị drop): truy phiên
    # lỗi sau này không cần tái hiện. temperature = nấc cao nhất whisper đã dùng.
    max_compression_ratio: float = 0.0
    max_no_speech_prob: float = 0.0
    temperature: float = 0.0


class DecodeBusy(Exception):
    """Partial decode bị bỏ vì engine đang bận phiên khác."""


_decode_lock = threading.Lock()

# Whisper train trên phụ đề YouTube → gặp im lặng/noise hay "đoán bừa" ra mấy
# câu outro quen thuộc. Dùng chung cho mọi backend (live tắt vad_filter nên dễ dính).
_HALLUCINATION_RE = re.compile(
    r"ghiền mì gõ|subscribe|đăng k[íý] .{0,25}kênh|hãy đăng k[íý]|like và chia sẻ"
    r"|cảm ơn các bạn đã (xem|theo dõi|lắng nghe)|hẹn gặp lại các bạn"
    r"|thanks for watching|please like and"
    # Nửa sau outro "hãy đăng ký kênh ĐỂ KHÔNG BỎ LỠ NHỮNG VIDEO HẤP DẪN" hay
    # xuất hiện MỘT MÌNH (T-013 2026-07-26: lọt cả 3 lần trong DB, 0 câu thật).
    r"|không bỏ lỡ những video|video hấp dẫn",
    re.IGNORECASE,
)

# Bản regex hallucination TRƯỚC ffe38d2 — chỉ dùng cho công cụ so sánh A/B
# (app.reanalyze) để tái dựng đúng hành vi "bản cũ". KHÔNG dùng ở đường ống live.
_HALLUCINATION_RE_LEGACY = re.compile(
    r"ghiền mì gõ|subscribe|đăng ký kênh|like và chia sẻ"
    r"|cảm ơn các bạn đã (xem|theo dõi|lắng nghe)|hẹn gặp lại các bạn"
    r"|thanks for watching|please like and",
    re.IGNORECASE,
)


# Lặp thật trong hội thoại tối đa vài lần ("không không không được đâu"); loop
# thoái hoá của Whisper lặp hàng chục–hàng trăm lần. Ngưỡng đặt cao để chỉ cắt
# cái sau (thực địa 2026-07-20: "là em bán" + 224 lần "để").
_LOOP_RUN_MIN = 6
# Segment logprob dưới _SUSPECT_LOGPROB: run ×3 đã là loop — đo 2026-07-26 trên
# 34 phiên: "đăng"×3 lp −0.57, "em môm cài"×3 lp −0.63 đều bịa; mọi stutter thật
# ("nó nó nó", "vâng, vâng, vâng") đều lp tốt hơn → 0 false positive.
_LOOP_RUN_MIN_SUSPECT = 3
_SUSPECT_LOGPROB = -0.5
_LOOP_TOKEN_MAXLEN = 4
# Loop chu kỳ ≥2 token ("tick là tick là …", "sao mà sao mà …" — thực địa
# live-1620): không thể là nhấn mạnh thật khi lặp nhiều lần. Period 2 giữ ≥4
# ("chuyện gì"×3, "bỏ ra"×3 là nói thật); period ≥3 hạ xuống ≥3 — kiểm 0 false
# positive trên toàn bộ transcript 2026-07-26 ("em môm cài"×3 là bịa).
_CYCLE_MAX_PERIOD = 5
_CYCLE_MIN_REPEAT = 4
_CYCLE_MIN_REPEAT_LONG = 3
# Whisper tự dùng cr>2.4 để retry nhiệt độ; segment cuối cùng vẫn cr cao là rác
# chắc chắn (đo: degenerate 4.9–40.6 vs câu sạch ≤1.7). mlx trả sẵn per-window.
_COMPRESSION_RATIO_MAX = 2.4
# Lặp bên trong 1 token, không có khoảng trắng ("Hbrightbrightbright…" —
# live-1653): đơn vị 2..12 ký tự lặp ≥4 lần → thu về 1. Câu thật không có dạng
# này (không từ tiếng Việt/Anh nào là 1 khối lặp liền ≥4 lần).
_INTRA_LOOP_RE = re.compile(r"(.{2,12}?)\1{3,}")


def _norm_tokens(text: str) -> list[str]:
    return [t for t in (w.strip(".,!?…-").casefold() for w in text.split()) if t]


def _norm_aligned(words: list[str]) -> list[str]:
    """Như _norm_tokens nhưng GIỮ chỉ số 1:1 với words (token rỗng → "")."""
    return [w.strip(".,!?…-").casefold() for w in words]


def _is_token_loop(text: str) -> bool:
    """Hallucination lặp token ("J. J. J.", "ừ ừ ừ ừ"): cả segment chỉ là 1 từ
    ngắn lặp ≥3 lần. Câu thật không có dạng này — Whisper loop khi im lặng.
    Cố ý KHÔNG nới sang "token áp đảo ≥90%": segment kiểu "để ×10 + bán" phải
    collapse giữ "bán" chứ không drop; loop có token đuôi lệch ("Hải, "×74+"H")
    đã bị gate compression_ratio trong keep_segment bắt."""
    tokens = _norm_tokens(text)
    if len(tokens) < 3:
        return False
    return len(set(tokens)) == 1 and len(tokens[0]) <= _LOOP_TOKEN_MAXLEN


def _is_digit_loop(text: str) -> bool:
    """Whisper thoái hoá thành chuỗi đếm khi nghe không rõ đoạn CÓ số thật:
    "là 1 lane, 4 lane" → "là 4 1 1 2 2 3 4 5 5 6 6 7 7" (thực địa 2026-07-20).
    Segment gần như toàn chữ số → không còn nội dung cứu được, drop cả segment.
    Câu thật có số ("nằm trong 400 tải thôi") tỉ lệ số thấp nên không dính."""
    tokens = _norm_tokens(text)
    if len(tokens) < 8:
        return False
    digits = sum(t.isdigit() for t in tokens)
    return digits >= 6 and digits / len(tokens) > 0.7


def _cycle_run(norm: list[str], i: int, min_run: int = _LOOP_RUN_MIN) -> tuple[int, int]:
    """Tại vị trí i, tìm chu kỳ ngắn nhất lặp thoái hoá. Trả (period, repeats);
    period=0 nghĩa là không có loop đáng cắt. Period nhỏ nhất thắng."""
    n = len(norm)
    for p in range(1, min(_CYCLE_MAX_PERIOD, (n - i) // 2) + 1):
        r = 1
        while i + (r + 1) * p <= n and norm[i + r * p : i + (r + 1) * p] == norm[i : i + p]:
            r += 1
        if r < 2:
            continue
        # period 1: chỉ cắt token NGẮN lặp ≥min_run (mặc định 6 — khỏi cắt nhấn
        # mạnh thật; segment logprob thấp hạ xuống 3 qua _collapse_suspect).
        if p == 1 and (r < min_run or len(norm[i]) > _LOOP_TOKEN_MAXLEN):
            continue
        if p == 2 and r < _CYCLE_MIN_REPEAT:
            continue
        if p >= 3 and r < _CYCLE_MIN_REPEAT_LONG:
            continue
        return p, r
    return 0, 0


def collapse_loops(text: str, min_run: int = _LOOP_RUN_MIN) -> str:
    """Cắt lặp thoái hoá, GIỮ phần câu thật xung quanh.

    Ba dạng loop của Whisper khi gặp noise/im lặng: token đơn lặp ("để để để…"),
    chu kỳ ≥2 token ("tick là tick là…"), và lặp bên trong 1 token dính liền
    ("Hbrightbright…"). Mỗi dạng thu về 1 lần thay vì drop cả segment (còn nội
    dung thật đứng trước/sau). `min_run` là ngưỡng run period-1 (hạ khi segment
    đáng ngờ — xem _collapse_suspect).
    """
    # Bước 1: lặp trong-token (không có space) — xử trước khi tách từ.
    intra = _INTRA_LOOP_RE.sub(r"\1", text)
    words = intra.split()
    norm = _norm_aligned(words)
    out: list[str] = []
    collapsed = intra != text
    i = 0
    while i < len(words):
        period, repeats = _cycle_run(norm, i, min_run)
        if period:
            out.extend(words[i : i + period])  # giữ đúng 1 chu kỳ
            i += period * repeats
            collapsed = True
        else:
            out.append(words[i])
            i += 1
    if not collapsed:
        return text  # không có loop → trả nguyên văn, không đụng khoảng trắng
    # mlx nối segment bằng "".join → phải giữ khoảng trắng đầu/cuối của segment.
    lead = text[: len(text) - len(text.lstrip())]
    trail = text[len(text.rstrip()) :]
    return f"{lead}{' '.join(out)}{trail}"


def is_hallucination_phrase(text: str) -> bool:
    """Câu chứa cụm outro YouTube Whisper hay bịa khi noise/im lặng (đăng ký
    kênh, thanks for watching…). Dùng cho cả rà hậu kỳ toàn văn (app.cleanup)."""
    return bool(_HALLUCINATION_RE.search(text))


def strip_hallucination_phrases(text: str) -> tuple[str, list[str]]:
    """Cắt ĐÚNG cụm outro YouTube khỏi văn bản (không đụng nội dung quanh nó —
    transcript hầu như không có dấu câu nên cắt theo câu sẽ mất đoạn thật). Trả
    (văn bản đã cắt, danh sách cụm đã cắt). Khoảng trắng dư sau khi cắt được thu gọn."""
    removed = [m.group(0) for m in _HALLUCINATION_RE.finditer(text)]
    if not removed:
        return text, []
    stripped = _HALLUCINATION_RE.sub(" ", text)
    return re.sub(r"\s{2,}", " ", stripped).strip(), removed


def keep_segment(
    text: str, no_speech_prob: float, avg_logprob: float, compression_ratio: float = 0.0
) -> bool:
    # cr>2.4 là chính ngưỡng Whisper dùng để retry nhiệt độ — kết quả CUỐI vẫn
    # cr cao nghĩa là mọi nấc đều degenerate (mlx chấp nhận vô điều kiện nấc
    # chót, đo 2026-07-26: rác 4.9–40.6 vs câu sạch ≤1.7). Default 0.0 giữ
    # tương thích caller cũ chưa có cr.
    if compression_ratio > _COMPRESSION_RATIO_MAX:
        return False
    # Ngưỡng như logic nội bộ của Whisper: no_speech cao + logprob thấp = bịa.
    # Lưu ý: điều kiện AND này KHÔNG bắt được hallucination "tự tin"
    # (nsp=0.000, logprob=-0.98) — nên phải chặn thêm theo hình dạng text.
    if no_speech_prob > 0.6 and avg_logprob < -1.0:
        return False
    if _is_token_loop(text) or _is_digit_loop(text):
        return False
    return not _HALLUCINATION_RE.search(text)


def _collapse_suspect(text: str, avg_logprob: float) -> str:
    """Segment logprob thấp: run ×3 token ngắn đã là loop ("đăng đăng đăng"
    lp −0.57 là bịa; stutter thật đều lp tốt hơn −0.5 — đo 2026-07-26)."""
    if avg_logprob < _SUSPECT_LOGPROB:
        return collapse_loops(text, min_run=_LOOP_RUN_MIN_SUSPECT)
    return text


# ── Hành vi ASR-cleanup "bản cũ" (trước ffe38d2) — chỉ cho công cụ so sánh A/B ──
def keep_segment_legacy(text: str, no_speech_prob: float, avg_logprob: float) -> bool:
    """Như keep_segment nhưng dùng regex hallucination gốc (hẹp hơn). Khác biệt
    duy nhất so với bản mới nằm ở regex — token/digit-loop không đổi."""
    if no_speech_prob > 0.6 and avg_logprob < -1.0:
        return False
    if _is_token_loop(text) or _is_digit_loop(text):
        return False
    return not _HALLUCINATION_RE_LEGACY.search(text)


def collapse_loops_legacy(text: str) -> str:
    """collapse_loops TRƯỚC ffe38d2: chỉ thu run token-đơn ≥_LOOP_RUN_MIN về 1
    (không bắt chu kỳ ≥2 token, không bắt lặp trong-token). Giữ nguyên để so A/B."""
    words = text.split()
    norm = _norm_tokens(text)
    if len(words) != len(norm):  # token rỗng sau khi strip → giữ nguyên, không đoán
        return text
    out: list[str] = []
    collapsed = False
    i = 0
    while i < len(words):
        j = i + 1
        while j < len(words) and norm[j] == norm[i]:
            j += 1
        run = j - i
        if run >= _LOOP_RUN_MIN and len(norm[i]) <= _LOOP_TOKEN_MAXLEN:
            out.append(words[i])
            collapsed = True
        else:
            out.extend(words[i:j])
        i = j
    if not collapsed:
        return text
    lead = text[: len(text) - len(text.lstrip())]
    trail = text[len(text.rstrip()) :]
    return f"{lead}{' '.join(out)}{trail}"


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

    @abstractmethod
    def raw_file_segments(self, path: Path, spec: DecodeSpec) -> list[dict]:
        """Giải mã file → segment THÔ, CHƯA qua keep_segment/collapse_loops.
        Mỗi phần tử: {text, no_speech_prob, avg_logprob}. Dùng cho công cụ so
        sánh A/B (app.reanalyze) để chạy cả hai bộ hậu-xử-lý trên cùng 1 decode."""
        ...


def _clean_utterance(texts: list[str]) -> str:
    """Nối các segment nội bộ của 1 utterance rồi gom lặp TRÊN CHUỖI ĐÃ NỐI.
    keep_segment/collapse_loops soi từng segment không thấy loop vắt qua ranh
    giới 2 segment ("NYE NYE" | "NYE NYE" → 4×NYE, live-2341): mỗi segment chỉ 2
    token, dưới ngưỡng; chỉ lộ khi nối. Cả utterance chỉ là token/digit-loop →
    trả rỗng (rác im lặng, live xoá dòng)."""
    joined = "".join(texts)
    if _is_token_loop(joined) or _is_digit_loop(joined):
        return ""
    return collapse_loops(joined).strip()


def _mlx_scored(segments: list[dict], *, with_words: bool) -> DecodeResult:
    kept = [
        seg
        for seg in segments
        if keep_segment(
            seg["text"],
            seg.get("no_speech_prob", 0.0),
            seg.get("avg_logprob", 0.0),
            seg.get("compression_ratio", 0.0),
        )
    ]
    text = _clean_utterance(
        [_collapse_suspect(seg["text"], seg.get("avg_logprob", 0.0)) for seg in kept]
    )
    words: tuple[tuple[str, float], ...] = ()
    if with_words and text:
        words = tuple(
            (w["word"], float(w["probability"])) for seg in kept for w in seg.get("words", [])
        )
    return DecodeResult(
        text,
        min((float(seg.get("avg_logprob", 0.0)) for seg in kept), default=0.0),
        words,
        max_compression_ratio=max(
            (float(seg.get("compression_ratio", 0.0)) for seg in segments), default=0.0
        ),
        max_no_speech_prob=max(
            (float(seg.get("no_speech_prob", 0.0)) for seg in segments), default=0.0
        ),
        temperature=max((float(seg.get("temperature", 0.0)) for seg in segments), default=0.0),
    )


def _fw_scored(segments: Iterable[object], *, with_words: bool) -> DecodeResult:
    # getattr thay vì attribute: faster_whisper không ship type stubs (Segment/Word).
    segs = list(segments)  # materialize: cần duyệt 2 lần (kept + telemetry)
    kept = [
        seg
        for seg in segs
        if keep_segment(
            str(getattr(seg, "text", "")),
            getattr(seg, "no_speech_prob", 0.0),
            getattr(seg, "avg_logprob", 0.0),
            getattr(seg, "compression_ratio", 0.0),
        )
    ]
    text = _clean_utterance(
        [
            _collapse_suspect(str(getattr(seg, "text", "")), getattr(seg, "avg_logprob", 0.0))
            for seg in kept
        ]
    )
    words: tuple[tuple[str, float], ...] = ()
    if with_words and text:
        words = tuple(
            (str(getattr(w, "word", "")), float(getattr(w, "probability", 0.0)))
            for seg in kept
            for w in (getattr(seg, "words", None) or [])
        )
    return DecodeResult(
        text,
        min((float(getattr(seg, "avg_logprob", 0.0)) for seg in kept), default=0.0),
        words,
        max_compression_ratio=max(
            (float(getattr(seg, "compression_ratio", 0.0)) for seg in segs), default=0.0
        ),
        max_no_speech_prob=max(
            (float(getattr(seg, "no_speech_prob", 0.0)) for seg in segs), default=0.0
        ),
        temperature=max((float(getattr(seg, "temperature", 0.0)) for seg in segs), default=0.0),
    )


def _mlx_seg_words(seg: dict) -> list[dict]:
    """Word-level confidence của 1 segment mlx → shape contract {"w","p"} (upload)."""
    return [
        {"w": w["word"], "p": round(float(w["probability"]), 3)}
        for w in seg.get("words", [])
    ]


def _fw_seg_words(seg: object) -> list[dict]:
    # faster_whisper không ship type stubs (Word) → getattr như _fw_scored.
    return [
        {"w": str(getattr(w, "word", "")), "p": round(float(getattr(w, "probability", 0.0)), 3)}
        for w in (getattr(seg, "words", None) or [])
    ]


class MlxEngine(Engine):
    """mlx-whisper trên GPU Metal — large-v3-turbo 8.4x realtime trên M1,
    thuật ngữ Anh xen tiếng Việt chính xác hơn mọi lựa chọn CPU (benchmark
    2026-07-03). Không có beam search / hotwords; utterance ≤28s = 1 cửa sổ
    decode nên initial_prompt phủ đủ glossary. Bỏ qua model_override."""

    def __init__(self):
        import mlx_whisper

        self._mlx = mlx_whisper
        self._repo = os.environ.get("LIVE_MLX_MODEL", "mlx-community/whisper-large-v3-turbo")
        # Upload/reanalyze KHÔNG realtime → dùng large-v3 full (32 lớp decoder,
        # chính xác hơn turbo trên audio khó + code-switching Việt-Anh); chậm hơn
        # ~2-3x nhưng không có ràng buộc thời gian thực. Live vẫn giữ turbo.
        self._upload_repo = os.environ.get("UPLOAD_MLX_MODEL", "mlx-community/whisper-large-v3-mlx")
        # US-811: revise nền câu live confidence thấp bằng large-v3 full. MẶC ĐỊNH
        # TẮT — thực địa (2026-07-25) revise MLX phá live: (1) nạp thêm model 3GB
        # thứ 2 + giữ chung _decode_lock ~4-5s/lần → _finalize (blocking lock) khựng,
        # partial rơi DecodeBusy → phụ đề đứng hình; (2) temperature fallback tới 1.0
        # sampling ra rác ("ừ ừ") rồi GHI ĐÈ câu final tốt. Chỉ bật khi mlx-whisper
        # có beam (yield lock rẻ) hoặc chấp nhận đánh đổi: MANJU_MLX_REVISE=1.
        self.supports_revise = os.environ.get("MANJU_MLX_REVISE", "0") == "1"
        self.info = EngineInfo("mlx", f"{self._repo.rsplit('/', 1)[-1]} (mlx)")

    def _transcribe(
        self,
        audio,
        spec: DecodeSpec,
        final: bool,
        *,
        word_timestamps: bool = False,
        repo: str | None = None,
        temps: tuple[float, ...] | float | None = None,
    ) -> dict:
        return self._mlx.transcribe(
            audio,
            path_or_hf_repo=repo or self._repo,
            language=spec.language,
            condition_on_previous_text=False,
            initial_prompt=spec.glossary or None,
            # final: cho phép fallback nhiệt độ khi đoạn "khó"; partial: 1 lần cho nhanh.
            temperature=temps if temps is not None else ((0.0, 0.2, 0.4, 0.6, 0.8, 1.0) if final else 0.0),
            word_timestamps=word_timestamps,
            verbose=None,
        )

    # Live final: KHÔNG leo thang tới T≥0.4 — đo 2026-07-26: mọi nấc cao chỉ
    # sinh rác sampling ("y plane"×71, glossary echo) mà mlx chấp nhận vô điều
    # kiện, và thang đủ 6 nấc giữ _decode_lock 18.5–25s ngay lúc Stop. Nấc
    # (0.0, 0.2) + accept-or-drop: fail gate thì bỏ utterance (im lặng/rác),
    # KHÔNG cố decode bằng được. Upload/reanalyze vẫn thang đủ (không realtime).
    _LIVE_FINAL_TEMPS = (0.0, 0.2)

    def _decode(self, audio, spec, *, final):
        with_words = spec.flag_words and final
        result = self._transcribe(
            audio, spec, final, word_timestamps=with_words,
            temps=self._LIVE_FINAL_TEMPS if final else None,
        )
        scored = _mlx_scored(result["segments"], with_words=with_words)
        if final and scored.text and scored.min_logprob < -1.0:
            # accept-or-drop: lp<-1.0 là decode fail theo chính chuẩn whisper —
            # thang cụt (0.0, 0.2) không cứu được thì thà rỗng còn hơn bịa.
            return DecodeResult(
                "",
                max_compression_ratio=scored.max_compression_ratio,
                max_no_speech_prob=scored.max_no_speech_prob,
                temperature=scored.temperature,
            )
        return scored

    def revise(self, audio: np.ndarray, spec: DecodeSpec) -> str | None:
        # Re-decode câu live confidence thấp bằng large-v3 full (nhiều lớp decoder
        # hơn turbo → thuật ngữ/câu khó chính xác hơn) + temperature fallback cho
        # đoạn khó. KHÔNG beam (mlx-whisper 0.4.3 raise NotImplementedError). Lock
        # non-blocking: engine bận vì live thì bỏ, để live luôn thắng lock.
        if not _decode_lock.acquire(blocking=False):
            return None
        try:
            result = self._mlx.transcribe(
                audio,
                path_or_hf_repo=self._upload_repo,
                language=spec.language,
                condition_on_previous_text=False,
                initial_prompt=spec.glossary or None,
                temperature=(0.0, 0.2, 0.4, 0.6, 0.8, 1.0),
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
            result = self._transcribe(
                str(path), spec, final=True, word_timestamps=spec.flag_words, repo=self._upload_repo
            )
        segs: list[dict] = []
        for seg in result["segments"]:
            if not keep_segment(
                seg["text"],
                seg.get("no_speech_prob", 0.0),
                seg.get("avg_logprob", 0.0),
                seg.get("compression_ratio", 0.0),
            ):
                continue
            s: dict = {
                "start": round(float(seg["start"]), 2),
                "end": round(float(seg["end"]), 2),
                "text": collapse_loops(seg["text"]).strip(),
            }
            if spec.flag_words:
                s["words"] = _mlx_seg_words(seg)
            segs.append(s)
        text = " ".join(str(s["text"]) for s in segs).strip()
        # mlx không trả duration file — lấy mốc cuối segment (đủ cho metadata).
        duration = float(result["segments"][-1]["end"]) if result["segments"] else 0.0
        on_progress(text, 1.0)
        return FileResult(text, duration, segs)

    def raw_file_segments(self, path, spec):
        with _decode_lock:
            result = self._transcribe(str(path), spec, final=True, word_timestamps=False, repo=self._upload_repo)
        return [
            {
                "text": str(seg["text"]),
                "no_speech_prob": float(seg.get("no_speech_prob", 0.0)),
                "avg_logprob": float(seg.get("avg_logprob", 0.0)),
                "compression_ratio": float(seg.get("compression_ratio", 0.0)),
                "temperature": float(seg.get("temperature", 0.0)),
            }
            for seg in result["segments"]
        ]


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
            word_timestamps=spec.flag_words,
        )
        duration = float(getattr(info, "duration", 0) or 0)
        parts: list[str] = []
        segs: list[dict] = []
        for seg in segments:
            parts.append(seg.text)
            s: dict = {
                "start": round(float(seg.start), 2),
                "end": round(float(seg.end), 2),
                "text": seg.text.strip(),
            }
            if spec.flag_words:
                s["words"] = _fw_seg_words(seg)
            segs.append(s)
            progress = min(seg.end / duration, 0.99) if duration else 0.0
            on_progress("".join(parts).strip(), progress)
        return FileResult("".join(parts).strip(), duration, segs)

    def raw_file_segments(self, path, spec):
        model = self._get_model(spec.model_override or self.upload_model)
        segments, _info = model.transcribe(
            str(path),
            language=spec.language,
            vad_filter=True,
            beam_size=5,
            initial_prompt=spec.glossary or None,
            hotwords=spec.glossary or None,
            condition_on_previous_text=False,
            temperature=[0.0, 0.2, 0.4, 0.6, 0.8, 1.0],
        )
        return [
            {
                "text": str(getattr(seg, "text", "")),
                "no_speech_prob": float(getattr(seg, "no_speech_prob", 0.0)),
                "avg_logprob": float(getattr(seg, "avg_logprob", 0.0)),
                "compression_ratio": float(getattr(seg, "compression_ratio", 0.0)),
                "temperature": float(getattr(seg, "temperature", 0.0)),
            }
            for seg in segments
        ]


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
