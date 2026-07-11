"""SQLite local `data/manju.db` — nguồn chân lý cho text + metadata + settings
(PRD FR-2). File `{id}.txt` vẫn được xuất làm artifact; file JSON cũ được
migrate vào DB lúc khởi động (idempotent, không xóa file gốc).

WAL + busy_timeout để app và MCP server (đọc read-only) không lock chéo.
Connection mở-đóng theo từng thao tác — traffic thấp, an toàn thread.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
DB_PATH = DATA / "manju.db"
DEFAULT_RECORDINGS = DATA / "recordings"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS transcripts (
  id         TEXT PRIMARY KEY,
  title      TEXT NOT NULL,
  language   TEXT NOT NULL,
  model      TEXT,
  duration   REAL,
  created_at TEXT NOT NULL,
  text       TEXT NOT NULL,
  raw_text   TEXT,
  segments   TEXT,
  chars      INTEGER,
  words      INTEGER,
  corrected  INTEGER DEFAULT 0,
  llm_model  TEXT,
  audio_file TEXT,
  audio_dir  TEXT,
  credits_spent REAL
);
CREATE INDEX IF NOT EXISTS idx_t_created ON transcripts(created_at DESC);

CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS sync_state (
  transcript_id TEXT PRIMARY KEY REFERENCES transcripts(id),
  org_id    TEXT,
  remote_id TEXT,
  pushed_at TEXT,
  status    TEXT DEFAULT 'pending'
);
"""


def _connect() -> sqlite3.Connection:
    DATA.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=5)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.row_factory = sqlite3.Row
    return conn


def init() -> None:
    with _connect() as conn:
        conn.executescript(_SCHEMA)
        _ensure_columns(conn)


def _ensure_columns(conn: sqlite3.Connection) -> None:
    """Migration additive cho DB đã tồn tại: CREATE TABLE IF NOT EXISTS không
    thêm cột mới vào bảng cũ → tự ALTER khi thiếu (idempotent)."""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(transcripts)")}
    if "credits_spent" not in cols:
        conn.execute("ALTER TABLE transcripts ADD COLUMN credits_spent REAL")


# ── Transcripts ────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class TranscriptRecord:
    """Một bản ghi transcripts đầy đủ — chars/words/corrected suy ra khi insert."""

    transcript_id: str
    title: str
    language: str
    model: str | None
    duration: float
    created_at: str
    text: str
    raw_text: str | None
    segments: list[dict] | None
    llm_model: str | None
    audio_file: str | None
    audio_dir: str | None
    credits_spent: float = 0.0  # credit đã tốn cho pass 2 cloud (FR-6)


def insert_transcript(rec: TranscriptRecord) -> None:
    with _connect() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO transcripts
               (id, title, language, model, duration, created_at, text, raw_text,
                segments, chars, words, corrected, llm_model, audio_file, audio_dir,
                credits_spent)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                rec.transcript_id, rec.title, rec.language, rec.model,
                round(rec.duration, 1), rec.created_at, rec.text, rec.raw_text,
                json.dumps(rec.segments, ensure_ascii=False) if rec.segments else None,
                len(rec.text), len(rec.text.split()), int(rec.raw_text is not None),
                rec.llm_model, rec.audio_file, rec.audio_dir,
                rec.credits_spent or None,
            ),
        )


def _meta(row: sqlite3.Row) -> dict:
    """Metadata cùng shape với file {id}.json cũ — API response không đổi."""
    return {
        "id": row["id"],
        "title": row["title"],
        "language": row["language"],
        "model": row["model"],
        "duration": row["duration"],
        "created_at": row["created_at"],
        "chars": row["chars"],
        "words": row["words"],
        "corrected": bool(row["corrected"]),
        "llm_model": row["llm_model"],
        "credits_spent": row["credits_spent"],
        "has_segments": row["segments"] is not None,
        "audio": row["audio_file"],
    }


def list_transcripts() -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            """SELECT t.id, t.title, t.language, t.model, t.duration, t.created_at,
                      t.chars, t.words, t.corrected, t.llm_model, t.credits_spent,
                      t.segments, t.audio_file, s.status AS sync_status
               FROM transcripts t
               LEFT JOIN sync_state s ON s.transcript_id = t.id
               ORDER BY t.created_at DESC"""
        ).fetchall()
    return [{**_meta(r), "sync": r["sync_status"]} for r in rows]


def read_transcript(transcript_id: str) -> dict | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM transcripts WHERE id = ?", (transcript_id,)
        ).fetchone()
    if row is None:
        return None
    data = {**_meta(row), "text": row["text"]}
    if row["raw_text"] is not None:
        data["raw_text"] = row["raw_text"]
    if row["segments"] is not None:
        try:
            data["segments"] = json.loads(row["segments"])
        except Exception:  # noqa: BLE001
            pass
    return data


