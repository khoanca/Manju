# Plan: Speaker Layer (diarization + nhận diện giọng)

- **Source**: US-701..US-704 (net-new — chưa có trong product-plan; định nghĩa tại đây, đã duyệt Feature Preview 2026-07-14)
- **Status**: In-Progress (PR1–PR3 xong)
- **Updated**: 2026-07-14
- **Scout decision**: BUILD path — tích hợp `sherpa-onnx` (Apache-2.0, offline, KHÔNG token HF), giữ nguyên mlx-whisper. Xem lịch sử scout branch này.

## User Stories (net-new)

- **US-701** — Là người dùng, tôi muốn transcript hiển thị *ai nói câu nào* kèm mốc `[mm:ss]`, để đọc/tra cứu cuộc họp nhanh.
  - AC1: mỗi câu có nhãn giọng (S1/S2… hoặc tên) + timestamp bắt đầu.
  - AC2: file không diarize được (1 giọng / lỗi model) vẫn hiển thị transcript bình thường, không nhãn.
- **US-702** — Là người dùng, tôi muốn đặt tên thủ công cho từng giọng, áp cho toàn transcript.
  - AC1: đổi "S1" → "Minh" cập nhật mọi câu của cụm đó.
  - AC2: tên lưu bền, mở lại transcript vẫn còn.
- **US-703** — Là người dùng, tôi muốn "ghi nhớ giọng" của một người; bản ghi sau tự gán tên người đó.
  - AC1: sau khi gán tên + "ghi nhớ giọng", voiceprint được lưu.
  - AC2: transcript mới có giọng đó → tự điền tên (cosine ≥ ngưỡng); dưới ngưỡng → "Người N" (Unknown).
  - AC3: match sai → user sửa lại tay (ghi đè); có thể enroll thêm mẫu cho cùng người.
- **US-704** — Là người dùng, tôi muốn xuất phụ đề SRT/VTT kèm tên người + timestamp.
  - AC1: SRT/VTT hợp lệ, mỗi cue có `start→end` và prefix tên nếu có.

## Approach

Thêm **pass 3 (diarize + ID)** chạy sau ASR (và sau pass 2 nếu bật), trên chính `audio_path` WAV 16k mono trong [`_process`](../app/transcribe.py) — cùng pattern pass 2 (`_maybe_correct`), thêm job status `diarizing`. KHÔNG đụng luồng live real-time; recording live lưu WAV nên diarize hậu kỳ qua endpoint re-run.

Diarization (`sherpa-onnx OfflineSpeakerDiarization`) trả các đoạn `{start, end, speaker_local}`; **align theo max-overlap thời gian** với segment whisper để gán `spk` (chỉ số cụm local) cho từng câu. Với mỗi cụm local: tính **embedding trung bình** (`SpeakerEmbeddingExtractor`) → cosine với voiceprint đã enroll → điền `speaker_map[local] = speaker_id` nếu ≥ ngưỡng, ngược lại `null` (Unknown).

Tách quan hệ để tên đổi được mà không phải rewrite segments:
- segment JSON chỉ giữ chỉ số cụm local: `{start, end, text, spk}`.
- ánh xạ cụm→người ở cột `speaker_map` (JSON `{"0": speaker_id|null}`) của transcript.
- `speakers` (người có tên) + `voiceprints` (embedding) là bảng global, tái dùng xuyên transcript.

**Vì sao không WhisperX/pyannote**: WhisperX buộc đổi ASR sang CTranslate2 (phá quyết định mlx đã benchmark); pyannote model gated HF → mỗi user cần token. sherpa-onnx bỏ cả hai vấn đề.

## Tech stack mới (evaluation)

| Dep | Lý do | Rủi ro | Giảm thiểu |
|-----|-------|--------|-----------|
| `sherpa-onnx` (pip) | diarization + speaker embedding offline, không token | +onnxruntime; model ~50-100MB tải lần đầu | lazy-load singleton như `engines`; script fetch model idempotent; feature tắt được qua setting |
| model: `sherpa-onnx-pyannote-segmentation-3-0` + embedding `3dspeaker/wespeaker` ONNX | chuẩn, có sẵn trên release sherpa-onnx | model không nằm trong repo | tải khi lần đầu bật diarize; verify checksum; báo lỗi rõ nếu thiếu |

## Tasks

