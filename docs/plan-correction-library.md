# Plan: Correction Library (sửa transcript + thư viện tự học)

- **Source**: US-801..US-805 (net-new — chưa có trong PRD; định nghĩa tại đây, Feature Preview duyệt 2026-07-15). PRD sẽ thêm stub FR-6 trỏ về file này.
- **Status**: Implemented (2026-07-15)
- **Updated**: 2026-07-15

## User Stories (net-new)

- **US-801** — Là người dùng, tôi muốn sửa lại đoạn thoại trong transcript cho chính xác, và bản máy gốc vẫn được giữ để đối chiếu.
  - AC1: Sửa từng segment hoặc cả bản trong màn detail → Lưu → reload vẫn thấy bản đã sửa; badge "đã sửa tay".
  - AC2: Toggle xem 3 bản: máy thô (raw) / pass-2 / user sửa. Bản user sửa là bản chính khi export/push.
- **US-802** — Là người dùng, tôi muốn app tự học từ chỉnh sửa của tôi: trích cặp (sai → đúng) vào thư viện, có kiểm soát.
  - AC1: Lưu bản sửa → app diff vs bản máy, trích cặp token-level vào bảng `corrections` trạng thái `pending`.
  - AC2: Cặp xuất hiện ≥2 lần tự chuyển `approved`; tôi duyệt/loại thủ công được trong màn Thư viện.
  - AC3: Sửa văn phong (thay cụm dài, đảo câu) KHÔNG bị trích thành cặp (lọc độ dài + tỉ lệ tương đồng).
- **US-803** — Là người dùng, tôi muốn thư viện tự mồi vào lần nghe sau: Whisper nhận đúng từ hơn, pass 2 sửa đúng hơn — không phải gõ lại glossary.
  - AC1: Upload + live đều merge library (entry `approved`) vào prompt mồi ASR và glossary/few-shot pass 2; rank theo tần suất, cap ~224 token.
  - AC2: Library lỗi/rỗng → job vẫn chạy bình thường (never-fail).
  - AC3: Glossary chuyển nguồn chân lý về server (bảng settings); localStorage chỉ là cache, tự đồng bộ lên lần đầu.
- **US-804** — Là người dùng, tôi muốn thư viện có sẵn từ địa phương Bắc/Trung/Nam và biến âm accent tiếng Anh, bật/tắt theo vùng, cập nhật được (opt-in).
  - AC1: Seed lexicon đóng gói repo (JSON theo vùng + accent); toggle từng vùng trong Settings; entry seed đánh dấu `source=seed`.
  - AC2: Nút "Cập nhật thư viện" (opt-in) fetch JSON từ nguồn cấu hình được (mặc định GitHub raw của repo) + verify checksum; offline/lỗi → giữ bản cũ.
- **US-805** — Là người dùng live subtitle, tôi muốn app bám ngữ cảnh đang diễn ra (chủ đề có thể đổi giữa phiên) để câu sub được sửa chính xác hơn.
  - AC1: Pass 2 live nhận rolling context (tóm tắt chủ đề + các câu gần nhất, cập nhật liên tục) — cả backend Ollama lẫn OpenRouter (hiện chỉ OpenRouter có context 3 câu).
  - AC2: Cập nhật context không chặn subtitle (chạy nền, chậm/lỗi thì dùng context cũ).

## Approach

Không train model. "Học" = vòng dữ liệu: user sửa → diff trích cặp → bảng `corrections` (SQLite, một bảng cho cả cặp user / seed lexicon / remote, phân biệt bằng `source`) → build chuỗi mồi có rank+cap, merge vào 2 điểm đã có sẵn: `initial_prompt` của Whisper (`DecodeSpec.glossary`) và glossary/few-shot của pass 2 (`LlmOpts`). Chọn hướng này thay vì fine-tune LoRA vì local-first, chạy ngay không cần GPU/dataset, và tái dùng toàn bộ hạ tầng hotwords + pass 2 hiện có. Rolling context live mở rộng cơ chế context 3-câu sẵn có trong `_correct_loop` thành `ContextTracker` (tóm tắt chủ đề cập nhật nền). Mọi pass giữ contract never-fail như pass 2/3 hiện tại.

**Nhánh code**: các file đích (`app.js`, `db.py`, `main.py`) đang thay đổi trên `feat/speaker-layer` (chưa merge main). Feature này stack lên `feat/speaker-layer` (branch `feat/correction-library`) để tránh conflict; nếu speaker-layer merge trước thì rebase lên main.

## Tasks

