"""ContextTracker (US-805) — biến ngữ cảnh đang diễn ra cho pass 2 live.

Không gọi LLM thật: inject fake summarize / fake httpx client. Đồng bộ thread
bằng Event + join (không sleep).
"""
from __future__ import annotations

import threading

from app import correct, live

M = live.CONTEXT_CONDENSE_EVERY
K = live.CONTEXT_RECENT_K


def _fill(tracker: live.ContextTracker, n: int, prefix: str = "câu") -> None:
    for i in range(n):
        tracker.add(f"{prefix} {i} nói về kubernetes")


def test_condense_called_after_m_sentences_and_topic_updates():
    calls: list[tuple[str, str]] = []
    started = threading.Event()

    def fake(topic: str, batch: str) -> str:
        calls.append((topic, batch))
        started.set()
        return "đang bàn deploy Kubernetes"

    tracker = live.ContextTracker(summarize=fake)
    _fill(tracker, M)
    assert started.wait(5)
    tracker.close()  # join thread nền → topic đã ghi xong

    assert len(calls) == 1
    old_topic, batch = calls[0]
    assert old_topic == ""  # lần đầu chưa có topic
    assert "câu 0" in batch and f"câu {M - 1}" in batch  # đủ các câu dồn
    assert "đang bàn deploy Kubernetes" in tracker.context()


def test_condense_error_keeps_old_topic_and_context_still_valid():
    calls: list[int] = []

    def fake(topic: str, batch: str) -> str:
        calls.append(1)
        if len(calls) == 1:
            return "chủ đề A"
        raise RuntimeError("LLM down")

    tracker = live.ContextTracker(summarize=fake)
    _fill(tracker, M)
    assert tracker._thread is not None
    tracker._thread.join(timeout=5)
    assert "chủ đề A" in tracker.context()

    _fill(tracker, M, prefix="đợt hai")  # round 2: summarize raise
    assert tracker._thread is not None
    tracker._thread.join(timeout=5)
    ctx = tracker.context()
    assert "chủ đề A" in ctx  # topic cũ giữ nguyên, không exception
    assert f"đợt hai {M - 1}" in ctx  # recent vẫn cập nhật bình thường
    tracker.close()


def test_context_contains_topic_and_recent_limited_to_k():
    def fake(topic: str, batch: str) -> str:
        return "chủ đề X"

    tracker = live.ContextTracker(summarize=fake)
    _fill(tracker, M)
    assert tracker._thread is not None
    tracker._thread.join(timeout=5)
    tracker.add("câu chốt về CI/CD")

    ctx = tracker.context()
    assert "Chủ đề đang bàn: chủ đề X" in ctx
    assert "câu chốt về CI/CD" in ctx
    # M+1 câu đã add, deque chỉ giữ K câu cuối → các câu đầu rớt khỏi context.
    dropped = M + 1 - K
    for i in range(dropped):
        assert f"câu {i} nói" not in ctx
    assert f"câu {dropped} nói" in ctx
    tracker.close()


def test_new_sentences_use_old_topic_while_condense_in_progress():
    started = threading.Event()
    release = threading.Event()

    def fake(topic: str, batch: str) -> str:
        started.set()
        assert release.wait(10)  # giả LLM chậm — chặn bằng event, không sleep
        return "topic mới"

    tracker = live.ContextTracker(summarize=fake)
    _fill(tracker, M)
    assert started.wait(5)

    # Condense đang treo: add + context không block, vẫn dùng topic cũ (rỗng).
    tracker.add("câu trong lúc condense")
    ctx = tracker.context()
    assert "câu trong lúc condense" in ctx
    assert "topic mới" not in ctx

    release.set()
    tracker.close()
    assert "topic mới" in tracker.context()


def test_close_prevents_new_condense():
    calls: list[int] = []
    tracker = live.ContextTracker(summarize=lambda t, b: calls.append(1) or "x")
    tracker.close()
    _fill(tracker, M * 2)  # sau close: add không kick thread nền nữa
    assert tracker._thread is None
    assert calls == []


# ── on_topic callback + initial_topic (US-806/809) ─────────────────────────


def test_on_topic_called_with_new_topic_after_condense():
    topics: list[str] = []
    tracker = live.ContextTracker(summarize=lambda t, b: "chủ đề mới", on_topic=topics.append)
    _fill(tracker, M)
    tracker.close()
    assert topics == ["chủ đề mới"]


