"""Cloud STT (Soniox) — nhánh online của FR-10.

Realtime: `SonioxLive` giữ WebSocket tới stt-rt-v5, nhận token liên tục và gom
thành utterance (khoảng lặng >= UTT_GAP_MS hoặc token "<end>") để tái dùng
nguyên protocol partial/final của live.py. Async: `transcribe_file_async` cho
re-listen sau Stop + upload online (stt-async-v5), xong PHẢI xóa file/
transcription phía Soniox (privacy: realtime không bị lưu, async lưu tới khi
xóa — soniox.com/docs/security-and-privacy).

Transport injectable (connect factory / httpx client) — test chạy mock, không
cần key. Mọi callback đều được nuốt lỗi: cloud hỏng không được phá phiên live.
"""
from __future__ import annotations

import json
import os
import queue
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import httpx

RT_URL = "wss://stt-rt.soniox.com/transcribe-websocket"
RT_MODEL = "stt-rt-v5"
ASYNC_MODEL = "stt-async-v5"
API_BASE = "https://api.soniox.com/v1"
SAMPLE_RATE = 16000
UTT_GAP_MS = 700  # khớp ENDPOINT_SILENCE_S của live.py
MAX_UTT_MS = 28_000
CONTEXT_MAX_CHARS = 10_000
KEEPALIVE_S = 15.0  # Soniox ngắt kết nối idle — bơm keepalive khi không có audio
POLL_INTERVAL_S = 2.0


class CloudError(Exception):
    """Lỗi phía provider (config sai, hết balance, timeout job async...)."""


def api_key() -> str | None:
    return os.environ.get("SONIOX_API_KEY") or None


def available() -> bool:
    """Tier cloud khả dụng = có key. Mất mạng phát hiện lúc connect (fallback)."""
    return api_key() is not None


def language_hints(language: str) -> list[str]:
    # Họp Việt xen thuật ngữ Anh là use case chính; phiên tiếng Anh thuần vẫn hint en.
    return ["vi", "en"] if language == "vi" else ["en"]


def build_context(glossary: str, pairs: Iterable[tuple[str, str]]) -> dict | None:
    """context.terms cho Soniox: glossary user (ưu tiên, giữ trọn) + vế đúng của
    cặp sửa đã duyệt. Cap CONTEXT_MAX_CHARS — cắt phía corrections trước."""
    seen: set[str] = set()
    terms: list[str] = []
    total = 0
    for src in (glossary.replace("\n", ",").split(","), (right for _, right in pairs)):
        for raw in src:
            term = raw.strip()
            key = term.lower()
            if not term or key in seen:
                continue
            if total + len(term) > CONTEXT_MAX_CHARS:
                return {"terms": terms}
            seen.add(key)
            terms.append(term)
            total += len(term)
    return {"terms": terms} if terms else None


# ── Realtime ───────────────────────────────────────────────────────────────


class _Transport(Protocol):
    """Phần giao diện websocket-client mà SonioxLive dùng (test bơm fake)."""

    def send(self, data: str) -> Any: ...
    def send_binary(self, data: bytes) -> Any: ...
    def recv(self) -> str | bytes: ...
    def close(self) -> Any: ...


def _connect_real() -> _Transport:
    from websocket import create_connection  # lazy: test không cần lib thật

    return create_connection(RT_URL, timeout=10)  # type: ignore[no-any-return]


@dataclass
class LiveCloudSpec:
    """Config 1 phiên realtime + callbacks về LiveSession."""

    language: str
    context: dict | None
    # on_partial(text, start_ms): utterance đang mở đổi nội dung.
    on_partial: Callable[[str, int], None]
    # on_final(text, start_ms, speaker): utterance chốt.
    on_final: Callable[[str, int, str | None], None]
    # on_error(message): provider chết — LiveSession fallback local.
    on_error: Callable[[str], None]
    connect: Callable[[], _Transport] = _connect_real


@dataclass
class _UttState:
    tokens: list[dict] = field(default_factory=list)
    nonfinal: str = ""
    start_ms: int = -1
    end_ms: int = -1
    speaker: str | None = None