| ID | Task | Source | Dep | Files | Status |
|----|------|--------|-----|-------|--------|
| T-001 | Thêm `end` vào segment `transcribe_file` (đã có sẵn từ mlx) + cập nhật shape `{start,end,text}`; giữ tương thích segment cũ (chỉ `start`) | US-701 AC1, US-704 AC1 | ‖ | `app/engines.py` | [x] |
| T-002 | Migration: bảng `speakers`, `voiceprints`; cột `speaker_map` trên `transcripts` (additive `_ensure_columns`) + rollback | US-702 AC2, US-703 | ‖ | `app/db.py` | [x] |
| T-003 | Export SRT/VTT từ segments+speaker_map (hàm thuần, không cần model) | US-704 AC1 | → T-001,T-002 | `app/subtitle.py` (mới), `app/main.py` | [x] |
| T-004 | `app/diarize.py`: lazy singleton load sherpa-onnx; `diarize_file(wav,num_speakers=-1) -> list[{start,end,spk}]`; xử lý thiếu model/1 giọng | US-701 AC2 | → T-002 | `app/diarize.py` (mới) | [x] |
| T-005 | Script fetch model idempotent + config đường dẫn model trong settings | US-701 | ‖ với T-004 | `scripts/fetch_diarize_models.py` (mới), `app/db.py` settings | [x] |
| T-006 | Align diarization ↔ segment whisper (max-overlap) → gán `spk`; pass 3 hook trong `_process` + job status `diarizing` + setting bật/tắt | US-701 AC1 | → T-004 | `app/transcribe.py`, `app/diarize.py` | [x] |
| T-007 | Endpoints: `POST /api/transcripts/{id}/diarize` (run/re-run, cả recording live), CRUD `speakers`, `PUT speaker_map` (gán tên cụm) | US-702 AC1, US-701 | → T-006 | `app/main.py`, `app/db.py` | [x] |
| T-008 | UI transcript: nhóm câu theo giọng, màu + `[mm:ss]`, dropdown gán/đổi tên, nút xuất SRT/VTT | US-701, US-702, US-704 | → T-007,T-003 | `app/static/app.js`, `index.html` | [x] |
| T-009 | Embedding + match: `embed_cluster(wav,spans)->vec`, `match(vec)->speaker_id\|None` (cosine, ngưỡng cấu hình); auto-điền `speaker_map` khi diarize | US-703 AC2 | → T-006 | `app/diarize.py`, `app/db.py` | [ ] |
| T-010 | Enroll: `POST /api/speakers/{id}/enroll` từ 1 transcript+cụm → lưu voiceprint; hỗ trợ thêm mẫu (centroid cập nhật) | US-703 AC1, AC3 | → T-009 | `app/main.py`, `app/db.py` | [ ] |
| T-011 | UI: nút "ghi nhớ giọng" trên cụm đã đặt tên; trang **Quản lý giọng** (list/rename/xóa speaker+voiceprint) | US-703 | → T-010 | `app/static/app.js`, `index.html` | [ ] |
| T-012 | Tests: unit align/cosine/SRT, diarize smoke (file mẫu 2 giọng), enroll→match round-trip, migration rollback, endpoint tests | US-701..704 (mọi AC) | → mỗi task land | `tests/` | [ ] |

## Stacked PRs (>400 LOC → chia nhỏ, mỗi PR ≤400, deploy độc lập)

1. **PR1 — Timestamp & Export** (T-001, T-002, T-003, +test): segment `end`, schema, SRT/VTT. *User-visible ngay* (timestamp + tải phụ đề) kể cả trước diarize.
2. **PR2 — Diarization pass** (T-004, T-005, T-006, +test): nhãn S1/S2 tự động. **Integration point** — speaker thành hình.
3. **PR3 — Đặt tên thủ công** (T-007, T-008, +test): gán/đổi tên tương tác. *Điểm user-visible tương tác chính.*
4. **PR4 — Voiceprint & auto-ID** (T-009, T-010, T-011, +test): enroll + tự nhận diện bản ghi mới.
5. Tests E2E xuyên suốt gộp vào từng PR (T-012 phân bổ theo scope).

Feature flag: setting `diarize_enabled` (mặc định off tới khi model tải) → PR2+ merge sau cờ, không phá luồng hiện tại.

## Edge Cases & Error Handling

- Thiếu/hỏng model ONNX → skip pass 3, transcript vẫn lưu, job báo `diarize_skipped` (US-701 AC2).
- 1 giọng / audio quá ngắn (<~3s) → không diarize, không nhãn.
- Giọng chồng lấn → segment lấy speaker overlap trội; ghi chú giới hạn cho user.
- Segment whisper không overlap đoạn diarize nào → `spk=null` (không nhãn), không rớt câu.
- Cosine dưới ngưỡng cho mọi voiceprint → "Người N" (Unknown), cho user gán tay (US-703 AC2/AC3).
- Segment cũ chỉ có `start` (không `end`, không `spk`) → UI/export fallback dùng `start` của câu kế làm `end`.
- Xóa speaker đang được transcript tham chiếu → set `speaker_map` liên quan về null (không xóa segment).
- Re-run diarize ghi đè `spk`/`speaker_map` cũ → xác nhận trước khi ghi đè tên đã gán tay.

## Test Strategy (ưu tiên viết test trước)

- **Unit thuần** (không model): align max-overlap (segment↔diarize spans), cosine similarity + ngưỡng, SRT/VTT format, migration additive + rollback.
- **Smoke diarize**: 1 file WAV mẫu 2 giọng ngắn (fixture) → assert ≥2 cụm, thứ tự thời gian. Model tải trong CI hoặc skip-if-missing (đánh dấu `@pytest.mark.diarize`).
- **Round-trip US-703**: enroll giọng A từ file1 → diarize file2 chứa A → assert auto-gán đúng speaker_id (cosine ≥ ngưỡng); file chỉ giọng lạ → Unknown.
- **Endpoint**: diarize/rename/enroll trả đúng shape; ghi đè tên tay được bảo vệ.
- Chạy full `uv run pytest` + `ruff` + `mypy` sau mỗi PR.

## Rollback

- Mỗi migration có down tương ứng: `DROP TABLE voiceprints; DROP TABLE speakers;` + cột `speaker_map` (SQLite: giữ cột, code bỏ đọc — SQLite không drop column dễ; document 2-deploy nếu cần gỡ).
- Test rollback DURING implement (không hoãn tới deploy): script down chạy sạch trên DB có dữ liệu mẫu.
- `speaker_map`/`spk` additive → gỡ feature = tắt cờ `diarize_enabled`, không mất transcript.
- sherpa-onnx là dep mới trong `uv.lock` → rollback = revert lock + xóa model dir.