def test_on_topic_raising_does_not_kill_condense():
    def boom(topic: str) -> None:
        raise RuntimeError("refresh hỏng")

    tracker = live.ContextTracker(summarize=lambda t, b: "chủ đề A", on_topic=boom)
    _fill(tracker, M)
    tracker.close()
    assert "chủ đề A" in tracker.context()  # topic vẫn ghi dù callback nổ


def test_on_topic_not_called_when_summarize_fails():
    topics: list[str] = []

    def fail(t: str, b: str) -> str:
        raise RuntimeError("LLM down")

    tracker = live.ContextTracker(summarize=fail, on_topic=topics.append)
    _fill(tracker, M)
    tracker.close()
    assert topics == []


def test_initial_topic_seeds_context_and_condense():
    calls: list[tuple[str, str]] = []

    def fake(topic: str, batch: str) -> str:
        calls.append((topic, batch))
        return "topic trôi"

    tracker = live.ContextTracker(summarize=fake, initial_topic="họp sprint Manju")
    assert tracker.topic() == "họp sprint Manju"
    assert "Chủ đề đang bàn: họp sprint Manju" in tracker.context()  # trước mọi condense
    _fill(tracker, M)
    tracker.close()
    assert calls[0][0] == "họp sprint Manju"  # condense nhận topic mồi làm topic cũ
    assert tracker.topic() == "topic trôi"  # rồi vẫn tự trôi theo cuộc họp


# ── Backend Ollama nhận context (US-805 AC1) ───────────────────────────────


class _FakeResp:
    def __init__(self, payload: dict):
        self._payload = payload

    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict:
        return self._payload


class _FakeClient:
    """Ghi lại payload gửi Ollama, trả lại đúng câu gốc (qua _guard trót lọt)."""

    def __init__(self, content: str):
        self.sent: dict | None = None
        self._content = content

    def post(
        self, url: str, json: dict | None = None, timeout: float | None = None, **kw
    ) -> _FakeResp:
        self.sent = json
        return _FakeResp({"message": {"content": self._content}})

    def __enter__(self) -> _FakeClient:
        return self

    def __exit__(self, *exc: object) -> None:
        pass


def test_ollama_prompt_includes_context_when_present():
    client = _FakeClient("câu cần soát")
    opts = correct.LlmOpts(
        glossary="Kubernetes", context="Chủ đề đang bàn: hạ tầng k8s", num_ctx=4096
    )
    correct._correct_chunk(client, "câu cần soát", opts)  # type: ignore[arg-type]

    assert client.sent is not None
    user_msg = client.sent["messages"][1]["content"]
    assert "Chủ đề đang bàn: hạ tầng k8s" in user_msg
    assert "câu cần soát" in user_msg
    # num_ctx phải set mọi request (server config 262k làm tràn RAM).
    assert client.sent["options"]["num_ctx"] == 4096


def test_ollama_prompt_omits_context_section_when_empty():
    client = _FakeClient("câu cần soát")
    correct._correct_chunk(client, "câu cần soát", correct.LlmOpts(glossary="Kubernetes"))  # type: ignore[arg-type]

    assert client.sent is not None
    user_msg = client.sent["messages"][1]["content"]
    assert "Các câu ngay trước đó" not in user_msg


def test_summarize_topic_sends_old_topic_and_sentences(monkeypatch):
    fake = _FakeClient("chủ đề mới về CI/CD")
    monkeypatch.setattr(correct, "OPENROUTER_API_KEY", "")
    monkeypatch.setattr(correct.httpx, "Client", lambda *a, **k: fake)

    topic = correct.summarize_topic("chủ đề cũ", "câu một. câu hai.", correct.LlmOpts())

    assert topic == "chủ đề mới về CI/CD"
    assert fake.sent is not None
    user_msg = fake.sent["messages"][1]["content"]
    assert "chủ đề cũ" in user_msg
    assert "câu một. câu hai." in user_msg


def test_summarize_topic_returns_none_on_llm_error(monkeypatch):
    def boom(*a: object, **k: object) -> None:
        raise RuntimeError("Ollama tắt")

    monkeypatch.setattr(correct, "OPENROUTER_API_KEY", "")
    monkeypatch.setattr(correct.httpx, "Client", boom)

    assert correct.summarize_topic("cũ", "câu mới", correct.LlmOpts()) is None