class SonioxLive:
    """Cầu PCM → Soniox realtime. 2 thread: tx (queue audio + keepalive) và
    rx (token → utterance). Không tự reconnect — lỗi là báo on_error để
    LiveSession rơi về local (quyết định FR-10: fallback thay vì retry)."""

    def __init__(self, spec: LiveCloudSpec):
        self.spec = spec
        self._q: queue.Queue[bytes | None] = queue.Queue()
        self._utt = _UttState()
        self._failed = False
        self._finished = threading.Event()
        self.last_final_end_ms = 0  # mốc trim buffer khi fallback
        self._ws: _Transport | None = None
        self._tx = threading.Thread(target=self._tx_loop, daemon=True)
        self._rx = threading.Thread(target=self._rx_loop, daemon=True)

    # -- lifecycle --
    def start(self) -> None:
        """Connect + gửi config. Raise CloudError nếu không mở được (caller
        quyết định fallback ngay từ đầu phiên)."""
        key = api_key()
        if key is None:
            raise CloudError("thiếu SONIOX_API_KEY")
        try:
            self._ws = self.spec.connect()
            self._ws.send(json.dumps(self._config(key)))
        except Exception as exc:  # noqa: BLE001
            raise CloudError(f"không mở được kết nối Soniox: {exc}") from exc
        self._tx.start()
        self._rx.start()

    def _config(self, key: str) -> dict:
        cfg: dict = {
            "api_key": key,
            "model": RT_MODEL,
            "audio_format": "pcm_s16le",
            "sample_rate": SAMPLE_RATE,
            "num_channels": 1,
            "language_hints": language_hints(self.spec.language),
            "enable_speaker_diarization": True,
            "enable_endpoint_detection": True,
        }
        if self.spec.context:
            cfg["context"] = self.spec.context
        return cfg

    def feed(self, data: bytes) -> None:
        if not self._failed:
            self._q.put(data)

    def stop(self, timeout: float = 10.0) -> None:
        """Hết audio: gửi frame rỗng để server chốt nốt token rồi chờ finished
        (utterance cuối được flush qua on_final trong rx loop)."""
        self._q.put(None)
        self._finished.wait(timeout)
        self._close()

    def abort(self) -> None:
        self._failed = True
        self._q.put(None)
        self._close()

    def _close(self) -> None:
        try:
            if self._ws is not None:
                self._ws.close()
        except Exception:  # noqa: BLE001
            pass

    # -- tx: audio + keepalive --
    def _tx_loop(self) -> None:
        while True:
            try:
                item = self._q.get(timeout=KEEPALIVE_S)
            except queue.Empty:
                self._tx_send(json.dumps({"type": "keepalive"}), binary=False)
                continue
            if item is None:
                self._tx_send(b"", binary=True)  # frame rỗng = hết audio
                return
            self._tx_send(item, binary=True)

    def _tx_send(self, data: str | bytes, *, binary: bool) -> None:
        try:
            assert self._ws is not None
            if binary:
                self._ws.send_binary(data if isinstance(data, bytes) else data.encode())
            else:
                self._ws.send(data if isinstance(data, str) else data.decode())
        except Exception:  # noqa: BLE001 — rx loop sẽ báo on_error khi recv vỡ
            pass

    # -- rx: token → utterance --
    def _rx_loop(self) -> None:
        while True:
            try:
                raw = self._ws.recv() if self._ws is not None else b""
            except Exception as exc:  # noqa: BLE001
                self._fail(f"mất kết nối Soniox: {exc}")
                return
            if not raw:
                self._fail("Soniox đóng kết nối")
                return
            try:
                msg = json.loads(raw)
            except Exception:  # noqa: BLE001
                continue
            if msg.get("error_code") is not None:
                self._fail(str(msg.get("error_message") or msg.get("error_type")))
                return
            self._handle_tokens(msg.get("tokens") or [])
            if msg.get("finished"):
                self._flush_utt()
                self._finished.set()
                return

    def _fail(self, message: str) -> None:
        if self._failed or self._finished.is_set():
            self._finished.set()
            return
        self._failed = True
        self._finished.set()
        # Token is_final là text Soniox ĐÃ xác nhận — giao nốt utterance đang gom
        # trước khi báo lỗi (trim fallback dựa trên last_final_end_ms nên audio
        # đoạn này sẽ bị bỏ, không flush là mất câu). Đuôi nonfinal thì bỏ —
        # local decode lại từ mốc trim.
        self._flush_utt()
        try:
            self.spec.on_error(message)
        except Exception:  # noqa: BLE001
            pass

    def _handle_tokens(self, tokens: list[dict]) -> None:
        changed = False
        self._utt.nonfinal = ""
        for tok in tokens:
            if tok.get("is_final"):
                changed |= self._take_final(tok)
            else:
                self._utt.nonfinal += str(tok.get("text") or "")
        if self._utt.nonfinal:
            changed = True
        if changed and (self._utt.tokens or self._utt.nonfinal):
            self._emit_partial()

    def _take_final(self, tok: dict) -> bool:
        text = str(tok.get("text") or "")
        start = int(tok.get("start_ms") or 0)
        end = int(tok.get("end_ms") or start)
        self.last_final_end_ms = max(self.last_final_end_ms, end)
        if text.strip() == "<end>":  # endpoint detection: dứt câu
            self._flush_utt()
            return False
        utt = self._utt
        gap_break = utt.end_ms >= 0 and start - utt.end_ms >= UTT_GAP_MS
        long_break = utt.start_ms >= 0 and end - utt.start_ms >= MAX_UTT_MS
        if utt.tokens and (gap_break or long_break):
            self._flush_utt()
            utt = self._utt
        if not utt.tokens:
            utt.start_ms = start
            utt.speaker = tok.get("speaker")
        utt.tokens.append(tok)
        utt.end_ms = max(utt.end_ms, end)
        return bool(text)

    def _utt_raw(self) -> str:
        # Giữ nguyên khoảng trắng trong token — chỉ strip ở biên khi hiển thị.
        return "".join(str(t.get("text") or "") for t in self._utt.tokens)

    def _utt_text(self) -> str:
        return self._utt_raw().strip()

    def _emit_partial(self) -> None:
        text = (self._utt_raw() + self._utt.nonfinal).strip()
        start = self._utt.start_ms if self._utt.start_ms >= 0 else self.last_final_end_ms
        if not text:
            return
        try:
            self.spec.on_partial(text, start)
        except Exception:  # noqa: BLE001
            pass

    def _flush_utt(self) -> None:
        text = self._utt_text()
        utt = self._utt
        self._utt = _UttState()
        if not text:
            return
        try:
            self.spec.on_final(text, max(utt.start_ms, 0), utt.speaker)
        except Exception:  # noqa: BLE001
            pass


