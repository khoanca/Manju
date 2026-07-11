"""Pass 2 (correct.py) — logic thuần, không gọi LLM."""
from __future__ import annotations

from app import correct


def test_split_chunks_respects_sentence_boundaries():
    text = "Câu một. Câu hai! Câu ba? Câu bốn."
    chunks = correct._split_chunks(text, size=12)
    # Mỗi chunk ghép nguyên câu, không cắt giữa câu.
    assert len(chunks) >= 2
    assert " ".join(chunks).replace("  ", " ") == text
    assert all(c.strip() for c in chunks)


def test_clean_output_strips_think_and_code_fence():
    assert correct._clean_output("<think>lan man</think>Kết quả") == "Kết quả"
    assert correct._clean_output("```\nKubernetes deploy\n```") == "Kubernetes deploy"
    assert correct._clean_output("```text\nabc\n```") == "abc"


def test_guard_keeps_original_when_empty():
    assert correct._guard("bản gốc", "") == "bản gốc"


def test_guard_keeps_original_when_over_edited():
    # Khác gốc quá MIN_SIMILARITY (0.6) → coi là "sửa quá tay", giữ gốc.
    original = "một hai ba bốn năm"
    mangled = "hoàn toàn khác biệt không liên quan gì cả xyz"
    assert correct._guard(original, mangled) == original


def test_guard_accepts_minor_fix():
    original = "chúng ta cu bơ nét trên cloud"
    fixed = "chúng ta Kubernetes trên cloud"
    assert correct._guard(original, fixed) == fixed


def test_prompt_for_includes_glossary_and_context():
    out = correct._prompt_for("text cần soát", glossary="Kubernetes, deploy", context="câu trước")
    assert "Kubernetes, deploy" in out
    assert "câu trước" in out
    assert "text cần soát" in out


def test_llm_model_name_follows_backend(monkeypatch):
    monkeypatch.setattr(correct, "llm_backend", lambda: "ollama")
    assert correct.llm_model_name() == correct.OLLAMA_MODEL
    monkeypatch.setattr(correct, "llm_backend", lambda: "cloud")
    assert correct.llm_model_name() == correct.CLOUD_LLM_MODEL
