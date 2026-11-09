"""FR-11 Enterprise — phiên live "direct": browser tự stream PCM tới Soniox
(temp key), server CHỈ nhận text từng utterance qua WebSocket và lưu transcript.

Không có audio phía server (US-903). Protocol WS (client → server):
  {"type":"start","mode":"direct","language","title","correct"}
  {"type":"utt","utt":N,"text","start_ms","end_ms","speaker"}   idempotent theo utt
  {"type":"resume","token"}  /  {"type":"stop"}
Server → client: session{token,mode:"direct"} · ack_utt{utt} · resumed · saved{transcript_id} · error.

Rớt WS → park RESUME_GRACE_S chờ client nối lại (client replay utterance chưa ack).
"""
from __future__ import annotations

import asyncio
import json
import threading
import uuid
from datetime import UTC, datetime

from fastapi import WebSocket

from app import cloud_stt, corrections, db, engines, transcribe
from app.correct import correct_text, openrouter_enabled

RESUME_GRACE_S = 60.0


class DirectSession:
    def __init__(self, cfg: dict, owner_id: str | None):
        self.language = "en" if cfg.get("language") == "en" else "vi"
        self.title = str(cfg.get("title") or "").strip()
        self.correct_requested = bool(cfg.get("correct", True))
        self.owner_id = owner_id
        self.token = uuid.uuid4().hex
        self.started_at = datetime.now(UTC).astimezone()
        self.utts: dict[int, dict] = {}
        self.saved_tid: str | None = None
        self._lock = threading.Lock()

    def add_utt(self, msg: dict) -> int | None:
        """Ghi 1 utterance final từ client. Trả utt đã nhận (None nếu msg hỏng).
        Trùng utt (replay sau resume) → ghi đè cùng nội dung, idempotent."""
        try:
            utt = int(msg["utt"])
            text = str(msg.get("text") or "").strip()
        except (KeyError, TypeError, ValueError):
            return None
        if not text:
            return utt
        with self._lock:
            self.utts[utt] = {
                "text": text,
                "start_ms": int(msg.get("start_ms") or 0),
                "end_ms": int(msg.get("end_ms") or 0),
                "speaker": msg.get("speaker"),
            }
        return utt

    def _render(self) -> tuple[str, list[dict], float]:
        with self._lock:
            order = sorted(self.utts)
            items = [self.utts[k] for k in order]
        text = engines.collapse_loops(" ".join(u["text"] for u in items)).strip()
        segments = [
            {
                "start": round(u["start_ms"] / 1000.0, 2),
                "end": round(u["end_ms"] / 1000.0, 2),
                "text": u["text"],
                **({"speaker": u["speaker"]} if u["speaker"] else {}),
            }
            for u in items
        ]
        duration = max((u["end_ms"] for u in items), default=0) / 1000.0
        return text, segments, duration

    def _cleanup(self, text: str) -> str:
        """Pass 2 tuỳ chọn (setting cloud_cleanup, mặc định TẮT — PRD FR-10).
        Never-fail: LLM lỗi → giữ bản Soniox."""
        if not (self.correct_requested and db.get_setting("cloud_cleanup", "0") == "1"):
            return text
        if not openrouter_enabled():
            return text
        try:
            glossary = corrections.build_bias(db.get_setting("glossary", "") or "")
            fixed, _ = correct_text(
                text, glossary, pairs=tuple(corrections.top_pairs(10)), backend="openrouter"
            )
            return fixed or text
        except Exception:  # noqa: BLE001
            return text

    def shutdown(self) -> str | None:
        """Chốt & lưu. Gọi 1 lần; không có câu nào → None."""
        if self.saved_tid is not None:
            return self.saved_tid
        raw, segments, duration = self._render()
        if not raw:
            return None
        text = self._cleanup(raw)
        self.saved_tid = transcribe.save_transcript(transcribe.TranscriptDraft(
            original_name=self.title or f"live-{self.started_at:%H%M}",
            language=self.language,
            duration=duration,
            text=text,
            model_name=cloud_stt.RT_MODEL,
            raw_text=raw if raw != text else None,
            segments=segments,
            audio_path=None,
            owner_id=self.owner_id,
        ))
        return self.saved_tid


# ── Resume ─────────────────────────────────────────────────────────────────
_parked: dict[str, tuple[DirectSession, threading.Timer]] = {}
_parked_lock = threading.Lock()


def _park(session: DirectSession) -> None:
    def expire() -> None:
        with _parked_lock:
            gone = _parked.pop(session.token, None)
        if gone is not None:
            session.shutdown()

    timer = threading.Timer(RESUME_GRACE_S, expire)
    timer.daemon = True
    with _parked_lock:
        _parked[session.token] = (session, timer)
    timer.start()


def _claim(token: str | None) -> DirectSession | None:
    if not token:
        return None
    with _parked_lock:
        item = _parked.pop(token, None)
    if item is None:
        return None
    item[1].cancel()
    return item[0]


async def _send(ws: WebSocket, msg: dict) -> None:
    try:
        await ws.send_text(json.dumps(msg, ensure_ascii=False))
    except Exception:  # noqa: BLE001 — client đã rớt
        pass


async def handle(ws: WebSocket, first: dict, user: dict | None) -> None:
    """Vòng nhận cho phiên direct. `first` = message start/resume đã đọc bởi
    live.handle (WS đã accept)."""
    session: DirectSession | None
    if first.get("type") == "resume":
        session = _claim(first.get("token"))
        if session is None:
            await _send(ws, {"type": "error", "message": "Phiên đã kết thúc — bản ghi (nếu có) đã tự lưu."})
            await ws.close()
            return
        await _send(ws, {"type": "resumed", "utts": sorted(session.utts)})
    else:
        session = DirectSession(first, user["id"] if user else None)
        await _send(ws, {"type": "session", "token": session.token, "mode": "direct"})
        await _send(ws, {"type": "ready"})
    finish = False
    try:
        while True:
            msg = await ws.receive()
            if msg["type"] == "websocket.disconnect":
                break
            if not msg.get("text"):
                continue  # direct không nhận PCM — bỏ qua frame binary lạc
            data = json.loads(msg["text"])
            kind = data.get("type")
            if kind == "utt":
                utt = session.add_utt(data)
                if utt is not None:
                    await _send(ws, {"type": "ack_utt", "utt": utt})
            elif kind == "stop":
                finish = True
                break
    finally:
        if finish:
            tid = await asyncio.to_thread(session.shutdown)
            await _send(ws, {"type": "saved", "transcript_id": tid})
        else:
            _park(session)
        try:
            await ws.close()
        except Exception:  # noqa: BLE001
            pass
