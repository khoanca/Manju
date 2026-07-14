"""Pass 3 — speaker diarization qua sherpa-onnx (offline, không token HF).

Wheel sherpa-onnx trên macOS không bundle `libonnxruntime.*.dylib` mà tham chiếu
qua @rpath → tự symlink từ package `onnxruntime` (đã là dep) vào thư mục lib của
sherpa_onnx trước khi import lần đầu. Model tải bằng scripts/fetch_diarize_models.py.
"""
from __future__ import annotations

import importlib.util
import os
import sys
import threading
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
MODEL_DIR = Path(os.environ.get("DIARIZE_MODEL_DIR", ROOT / "models"))
SEG_MODEL = MODEL_DIR / "segmentation.onnx"
EMB_MODEL = MODEL_DIR / "embedding.onnx"
SAMPLE_RATE = 16000
MIN_AUDIO_SEC = 3.0  # dưới ngưỡng: không đủ để tách giọng đáng tin
MIN_SPAN_SEC = 0.5   # đoạn ngắn hơn: bỏ khi tính voiceprint
# Ngưỡng cosine để coi 2 giọng là cùng người (CAM++ zh_en). Cao = ít nhận nhầm.
MATCH_THRESHOLD = float(os.environ.get("DIARIZE_MATCH_THRESHOLD", "0.5"))

_lock = threading.Lock()
_diarizer = None
_extractor = None


def models_present() -> bool:
    """Đã tải đủ model chưa (quyết định có chạy pass 3 hay skip)."""
    return SEG_MODEL.exists() and EMB_MODEL.exists()


def _link_onnxruntime() -> None:
    """Vá bug packaging sherpa-onnx trên macOS: symlink libonnxruntime từ package
    onnxruntime vào thư mục lib của sherpa_onnx (idempotent, no-op ngoài macOS)."""
    if sys.platform != "darwin":
        return
    spec = importlib.util.find_spec("sherpa_onnx")  # KHÔNG import (init sẽ lỗi nếu thiếu lib)
    if spec is None or not spec.origin:
        return
    lib_dir = Path(spec.origin).parent / "lib"
    import onnxruntime

    cap = Path(onnxruntime.__file__).parent / "capi"
    for dylib in cap.glob("libonnxruntime*.dylib"):
        target = lib_dir / dylib.name
        if not target.exists():
            try:
                target.symlink_to(dylib)
            except OSError:
                pass


def _build_config(num_clusters: int):
    _link_onnxruntime()
    import sherpa_onnx as so

    return so.OfflineSpeakerDiarizationConfig(
        segmentation=so.OfflineSpeakerSegmentationModelConfig(
            pyannote=so.OfflineSpeakerSegmentationPyannoteModelConfig(model=str(SEG_MODEL)),
        ),
        embedding=so.SpeakerEmbeddingExtractorConfig(model=str(EMB_MODEL)),
        clustering=so.FastClusteringConfig(num_clusters=num_clusters, threshold=0.5),
    )


def _get_diarizer():
    global _diarizer
    with _lock:
        if _diarizer is None:
            import sherpa_onnx as so

            _diarizer = so.OfflineSpeakerDiarization(_build_config(-1))
        return _diarizer


def diarize_file(audio_path: Path | str, num_speakers: int = -1) -> list[dict] | None:
    """Tách giọng 1 file → [{start, end, spk}] (spk = chỉ số cụm local).
    None nếu thiếu model (caller skip pass 3); [] nếu audio quá ngắn/1 giọng.
    num_speakers > 0: ép số người; -1: tự động (clustering theo ngưỡng)."""
    if not models_present():
        return None
    from faster_whisper.audio import decode_audio

    samples = decode_audio(str(audio_path), sampling_rate=SAMPLE_RATE)
    if samples.size < SAMPLE_RATE * MIN_AUDIO_SEC:
        return []
    diar = _get_diarizer()
    if num_speakers and num_speakers > 0:
        diar.set_config(_build_config(num_speakers))
    try:
        result = diar.process(samples).sort_by_start_time()
    finally:
        if num_speakers and num_speakers > 0:
            diar.set_config(_build_config(-1))  # trả lại chế độ auto cho lần sau
    return [
        {"start": round(s.start, 2), "end": round(s.end, 2), "spk": int(s.speaker)}
        for s in result
    ]


def _seg_end(segments: list[dict], i: int) -> float:
    seg = segments[i]
    if seg.get("end") is not None:
        return float(seg["end"])
    if i + 1 < len(segments):
        return float(segments[i + 1]["start"])
    return float(seg["start"]) + 2.0


def assign_speakers(segments: list[dict], diar_spans: list[dict]) -> list[dict]:
    """Gán `spk` cho từng segment whisper theo cụm diarization overlap nhiều nhất.
    Segment không overlap cụm nào → không có `spk` (không rớt câu)."""
    out: list[dict] = []
    for i, seg in enumerate(segments):
        s0, s1 = float(seg["start"]), _seg_end(segments, i)
        best, best_ov = None, 0.0
        for d in diar_spans:
            ov = min(s1, float(d["end"])) - max(s0, float(d["start"]))
            if ov > best_ov:
                best_ov, best = ov, d["spk"]
        new = dict(seg)
        if best is not None:
            new["spk"] = best
        out.append(new)
    return out


