"""FR-10 T-008: unit cloud_stt — utterance grouping, context builder, async job.

Không mạng thật: SonioxLive nhận transport fake (kịch bản token script sẵn),
async client dùng httpx.MockTransport. Đồng bộ bằng Event, không sleep cố định.
"""
from __future__ import annotations

import json
import queue
import threading

import httpx
import pytest

from app import cloud_stt

# ── build_context ──────────────────────────────────────────────────────────


def test_build_context_glossary_first_and_dedup():
    ctx = cloud_stt.build_context(
        "Kubernetes, RAG\nembedding", [("mô độ", "model"), ("rác", "RAG")]
    )
    assert ctx == {"terms": ["Kubernetes", "RAG", "embedding", "model"]}


def test_build_context_empty_returns_none():
    assert cloud_stt.build_context("", []) is None
    assert cloud_stt.build_context(" , ,\n", []) is None


def test_build_context_caps_corrections_keeps_glossary():
    long_terms = [(f"w{i}", "t" * 500) for i in range(40)]  # ~20k chars phía corrections
    ctx = cloud_stt.build_context("Claude", long_terms)
    assert ctx is not None
    assert ctx["terms"][0] == "Claude"
    assert sum(len(t) for t in ctx["terms"]) <= cloud_stt.CONTEXT_MAX_CHARS


# ── SonioxLive với transport fake ──────────────────────────────────────────


class FakeWS:
    """Transport script sẵn: recv() phát từng message trong kịch bản; send ghi lại."""

    def __init__(self, script: list[dict]):
        self.incoming: queue.Queue[str | bytes] = queue.Queue()
        for msg in script:
            self.incoming.put(json.dumps(msg))
        self.sent: list[str] = []
        self.sent_binary: list[bytes] = []
        self.closed = False

    def send(self, data: str):
        self.sent.append(data)

    def send_binary(self, data: bytes):
        self.sent_binary.append(data)
        if data == b"":  # frame rỗng = hết audio → server chốt rồi finished
            self.incoming.put(json.dumps({"tokens": [], "finished": True}))

    def recv(self) -> str | bytes:
        return self.incoming.get(timeout=2)

    def close(self):
        self.closed = True
        self.incoming.put("")  # recv đang chờ thấy EOF


def tok(text: str, start: int, end: int, *, final=True, speaker=None) -> dict:
    t: dict = {"text": text, "start_ms": start, "end_ms": end, "is_final": final}
    if speaker is not None:
        t["speaker"] = speaker
    return t


@pytest.fixture
def run_live(monkeypatch):
    monkeypatch.setenv("SONIOX_API_KEY", "test-key")

    def run(script: list[dict], *, stop=True):
        ws = FakeWS(script)
        partials: list[tuple[str, int]] = []
        finals: list[tuple[str, int, str | None]] = []
        errors: list[str] = []
        done = threading.Event()
        spec = cloud_stt.LiveCloudSpec(
            language="vi",
            context={"terms": ["Claude"]},
            on_partial=lambda t, s: partials.append((t, s)),
            on_final=lambda t, s, spk: finals.append((t, s, spk)),
            on_error=lambda m: (errors.append(m), done.set()),
            connect=lambda: ws,
        )
        worker = cloud_stt.SonioxLive(spec)
        worker.start()
        if stop:
            worker.stop(timeout=3)
        return worker, ws, partials, finals, errors

    return run


def test_config_sent_first_with_context(run_live):
    _, ws, _, _, _ = run_live([])
    cfg = json.loads(ws.sent[0])
    assert cfg["model"] == cloud_stt.RT_MODEL
    assert cfg["language_hints"] == ["vi", "en"]
    assert cfg["context"] == {"terms": ["Claude"]}
    assert cfg["enable_speaker_diarization"] is True


def test_utterances_split_on_gap_and_speaker_kept(run_live):
    script = [
        {"tokens": [tok("xin ", 0, 200, speaker="1"), tok("chào", 200, 400)]},
        # gap 400 → 1300 >= 700ms → utterance mới
        {"tokens": [tok("deploy ", 1300, 1500, speaker="2"), tok("model", 1500, 1800)]},
    ]
    worker, _, _, finals, errors = run_live(script)
    assert errors == []
    assert finals == [
        ("xin chào", 0, "1"),
        ("deploy model", 1300, "2"),  # utterance cuối flush khi stop/finished
    ]
    assert worker.last_final_end_ms == 1800


def test_partial_mixes_final_and_nonfinal(run_live):
    script = [
        {"tokens": [tok("xin ", 0, 200), tok("ch", 200, 300, final=False)]},
    ]
    _, _, partials, finals, _ = run_live(script)
    assert ("xin ch", 0) in partials
    assert finals == [("xin", 0, None)]  # nonfinal không vào final