| ID | Task | Source | Dep | Files | Status |
|-----|------|--------|-----|-------|--------|
| T-001 | Migration additive: cột `transcripts.edited_text`; bảng `corrections` (`cor-{uuid12}`, wrong, right, tag vùng/accent, source `user\|seed\|remote`, count, status `pending\|approved\|rejected`, timestamps); UNIQUE(wrong,right); rollback ghi lại | US-801, US-802 | ‖ | `app/db.py` | [x] |
| T-002 | DB funcs: `set_edited_text`, CRUD corrections (`upsert_correction` count++/auto-approve ≥2, `list_corrections` filter, `set_correction_status`, `delete_correction`) | US-801, US-802 | → T-001 | `app/db.py` | [x] |
| T-003 | API: `PATCH /api/transcripts/{id}/text` (body: edited_text, base_version chống ghi đè); CRUD `/api/corrections` — Pydantic models, lỗi tiếng Việt | US-801, US-802 | → T-002 | `app/main.py` | [x] |
| T-004 | UI detail: segment click-to-edit + nút Lưu; toggle 3 bản raw/pass-2/edited; badge "đã sửa tay"; export/push dùng edited_text nếu có | US-801 | → T-003 | `app/static/app.js`, `index.html` | [x] |
| T-005 | `app/corrections.py`: `extract_pairs(machine, edited)` — difflib token-level, lọc: span thay ≤4 từ, similarity cặp ≥0.3 hoặc cùng số từ, bỏ cặp chỉ khác hoa-thường/dấu câu | US-802 AC1, AC3 | ‖ với T-003 (sau T-001) | `app/corrections.py` (mới) | [x] |
| T-006 | Hook: PATCH text → `extract_pairs` (diff vs bản `text` tại thời điểm mở editor, gửi kèm request) → upsert pending | US-802 | → T-003, T-005 | `app/main.py`, `app/corrections.py` | [x] |
| T-007 | UI Thư viện (tab Settings): bảng cặp, filter vùng/accent/source/status, duyệt/loại/xoá, sửa tag | US-802 AC2, US-804 | → T-003 | `app/static/app.js`, `index.html` | [x] |
| T-008 | `build_bias(user_glossary) -> str`: merge glossary user + entry approved, rank count desc + recency, cap ký tự (~224 token Whisper); vị trí trong `app/corrections.py` | US-803 AC1 | → T-002 | `app/corrections.py` | [x] |
| T-009 | Glossary server-side: key `glossary` trong settings + GET/PUT; client đồng bộ localStorage lên 1 lần rồi đọc từ server | US-803 AC3 | → T-002 | `app/main.py`, `app/db.py`, `app/static/app.js` | [x] |
| T-010 | Wire upload: `transcribe._process` gọi `build_bias` cho `DecodeSpec.glossary` + `LlmOpts.glossary`; few-shot pass 2 từ top cặp (thêm vào `_prompt_for`) — try/except never-fail | US-803 AC1, AC2 | → T-008 | `app/transcribe.py`, `app/correct.py` | [x] |
| T-011 | Wire live: `LiveSession.__init__` merge `build_bias` vào spec (chốt lúc start, document là đổi giữa phiên không hiệu lực) | US-803 AC1 | → T-008 | `app/live.py` | [x] |
| T-012 | Seed lexicon: `app/data/lexicon/{bac,trung,nam,en_accent}.json` (soạn nội dung, vài trăm entry); import vào `corrections` với `source=seed` theo toggle vùng trong settings | US-804 AC1 | ‖ với T-008 (sau T-002) | `app/data/lexicon/*` (mới), `app/corrections.py`, `app/main.py` | [x] |
| T-013 | Cập nhật online opt-in: `scripts/fetch_lexicon.py` (pattern fetch_diarize_models) + endpoint `POST /api/lexicon/update` + nút UI; URL cấu hình trong settings, verify sha256, merge không đè entry user | US-804 AC2 | → T-012 | `scripts/fetch_lexicon.py` (mới), `app/main.py`, `app/static/app.js` | [x] |
| T-014 | `ContextTracker` trong live: giữ K câu gần nhất + tóm tắt chủ đề (LLM condense mỗi M câu, chạy nền never-fail); truyền context cho cả Ollama (bump num_ctx) lẫn OpenRouter | US-805 | ‖ với T-010..T-013 (sau T-001) | `app/live.py`, `app/correct.py` | [x] |
| T-015 | Tests theo từng PR: migration+rollback, extract_pairs (cặp thuật ngữ vs văn phong), build_bias rank/cap, API text+corrections, seed import idempotent, ContextTracker (fake LLM, không sleep) | US-801..805 | → theo từng task land | `tests/test_corrections.py` (mới), `tests/test_db.py`, `tests/test_live_context.py` (mới) | [x] |

### Ghi chú triển khai (write-back PR4/PR5)
- Seed lexicon: en_accent 115 entry (vượt guideline 40-80 vì mỗi thuật ngữ 1-2 biến âm); GET settings trả nhóm `lexicon: {bac,trung,nam,en,url}` theo style nested của `diarize`. Không đưa cặp rủi ro (dzậy/hén) — biến âm 2 chiều làm bias tệ đi.
- ContextTracker đặt ở `app/live.py` (state theo vòng đời phiên + thread nền, join khi Dừng); `app/correct.py` chỉ thêm `summarize_topic` stateless. Tracker nuôi bằng câu final raw tại `_finalize` (mọi câu, kể cả câu ngắn không qua pass 2). Refactor 2 backend về `_chat_ollama`/`_chat_openrouter` cùng build prompt qua `_prompt_for` — Ollama giờ nhận context; `CORRECT_NUM_CTX` 2048→4096.

