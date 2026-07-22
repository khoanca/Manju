"""SQLite local `data/manju.db` — nguồn chân lý cho text + metadata + settings
(PRD FR-2). File `{id}.txt` vẫn được xuất làm artifact; file JSON cũ được
migrate vào DB lúc khởi động (idempotent, không xóa file gốc).

WAL + busy_timeout để app và MCP server (đọc read-only) không lock chéo.
Connection mở-đóng theo từng thao tác — traffic thấp, an toàn thread.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
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
  speaker_map TEXT,  -- JSON {"<local_cluster_idx>": speaker_id | null} (lớp speaker)
  edited_text TEXT,  -- bản user sửa tay (US-801); NULL = chưa sửa, dùng text
  golden INTEGER DEFAULT 0,  -- US-819: edited_text đủ đúng để làm chuẩn đo WER
  domain TEXT  -- Lớp B: ngành nghề dự đoán (devops|finance|...); NULL = chưa/không rõ
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

-- Lớp speaker: người có tên (global) + voiceprint để nhận diện bản ghi mới.
CREATE TABLE IF NOT EXISTS speakers (
  id         TEXT PRIMARY KEY,
  name       TEXT NOT NULL,
  created_at TEXT NOT NULL,
  region     TEXT  -- vùng miền giọng: 'bac'|'trung'|'nam'|NULL (chưa rõ)
);

CREATE TABLE IF NOT EXISTS voiceprints (
  id                TEXT PRIMARY KEY,
  speaker_id        TEXT NOT NULL REFERENCES speakers(id),
  embedding         BLOB NOT NULL,   -- float32 centroid, dài = dim
  dim               INTEGER NOT NULL,
  sample_count      INTEGER NOT NULL DEFAULT 1,
  source_transcript TEXT,
  created_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_vp_speaker ON voiceprints(speaker_id);

-- Từ vựng cá nhân mine từ transcript đã gán tên người nói — bias ASR phiên sau.
CREATE TABLE IF NOT EXISTS speaker_terms (
  transcript_id TEXT NOT NULL,
  speaker_id    TEXT NOT NULL REFERENCES speakers(id),
  term          TEXT NOT NULL,
  count         INTEGER NOT NULL DEFAULT 1,
  created_at    TEXT NOT NULL,
  PRIMARY KEY (transcript_id, speaker_id, term)
);
CREATE INDEX IF NOT EXISTS idx_st_speaker ON speaker_terms(speaker_id);

-- Thư viện sửa lỗi (US-802): cặp (sai → đúng) trích từ chỉnh sửa của user,
-- seed lexicon vùng miền, hoặc nguồn remote. Chỉ entry 'approved' được mồi.
CREATE TABLE IF NOT EXISTS corrections (
  id         TEXT PRIMARY KEY,               -- cor-{uuid12}
  wrong      TEXT NOT NULL,
  right      TEXT NOT NULL,
  tag        TEXT NOT NULL DEFAULT '',       -- vùng miền/accent (bac|trung|nam|en_accent|...)
  source     TEXT NOT NULL DEFAULT 'user',   -- user|seed|remote
  count      INTEGER NOT NULL DEFAULT 1,
  status     TEXT NOT NULL DEFAULT 'pending', -- pending|approved|rejected
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(wrong, right)
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
    if "speaker_map" not in cols:
        conn.execute("ALTER TABLE transcripts ADD COLUMN speaker_map TEXT")
    if "edited_text" not in cols:
        conn.execute("ALTER TABLE transcripts ADD COLUMN edited_text TEXT")
    if "golden" not in cols:
        conn.execute("ALTER TABLE transcripts ADD COLUMN golden INTEGER DEFAULT 0")
    if "domain" not in cols:
        conn.execute("ALTER TABLE transcripts ADD COLUMN domain TEXT")
    if "raw_segments" not in cols:
        # Segment ASR thô của phiên live (text + avg_logprob) — để reanalyze chạy
        # lại đúng cái live đã nghe, không phải batch-decode lại (khác live).
        conn.execute("ALTER TABLE transcripts ADD COLUMN raw_segments TEXT")
    spk_cols = {r["name"] for r in conn.execute("PRAGMA table_info(speakers)")}
    if "region" not in spk_cols:
        conn.execute("ALTER TABLE speakers ADD COLUMN region TEXT")


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
    speaker_map: dict | None = None  # {local_cluster_idx: speaker_id | null} (lớp speaker)
    domain: str | None = None  # Lớp B: ngành nghề dự đoán từ nội dung
    raw_segments: list[dict] | None = None  # ASR thô live [{text, no_speech_prob, avg_logprob}]


def insert_transcript(rec: TranscriptRecord) -> None:
    with _connect() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO transcripts
               (id, title, language, model, duration, created_at, text, raw_text,
                segments, chars, words, corrected, llm_model, audio_file, audio_dir,
                speaker_map, domain, raw_segments)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                rec.transcript_id, rec.title, rec.language, rec.model,
                round(rec.duration, 1), rec.created_at, rec.text, rec.raw_text,
                json.dumps(rec.segments, ensure_ascii=False) if rec.segments else None,
                len(rec.text), len(rec.text.split()), int(rec.raw_text is not None),
                rec.llm_model, rec.audio_file, rec.audio_dir,
                json.dumps(rec.speaker_map, ensure_ascii=False) if rec.speaker_map else None,
                rec.domain,
                json.dumps(rec.raw_segments, ensure_ascii=False) if rec.raw_segments else None,
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
        "has_segments": row["segments"] is not None,
        "audio": row["audio_file"],
        "golden": bool(row["golden"]),
        "domain": row["domain"],
    }


def list_transcripts() -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            """SELECT t.id, t.title, t.language, t.model, t.duration, t.created_at,
                      t.chars, t.words, t.corrected, t.llm_model, t.segments,
                      t.audio_file, t.golden, t.domain, s.status AS sync_status
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
    if row["edited_text"] is not None:
        data["edited_text"] = row["edited_text"]
    if row["segments"] is not None:
        try:
            data["segments"] = json.loads(row["segments"])
        except Exception:  # noqa: BLE001
            pass
    if row["speaker_map"] is not None:
        try:
            data["speaker_map"] = json.loads(row["speaker_map"])
        except Exception:  # noqa: BLE001
            pass
    if row["raw_segments"] is not None:
        try:
            data["raw_segments"] = json.loads(row["raw_segments"])
        except Exception:  # noqa: BLE001
            pass
    return data