# ── Async (re-listen sau Stop + upload online) ─────────────────────────────


@dataclass(frozen=True)
class AsyncJobSpec:
    language: str
    context: dict | None = None
    timeout_s: float = 600.0


def _client(client: httpx.Client | None) -> tuple[httpx.Client, bool]:
    if client is not None:
        return client, False
    return httpx.Client(base_url=API_BASE, timeout=60.0), True


def transcribe_file_async(
    path: Path, spec: AsyncJobSpec, client: httpx.Client | None = None
) -> str:
    """Upload WAV → stt-async-v5 → poll → text. LUÔN xóa file + transcription
    phía Soniox trước khi trả (kể cả khi lỗi) — audio không được nằm lại cloud."""
    key = api_key()
    if key is None:
        raise CloudError("thiếu SONIOX_API_KEY")
    http, own = _client(client)
    headers = {"Authorization": f"Bearer {key}"}
    file_id = tid = None
    try:
        with path.open("rb") as f:
            up = http.post("/files", headers=headers,
                           files={"file": (path.name, f, "audio/wav")})
        up.raise_for_status()
        file_id = up.json()["id"]
        job: dict = {"file_id": file_id, "model": ASYNC_MODEL,
                     "language_hints": language_hints(spec.language),
                     "enable_speaker_diarization": True}
        if spec.context:
            job["context"] = spec.context
        r = http.post("/transcriptions", headers=headers, json=job)
        r.raise_for_status()
        tid = r.json()["id"]
        return _poll_transcript(http, headers, tid, spec.timeout_s)
    except httpx.HTTPError as exc:
        raise CloudError(f"async Soniox lỗi: {exc}") from exc
    finally:
        _delete_remote(http, headers, tid=tid, file_id=file_id)
        if own:
            http.close()


def _poll_transcript(
    http: httpx.Client, headers: dict, tid: str, timeout_s: float
) -> str:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        r = http.get(f"/transcriptions/{tid}", headers=headers)
        r.raise_for_status()
        status = r.json().get("status")
        if status == "completed":
            tr = http.get(f"/transcriptions/{tid}/transcript", headers=headers)
            tr.raise_for_status()
            body = tr.json()
            text = body.get("text") or "".join(
                t.get("text", "") for t in body.get("tokens", [])
            )
            return str(text).strip()
        if status == "error":
            raise CloudError(f"job async lỗi: {r.json()}")
        time.sleep(POLL_INTERVAL_S)
    raise CloudError(f"job async quá {timeout_s:.0f}s chưa xong")


def _delete_remote(
    http: httpx.Client, headers: dict, *, tid: str | None, file_id: str | None
) -> None:
    """Best-effort dọn dữ liệu phía Soniox — lỗi xóa không được che lỗi chính."""
    for url in ([f"/transcriptions/{tid}"] if tid else []) + (
        [f"/files/{file_id}"] if file_id else []
    ):
        try:
            http.delete(url, headers=headers)
        except Exception:  # noqa: BLE001
            pass
