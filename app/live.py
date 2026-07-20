"""Live mode: nhận PCM 16kHz từ mic qua WebSocket → subtitle theo thời gian thực.

faster-whisper không hỗ trợ streaming thật, nên mỗi phiên giữ 1 buffer theo
utterance và re-transcribe định kỳ: `partial` (greedy, nhanh) trong lúc đang
nói; khi Silero VAD thấy lặng đủ lâu thì decode `final` (beam search) rồi đưa
câu qua pass 2 (Ollama) → gửi `corrected` thay thế. Mỗi WebSocket = 1 phiên;
kết thúc thì lưu transcript vào data/transcripts/ như flow upload.
"""
from __future__ import annotations

import asyncio
import json
import os
import queue
import threading
import time
import uuid
from collections import deque
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
from fastapi import WebSocket
from faster_whisper.vad import VadOptions, get_speech_timestamps

from app import corrections, db, denoise, diarize, engines, transcribe
from app.correct import LlmOpts, correct_sentence, openrouter_enabled, summarize_topic

SAMPLE_RATE = 16000
TICK_S = 1.2  # nhịp vòng decode
MIN_NEW_AUDIO_S = 0.8  # có ít nhất chừng này audio mới thì mới decode partial
ENDPOINT_SILENCE_S = 0.7  # lặng liên tục chừng này → hết câu
VAD_WINDOW_S = 1.5  # chỉ chạy VAD trên đuôi buffer chừng này
PREROLL_S = 0.32  # giữ lại chút audio trước điểm bắt đầu nói (khỏi mất phụ âm đầu)
MIN_SPEECH_S = 0.25  # tối thiểu chừng này speech mới mở utterance
MAX_UTTERANCE_S = 28.0  # nói liên tục quá → ép chốt câu (Whisper window 30s)
NO_AUDIO_TIMEOUT_S = 2.0  # mic ngừng gửi frame → coi như hết câu
CORRECTION_BACKLOG_MAX = 5  # hàng đợi pass 2 dồn quá → bỏ qua câu mới
MIN_CORRECT_CHARS = 10  # câu quá ngắn không đáng gọi LLM
# 2048→4096: prompt pass 2 giờ kèm ngữ cảnh đang diễn ra (topic + K câu recent,
# US-805) nên 2048 dễ tràn khi câu dài + glossary + pairs; vẫn ctx nhỏ cho nhanh
# (KHÔNG kế thừa config server — num_ctx phải set mọi request kẻo tràn RAM).
CORRECT_NUM_CTX = 4096
CORRECT_TIMEOUT_S = 20.0  # câu đơn phải kịp subtitle — không chờ như full-text
CONTEXT_RECENT_K = 6  # số câu final gần nhất giữ làm ngữ cảnh pass 2
CONTEXT_CONDENSE_EVERY = 8  # đủ chừng này câu mới thì condense topic 1 lần
CONTEXT_TOPIC_NUM_CTX = 2048  # condense chỉ tóm vài câu → ctx nhỏ
CONTEXT_TOPIC_TIMEOUT_S = 20.0  # condense chạy nền nhưng không để treo lâu
# US-811: final min avg_logprob dưới ngưỡng → re-decode nền setting mạnh hơn.
# −1.0 là sàn hallucination (keep_segment); −0.6 bắt câu "giữ lại nhưng run".
REVISE_LOGPROB = -0.6
REVISION_BACKLOG_MAX = 2
REVISE_ENABLED = os.environ.get("MANJU_REVISE", "1") != "0"
UNCERTAIN_PROB = 0.5  # word probability dưới ngưỡng → báo pass 2 soát kỹ (US-812)
UNCERTAIN_MAX = 8
IDENT_BACKLOG_MAX = 3  # hàng đợi speaker-ID live (US-814) — đầy thì bỏ, chặn CPU

# Silero mặc định cần lặng 2s mới tách đoạn — quá chậm cho subtitle; các giá trị
# nhỏ ở đây chỉ để đo "đuôi buffer còn speech không", không dùng để cắt audio.
_VAD_OPTS = VadOptions(min_speech_duration_ms=100, min_silence_duration_ms=150, speech_pad_ms=0)

# Số phiên live đồng thời. Decode đã được serialize trong engines._decode_lock
# (partial bận thì bỏ tick) nên N phiên chỉ làm partial trễ thêm ~1 tick.
MAX_LIVE_SESSIONS = int(os.environ.get("MAX_LIVE_SESSIONS", "2"))
_slot = threading.Semaphore(MAX_LIVE_SESSIONS)


