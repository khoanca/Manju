"""MCP server — cho Claude đọc các transcript cuộc họp đã có.

Đọc SQLite `data/manju.db` (nguồn chân lý, xem app/db.py) ở chế độ read-only
— WAL + busy_timeout nên không lock chéo với app đang chạy.

Đăng ký vào Claude Code:
    claude mcp add meeting-transcripts -- \
        uv --directory /Users/m/Manju run python mcp_server/server.py
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from mcp.server.fastmcp import FastMCP

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "manju.db"

mcp = FastMCP("meeting-transcripts")


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=5)
    conn.execute("PRAGMA busy_timeout=5000")
    conn.row_factory = sqlite3.Row
    return conn


@mcp.tool()
def list_transcripts() -> list[dict]:
    """Liệt kê các cuộc họp đã transcribe (id, tiêu đề, ngôn ngữ, thời gian)."""
    if not DB_PATH.exists():
        return []
    with _connect() as conn:
        rows = conn.execute(
            """SELECT id, title, language, model, duration, created_at,
                      chars, words, corrected
               FROM transcripts ORDER BY created_at DESC"""
        ).fetchall()
    return [dict(r) for r in rows]


@mcp.tool()
def read_transcript(transcript_id: str) -> dict:
    """Đọc toàn bộ nội dung text của một cuộc họp theo id.

    Dùng `list_transcripts` để lấy id trước.
    """
    if not DB_PATH.exists():
        return {"error": "Chưa có database (app chưa chạy lần nào)"}
    with _connect() as conn:
        row = conn.execute(
            """SELECT id, title, language, model, duration, created_at, text
               FROM transcripts WHERE id = ?""",
            (transcript_id,),
        ).fetchone()
    if row is None:
        return {"error": f"Không tìm thấy transcript '{transcript_id}'"}
    return dict(row)


if __name__ == "__main__":
    mcp.run()
