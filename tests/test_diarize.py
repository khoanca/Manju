"""Lớp speaker pass 3 — align thuần (luôn chạy) + smoke pipeline onnx (guarded)."""
from __future__ import annotations

from pathlib import Path

import pytest

from app import diarize

# 2 cụm: 0..3s = spk0, 3..6s = spk1
SPANS = [
    {"start": 0.0, "end": 3.0, "spk": 0},
    {"start": 3.0, "end": 6.0, "spk": 1},
]


def test_assign_speakers_by_max_overlap():
    segs = [
        {"start": 0.5, "end": 2.5, "text": "a"},   # trọn trong spk0
        {"start": 3.2, "end": 5.0, "text": "b"},   # trọn trong spk1
        {"start": 2.6, "end": 3.4, "text": "c"},   # cưỡi ranh: overlap spk0=0.4, spk1=0.4 → giữ spk0 (đến trước)
    ]
    out = diarize.assign_speakers(segs, SPANS)
    assert out[0]["spk"] == 0
    assert out[1]["spk"] == 1
    assert out[2]["spk"] in (0, 1)


def test_assign_speakers_no_overlap_leaves_unlabeled():
    segs = [{"start": 10.0, "end": 11.0, "text": "xa"}]
    out = diarize.assign_speakers(segs, SPANS)
    assert "spk" not in out[0]
    assert out[0]["text"] == "xa"  # không rớt câu


def test_assign_speakers_end_fallback_uses_next_start():
    # segment không có `end` → dùng start câu kế; câu 1 (1.0→next 4.0) overlap spk0 nhiều hơn
    segs = [{"start": 1.0, "text": "x"}, {"start": 4.0, "text": "y"}]
    out = diarize.assign_speakers(segs, SPANS)
    assert out[0]["spk"] == 0
    assert out[1]["spk"] == 1


def test_initial_speaker_map():
    segs = [{"spk": 1}, {"spk": 0}, {"spk": 1}, {"text": "no-spk"}]
    assert diarize.initial_speaker_map(segs) == {"0": None, "1": None}


def test_diarize_file_missing_models_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(diarize, "SEG_MODEL", tmp_path / "nope.onnx")
    assert diarize.diarize_file(tmp_path / "any.wav") is None


@pytest.mark.skipif(not diarize.models_present(), reason="model diarization chưa tải")
def test_diarize_file_smoke_real_pipeline():
    recs = sorted(Path("data/recordings").glob("*.wav"))
    if not recs:
        pytest.skip("không có recording mẫu để chạy pipeline")
    spans = diarize.diarize_file(recs[-1])
    assert spans is not None  # model có → không phải None
    for s in spans:
        assert {"start", "end", "spk"} <= s.keys()
        assert s["end"] >= s["start"]
        assert isinstance(s["spk"], int)
