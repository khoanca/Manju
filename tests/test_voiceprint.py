"""Voiceprint (US-703) — match/merge thuần + round-trip enroll→match (guarded)."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from app import diarize


def _unit(v) -> np.ndarray:
    v = np.asarray(v, dtype=np.float32)
    return v / np.linalg.norm(v)


def test_best_match_above_threshold_returns_closest():
    vps = [("A", _unit([1, 0, 0])), ("B", _unit([0, 1, 0]))]
    assert diarize.best_match(_unit([0.9, 0.1, 0]), vps, 0.5) == "A"


def test_best_match_below_threshold_returns_none():
    assert diarize.best_match(_unit([0, 1, 0]), [("A", _unit([1, 0, 0]))], 0.5) is None


def test_merge_centroid_first_sample():
    a = _unit([1, 0, 0])
    vec, count = diarize.merge_centroid(None, 0, a)
    assert count == 1
    np.testing.assert_allclose(vec, a, atol=1e-6)


def test_merge_centroid_weighted_and_normalized():
    a, b = _unit([1, 0, 0]), _unit([0, 1, 0])
    vec, count = diarize.merge_centroid(a, 1, b)
    assert count == 2
    assert abs(float(np.linalg.norm(vec)) - 1.0) < 1e-5
    # centroid cân giữa a và b → cosine với cả hai xấp xỉ nhau
    assert abs(float(np.dot(vec, a)) - float(np.dot(vec, b))) < 1e-5


def test_to_np_voiceprints_roundtrip():
    a = _unit([1, 2, 3])
    out = diarize.to_np_voiceprints([("A", a.tobytes())])
    np.testing.assert_allclose(out[0][1], a, atol=1e-6)


def test_identify_clusters_no_voiceprints_noop():
    smap = {"0": None}
    assert diarize.identify_clusters("x.wav", [{"spk": 0}], smap, []) == {"0": None}


@pytest.mark.skipif(not diarize.models_present(), reason="model diarization chưa tải")
def test_enroll_then_match_self():
    """Học voiceprint 1 cụm rồi nhận diện lại chính nó → phải tự khớp."""
    recs = sorted(Path("data/recordings").glob("*.wav"))
    if not recs:
        pytest.skip("không có recording mẫu")
    f = recs[-1]
    spans = diarize.diarize_file(f)
    if not spans:
        pytest.skip("không tách được giọng")
    segs = [{"start": s["start"], "end": s["end"], "text": "x", "spk": s["spk"]} for s in spans]
    spk0 = sorted({s["spk"] for s in segs})[0]
    vec = diarize.embed_spans(f, diarize.cluster_spans(segs, spk0))
    assert vec is not None and abs(float(np.linalg.norm(vec)) - 1.0) < 1e-4
    out = diarize.identify_clusters(f, segs, {str(spk0): None}, [("spk-A", vec)], threshold=0.5)
    assert out[str(spk0)] == "spk-A"  # cosine với chính nó ~1.0 → khớp
