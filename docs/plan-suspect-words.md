# Plan — Từ khả nghi: gạch đuôi đỏ + chọn phương án

Mục tiêu: từ nào ASR đọc không chắc (độ tin cậy thấp) được gạch chân đỏ lượn sóng;
click vào hiện popup nhiều phương án thay thế để user chọn. Chọn xong thay vào transcript
và (bản upload) học cặp wrong→right qua hook có sẵn.

## Nguồn tín hiệu
- **Khả nghi** = `probability` của từ (Whisper word) `< SUSPECT_PROB (0.5)`. Có sẵn mọi engine.
  KHÔNG dùng n-best (mlx 0.4.3 không có beam search — bất khả thi trên máy Mac).
- **Phương án thay thế** = lazy khi click:
  1. tra thư viện `corrections` (cặp `wrong≈từ → right`, status approved),
  2. thiếu thì gọi pass-2 LLM sinh 2–3 candidate.

## Contract (ĐÓNG BĂNG — không đổi khi implement)

### 1. Segment word shape (upload path, DB `segments` JSON — không ALTER)
Mỗi segment khi bật `flag_words` thêm field `words`:
```json
{"start": 1.2, "end": 3.4, "text": "...", "words": [{"w": "kubernetes", "p": 0.31}, ...]}
```
- `w` = từ (giữ nguyên khoảng trắng gốc như Whisper trả), `p` = probability (float).
- Không bật flag_words → không có field `words` (backward compat).
- Frontend: từ suspect khi `p < 0.5`.

### 2. Live WS message `final` thêm `words`
```json
{"type": "final", "utt": "...", "text": "...", "words": [["kubernetes", 0.31], ...]}
```
- Shape `[[word, prob], ...]` = đúng `DecodeResult.words`. Chỉ gửi khi có (flag_words on).
- `corrected` KHÔNG mang words (text đã do LLM sửa, prob không còn khớp) — chấp nhận mất gạch đỏ sau khi corrected.

### 3. Suggest endpoint (mới)
```
POST /api/suggest
body: {"word": "<từ khả nghi>", "context": "<câu/segment chứa từ>", "language": "vi"}
resp: {"alternatives": ["opt1", "opt2", ...]}   # 0–5 phần tử, có thể rỗng; never-fail
```
Logic: corrections lookup (wrong≈word, approved) → nếu < 3, LLM sinh thêm → merge/dedupe/cap 5.

### 4. Áp dụng lựa chọn
- Upload: thay từ trong segment → `applySegmentEdit(s, newFullText)` → `PATCH /api/transcripts/{id}/text`
  (đã tự `extract_pairs` + `upsert_correction`, học luôn).
- Live: thay từ trong dòng (`setLine`), lưu khi kết thúc phiên như hiện tại.

## Phân chia sở hữu file (parallel)
- **Agent 1 — ASR+DB (upload)**: `app/engines.py`, `app/transcribe.py`, `app/db.py`.
  Bật word_timestamps cho upload khi flag_words; giữ `words:[{w,p}]` vào segment; round-trip DB.
- **Agent 2 — Suggest API**: `app/main.py` (endpoint `/api/suggest`), `app/correct.py` (helper LLM),
  `app/corrections.py` (helper lookup). Không đụng logic pass-2 text→text hiện có.
- **Agent 3 — Frontend**: `app/static/index.html`, `app/static/app.js`. CSS wavy underline + popup;
  render span-per-word ở segment list và live; click → /api/suggest → chọn → apply.
- **Main session — live.py**: gửi `words` trong message `final`.

Tests: mỗi agent thêm test cho phần mình (`tests/`). Grep chữ ký hàm đổi trong `tests/` trước khi sửa.
Lưu ý: working tree đang dirty (nhiều session song song) — RE-READ file nóng trước khi edit, chỉ thêm surgical.
