"""Load .env ở gốc repo trước khi các module con đọc os.environ lúc import.

Không dùng python-dotenv để khỏi thêm dep — chỉ cần KEY=value từng dòng.
Biến đã có trong môi trường (export tay) được ưu tiên, không ghi đè.
"""
from __future__ import annotations

import os
from pathlib import Path

_env_file = Path(__file__).resolve().parent.parent / ".env"
if _env_file.exists():
    for _line in _env_file.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if not _line or _line.startswith("#") or "=" not in _line:
            continue
        _key, _value = _line.split("=", 1)
        os.environ.setdefault(_key.strip(), _value.strip().strip("'\""))
