"""MCP server (stdio) cho Manju Enterprise — chạy trên MÁY NHÂN VIÊN, gọi HTTP
API của server công ty bằng API token cá nhân (FR-11 US-905). DB nằm trên server
nên không đọc SQLite trực tiếp như mcp_server/server.py.

Env: MANJU_URL (vd https://manju.congty.vn), MANJU_TOKEN (tạo trong Settings →
"API token cho LLM"). Quyền = đúng quyền của người giữ token (member: bản của
mình; admin: tất cả) — server áp, client không tự quyết.

Đăng ký vào Claude Code:
    claude mcp add manju-enterprise -e MANJU_URL=https://... -e MANJU_TOKEN=mj_... -- \
        uv --directory /path/to/manju run python mcp_server/enterprise.py
"""
from __future__ import annotations

import os

import httpx
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("manju-enterprise")


def _client() -> httpx.Client:
    url = os.environ.get("MANJU_URL", "").rstrip("/")
    token = os.environ.get("MANJU_TOKEN", "")
    if not url or not token:
        raise RuntimeError("Thiếu MANJU_URL hoặc MANJU_TOKEN")
    return httpx.Client(base_url=url, headers={"Authorization": f"Bearer {token}"}, timeout=30.0)


def _get(path: str, params: dict | None = None) -> dict | list:
    with _client() as c:
        r = c.get(path, params=params)
        if r.status_code == 401:
            raise RuntimeError("Token không hợp lệ hoặc đã bị xoá")
        if r.status_code == 403:
            raise RuntimeError(r.json().get("detail", "Không có quyền"))
        r.raise_for_status()
        return r.json()


@mcp.tool()
def list_transcripts() -> list[dict]:
    """Liệt kê các cuộc họp bạn được xem (id, tiêu đề, ngôn ngữ, thời lượng, ngày)."""
    rows = _get("/api/transcripts")
    assert isinstance(rows, list)
    return [
        {k: r.get(k) for k in ("id", "title", "language", "duration", "created_at", "words")}
        for r in rows
    ]


@mcp.tool()
def search_transcripts(query: str) -> list[dict]:
    """Tìm cuộc họp có tiêu đề hoặc nội dung chứa `query` (không phân biệt hoa thường)."""
    rows = _get("/api/transcripts", {"q": query})
    assert isinstance(rows, list)
    return [{k: r.get(k) for k in ("id", "title", "created_at", "duration")} for r in rows]


@mcp.tool()
def read_transcript(transcript_id: str) -> dict:
    """Đọc toàn bộ text một cuộc họp theo id (bản user đã sửa tay nếu có).

    Dùng `list_transcripts` / `search_transcripts` để lấy id trước.
    """
    data = _get(f"/api/transcripts/{transcript_id}")
    assert isinstance(data, dict)
    text = data.get("edited_text") or data.get("text") or ""
    return {
        "id": data.get("id"), "title": data.get("title"), "created_at": data.get("created_at"),
        "duration": data.get("duration"), "text": text, "segments": data.get("segments"),
    }


if __name__ == "__main__":
    mcp.run()