### Ghi chú triển khai (write-back PR3)
- T-008 tách 2 hàm thay vì 1: `build_bias(user_glossary)` (term `right` cho ASR, cap 800 ký tự, phần user không bao giờ bị cắt) + `top_pairs(limit)` (cặp few-shot cho pass 2). Dedupe so casefold theo term tách dấu phẩy (exact), không substring — tránh drop "git" khi glossary có "GitHub".
- `_prompt_for`/`correct_text`/`_maybe_correct` thêm param `pairs` (4 param, 3 default) thay vì gom LlmOpts — chấp nhận vượt nhẹ guideline ≤3 param để sửa tối thiểu.
- Live snapshot `top_pairs(10)` tại `__init__` (start phiên), ít hơn upload (20) vì prompt live phải ngắn.

## Stacked PRs (>400 LOC → chia 5 PR, mỗi PR ≤400, độc lập deploy)

| PR | Tasks | Nội dung | Ghi chú |
|----|-------|----------|---------|
| PR1 | T-001, T-002, T-003, T-004 (+tests) | Schema + API + UI sửa transcript | **Integration point 1**: user sửa được ngay |
| PR2 | T-005, T-006, T-007 (+tests) | Trích cặp + màn Thư viện | Phụ thuộc PR1 |
| PR3 | T-008, T-009, T-010, T-011 (+tests) | Mồi library vào ASR + pass 2, glossary về server | **Integration point 2**: vòng học khép kín |
| PR4 | T-012, T-013 (+tests) | Seed lexicon 3 miền + accent, cập nhật online opt-in | Phụ thuộc PR3 (dùng build_bias) |
| PR5 | T-014 (+tests) | Rolling context live | Độc lập PR2-4, cần PR1 merge trước cho gọn diff |

## Edge Cases & Error Handling

- Library/DB lỗi khi build bias → dùng glossary user như hiện tại, job không fail (US-803 AC2).
- Diff nguồn sai: client gửi kèm `base_version` (bản text lúc mở editor); server diff vs bản đó, không diff vs raw_text (tránh trích trùng cặp LLM đã sửa). Version lệch (pass 2/diarize vừa ghi) → 409, UI báo reload.
- Bias vượt ~224 token initial_prompt → cap cứng khi build, ưu tiên count cao (Whisper bỏ đuôi âm thầm nếu không cap).
- Cặp văn phong lọt lưới → mặc định `pending`, chỉ `approved` mới được mồi; auto-approve chỉ khi lặp ≥2.
- Seed trùng cặp user tạo → UNIQUE(wrong,right), upsert giữ source user, cộng count.
- Fetch lexicon offline/checksum sai → giữ bản cũ, báo lỗi tiếng Việt, không ghi dở dang.
- ContextTracker LLM chậm/lỗi → subtitle dùng context cũ, không chặn `_correct_loop`.
- Live: library đổi giữa phiên không hiệu lực (spec frozen) — document trong UI như glossary hiện tại.

## Test Strategy (viết test trước theo AC)

- **US-801**: PATCH text → GET thấy edited_text; version lệch → 409. UI toggle 3 bản (test API trả đủ 3 field).
- **US-802**: `extract_pairs("cu bơ nét", "Kubernetes")` → 1 cặp; đảo cả câu/viết lại văn phong → 0 cặp; cặp lặp 2 lần → approved.
- **US-803**: `build_bias` rank theo count, cap độ dài; corrections rỗng → trả nguyên glossary user; DB hỏng (monkeypatch raise) → không ném ra ngoài.
- **US-804**: import seed idempotent (chạy 2 lần không nhân đôi); toggle vùng tắt → entry vùng đó không vào bias; checksum sai → bảng không đổi.
- **US-805**: fake LLM (không gọi thật, không sleep) — sau M câu context được condense; LLM raise → context cũ giữ nguyên, câu vẫn ra.
- Full suite (`pytest` + ruff + mypy) sau mỗi PR.

## Rollback

- Migration additive: rollback = `ALTER TABLE transcripts DROP COLUMN edited_text` (SQLite ≥3.35) + `DROP TABLE corrections`. Test rollback trong test_db.py ngay PR1, không dời đến deploy.
- Mỗi PR revert độc lập: PR3-5 chỉ thêm nhánh code có guard, revert không hỏng PR trước.
- Seed/remote lexicon xoá được bằng `DELETE FROM corrections WHERE source != 'user'` — không đụng dữ liệu user.