def update_speaker_layer(
    transcript_id: str,
    segments: list[dict] | None,
    speaker_map: dict | None,
) -> None:
    """Ghi lại kết quả pass 3 (segment có `spk` + ánh xạ cụm→người) cho 1
    transcript đã tồn tại — dùng cho diarize/re-run và gán tên thủ công."""
    with _connect() as conn:
        conn.execute(
            "UPDATE transcripts SET segments = ?, speaker_map = ? WHERE id = ?",
            (
                json.dumps(segments, ensure_ascii=False) if segments else None,
                json.dumps(speaker_map, ensure_ascii=False) if speaker_map else None,
                transcript_id,
            ),
        )


def set_edited_text(transcript_id: str, edited_text: str | None) -> bool:
    """Lưu bản user sửa tay (US-801). None = xoá bản sửa, quay về bản máy.
    Trả False nếu transcript không tồn tại."""
    with _connect() as conn:
        cur = conn.execute(
            "UPDATE transcripts SET edited_text = ? WHERE id = ?",
            (edited_text, transcript_id),
        )
    return cur.rowcount > 0


def set_golden(transcript_id: str, golden: bool) -> bool:
    """Đánh dấu bản sửa tay đủ đúng để làm chuẩn đo WER (US-819).
    Trả False nếu transcript không tồn tại."""
    with _connect() as conn:
        cur = conn.execute(
            "UPDATE transcripts SET golden = ? WHERE id = ?",
            (1 if golden else 0, transcript_id),
        )
    return cur.rowcount > 0


