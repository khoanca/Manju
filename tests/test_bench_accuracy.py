"""Script đo độ chính xác (US-820/821) — không chạy ASR thật, dùng engine giả."""
from __future__ import annotations

import importlib.util
import sys
import wave
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("bench_accuracy", ROOT / "scripts" / "bench_accuracy.py")
bench = importlib.util.module_from_spec(_spec)
sys.modules["bench_accuracy"] = bench
_spec.loader.exec_module(bench)


def _row(**over):
    base = {
        "id": "20260101-000000-hop", "title": "Họp",
        "edited_text": "triển khai Kubernetes rồi deploy",
        "text": "triển khai Kubernetes rồi deploy",
        "raw_text": "triển khai cu bơ nét ét rồi đíp lôi",
        "segments": None, "audio_file": None, "audio_dir": None,
    }
    return {**base, **over}


def test_score_stored_bat_pass2_giup():
    scores = {s.label: s for s in bench.score_stored(_row())}
    assert scores["pass 2"].wer == 0.0          # pass 2 khớp chuẩn
    assert scores["máy thô"].wer > 0.0          # bản thô sai thuật ngữ
    assert scores["máy thô"].wer > scores["pass 2"].wer


def test_score_stored_bat_pass2_lam_hai():
    # Pass 2 sửa quá tay thành sai hơn bản thô — bộ đo phải chỉ ra được.
    row = _row(raw_text="triển khai Kubernetes rồi deploy", text="hoàn toàn khác hẳn nội dung")
    scores = {s.label: s for s in bench.score_stored(row)}
    assert scores["máy thô"].wer == 0.0
    assert scores["pass 2"].wer > scores["máy thô"].wer


def test_score_stored_khong_co_raw_text():
    scores = bench.score_stored(_row(raw_text=None))
    assert [s.label for s in scores] == ["pass 2"]


def test_score_stored_bao_lap_cheo_segment():
    row = _row(segments='[{"text": "mình đi là đáng chí"}, {"text": "đáng chí dạng tới"}]')
    assert {s.label: s for s in bench.score_stored(row)}["máy thô"].cross_repeat == 2


def test_score_stored_segments_hong_khong_chan_do_text():
    scores = {s.label: s for s in bench.score_stored(_row(segments="{khong phai json"))}
    assert scores["pass 2"].wer == 0.0


@pytest.fixture
def wav_row(tmp_path):
    path = tmp_path / "a.wav"
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes((np.zeros(16000 * 6, dtype=np.int16)).tobytes())
    return _row(
        audio_file="a.wav", audio_dir=str(tmp_path),
        segments='[{"start": 0.0}, {"start": 2.0}, {"start": 4.0}]',
    )


def test_sweep_pads_cham_diem_tung_muc_dem(wav_row, monkeypatch):
    from app import engines

    class FakeEngine:
        def decode_scored(self, audio, spec, *, final):
            # Trả text tỉ lệ với độ dài cửa sổ → đệm lớn hơn ra text dài hơn.
            return engines.DecodeResult("triển khai" if len(audio) > 32000 else "triển")

    monkeypatch.setattr(engines, "get_engine", lambda: FakeEngine())
    scores = bench.sweep_pads(wav_row, (0.0, 0.5))
    assert [s.label for s in scores] == ["pad 0.00s", "pad 0.50s"]
    assert all(s.wer >= 0.0 for s in scores)


def test_sweep_pads_thieu_audio_tra_rong():
    assert bench.sweep_pads(_row(), (0.0,)) == []


def test_sweep_pads_thieu_segments_tra_rong(wav_row):
    assert bench.sweep_pads({**wav_row, "segments": None}, (0.0,)) == []


def test_sweep_pads_bo_qua_cua_so_hong(wav_row, monkeypatch):
    from app import engines

    class BrokenEngine:
        def decode_scored(self, audio, spec, *, final):
            raise RuntimeError("engine bận")

    monkeypatch.setattr(engines, "get_engine", lambda: BrokenEngine())
    # Mọi cửa sổ hỏng → text rỗng, WER = 1.0 (chuẩn có chữ mà máy ra rỗng), không raise.
    scores = bench.sweep_pads(wav_row, (0.0,))
    assert scores[0].wer == 1.0