def transcript_audio_path(transcript_id: str) -> Path | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT audio_file, audio_dir FROM transcripts WHERE id = ?",
            (transcript_id,),
        ).fetchone()
    if row is None or not row["audio_file"]:
        return None
    p = Path(row["audio_dir"] or DEFAULT_RECORDINGS) / row["audio_file"]
    return p if p.exists() else None


# ── Settings ───────────────────────────────────────────────────────────────
def get_setting(key: str, default: str | None = None) -> str | None:
    with _connect() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )


def get_audio_dir() -> Path:
    value = get_setting("audio_dir")
    return Path(value) if value else DEFAULT_RECORDINGS


def set_audio_dir(path: str) -> Path:
    """Validate + lưu thư mục audio. Raise ValueError nếu không dùng được.
    Chỉ ảnh hưởng bản ghi mới — bản cũ nhớ audio_dir riêng từng row."""
    p = Path(path).expanduser()
    if not p.is_absolute():
        raise ValueError("Đường dẫn phải là tuyệt đối (ví dụ /Users/ban/Recordings)")
    try:
        p.mkdir(parents=True, exist_ok=True)
        probe = p / ".manju-write-test"
        probe.write_bytes(b"")
        probe.unlink()
    except OSError as exc:
        raise ValueError(f"Không ghi được vào thư mục: {exc}") from exc
    set_setting("audio_dir", str(p))
    return p


# ── Sync state (Đợt 2 — đẩy text lên org) ──────────────────────────────────
def get_sync_state(transcript_id: str) -> dict | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT transcript_id, org_id, remote_id, pushed_at, status "
            "FROM sync_state WHERE transcript_id = ?",
            (transcript_id,),
        ).fetchone()
    return dict(row) if row else None


@dataclass(frozen=True)
class SyncState:
    org_id: str
    remote_id: str | None
    pushed_at: str
    status: str = "pushed"


def set_sync_state(transcript_id: str, state: SyncState) -> None:
    with _connect() as conn:
        conn.execute(
            """INSERT INTO sync_state (transcript_id, org_id, remote_id, pushed_at, status)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(transcript_id) DO UPDATE SET
                 org_id = excluded.org_id, remote_id = excluded.remote_id,
                 pushed_at = excluded.pushed_at, status = excluded.status""",
            (transcript_id, state.org_id, state.remote_id, state.pushed_at, state.status),
        )


# ── Migration từ file JSON cũ ──────────────────────────────────────────────
def _legacy_segments(seg: Path) -> str | None:
    """Đọc file {id}.segments.json cũ → chuỗi JSON cho cột segments; None nếu hỏng."""
    if not seg.exists():
        return None
    try:
        return json.dumps(json.loads(seg.read_text(encoding="utf-8")), ensure_ascii=False)
    except Exception:  # noqa: BLE001
        return None


def _legacy_row(meta_file: Path, recordings_dir: Path) -> tuple | None:
    """Đọc bộ file {id}.json/.txt/.raw.txt/.segments.json cũ thành tuple VALUES;
    None nếu metadata hỏng hoặc thiếu file text."""
    try:
        meta = json.loads(meta_file.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(meta, dict) or "id" not in meta:
        return None
    tid = meta["id"]
    txt = meta_file.parent / f"{tid}.txt"
    if not txt.exists():
        return None
    raw = meta_file.parent / f"{tid}.raw.txt"
    segments = _legacy_segments(meta_file.parent / f"{tid}.segments.json")
    text = txt.read_text(encoding="utf-8")
    return (
        tid,
        meta.get("title") or tid,
        meta.get("language") or "vi",
        meta.get("model"),
        meta.get("duration"),
        meta.get("created_at") or "",
        text,
        raw.read_text(encoding="utf-8") if raw.exists() else None,
        segments,
        meta.get("chars") or len(text),
        meta.get("words") or len(text.split()),
        int(bool(meta.get("corrected"))),
        meta.get("llm_model"),
        meta.get("audio"),
        str(recordings_dir),
    )


def migrate_from_files(transcripts_dir: Path, recordings_dir: Path) -> int:
    """Đưa transcript dạng file (trước khi có DB) vào SQLite. Idempotent
    (INSERT OR IGNORE), chỉ đọc — không xóa/sửa file gốc. Trả số bản đã thêm."""
    added = 0
    with _connect() as conn:
        for meta_file in sorted(transcripts_dir.glob("*.json")):
            if meta_file.name.endswith(".segments.json"):
                continue
            row = _legacy_row(meta_file, recordings_dir)
            if row is None:
                continue
            cur = conn.execute(
                """INSERT OR IGNORE INTO transcripts
                   (id, title, language, model, duration, created_at, text, raw_text,
                    segments, chars, words, corrected, llm_model, audio_file, audio_dir)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                row,
            )
            added += cur.rowcount
    return added