def golden_transcripts() -> list[dict]:
    """Các bản chuẩn dùng để chấm điểm (US-820).

    Chỉ nhận bản có `edited_text` thực sự có chữ: cờ golden bật mà bản sửa
    rỗng thì không phải chuẩn, đo với nó sẽ ra số vô nghĩa.
    """
    with _connect() as conn:
        rows = conn.execute(
            """SELECT id, title, text, raw_text, edited_text, segments, audio_file, audio_dir
               FROM transcripts
               WHERE golden = 1 AND edited_text IS NOT NULL AND TRIM(edited_text) != ''
               ORDER BY created_at DESC"""
        ).fetchall()
    return [dict(r) for r in rows]


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


# ── Speakers (lớp speaker — tên hiển thị cho nhãn/phụ đề) ──────────────────
def speaker_names() -> dict[str, str]:
    """Map speaker_id → tên. Rỗng cho tới khi user gán tên (PR3)."""
    with _connect() as conn:
        rows = conn.execute("SELECT id, name FROM speakers").fetchall()
    return {r["id"]: r["name"] for r in rows}


def list_speakers() -> list[dict]:
    """Người có tên + số voiceprint đã enroll (cho trang Quản lý giọng)."""
    with _connect() as conn:
        rows = conn.execute(
            """SELECT s.id, s.name, s.created_at, s.region, COUNT(v.id) AS voiceprints
               FROM speakers s LEFT JOIN voiceprints v ON v.speaker_id = s.id
               GROUP BY s.id ORDER BY s.name COLLATE NOCASE"""
        ).fetchall()
    return [dict(r) for r in rows]


def find_or_create_speaker(name: str) -> str:
    """Tìm speaker theo tên (không phân biệt hoa/thường) hoặc tạo mới. Trả id."""
    name = name.strip()
    with _connect() as conn:
        row = conn.execute(
            "SELECT id FROM speakers WHERE name = ? COLLATE NOCASE", (name,)
        ).fetchone()
        if row:
            return row["id"]
        sid = f"spk-{uuid.uuid4().hex[:12]}"
        conn.execute(
            "INSERT INTO speakers (id, name, created_at) VALUES (?, ?, ?)",
            (sid, name, datetime.now(UTC).astimezone().isoformat()),
        )
        return sid


def rename_speaker(speaker_id: str, name: str) -> None:
    with _connect() as conn:
        conn.execute("UPDATE speakers SET name = ? WHERE id = ?", (name.strip(), speaker_id))


def set_speaker_region(speaker_id: str, region: str | None) -> None:
    """Gán vùng miền giọng của 1 người ('bac'|'trung'|'nam') — None = bỏ gán."""
    with _connect() as conn:
        conn.execute("UPDATE speakers SET region = ? WHERE id = ?", (region, speaker_id))


def delete_speaker(speaker_id: str) -> None:
    """Xóa người + voiceprint + từ vựng cá nhân; gỡ tham chiếu khỏi mọi
    speaker_map (không xóa câu)."""
    with _connect() as conn:
        conn.execute("DELETE FROM voiceprints WHERE speaker_id = ?", (speaker_id,))
        conn.execute("DELETE FROM speaker_terms WHERE speaker_id = ?", (speaker_id,))
        conn.execute("DELETE FROM speakers WHERE id = ?", (speaker_id,))
        rows = conn.execute(
            "SELECT id, speaker_map FROM transcripts WHERE speaker_map LIKE ?",
            (f"%{speaker_id}%",),
        ).fetchall()
        for r in rows:
            smap = json.loads(r["speaker_map"])
            changed = {k: (None if v == speaker_id else v) for k, v in smap.items()}
            if changed != smap:
                conn.execute(
                    "UPDATE transcripts SET speaker_map = ? WHERE id = ?",
                    (json.dumps(changed, ensure_ascii=False), r["id"]),
                )


