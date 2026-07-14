"""Xuất phụ đề SRT/VTT — hàm thuần, kiểm định dạng + fallback end + nhãn speaker."""
from __future__ import annotations

from app import subtitle

SEGS = [
    {"start": 0.0, "end": 2.5, "text": "Chào mọi người", "spk": 0},
    {"start": 2.5, "end": 5.0, "text": "Triển khai Kubernetes", "spk": 1},
]


def test_srt_format_and_index():
    out = subtitle.render(SEGS)
    assert out.startswith("1\n00:00:00,000 --> 00:00:02,500\nChào mọi người")
    assert "2\n00:00:02,500 --> 00:00:05,000\nTriển khai Kubernetes" in out


def test_vtt_header_and_dot_separator():
    out = subtitle.render(SEGS, vtt=True)
    assert out.startswith("WEBVTT\n\n")
    assert "00:00:00.000 --> 00:00:02.500" in out
    assert "-->" in out and "," not in out.split("\n")[2]


def test_speaker_labels_prefix():
    out = subtitle.render(SEGS, {0: "An", 1: "Bình"})
    assert "An: Chào mọi người" in out
    assert "Bình: Triển khai Kubernetes" in out


def test_end_fallback_uses_next_start_then_plus_two():
    segs = [{"start": 1.0, "text": "một"}, {"start": 3.0, "text": "hai"}]
    out = subtitle.render(segs)
    # cue 1: end = start câu kế (3.0); cue 2: end = start + 2 (5.0)
    assert "00:00:01,000 --> 00:00:03,000" in out
    assert "00:00:03,000 --> 00:00:05,000" in out


def test_empty_text_segments_skipped():
    segs = [{"start": 0.0, "end": 1.0, "text": "  "}, {"start": 1.0, "end": 2.0, "text": "ok"}]
    out = subtitle.render(segs)
    assert out.strip() == "1\n00:00:01,000 --> 00:00:02,000\nok"


def test_hours_formatting():
    out = subtitle.render([{"start": 3661.0, "end": 3662.0, "text": "x"}])
    assert "01:01:01,000 --> 01:01:02,000" in out