def _speech_spans(audio: np.ndarray) -> list[dict]:
    """Các khoảng speech trong audio, đơn vị sample."""
    return get_speech_timestamps(audio, _VAD_OPTS, sampling_rate=SAMPLE_RATE)


class ContextTracker:
    """Biến ngữ cảnh đang diễn ra cho pass 2 (US-805).

    Giữ K câu final gần nhất + `topic` (tóm tắt chủ đề, cập nhật dần). Mỗi
    CONTEXT_CONDENSE_EVERY câu, condense chạy thread NỀN (không chặn subtitle):
    gọi LLM tóm tắt chủ đề từ topic cũ + các câu mới. Đang condense thì câu mới
    vẫn dùng topic cũ; LLM lỗi/timeout → giữ topic cũ (never-fail). Cuộc họp đổi
    chủ đề giữa chừng thì topic tự trôi theo, pass 2 sửa thuật ngữ đúng mạch.
    """

    def __init__(
        self,
        summarize: Callable[[str, str], str | None] | None = None,
        on_topic: Callable[[str], None] | None = None,
        initial_topic: str = "",
    ):
        # `summarize` inject được để test không gọi LLM thật. `on_topic` báo
        # topic mới cho caller (US-806: refresh bias); `initial_topic` mồi từ
        # metadata cuộc họp (US-809) — bias được ngay từ câu đầu tiên.
        self._summarize = summarize or _summarize_topic_llm
        self._on_topic = on_topic
        self._lock = threading.Lock()
        self._recent: deque[str] = deque(maxlen=CONTEXT_RECENT_K)
        self._new: list[str] = []  # câu dồn từ lần condense trước
        self._topic = initial_topic
        self._thread: threading.Thread | None = None
        self._closed = False

    def add(self, text: str) -> None:
        """Nhận 1 câu final; đủ M câu mới (và không có condense đang chạy) → kick nền."""
        with self._lock:
            self._recent.append(text)
            self._new.append(text)
            busy = self._thread is not None and self._thread.is_alive()
            if self._closed or busy or len(self._new) < CONTEXT_CONDENSE_EVERY:
                return  # câu dồn tiếp trong _new, lần add sau thử lại
            topic, batch = self._topic, " ".join(self._new)
            self._new.clear()
            self._thread = threading.Thread(
                target=self._condense, args=(topic, batch), daemon=True
            )
            self._thread.start()

    def _condense(self, topic: str, batch: str) -> None:
        try:
            new_topic = self._summarize(topic, batch)
        except Exception:  # noqa: BLE001 — condense lỗi thì giữ topic cũ
            return
        if not new_topic:
            return
        with self._lock:
            self._topic = new_topic
        if self._on_topic is not None:
            try:
                self._on_topic(new_topic)
            except Exception:  # noqa: BLE001 — callback lỗi không được giết condense
                pass

    def topic(self) -> str:
        with self._lock:
            return self._topic

    def context(self) -> str:
        """Chuỗi ngữ cảnh cho LlmOpts.context: topic (nếu có) + các câu recent."""
        with self._lock:
            parts = []
            if self._topic:
                parts.append("Chủ đề đang bàn: " + self._topic)
            if self._recent:
                parts.append(" ".join(self._recent))
            return "\n".join(parts)

    def close(self) -> None:
        """Ngừng nhận condense mới, chờ condense đang chạy xong — không rò thread."""
        with self._lock:
            self._closed = True
            thread = self._thread
        if thread is not None:
            thread.join(timeout=CONTEXT_TOPIC_TIMEOUT_S + 5)


def _summarize_topic_llm(topic: str, batch: str) -> str | None:
    return summarize_topic(
        topic, batch, LlmOpts(num_ctx=CONTEXT_TOPIC_NUM_CTX, timeout=CONTEXT_TOPIC_TIMEOUT_S)
    )


def _setting_on(key: str) -> bool:
    try:
        return db.get_setting(key, "0") == "1"
    except Exception:  # noqa: BLE001 — settings hỏng không được chặn phiên live
        return False


