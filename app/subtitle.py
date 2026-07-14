"""Xuất phụ đề SRT/VTT từ segments (+ nhãn speaker). Hàm thuần, không I/O."""
from __future__ import annotations


def _fmt_ts(t: float, *, vtt: bool) -> str:
    """Giây (float) → 'HH:MM:SS,mmm' (SRT) hoặc 'HH:MM:SS.mmm' (VTT)."""
    ms = int(round(max(t, 0.0) * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    sep = "." if vtt else ","
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


def _end_of(segments: list[dict], i: int) -> float:
    """Mốc kết của cue i: ưu tiên `end`; fallback `start` câu kế; cuối cùng +2s
    (segment cũ chỉ có `start` — tương thích ngược)."""
    seg = segments[i]
    if seg.get("end") is not None:
        return float(seg["end"])
    if i + 1 < len(segments):
        return float(segments[i + 1]["start"])
    return float(seg["start"]) + 2.0


def render(segments: list[dict], labels: dict | None = None, *, vtt: bool = False) -> str:
    """segments: [{start, end?, text, spk?}]. labels: {local_cluster_idx: tên}.
    Trả chuỗi SRT (mặc định) hoặc WebVTT."""
    labels = labels or {}
    blocks: list[str] = []
    n = 0
    for i, seg in enumerate(segments):
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        n += 1
        header = f"{_fmt_ts(float(seg['start']), vtt=vtt)} --> {_fmt_ts(_end_of(segments, i), vtt=vtt)}"
        name = labels.get(seg.get("spk"))
        cue = f"{name}: {text}" if name else text
        blocks.append(f"{header}\n{cue}" if vtt else f"{n}\n{header}\n{cue}")
    body = "\n\n".join(blocks)
    return f"WEBVTT\n\n{body}\n" if vtt else f"{body}\n"