def test_end_token_closes_utterance(run_live):
    script = [
        {"tokens": [tok("một câu", 0, 500), tok("<end>", 500, 500)]},
        {"tokens": [tok("câu hai", 600, 900)]},  # gap < 700 nhưng đã có <end>
    ]
    _, _, _, finals, _ = run_live(script)
    assert [f[0] for f in finals] == ["một câu", "câu hai"]


def test_error_message_triggers_on_error_once(run_live):
    script = [
        {"tokens": [tok("nửa đầu", 0, 300)]},
        {"error_code": 402, "error_type": "organization_balance_exhausted",
         "error_message": "hết tiền"},
    ]
    worker, _, _, finals, errors = run_live(script, stop=False)
    assert worker._finished.wait(2)
    assert errors == ["hết tiền"]
    assert worker.last_final_end_ms == 300  # mốc trim cho fallback
    # Token final đã xác nhận phải được giao TRƯỚC on_error — trim fallback bỏ
    # audio đoạn này, không flush là mất câu.
    assert finals == [("nửa đầu", 0, None)]


def test_stop_sends_empty_frame_and_flushes(run_live):
    _, ws, _, finals, _ = run_live([{"tokens": [tok("cuối", 0, 300)]}])
    assert b"" in ws.sent_binary
    assert finals == [("cuối", 0, None)]


def test_missing_key_raises(monkeypatch):
    monkeypatch.delenv("SONIOX_API_KEY", raising=False)
    spec = cloud_stt.LiveCloudSpec(
        "vi", None, lambda *_: None, lambda *_: None, lambda *_: None
    )
    with pytest.raises(cloud_stt.CloudError):
        cloud_stt.SonioxLive(spec).start()
    assert cloud_stt.available() is False


# ── Async: upload → poll → transcript → DELETE remote ──────────────────────


def _async_handler(state: dict, *, fail_poll=False):
    def handler(request: httpx.Request) -> httpx.Response:
        path, method = request.url.path.removeprefix("/v1"), request.method
        state.setdefault("calls", []).append((method, path))
        if method == "POST" and path == "/files":
            return httpx.Response(200, json={"id": "f1"})
        if method == "POST" and path == "/transcriptions":
            state["job"] = json.loads(request.content)
            return httpx.Response(200, json={"id": "t1"})
        if method == "GET" and path == "/transcriptions/t1":
            if fail_poll:
                return httpx.Response(200, json={"status": "error", "message": "hỏng"})
            return httpx.Response(200, json={"status": "completed"})
        if method == "GET" and path == "/transcriptions/t1/transcript":
            return httpx.Response(200, json={"text": "bản chuẩn hơn"})
        if method == "DELETE":
            return httpx.Response(200, json={})
        return httpx.Response(404)

    return handler


def _mock_client(state: dict, **kw) -> httpx.Client:
    return httpx.Client(
        base_url=cloud_stt.API_BASE, transport=httpx.MockTransport(_async_handler(state, **kw))
    )


def test_async_full_lifecycle_deletes_remote(tmp_path, monkeypatch):
    monkeypatch.setenv("SONIOX_API_KEY", "k")
    wav = tmp_path / "a.wav"
    wav.write_bytes(b"RIFFfake")
    state: dict = {}
    text = cloud_stt.transcribe_file_async(
        wav, cloud_stt.AsyncJobSpec(language="vi", context={"terms": ["Claude"]}),
        client=_mock_client(state),
    )
    assert text == "bản chuẩn hơn"
    assert state["job"]["model"] == cloud_stt.ASYNC_MODEL
    assert state["job"]["context"] == {"terms": ["Claude"]}
    # Privacy FR-10: xóa cả transcription lẫn file sau khi nhận.
    assert ("DELETE", "/transcriptions/t1") in state["calls"]
    assert ("DELETE", "/files/f1") in state["calls"]


def test_async_error_still_deletes_remote(tmp_path, monkeypatch):
    monkeypatch.setenv("SONIOX_API_KEY", "k")
    wav = tmp_path / "a.wav"
    wav.write_bytes(b"RIFFfake")
    state: dict = {}
    with pytest.raises(cloud_stt.CloudError):
        cloud_stt.transcribe_file_async(
            wav, cloud_stt.AsyncJobSpec(language="vi"),
            client=_mock_client(state, fail_poll=True),
        )
    assert ("DELETE", "/transcriptions/t1") in state["calls"]
    assert ("DELETE", "/files/f1") in state["calls"]