def load_voiceprints() -> list[tuple[str, bytes]]:
    """(speaker_id, embedding bytes float32) mọi người đã enroll — cho auto-match."""
    with _connect() as conn:
        rows = conn.execute("SELECT speaker_id, embedding FROM voiceprints").fetchall()
    return [(r["speaker_id"], r["embedding"]) for r in rows]


def get_voiceprint(speaker_id: str) -> tuple[bytes, int] | None:
    """(embedding bytes, sample_count) của 1 người — None nếu chưa enroll."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT embedding, sample_count FROM voiceprints WHERE speaker_id = ?",
            (speaker_id,),
        ).fetchone()
    return (row["embedding"], row["sample_count"]) if row else None


def save_voiceprint(
    speaker_id: str, embedding: bytes, dim: int, sample_count: int, source_transcript: str | None
) -> None:
    """Lưu/ghi đè centroid voiceprint của 1 người (1 hàng/người, id = vp-<speaker_id>)."""
    with _connect() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO voiceprints
               (id, speaker_id, embedding, dim, sample_count, source_transcript, created_at)
               VALUES (?,?,?,?,?,?,?)""",
            (
                f"vp-{speaker_id}", speaker_id, embedding, dim, sample_count,
                source_transcript, datetime.now(UTC).astimezone().isoformat(),
            ),
        )


def set_transcript_cluster(transcript_id: str, cluster: int, speaker_id: str | None) -> dict:
    """Gán cụm giọng local `cluster` của 1 transcript → speaker_id (hoặc None để
    bỏ gán). Trả speaker_map mới. Raise KeyError nếu transcript không tồn tại."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT speaker_map FROM transcripts WHERE id = ?", (transcript_id,)
        ).fetchone()
        if row is None:
            raise KeyError(transcript_id)
        smap = json.loads(row["speaker_map"]) if row["speaker_map"] else {}
        smap[str(cluster)] = speaker_id
        conn.execute(
            "UPDATE transcripts SET speaker_map = ? WHERE id = ?",
            (json.dumps(smap, ensure_ascii=False), transcript_id),
        )
    return smap


# ── Speaker terms (từ vựng cá nhân theo người nói — bias ASR) ──────────────
def replace_speaker_terms(transcript_id: str, rows: list[tuple[str, str, int]]) -> int:
    """Ghi lại toàn bộ term mine được của 1 transcript — rows là
    (speaker_id, term, count). Một transaction: xoá hàng cũ rồi insert hàng
    loạt (chạy lại không nhân đôi). Trả số hàng đã insert."""
    now = datetime.now(UTC).astimezone().isoformat()
    with _connect() as conn:
        conn.execute("DELETE FROM speaker_terms WHERE transcript_id = ?", (transcript_id,))
        conn.executemany(
            """INSERT INTO speaker_terms (transcript_id, speaker_id, term, count, created_at)
               VALUES (?,?,?,?,?)""",
            [(transcript_id, sid, term, count, now) for sid, term, count in rows],
        )
    return len(rows)


def personal_terms(speaker_ids: list[str], limit: int = 40) -> list[str]:
    """Term của các người nói đã biết, gộp count qua mọi transcript, hay gặp
    nhất trước (tie-break theo term). [] nếu không truyền speaker nào."""
    if not speaker_ids:
        return []
    placeholders = ",".join("?" * len(speaker_ids))
    with _connect() as conn:
        rows = conn.execute(
            f"""SELECT term, SUM(count) AS c FROM speaker_terms
                WHERE speaker_id IN ({placeholders})
                GROUP BY term ORDER BY c DESC, term LIMIT ?""",
            [*speaker_ids, limit],
        ).fetchall()
    return [r["term"] for r in rows]


# ── Corrections (thư viện sửa lỗi — US-802) ────────────────────────────────
def upsert_correction(wrong: str, right: str, tag: str = "", source: str = "user") -> dict:
    """Thêm cặp (sai → đúng) hoặc cộng count nếu đã có (UNIQUE wrong,right).
    Cặp lặp ≥2 lần đang pending → tự chuyển approved (US-802 AC2). Trả row dict."""
    now = datetime.now(UTC).astimezone().isoformat()
    with _connect() as conn:
        row = conn.execute(
            "SELECT id, count, status FROM corrections WHERE wrong = ? AND right = ?",
            (wrong, right),
        ).fetchone()
        if row is None:
            cid = f"cor-{uuid.uuid4().hex[:12]}"
            conn.execute(
                """INSERT INTO corrections
                   (id, wrong, right, tag, source, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?)""",
                (cid, wrong, right, tag, source, now, now),
            )
        else:
            cid = row["id"]
            status = "approved" if row["status"] == "pending" else row["status"]
            conn.execute(
                "UPDATE corrections SET count = count + 1, status = ?, updated_at = ? "
                "WHERE id = ?",
                (status, now, cid),
            )
        out = conn.execute("SELECT * FROM corrections WHERE id = ?", (cid,)).fetchone()
    return dict(out)


def list_corrections(
    status: str | None = None, source: str | None = None, tag: str | None = None
) -> list[dict]:
    """Liệt kê thư viện, filter optional; cặp gặp nhiều nhất + mới nhất lên đầu."""
    clauses: list[str] = []
    params: list[str] = []
    for col, val in (("status", status), ("source", source), ("tag", tag)):
        if val is not None:
            clauses.append(f"{col} = ?")
            params.append(val)
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    with _connect() as conn:
        rows = conn.execute(
            f"SELECT * FROM corrections{where} ORDER BY count DESC, updated_at DESC",
            params,
        ).fetchall()
    return [dict(r) for r in rows]


def add_corrections_ignore(
    entries: list[tuple[str, str, str]], source: str, status: str = "approved"
) -> int:
    """INSERT OR IGNORE hàng loạt (wrong, right, tag) cho seed/remote (US-804).
    Cặp đã có (kể cả của user — UNIQUE wrong,right) giữ nguyên, KHÔNG đè,
    KHÔNG cộng count. Trả số hàng thêm mới."""
    now = datetime.now(UTC).astimezone().isoformat()
    added = 0
    with _connect() as conn:
        for wrong, right, tag in entries:
            cur = conn.execute(
                """INSERT OR IGNORE INTO corrections
                   (id, wrong, right, tag, source, status, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (f"cor-{uuid.uuid4().hex[:12]}", wrong, right, tag, source, status, now, now),
            )
            added += cur.rowcount
    return added


