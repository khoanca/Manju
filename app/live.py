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
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
from fastapi import WebSocket
from faster_whisper.vad import VadOptions, get_speech_timestamps

from app import corrections, db, denoise, engines, transcribe
from app.correct import LlmOpts, correct_sentence, openrouter_enabled

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
# num_ctx phải set mọi request kẻo tràn RAM (KHÔNG kế thừa config server).
CORRECT_NUM_CTX = 4096
CORRECT_TIMEOUT_S = 20.0  # câu đơn phải kịp subtitle — không chờ như full-text
# US-826: đuôi im lặng/nhiễu sau điểm speech cuối là thứ đẻ ra hallucination
# ("đăng đăng đăng") — final chỉ decode tới hết speech + pad này. 0.3s theo đo
# sơ bộ bench 2026-07-21 (pad 0.3 khử hết utterance hỏng, xem project-state).
FINAL_TAIL_PAD_S = 0.3
# US-811: final min avg_logprob dưới ngưỡng → re-decode nền setting mạnh hơn.
# −1.0 là sàn hallucination (keep_segment); −0.6 bắt câu "giữ lại nhưng run".
REVISE_LOGPROB = -0.6
REVISION_BACKLOG_MAX = 2
REVISE_ENABLED = os.environ.get("MANJU_REVISE", "1") != "0"
UNCERTAIN_PROB = 0.5  # word probability dưới ngưỡng → báo pass 2 soát kỹ (US-812)
UNCERTAIN_MAX = 8

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
        self.title = str(cfg.get("title") or "").strip()  # chỉ để đặt tên transcript
        self.flag_words = _setting_on("flag_words")  # US-812
        # US-826/827: prompt live CHỈ là glossary user gõ tay — bias tự động
        # (lexicon nền, topic, personal, region) bị PARK: đo 2026-07-26 nó bị
        # decoder echo lên subtitle khi im lặng/nhiễu ("kubernetes") và kéo
        # no_speech_prob xuống 0 làm gate chống bịa mù. Xem plan-live-reliability.
        self.glossary = self.user_glossary
        self.pairs = tuple(corrections.top_pairs(10))
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
        # initial_prompt CHỈ là danh sách term user gõ. TUYỆT ĐỐI không tiêm
        # văn xuôi: Whisper nhại prompt vào subtitle khi im lặng/nhiễu (bug
        # thực địa 2026-07-20 "J. J. J."; đo lại 2026-07-26 với bias tự động).
        self.spec = engines.DecodeSpec(
            self.language, self.glossary, self._model_override, flag_words=self.flag_words,
        )

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
        self.raw_scores: dict[int, float] = {}  # min_logprob mỗi utt (cho reanalyze)
        self.raw_meta: dict[int, dict] = {}  # telemetry decode mỗi utt (US-828)
        self.utt_start: dict[int, float] = {}  # mốc bắt đầu mỗi câu (giây, tính từ đầu phiên)
        # US-813: denoise opt-in — chỉ thread decode đụng _clean (không cần lock);
        # bản WAV lưu vẫn là raw (feed ghi thẳng), artifact không dính vào file.
        self._denoiser: denoise.StreamDenoiser | None = None
        try:
            if _setting_on("denoise_enabled") and denoise.available():
                self._denoiser = denoise.StreamDenoiser(
                    SAMPLE_RATE, denoise.params_from_settings()
                )
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
        self.started_at = datetime.now(UTC).astimezone()
        self._decode_thread = threading.Thread(target=self._decode_loop, daemon=True)
        self._correct_thread = threading.Thread(target=self._correct_loop, daemon=True)
        self._revise_thread = threading.Thread(target=self._revise_loop, daemon=True)

    # ── Gọi từ event loop (WS handler) ────────────────────────────────────
    def start(self) -> None:
        self._decode_thread.start()
        if self.correct_enabled:
            self._correct_thread.start()
        if REVISE_ENABLED and self.engine.supports_revise:
            self._revise_thread.start()

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
        """Chốt câu dở, xả pass 2, lưu transcript. Chạy trong thread.

        Timeout join RÚT NGẮN để Dừng phản hồi nhanh (bug thực địa: shutdown chậm
        làm client bắn deadline 30s → mất mapping audio OPFS). Ưu tiên: decode câu
        cuối + pass-2 (ảnh hưởng text lưu) chờ vừa phải; revise/ident/condense là
        cải thiện nền → cắt ngắn, chưa xong thì bỏ, không chặn lưu."""
        self.stop_event.set()
        self._decode_thread.join(timeout=15)
        if self._revise_thread.is_alive():
            # Drain revision TRƯỚC pass 2 — câu revise xong còn kịp vào correction_q.
            self.revision_q.put(None)
            self._revise_thread.join(timeout=3)
        if self.correct_enabled:
            self.correction_q.put(None)
            self._correct_thread.join(timeout=8)
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

    def _trim_tail(self, audio: np.ndarray) -> np.ndarray:
        """US-826: final chỉ decode tới hết speech + FINAL_TAIL_PAD_S. Đuôi im
        lặng/nhiễu là nguồn hallucination ("đăng đăng đăng") và là thứ đẩy
        decode vào thang nhiệt độ 18-25s ngay lúc Stop. Không còn speech → rỗng
        (khỏi decode luôn — final rỗng, client xoá dòng partial)."""
        spans = _speech_spans(audio)
        if not spans:
            return audio[:0]
        end = spans[-1]["end"] + int(FINAL_TAIL_PAD_S * SAMPLE_RATE)
        return audio[: min(len(audio), end)]

    def _finalize(self, audio: np.ndarray, consumed: int | None = None) -> None:
        utt = self.utt_seq
        uncertain: tuple[str, ...] = ()
        low_conf = False
        words: list[list[object]] = []
        clipped = self._trim_tail(audio)
        started = time.monotonic()
        try:
            if clipped.size == 0:
                text = ""
            else:
                res = self.engine.decode_scored(clipped, self.spec, final=True)
                text = res.text
                # US-812: word confidence thấp → pass 2 biết chỗ cần soát kỹ.
                uncertain = tuple(w.strip() for w, p in res.words if p < UNCERTAIN_PROB)[:UNCERTAIN_MAX]
                low_conf = bool(text) and res.min_logprob < REVISE_LOGPROB
                # US-823: gửi (word, prob) để UI gạch đỏ từ khả nghi (p < SUSPECT_PROB).
                words = [[w, round(float(p), 3)] for w, p in res.words]
        except Exception:  # noqa: BLE001
            text = ""
        # text rỗng (chỉ là noise) → client xoá dòng partial của utt này.
        msg: dict[str, object] = {"type": "final", "utt": utt, "text": text}
        if words:
            msg["words"] = words
        self._send(msg)
        if text:
            self.sentences[utt] = text
            self.raw_sentences[utt] = text
            self.raw_scores[utt] = res.min_logprob
            # US-828: telemetry per-utterance — truy phiên lỗi không cần tái hiện.
            self.raw_meta[utt] = {
                "no_speech_prob": round(res.max_no_speech_prob, 4),
                "compression_ratio": round(res.max_compression_ratio, 2),
                "temperature": res.temperature,
                "decode_wall_s": round(time.monotonic() - started, 2),
            }
            if (
                REVISE_ENABLED
                and self.engine.supports_revise
                and low_conf
                and self.revision_q.qsize() < REVISION_BACKLOG_MAX
            ):
                # US-811: revise nền xong mới pass 2 — LLM sửa trên text tốt nhất;
                # backlog đầy thì rơi về path pass-2 thường, không nghẽn.
                self.revision_q.put((utt, np.array(clipped, copy=True), text, uncertain))
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
            utt, orig, uncertain = item
            fixed, ok = correct_sentence(
                orig,
                LlmOpts(
                    glossary=self.glossary,
                    num_ctx=CORRECT_NUM_CTX,
                    timeout=CORRECT_TIMEOUT_S,
                    pairs=self.pairs,
                    uncertain=uncertain,
                ),
            )
            final = fixed if ok else orig
            self.sentences[utt] = final
            changed = final != orig
            self._send({"type": "corrected", "utt": utt, "text": final, "changed": changed})

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
        # Gom lặp TOÀN VĂN trước khi lưu: bộ lọc live chỉ soi từng segment nên
        # loop thoái hoá vắt qua ranh giới 2 utterance ("NYE NYE" | "NYE NYE" →
        # 4×NYE, live-2341) lọt hết ngưỡng per-segment. collapse_loops trên chuỗi
        # đã nối bắt được (tất định, không đụng câu thật). Xem app/cleanup.py.
        text = engines.collapse_loops(" ".join(self.sentences[k] for k in order)).strip()
        if not text:
            return None
        raw = " ".join(self.raw_sentences[k] for k in order).strip()
        # Từng câu kèm mốc thời gian (giây từ đầu phiên) → history xem chi tiết đoạn.
        segments = [
            {"start": round(self.utt_start.get(k, 0.0), 2), "text": self.sentences[k]}
            for k in order
        ]
        # ASR thô mỗi utterance (text như live nghe, trước pass 2) — reanalyze dùng
        # THAY vì batch-decode lại (khác live). Kèm telemetry US-828 (nsp thật,
        # cr, temperature, wall) — trước đây nsp hardcode 0.0 nên không truy được.
        raw_segments = [
            {
                "text": self.raw_sentences[k],
                "avg_logprob": round(self.raw_scores.get(k, 0.0), 4),
                **self.raw_meta.get(k, {"no_speech_prob": 0.0}),
            }
            for k in order
            if k in self.raw_sentences
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
            raw_segments=raw_segments,
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
