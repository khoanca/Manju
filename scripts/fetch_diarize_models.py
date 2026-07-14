#!/usr/bin/env python3
"""Tải model cho lớp speaker (pass 3) từ GitHub release sherpa-onnx.

Offline sau khi tải; KHÔNG cần token/account (khác pyannote gốc trên HuggingFace).
Idempotent — bỏ qua nếu file đích đã tồn tại. Chạy: `uv run python scripts/fetch_diarize_models.py`.

Đích (khớp app/diarize.py):
  models/segmentation.onnx  — pyannote-segmentation-3.0 (phân đoạn ai-nói-lúc-nào)
  models/embedding.onnx     — 3D-Speaker CAM++ zh+en (voiceprint, đa ngữ hợp giọng Việt xen Anh)
"""
from __future__ import annotations

import sys
import tarfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODEL_DIR = ROOT / "models"
BASE = "https://github.com/k2-fsa/sherpa-onnx/releases/download"

SEG_ARCHIVE = f"{BASE}/speaker-segmentation-models/sherpa-onnx-pyannote-segmentation-3-0.tar.bz2"
SEG_INNER = "sherpa-onnx-pyannote-segmentation-3-0/model.onnx"
EMB_URL = (
    f"{BASE}/speaker-recongition-models/"
    "3dspeaker_speech_campplus_sv_zh_en_16k-common_advanced.onnx"
)


def _download(url: str, dest: Path) -> None:
    print(f"↓ {url}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url) as r, dest.open("wb") as f:  # noqa: S310 (URL cố định)
        while chunk := r.read(1 << 20):
            f.write(chunk)


def _fetch_segmentation(target: Path) -> None:
    if target.exists():
        print(f"✓ {target.name} đã có")
        return
    archive = MODEL_DIR / "segmentation.tar.bz2"
    _download(SEG_ARCHIVE, archive)
    with tarfile.open(archive, "r:bz2") as tar:
        member = tar.getmember(SEG_INNER)
        member.name = target.name  # giải nén phẳng ra models/segmentation.onnx
        tar.extract(member, MODEL_DIR, filter="data")
    archive.unlink(missing_ok=True)
    print(f"✓ {target.name}")


def _fetch_embedding(target: Path) -> None:
    if target.exists():
        print(f"✓ {target.name} đã có")
        return
    _download(EMB_URL, target)
    print(f"✓ {target.name}")


def main() -> int:
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    _fetch_segmentation(MODEL_DIR / "segmentation.onnx")
    _fetch_embedding(MODEL_DIR / "embedding.onnx")
    print("Xong. Bật diarize trong Cài đặt để dùng.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