def delete_corrections(source: str, tag: str) -> int:
    """Xoá entry theo nguồn + tag (gỡ seed 1 vùng) — không đụng nguồn khác.
    Trả số hàng đã xoá."""
    with _connect() as conn:
        cur = conn.execute(
            "DELETE FROM corrections WHERE source = ? AND tag = ?", (source, tag)
        )
    return cur.rowcount


def set_correction_status(correction_id: str, status: str) -> bool:
    """Duyệt/loại thủ công. Trả False nếu id không tồn tại."""
    with _connect() as conn:
        cur = conn.execute(
            "UPDATE corrections SET status = ?, updated_at = ? WHERE id = ?",
            (status, datetime.now(UTC).astimezone().isoformat(), correction_id),
        )
    return cur.rowcount > 0


def set_correction_tag(correction_id: str, tag: str) -> bool:
    """Sửa tag vùng miền/accent. Trả False nếu id không tồn tại."""
    with _connect() as conn:
        cur = conn.execute(
            "UPDATE corrections SET tag = ?, updated_at = ? WHERE id = ?",
            (tag, datetime.now(UTC).astimezone().isoformat(), correction_id),
        )
    return cur.rowcount > 0


def delete_correction(correction_id: str) -> bool:
    with _connect() as conn:
        cur = conn.execute("DELETE FROM corrections WHERE id = ?", (correction_id,))
    return cur.rowcount > 0


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