class LiveSession:
    def __init__(self, ws: WebSocket, loop: asyncio.AbstractEventLoop, cfg: dict):
        self.ws = ws
        self.loop = loop
        self.language = "en" if cfg.get("language") == "en" else "vi"
        self.engine = engines.get_engine()
        self.user_glossary = (cfg.get("glossary") or "").strip()
        # US-809: title/agenda cuộc họp = topic khởi tạo — bias ngay từ câu đầu.
        self.title = str(cfg.get("title") or "").strip()
        agenda = str(cfg.get("agenda") or "").strip()
        self.initial_topic = " ".join(p for p in (self.title, agenda) if p)
        # US-808/814: participants → lexicon cá nhân + vùng miền vào bias.
        self.participants = [str(p) for p in (cfg.get("participants") or []) if p]
        self._personal = self._load_personal(self.participants)
        self._personal_now = self._personal  # đổi khi speaker-ID thấy người khác nói
        self._regions = self._load_regions(self.participants)
        self.flag_words = _setting_on("flag_words")  # US-812
        # US-803/806: bias + few-shot chốt lúc start; topic mới (condense) hoặc
        # đổi người nói sẽ rebuild qua _refresh_bias — glossary user thì bất biến.
        self.glossary = corrections.build_bias(
            self.user_glossary, personal=self._personal, topic=self.initial_topic,
            regions=self._regions,
        )
        self.pairs = tuple(corrections.top_pairs(10, regions=self._regions))
        self.correct_enabled = bool(cfg.get("correct", True))
        # Client mỏng (PWA) tự giữ audio trong OPFS trên thiết bị → server
        # không ghi WAV (PRD FR-4).
        self.store_audio = bool(cfg.get("store_audio", True))
        # Token để client nối lại phiên sau khi rớt WS (resume).
        self.token = uuid.uuid4().hex
        self._pick_model(cfg.get("model"))
        self._init_buffers()
        self._init_recording()
        self._init_workers()

    @staticmethod
    def _load_personal(ids: list[str]) -> tuple[str, ...]:
        if not ids:
            return ()
        try:
            return tuple(db.personal_terms(ids))
        except Exception:  # noqa: BLE001 — thư viện cá nhân hỏng không chặn phiên
            return ()

    @staticmethod
    def _load_regions(ids: list[str]) -> tuple[str, ...]:
        if not ids:
            return ()
        try:
            rows = db.list_speakers()
        except Exception:  # noqa: BLE001
            return ()
        sel = set(ids)
        return tuple(sorted({r["region"] for r in rows if r["id"] in sel and r.get("region")}))

    def _pick_model(self, model: str | None) -> None:
        # Tier CPU: user chọn size model để cân tốc độ máy mình; GPU (mlx/cuda)
        # luôn dùng model mặc định của engine — nhanh và chính xác hơn mọi lựa chọn.
        if self.engine.info.tier == "cpu" and model in transcribe.ALLOWED_MODELS:
            override: str | None = model
            self.model_name = model
        else:
            override = None
            self.model_name = self.engine.info.model_name
        self._model_override = override
        self.spec = self._make_spec(self.glossary)

    def _make_spec(self, gloss: str) -> engines.DecodeSpec:
        # initial_prompt CHỈ là danh sách term (user-first). TUYỆT ĐỐI không
        # tiêm văn xuôi ("Chủ đề: ..."): Whisper nhại prompt văn xuôi vào
        # subtitle khi im lặng/nhiễu (bug thực địa 2026-07-20 — "chủ đề",
        # "J. J. J."). Topic chỉ dùng để XẾP HẠNG term trong build_bias.
        return engines.DecodeSpec(
            self.language, gloss, self._model_override, flag_words=self.flag_words,
        )

    def _refresh_bias(self, topic: str) -> None:
        """Rebuild bias khi topic đổi (US-806) / đổi người nói (US-814):
        topic chỉ re-rank term thư viện, không vào prompt.

        Gán attribute là atomic (GIL) + DecodeSpec frozen → decode/pass-2 luôn
        thấy spec cũ hoặc mới trọn vẹn, không cần lock; lỗi → giữ nguyên."""
        try:
            gloss = corrections.build_bias(
                self.user_glossary, personal=self._personal_now, topic=topic,
                regions=self._regions,
            )
            spec = self._make_spec(gloss)
            self.glossary = gloss
            self.spec = spec
        except Exception:  # noqa: BLE001 — never-fail, giữ bias cũ
            pass

    def _init_buffers(self) -> None:
        self.buffer = bytearray()  # PCM16 của utterance đang mở (+ đuôi chờ khi idle)
        self.buf_lock = threading.Lock()
        self.total_samples = 0
        self.consumed_samples = 0  # tổng sample đã xoá khỏi buffer = vị trí đầu buffer trên timeline
        self.last_rx = time.monotonic()
        self.state = "idle"  # idle | open
        self.utt_seq = 0
        self.sentences: dict[int, str] = {}  # bản chính (corrected ghi đè final)
        self.raw_sentences: dict[int, str] = {}
        self.utt_start: dict[int, float] = {}  # mốc bắt đầu mỗi câu (giây, tính từ đầu phiên)
        # US-813: denoise opt-in — chỉ thread decode đụng _clean (không cần lock);
        # bản WAV lưu vẫn là raw (feed ghi thẳng), artifact không dính vào file.
        self._denoiser: denoise.StreamDenoiser | None = None
        try:
            if _setting_on("denoise_enabled") and denoise.available():
                self._denoiser = denoise.StreamDenoiser(SAMPLE_RATE)
        except Exception:  # noqa: BLE001 — denoise hỏng thì chạy raw
            self._denoiser = None
        self._clean = np.zeros(0, dtype=np.float32)

    def _init_recording(self) -> None:
        # Ghi toàn bộ PCM ra file tạm ngay khi nhận (không đọng cả phiên trong
        # RAM) → cuối phiên đóng gói WAV lưu kèm transcript để nghe lại.
        self._rec_path = (
            transcribe.UPLOADS / f"live-{uuid.uuid4().hex[:12]}.pcm" if self.store_audio else None
        )
        self._rec_file = open(self._rec_path, "wb") if self._rec_path else None
        self._rec_lock = threading.Lock()

    def _init_workers(self) -> None:
        self.stop_event = threading.Event()
        self.correction_q: queue.Queue = queue.Queue()
        self.revision_q: queue.Queue = queue.Queue()  # US-811
        # Ngữ cảnh đang diễn ra (US-805) + topic mới → refresh bias (US-806).
        self.tracker = ContextTracker(
            on_topic=self._refresh_bias, initial_topic=self.initial_topic
        )
        self.started_at = datetime.now(UTC).astimezone()
        self._decode_thread = threading.Thread(target=self._decode_loop, daemon=True)
        self._correct_thread = threading.Thread(target=self._correct_loop, daemon=True)
        self._revise_thread = threading.Thread(target=self._revise_loop, daemon=True)
        self._init_ident()

    def _init_ident(self) -> None:
        # US-814: speaker-ID live — chỉ bật khi có model + voiceprint đã enroll;
        # thiếu gì cũng tắt im lặng, không chặn phiên.
        self._active_spk: str | None = None
        self._vps: list[tuple[str, np.ndarray]] = []
        self._spk_names: dict[str, str] = {}
        self.ident_q: queue.Queue = queue.Queue()
        self._ident_thread: threading.Thread | None = None
        # Opt-in (default OFF): embedding ONNX mỗi utterance tranh CPU/RAM với
        # decode → tick chậm, cắt câu lệch (regression thực địa 2026-07-20).
        try:
            if _setting_on("live_ident") and diarize.models_present():
                self._vps = diarize.to_np_voiceprints(db.load_voiceprints())
                self._spk_names = db.speaker_names()
        except Exception:  # noqa: BLE001
            self._vps = []
        if self._vps:
            self._ident_thread = threading.Thread(target=self._ident_loop, daemon=True)

    # ── Gọi từ event loop (WS handler) ────────────────────────────────────
    def start(self) -> None:
        self._decode_thread.start()
        if self.correct_enabled:
            self._correct_thread.start()
        if REVISE_ENABLED and self.engine.supports_revise:
            self._revise_thread.start()
        if self._ident_thread is not None:
            self._ident_thread.start()

    def feed(self, data: bytes) -> None:
        with self.buf_lock:
            self.buffer.extend(data)
        with self._rec_lock:
            if self._rec_file is not None:
                self._rec_file.write(data)  # bản ghi đầy đủ, không bị drop_prefix cắt
        self.total_samples += len(data) // 2
        self.last_rx = time.monotonic()

    def rebind(self, ws: WebSocket, loop: asyncio.AbstractEventLoop) -> None:
        """Gắn WebSocket mới vào phiên đang chờ resume (client nối lại)."""
        self.ws = ws
        self.loop = loop
        self.last_rx = time.monotonic()

    def shutdown(self) -> str | None:
        """Chốt câu dở, chờ pass 2 xả hàng đợi, lưu transcript. Chạy trong thread."""
        self.stop_event.set()
        self._decode_thread.join(timeout=30)
        if self._ident_thread is not None and self._ident_thread.is_alive():
            self.ident_q.put(None)
            self._ident_thread.join(timeout=10)
        if self._revise_thread.is_alive():
            # Drain revision TRƯỚC pass 2 — câu revise xong còn kịp vào correction_q.
            self.revision_q.put(None)
            self._revise_thread.join(timeout=20)
        if self.correct_enabled:
            self.correction_q.put(None)
            self._correct_thread.join(timeout=12)
        self.tracker.close()  # chờ condense nền xong — không rò thread sau Dừng
        return self._save()

    # ── Helpers ───────────────────────────────────────────────────────────
    def _send(self, msg: dict) -> None:
        """Gửi JSON cho client từ worker thread; client rớt thì bỏ qua."""
        try:
            asyncio.run_coroutine_threadsafe(
                self.ws.send_text(json.dumps(msg, ensure_ascii=False)), self.loop
            )
        except Exception:  # noqa: BLE001
            pass

    def _snapshot(self) -> np.ndarray:
        with self.buf_lock:
            data = bytes(self.buffer)
        audio = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0
        return audio if self._denoiser is None else self._denoised(audio)

    def _denoised(self, audio: np.ndarray) -> np.ndarray:
        # US-813: chỉ denoise phần sample MỚI mỗi tick (≤~1.2s → vài ms FFT),
        # phần cũ tái dùng — chạy trong thread decode, cùng thread _drop_prefix.
        assert self._denoiser is not None
        if audio.size > self._clean.size:
            fresh = self._denoiser.process(audio[self._clean.size:])
            self._clean = np.concatenate([self._clean, fresh])
        return self._clean[: audio.size]

    def _drop_prefix(self, n_samples: int) -> None:
        # Chỉ thread decode xoá đầu buffer; receive loop chỉ append cuối →
        # index tính trên snapshot vẫn đúng.
        with self.buf_lock:
            del self.buffer[: n_samples * 2]
        self.consumed_samples += n_samples
        if self._denoiser is not None:
            self._clean = self._clean[n_samples:]

    def _decode(self, audio: np.ndarray, final: bool) -> str:
        # final=False có thể raise engines.DecodeBusy (phiên khác đang giữ
        # engine) — caller bỏ tick đó, thử lại tick sau.
        return self.engine.decode(audio, self.spec, final=final)

    # ── Thread decode: state machine idle/open ────────────────────────────
    def _decode_loop(self) -> None:
        try:
            # Warm-up: lần decode đầu chậm (load model) — trả trước khi báo ready.
            # Engine bận = phiên khác đang decode, tức model đã/đang được load
            # sẵn → thử lại vài lần rồi cứ ready (partial sau tự chờ tick).
            for _ in range(3):
                try:
                    self._decode(np.zeros(SAMPLE_RATE // 2, dtype=np.float32), final=False)
                    break
                except engines.DecodeBusy:
                    time.sleep(0.5)
        except Exception as exc:  # noqa: BLE001
            self._send({"type": "error", "message": f"Không load được model: {exc}"})
            return
        self._send({"type": "ready"})

        last_partial_samples = 0  # số sample đã decode ở partial gần nhất
        while not self.stop_event.wait(TICK_S):
            audio = self._snapshot()
            if self.state == "idle":
                last_partial_samples = self._tick_idle(audio)
            else:
                last_partial_samples = self._tick_open(audio, last_partial_samples)

        # Stop: chốt nốt câu đang dở — kể cả khi còn idle (audio dồn trong
        # buffer lúc warm-up / giữa 2 tick mà chưa kịp mở utterance).
        audio = self._snapshot()
        if self.state == "open":
            self._finalize(audio)
        elif len(audio):
            spans = _speech_spans(audio)
            speech_s = sum(s["end"] - s["start"] for s in spans) / SAMPLE_RATE
            if speech_s >= MIN_SPEECH_S:
                self.utt_seq += 1
                self.utt_start[self.utt_seq] = self.consumed_samples / SAMPLE_RATE
                self._finalize(audio)

    def _tick_idle(self, audio: np.ndarray) -> int:
        # VAD trên CẢ buffer: steady state nó chỉ dài ~1.8s (được trim bên dưới),
        # nhưng sau warm-up / tick chậm có thể tồn đọng nhiều — speech nằm giữa
        # buffer mà chỉ soi đuôi thì sẽ bị trim mất trước khi kịp decode.
        if len(audio) == 0 or float(np.abs(audio).max()) < 0.01:  # RMS gate rẻ trước
            spans = []
        else:
            spans = _speech_spans(audio)
        speech_s = sum(s["end"] - s["start"] for s in spans) / SAMPLE_RATE
        if speech_s >= MIN_SPEECH_S:
            start = spans[0]["start"] - int(PREROLL_S * SAMPLE_RATE)
            self._drop_prefix(max(0, start))
            self.state = "open"
            self.utt_seq += 1
            self.utt_start[self.utt_seq] = self.consumed_samples / SAMPLE_RATE
            return 0
        # Vẫn im lặng: chỉ giữ đuôi buffer, khỏi phình vô hạn.
        tail_keep = int((VAD_WINDOW_S + PREROLL_S) * SAMPLE_RATE)
        if len(audio) > tail_keep:
            self._drop_prefix(len(audio) - tail_keep)
        return 0

    def _tick_open(self, audio: np.ndarray, last_partial_samples: int) -> int:
        duration_s = len(audio) / SAMPLE_RATE
        # VAD cả buffer đang mở (≤28s → vài chục ms): tick + decode có thể mất
        # ~2.5s nên khoảng lặng giữa 2 câu dễ bị "nhảy qua" — phải tìm gap ở
        # giữa buffer chứ không chỉ đo đuôi.
        spans = _speech_spans(audio)
        mic_stalled = time.monotonic() - self.last_rx > NO_AUDIO_TIMEOUT_S

        if not spans:
            self._finalize(audio)  # VAD đổi ý, toàn noise → final rỗng, client xoá dòng
            return 0
        for a, b in zip(spans, spans[1:], strict=False):  # cặp liền kề; đuôi lệch 1 là cố ý
            if (b["start"] - a["end"]) / SAMPLE_RATE >= ENDPOINT_SILENCE_S:
                # Hết câu ở giữa buffer: chốt tới đó, phần sau là câu kế tiếp.
                cut = a["end"] + int(0.2 * SAMPLE_RATE)
                self._finalize(audio[:cut], consumed=cut)
                return 0
        trailing_silence_s = (len(audio) - spans[-1]["end"]) / SAMPLE_RATE
        if trailing_silence_s >= ENDPOINT_SILENCE_S or duration_s >= MAX_UTTERANCE_S or mic_stalled:
            self._finalize(audio)
            return 0
        if (len(audio) - last_partial_samples) / SAMPLE_RATE >= MIN_NEW_AUDIO_S:
            try:
                text = self._decode(audio, final=False)
            except Exception:  # noqa: BLE001 — partial hỏng thì chờ tick sau
                return last_partial_samples
            if text:
                self._send({"type": "partial", "utt": self.utt_seq, "text": text})
            return len(audio)
        return last_partial_samples

    def _finalize(self, audio: np.ndarray, consumed: int | None = None) -> None:
        utt = self.utt_seq
        uncertain: tuple[str, ...] = ()
        low_conf = False
        try:
            res = self.engine.decode_scored(audio, self.spec, final=True)
            text = res.text
            # US-812: word confidence thấp → pass 2 biết chỗ cần soát kỹ.
            uncertain = tuple(w.strip() for w, p in res.words if p < UNCERTAIN_PROB)[:UNCERTAIN_MAX]
            low_conf = bool(text) and res.min_logprob < REVISE_LOGPROB
        except Exception:  # noqa: BLE001
            text = ""
        # text rỗng (chỉ là noise) → client xoá dòng partial của utt này.
        self._send({"type": "final", "utt": utt, "text": text})
        if text:
            self.sentences[utt] = text
            self.raw_sentences[utt] = text
            if self.correct_enabled:
                # Mọi câu final (kể cả câu ngắn bỏ qua pass 2) đều nuôi tracker
                # để topic bám sát mạch cuộc họp.
                self.tracker.add(text)
            self._queue_ident(utt, audio)
            if (
                REVISE_ENABLED
                and self.engine.supports_revise
                and low_conf
                and self.revision_q.qsize() < REVISION_BACKLOG_MAX
            ):
                # US-811: revise nền xong mới pass 2 — LLM sửa trên text tốt nhất;
                # backlog đầy thì rơi về path pass-2 thường, không nghẽn.
                self.revision_q.put((utt, np.array(audio, copy=True), text, uncertain))
            else:
                self._queue_correction(utt, text, uncertain)
        # Chỉ bỏ phần đã chốt: audio sau đó (câu kế tiếp đã bắt đầu trong lúc
        # decode / sau điểm cắt) giữ lại cho tick idle mở utterance mới.
        self._drop_prefix(len(audio) if consumed is None else consumed)
        self.state = "idle"

    def _queue_correction(self, utt: int, text: str, uncertain: tuple[str, ...]) -> None:
        if (
            self.correct_enabled
            and len(text) >= MIN_CORRECT_CHARS
            and self.correction_q.qsize() < CORRECTION_BACKLOG_MAX
        ):
            self.correction_q.put((utt, text, uncertain))

    def _queue_ident(self, utt: int, audio: np.ndarray) -> None:
        if (
            self._ident_thread is not None
            and audio.size >= SAMPLE_RATE * diarize.LIVE_ID_MIN_S
            and self.ident_q.qsize() < IDENT_BACKLOG_MAX
        ):
            self.ident_q.put((utt, np.array(audio, copy=True)))

    # ── Thread revise: re-decode câu confidence thấp bằng setting mạnh hơn ─
    def _revise_loop(self) -> None:
        while True:
            item = self.revision_q.get()
            if item is None:
                return
            utt, audio, text, uncertain = item
            revised = self._revise_once(audio)
            if revised is None and not self.stop_event.wait(0.5):
                revised = self._revise_once(audio)  # engine bận → thử lại 1 lần
            if revised and revised != text:
                self.sentences[utt] = revised
                self.raw_sentences[utt] = revised
                self._send({"type": "revise", "utt": utt, "text": revised})
                text = revised
            self._queue_correction(utt, text, uncertain)

    def _revise_once(self, audio: np.ndarray) -> str | None:
        try:
            return self.engine.revise(audio, self.spec)
        except Exception:  # noqa: BLE001 — revise hỏng thì giữ bản final
            return None

    # ── Thread speaker-ID: nhận diện người nói theo utterance final (US-814) ─
    def _ident_loop(self) -> None:
        while True:
            item = self.ident_q.get()
            if item is None:
                return
            utt, audio = item
            sid = self._identify(audio)
            if sid is None:
                continue  # unknown → không tag, giữ bias hiện tại
            name = self._spk_names.get(sid)
            if name:
                self._send({"type": "speaker", "utt": utt, "name": name})
            if sid != self._active_spk:
                # "Đổi người" chính là debounce — không rebuild mỗi utterance.
                self._active_spk = sid
                self._bias_speaker(sid)

    def _identify(self, audio: np.ndarray) -> str | None:
        try:
            vec = diarize.embed_utterance(audio)
            if vec is None:
                return None
            return diarize.best_match(vec, self._vps, diarize.LIVE_ID_THRESHOLD)
        except Exception:  # noqa: BLE001
            return None

    def _bias_speaker(self, sid: str) -> None:
        # US-814: term của người ĐANG nói xếp trước phần participants còn lại.
        try:
            active = tuple(db.personal_terms([sid]))
        except Exception:  # noqa: BLE001
            return
        self._personal_now = active + tuple(t for t in self._personal if t not in active)
        self._refresh_bias(self.tracker.topic())

    # ── Thread pass 2: sửa thuật ngữ từng câu ─────────────────────────────
    def _correct_loop(self) -> None:
        if not openrouter_enabled():
            # Warm-up Ollama: load LLM vào RAM để câu đầu không phải chờ.
            # (OpenRouter là API ngoài, không cần warm-up.)
            correct_sentence("ping", LlmOpts(num_ctx=CORRECT_NUM_CTX, timeout=60.0))
        while True:
            item = self.correction_q.get()
            if item is None:
                return
            utt, text, uncertain = item
            # Ngữ cảnh đang diễn ra (topic + câu gần nhất, US-805): LLM đoán
            # thuật ngữ theo mạch cuộc họp ("can ban che quýt" → "kanban checklist").
            context = self.tracker.context()
            fixed, ok = correct_sentence(
                text,
                LlmOpts(
                    glossary=self.glossary,
                    context=context,
                    num_ctx=CORRECT_NUM_CTX,
                    timeout=CORRECT_TIMEOUT_S,
                    pairs=self.pairs,
                    uncertain=uncertain,
                ),
            )
            changed = ok and fixed != text
            if changed:
                self.sentences[utt] = fixed
            self._send({"type": "corrected", "utt": utt, "text": fixed, "changed": changed})

    # ── Lưu transcript ────────────────────────────────────────────────────
    def _close_recording(self) -> bytes:
        """Đóng file PCM tạm, trả toàn bộ dữ liệu đã ghi rồi xoá file tạm."""
        with self._rec_lock:
            if self._rec_file is not None:
                self._rec_file.close()
                self._rec_file = None
        if self._rec_path is None:  # store_audio=False — audio nằm trên thiết bị client
            return b""
        try:
            data = self._rec_path.read_bytes()
        except Exception:  # noqa: BLE001
            data = b""
        self._rec_path.unlink(missing_ok=True)
        return data

    def _save(self) -> str | None:
        pcm = self._close_recording()
        order = sorted(self.sentences)
        text = " ".join(self.sentences[k] for k in order).strip()
        if not text:
            return None
        raw = " ".join(self.raw_sentences[k] for k in order).strip()
        # Từng câu kèm mốc thời gian (giây từ đầu phiên) → history xem chi tiết đoạn.
        segments = [
            {"start": round(self.utt_start.get(k, 0.0), 2), "text": self.sentences[k]}
            for k in order
        ]
        # Đóng gói bản ghi mic thành WAV để lưu kèm (nghe/tải lại từ lịch sử).
        wav_path: Path | None = None
        if pcm:
            wav_path = transcribe.UPLOADS / f"live-{uuid.uuid4().hex[:12]}.wav"
            try:
                transcribe.pcm_to_wav(pcm, wav_path, SAMPLE_RATE)
            except Exception:  # noqa: BLE001 — lỗi đóng gói audio không được chặn lưu text
                wav_path = None
        return transcribe.save_transcript(transcribe.TranscriptDraft(
            original_name=self.title or f"live-{self.started_at:%H%M}",
            language=self.language,
            duration=self.total_samples / SAMPLE_RATE,
            text=text,
            model_name=self.model_name,
            raw_text=raw if raw != text else None,
            segments=segments,
            audio_path=wav_path,
        ))


# ── Resume: phiên rớt WS được giữ chờ client nối lại trước khi chốt & lưu ──
RESUME_GRACE_S = 60.0
ACK_EVERY_BYTES = 65536  # ~2s audio — nhịp báo client cắt buffer replay

_resumable: dict[str, tuple[LiveSession, threading.Timer]] = {}
_resumable_lock = threading.Lock()


def _park_session(session: LiveSession) -> None:
    """Giữ phiên (vẫn giữ slot + decode tiếp phần audio còn trong buffer);
    quá RESUME_GRACE_S không ai claim → chốt câu, lưu transcript, trả slot."""

    def expire() -> None:
        with _resumable_lock:
            if _resumable.get(session.token, (None, None))[0] is not session:
                return  # đã được claim
            del _resumable[session.token]
        try:
            session.shutdown()
        finally:
            _slot.release()

    timer = threading.Timer(RESUME_GRACE_S, expire)
    timer.daemon = True
    with _resumable_lock:
        _resumable[session.token] = (session, timer)
    timer.start()


def _claim_session(token: str | None) -> LiveSession | None:
    with _resumable_lock:
        item = _resumable.pop(token or "", None)
    if item is None:
        return None
    session, timer = item
    timer.cancel()
    return session


async def _reject(ws: WebSocket, message: str) -> None:
    await ws.send_text(json.dumps({"type": "error", "message": message}))
    await ws.close()


async def _start_session(ws: WebSocket, cfg: dict) -> LiveSession | None:
    """Mở phiên mới; hết slot → báo lỗi, đóng WS, trả None."""
    if not _slot.acquire(blocking=False):
        await _reject(ws, "Server đang bận — đủ số phiên live.")
        return None
    session = LiveSession(ws, asyncio.get_running_loop(), cfg)
    session.start()
    await ws.send_text(json.dumps({"type": "session", "token": session.token}))
    return session


async def _resume_session(ws: WebSocket, token: str | None) -> LiveSession | None:
    """Nối lại phiên đang park; token hết hạn → báo lỗi, đóng WS, trả None."""
    session = _claim_session(token)
    if session is None:
        await _reject(ws, "Phiên đã kết thúc — bản ghi (nếu có) đã tự lưu.")
        return None
    session.rebind(ws, asyncio.get_running_loop())
    await ws.send_text(json.dumps({"type": "resumed", "bytes": session.total_samples * 2}))
    return session


async def _finish(ws: WebSocket, session: LiveSession | None, finish: bool) -> None:
    """Dọn phiên khi rời receive loop: stop chủ động → chốt & lưu ngay;
    rớt WS → park chờ resume (slot vẫn giữ theo phiên đang park)."""
    try:
        if session is not None:
            if finish:
                transcript_id = await asyncio.to_thread(session.shutdown)
                try:
                    await ws.send_text(
                        json.dumps({"type": "saved", "transcript_id": transcript_id})
                    )
                except Exception:  # noqa: BLE001 — client đã rớt
                    pass
                _slot.release()
            else:
                _park_session(session)
    finally:
        try:
            await ws.close()
        except Exception:  # noqa: BLE001
            pass


async def handle(ws: WebSocket) -> None:
    await ws.accept()
    session: LiveSession | None = None
    finish = False  # stop chủ động → chốt & lưu ngay; rớt WS → park chờ resume
    unacked = 0
    try:
        while True:
            msg = await ws.receive()
            if msg["type"] == "websocket.disconnect":
                break
            if msg.get("text"):
                data = json.loads(msg["text"])
                if data.get("type") == "start" and session is None:
                    session = await _start_session(ws, data)
                    if session is None:
                        return
                elif data.get("type") == "resume" and session is None:
                    session = await _resume_session(ws, data.get("token"))
                    if session is None:
                        return
                elif data.get("type") == "stop":
                    finish = True
                    break
            elif msg.get("bytes") and session is not None:
                session.feed(msg["bytes"])
                unacked += len(msg["bytes"])
                if unacked >= ACK_EVERY_BYTES:
                    unacked = 0
                    await ws.send_text(
                        json.dumps({"type": "ack", "bytes": session.total_samples * 2})
                    )
    finally:
        await _finish(ws, session, finish)