def initial_speaker_map(segments: list[dict]) -> dict:
    """{str(spk): None} cho mỗi cụm xuất hiện — chờ user/voiceprint gán tên."""
    clusters = sorted({s["spk"] for s in segments if s.get("spk") is not None})
    return {str(c): None for c in clusters}


# ── Voiceprint: embedding + so khớp (US-703) ───────────────────────────────
def _get_extractor():
    global _extractor
    with _lock:
        if _extractor is None:
            _link_onnxruntime()
            import sherpa_onnx as so

            _extractor = so.SpeakerEmbeddingExtractor(
                so.SpeakerEmbeddingExtractorConfig(model=str(EMB_MODEL))
            )
        return _extractor


def cluster_spans(segments: list[dict], spk: int) -> list[tuple[float, float]]:
    """Các đoạn (start, end) thuộc cụm giọng `spk` trong 1 transcript."""
    return [
        (float(s["start"]), _seg_end(segments, i))
        for i, s in enumerate(segments)
        if s.get("spk") == spk
    ]


def _embed_samples(samples: np.ndarray, spans: list[tuple[float, float]]) -> np.ndarray | None:
    """Embedding trung bình (L2-normalized) của các đoạn thuộc 1 giọng."""
    ext = _get_extractor()
    vecs: list[np.ndarray] = []
    for a, b in spans:
        seg = samples[int(a * SAMPLE_RATE) : int(b * SAMPLE_RATE)]
        if seg.size < SAMPLE_RATE * MIN_SPAN_SEC:
            continue
        stream = ext.create_stream()
        stream.accept_waveform(SAMPLE_RATE, seg)
        stream.input_finished()
        if ext.is_ready(stream):
            vecs.append(np.asarray(ext.compute(stream), dtype=np.float32))
    if not vecs:
        return None
    mean = np.mean(vecs, axis=0)
    norm = float(np.linalg.norm(mean))
    return (mean / norm).astype(np.float32) if norm else None


def embed_spans(audio_path: Path | str, spans: list[tuple[float, float]]) -> np.ndarray | None:
    """Voiceprint (vector 192 chiều đã chuẩn hoá) của 1 giọng từ file + các đoạn.
    None nếu thiếu model / không đủ audio hợp lệ."""
    if not models_present() or not spans:
        return None
    from faster_whisper.audio import decode_audio

    return _embed_samples(decode_audio(str(audio_path), sampling_rate=SAMPLE_RATE), spans)


def to_np_voiceprints(raw: list[tuple[str, bytes]]) -> list[tuple[str, np.ndarray]]:
    """DB bytes → vector numpy cho so khớp (giữ db.py không phụ thuộc numpy)."""
    return [(sid, np.frombuffer(b, dtype=np.float32)) for sid, b in raw]


def best_match(
    vec: np.ndarray, voiceprints: list[tuple[str, np.ndarray]], threshold: float = MATCH_THRESHOLD
) -> str | None:
    """speaker_id có cosine cao nhất & ≥ ngưỡng (vector đã chuẩn hoá → dot = cosine)."""
    best, best_score = None, -1.0
    for sid, ref in voiceprints:
        score = float(np.dot(vec, ref))
        if score > best_score:
            best_score, best = score, sid
    return best if best_score >= threshold else None


def merge_centroid(
    old: np.ndarray | None, old_count: int, new: np.ndarray
) -> tuple[np.ndarray, int]:
    """Gộp mẫu voiceprint mới vào centroid cũ (bình quân theo số mẫu, chuẩn hoá lại)."""
    if old is None or old_count <= 0:
        return new, 1
    combined = old * old_count + new
    norm = float(np.linalg.norm(combined))
    vec = (combined / norm).astype(np.float32) if norm else new
    return vec, old_count + 1


def identify_clusters(
    audio_path: Path | str, segments: list[dict], speaker_map: dict,
    voiceprints: list[tuple[str, np.ndarray]], threshold: float = MATCH_THRESHOLD,
) -> dict:
    """Tự điền speaker_map: mỗi cụm giọng → speaker_id nếu voiceprint khớp (US-703 AC2).
    Chỉ điền ô đang None (không ghi đè tên user đã gán)."""
    if not voiceprints:
        return speaker_map
    from faster_whisper.audio import decode_audio

    samples = decode_audio(str(audio_path), sampling_rate=SAMPLE_RATE)
    for spk in sorted({s["spk"] for s in segments if s.get("spk") is not None}):
        if speaker_map.get(str(spk)) is not None:
            continue
        vec = _embed_samples(samples, cluster_spans(segments, spk))
        if vec is None:
            continue
        sid = best_match(vec, voiceprints, threshold)
        if sid:
            speaker_map[str(spk)] = sid
    return speaker_map
