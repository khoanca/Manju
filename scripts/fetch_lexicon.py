#!/usr/bin/env python3
"""Tải & nhập thư viện sửa lỗi từ nguồn remote (US-804) — bản CLI của
`POST /api/lexicon/update`.

Chạy: `uv run python scripts/fetch_lexicon.py [URL]`
Không truyền URL → đọc setting `lexicon_url` (cấu hình trong Cài đặt → Thư viện từ).
Format nguồn 2 file: `{url}` = JSON `[{"wrong","right","tag"?}]`,
`{url}.sha256` = hex sha256 của raw bytes. Verify xong mới nhập
(INSERT OR IGNORE — không đè entry user); lỗi → bảng không đổi.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import corrections, db  # noqa: E402 — cần sys.path trỏ về repo trước


def main() -> int:
    db.init()
    url = sys.argv[1] if len(sys.argv) > 1 else (db.get_setting("lexicon_url", "") or "").strip()
    if not url:
        print("Chưa cấu hình nguồn: truyền URL làm tham số hoặc set lexicon_url trong Cài đặt.")
        return 1
    try:
        entries = corrections.fetch_remote_lexicon(url)
    except corrections.LexiconError as exc:
        print(f"Lỗi: {exc}")
        return 1
    print(f"Đã nhập {corrections.import_remote(entries)} cặp mới.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
