#!/usr/bin/env python3
"""Đo độ chính xác transcript so với bản chuẩn user sửa tay (US-820/821).

Chuẩn = `transcripts.edited_text` của bản ghi đã bật cờ `golden` (đánh dấu
trong màn Bản ghi). Không có bản chuẩn nào → script dừng sạch, vì mọi con số
đo được khi đó chỉ là máy tự chấm điểm máy.

Chạy:
  uv run python scripts/bench_accuracy.py            # T-008: pass 2 giúp hay hại
  uv run python scripts/bench_accuracy.py --sweep    # T-009: quét đệm biên VAD

`--sweep` decode lại audio gốc nên chiếm `engines._decode_lock` — sẽ chặn
partial của phiên live đang mở. Chạy khi không họp.
"""
from __future__ import annotations

import argparse
import json
import sys
import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import accuracy, db  # noqa: E402

# Đệm thử quanh biên utterance. 0.0 = cắt sát như hiện tại; live.py đệm đầu
# PREROLL_S=0.32 nhưng đuôi chỉ 0.2 — bất đối xứng, chưa từng đo.
DEFAULT_PADS = (0.0, 0.2, 0.3, 0.45, 0.6)


@dataclass(frozen=True)
class Score:
    """Điểm của một bản ghi dưới một cấu hình."""

    label: str
    wer: float
    cer: float
    cross_repeat: int = 0  # token lặp chéo 2 segment liền nhau (US-822)


def _load_wav(path: Path) -> np.ndarray:
    with wave.open(str(path)) as w:
        raw = w.readframes(w.getnframes())
    return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0


def _audio_path(row: dict) -> Path | None:
    if not row.get("audio_file"):
        return None
    p = Path(row.get("audio_dir") or "data/recordings") / row["audio_file"]
    return p if p.exists() else None


def _segment_texts(row: dict) -> list[str]:
    try:
        segs = json.loads(row["segments"]) if row.get("segments") else []
    except Exception:  # noqa: BLE001 — segments hỏng không chặn phần đo text
        return []
    return [s.get("text", "") for s in segs]


def score_stored(row: dict) -> list[Score]:
    """T-008: chấm bản máy thô và bản pass 2 so với chuẩn — pass 2 giúp hay hại."""
    ref = row["edited_text"]
    out = []
    if row.get("raw_text"):
        out.append(Score("máy thô", accuracy.wer(ref, row["raw_text"]),
                         accuracy.cer(ref, row["raw_text"]),
                         accuracy.max_cross_repeat(_segment_texts(row))))
    out.append(Score("pass 2", accuracy.wer(ref, row["text"]), accuracy.cer(ref, row["text"])))
    return out


def _decode_windows(audio: np.ndarray, bounds: list[float], pad: float) -> tuple[str, list[str]]:
    """Decode lại từng cửa sổ utterance với đệm `pad` hai đầu."""
    from app import engines

    eng = engines.get_engine()
    spec = engines.DecodeSpec("vi", "", None)
    sr = 16000
    parts: list[str] = []
    for lo, hi in zip(bounds, bounds[1:], strict=False):
        if hi - lo < 0.4:
            continue
        seg = audio[max(0, int((lo - pad) * sr)) : min(len(audio), int((hi + pad) * sr))]
        try:
            parts.append(eng.decode_scored(seg, spec, final=True).text)
        except Exception as exc:  # noqa: BLE001 — 1 cửa sổ hỏng không bỏ cả bản ghi
            print(f"    (bỏ qua cửa sổ {lo:.1f}-{hi:.1f}s: {exc})", file=sys.stderr)
    return " ".join(p.strip() for p in parts if p.strip()), parts


def sweep_pads(row: dict, pads: tuple[float, ...]) -> list[Score]:
    """T-009: decode lại theo từng mức đệm biên, chấm WER so với chuẩn.

    `cross_repeat` là cái giá của việc tăng đệm: đuôi nuốt sang câu kế tiếp
    làm một cụm bị phiên âm vào cả hai segment. Phải đọc kèm WER, không tách.
    """
    path = _audio_path(row)
    if path is None:
        return []
    audio = _load_wav(path)
    try:
        segs = json.loads(row["segments"]) if row.get("segments") else []
    except Exception:  # noqa: BLE001
        segs = []
    if not segs:
        return []
    bounds = [float(s.get("start", 0.0)) for s in segs] + [len(audio) / 16000]
    ref = row["edited_text"]
    out = []
    for pad in pads:
        text, parts = _decode_windows(audio, bounds, pad)
        out.append(Score(f"pad {pad:.2f}s", accuracy.wer(ref, text), accuracy.cer(ref, text),
                         accuracy.max_cross_repeat(parts)))
    return out


def _print_table(title: str, scores: list[Score]) -> None:
    print(f"\n  {title}")
    print(f"    {'cấu hình':<14} {'WER':>8} {'CER':>8} {'lặp chéo':>9}")
    for s in scores:
        print(f"    {s.label:<14} {s.wer:>8.3f} {s.cer:>8.3f} {s.cross_repeat:>9}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Đo độ chính xác transcript vs bản chuẩn")
    ap.add_argument("--sweep", action="store_true", help="decode lại, quét đệm biên VAD (chậm)")
    ap.add_argument("--pads", type=float, nargs="+", default=list(DEFAULT_PADS))
    args = ap.parse_args()

    db.init()
    rows = db.golden_transcripts()
    if not rows:
        print("Chưa có bản chuẩn nào.\n"
              "Mở một bản ghi, sửa cho đúng, bấm 'Lưu bản sửa' rồi 'Dùng làm chuẩn đo'.\n"
              "Cần 3-5 bản để số liệu có ý nghĩa.", file=sys.stderr)
        return 1

    print(f"Bản chuẩn: {len(rows)}  (WER thấp hơn = đúng hơn; >1.0 = máy bịa thêm)")
    for row in rows:
        print(f"\n─ {row['id']}  {row['title']}")
        _print_table("Bản đã lưu", score_stored(row))
        if args.sweep:
            scores = sweep_pads(row, tuple(args.pads))
            if scores:
                _print_table("Decode lại theo đệm biên", scores)
            else:
                print("    (thiếu audio gốc hoặc segments — bỏ qua phần decode lại)")
    if not args.sweep:
        print("\nThêm --sweep để đo ảnh hưởng của đệm biên VAD (decode lại, chậm).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
