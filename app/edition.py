"""Edition của bản cài (FR-11).

`MANJU_EDITION=enterprise` → bản công ty tự host: multi-user, audio không chạm
server, chỉ Soniox (không tier local). Mặc định (`local`) = hành vi Manju cũ,
không đổi gì. Đọc env mỗi lần gọi để test monkeypatch được.
"""
from __future__ import annotations

import os


def edition() -> str:
    return "enterprise" if os.environ.get("MANJU_EDITION", "").lower() == "enterprise" else "local"


def is_enterprise() -> bool:
    return edition() == "enterprise"
