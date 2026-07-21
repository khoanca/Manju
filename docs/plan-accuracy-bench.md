# Plan: Accuracy Bench — đo độ chính xác transcript bằng bản sửa tay

- **Source**: FR-8 (net-new 2026-07-20), US-819..822 — chưa có trong PRD lúc lập plan, write-back cùng commit implement
- **Status**: Implemented (chờ user tạo bản chuẩn để chạy T-009/T-010 lấy số thật)
- **Updated**: 2026-07-20

## Vấn đề

Repo có 228 test nhưng không phép đo độ chính xác nào (`grep WER|accuracy|benchmark|golden` trên `tests/` + `scripts/` = rỗng). Hệ quả: mọi lỗi chất lượng đều do user phát hiện ngoài thực địa — 3 hotfix trong ngày 2026-07-20 (`3dbfbf7`, `71375ab`, `438d4f3`), tất cả sau khi suite đã xanh. Test hiện tại kiểm tra code chạy đúng cơ chế, không kiểm tra sản phẩm phiên âm có đúng không.

Hệ quả thứ hai, nặng hơn: **FR-6 (thư viện tự học) chưa từng hoạt động**. `corrections.extract_pairs` chỉ chạy khi user sửa transcript; DB có **0 bản ghi `edited_text`** → bảng `corrections` rỗng → mọi thứ xây trên nó (bias ASR, few-shot pass 2, seed lexicon, slang US-815..818) chưa bao giờ có dữ liệu thật. UI sửa đã tồn tại đầy đủ nhưng không ai biết dùng.

## Approach

Dùng **bản user sửa tay (`edited_text`) làm ground truth**, không tạo dataset riêng — tận dụng đúng dữ liệu FR-6 đã thiết kế để thu thập, nên một công sửa phục vụ hai mục đích (nuôi thư viện tự học + làm chuẩn đo).

WER/CER **tự viết bằng Levenshtein** (~40 dòng, không thêm dependency) thay vì `jiwer`: dự án local-first, và cần kiểm soát cách chuẩn hoá tiếng Việt (dấu câu, hoa thường, số) — thứ mà transform pipeline có sẵn của thư viện ngoài không khớp.

Không dựng UI mới. Màn Detail đã có textarea sửa + `PATCH /api/transcripts/{id}/text` chống ghi đè 409 + 3 view; chỉ thêm affordance và cờ đánh dấu bản chuẩn.

## Tasks

| ID | Task | Source | Dep | Files | Status |
|----|------|--------|-----|-------|--------|
| T-001 | Module WER/CER: Levenshtein + chuẩn hoá tiếng Việt; trả cả sub/del/ins để soi lỗi | US-820 | ‖ | `app/accuracy.py` | [x] |
| T-002 | Phát hiện lặp chữ chéo 2 segment liền nhau (rủi ro của đệm đuôi) | US-822 | → T-001 | `app/accuracy.py` | [x] |
| T-003 | Test T-001/T-002 — fixture từ chuỗi thực địa 2026-07-20 | US-820 | → T-002 | `tests/test_accuracy.py` | [x] |
| T-004 | Cột `golden` + migration additive + `db.set_golden` | US-819 | ‖ | `app/db.py` | [x] |
| T-005 | `PATCH /api/transcripts/{id}/golden` | US-819 | → T-004 | `app/main.py` | [x] |
| T-006 | Test T-004/T-005 | US-819 | → T-005 | `tests/test_accuracy_api.py` | [x] |
| T-007 | UI: affordance sửa rõ ràng + nút đánh dấu bản chuẩn | US-819 | → T-005 | `app/static/{index.html,app.js}` | [x] |
| T-008 | Script đo: raw_text vs golden, text vs golden → pass 2 giúp hay hại | US-820 | → T-001,T-004 | `scripts/bench_accuracy.py` | [x] |
| T-009 | Chạy lại ASR trên audio cũ theo từng cấu hình, chấm WER so golden | US-821 | → T-008 | `scripts/bench_accuracy.py` | [x] |
| T-010 | Bảng báo cáo so sánh cấu hình | US-821 | → T-009 | `scripts/bench_accuracy.py` | [x] |
| T-011 | Write-back PRD: FR-8 + US-819..822 | — | ‖ | `PRD.md` | [x] |

Ghi chú T-006: gộp vào `tests/test_accuracy_api.py` (gồm cả test migration idempotent), không tách sang `tests/test_db.py` như dự kiến — cùng chủ đề, tránh phân mảnh.

## Kết quả kiểm chứng khi implement

- `tests/test_accuracy.py` 11 passed, `tests/test_accuracy_api.py` 7 passed, `tests/test_bench_accuracy.py` 9 passed.
- ruff + mypy (15 files) + `node --check app.js` sạch.
- Smoke server thật: `/` phục vụ đủ `#goldenBtn`/`#goldenBadge`/`#editHint`; `PATCH .../golden` trả 404 với id sai, 400 khi chưa sửa tay (thử trên bản ghi thật `20260720-142230-live-1418`), DB không đổi.
- **Chưa chạy được số thật**: cần user tạo bản chuẩn trước. Đây là đường găng đã nêu.

## Số đo thật đầu tiên (2026-07-21, 3 bản có `edited_text`)

Chuẩn = `edited_text`. Đo bằng `app/accuracy`:

| bản ghi | dài | WER raw | WER pass 2 | pass 2 đụng | phá chỗ vốn đúng |
|---|---|---|---|---|---|
| 07-17 live-1510 | 36ph | 0.025 | **0.003** | 145 token | 0 |
| 07-20 live-1418 | 3.7ph | 0.395 | 0.377 | 12 token | 0 |
| 07-20 live-1748 | 53s | 0.042 | 0.042 | 2 token | 0 |

**Kết luận, ngược với nghi ngờ ban đầu:** pass 2 net-GIÚP, chưa từng phá token vốn đúng (0/3). Trên 1510 nó sửa đúng 145 token, hạ WER ~10×. Bản "tệ" 1418 là lỗi ASR thô (chuỗi `để để…` ~200 lần, đã vá ở `438d4f3`), không phải pass 2.

**Khiếm khuyết thật của pass 2:** bịa ~1 thuật ngữ/bản ghi ở chỗ ASR nghe không rõ — `doanh thu`→`budget`, xác nhận bằng A/B feed raw_text (prompt cũ bịa 2/6, không phụ thuộc `context`/glossary — cả hai đều rỗng ở 1748). Tần suất thấp nhưng độc: từ bịa vào `text`/`segments` → đầu độc `summarize_topic`, keyword search, và **tự khuếch đại** qua `mine_speaker_terms` → bias phiên sau.

**Đã sửa (không đợi T-009):**
- `corrections.mine_speaker_terms` chỉ giữ term chứng thực trong `raw_text`/`edited_text` (`_attested_vocab`) — cắt vòng tự khuếch đại. Đánh đổi: cũng bỏ term pass-2-sửa-đúng-nhưng-mờ (`đây ta`→`data`); chấp nhận vì hại cộng dồn ≫ lợi cộng dồn.
- `correct._SYSTEM_PROMPT` thêm đường lui "không chắc phát âm thì giữ nguyên" — A/B raw hạ bịa `budget` 2/6→0/6, `Kubernetes`/`deploy`/`SQL` vẫn sửa đúng.
- **Còn nợ (đúng tầm plan-feature):** pass 2 ghi lại chính xác nó đổi gì (span + độ gần âm) để UI duyệt + summary/mining/keyword loại chỗ chưa xác nhận — fix triệt để cho harm summary/keyword *trong cùng cuộc*.

## User Stories

- **US-819** — Đánh dấu bản chuẩn: user sửa transcript cho đúng rồi đánh dấu "dùng làm chuẩn đo", để bộ đo biết bản nào tin được.
- **US-820** — Đo độ chính xác: đo WER/CER của bản máy thô và bản pass 2 so với bản chuẩn, để biết pass 2 giúp hay hại.
- **US-821** — So sánh cấu hình: chạy lại ASR trên audio cũ với các cấu hình khác nhau (đệm biên VAD, denoise, lexicon, revise) rồi chấm điểm, để chốt hằng số bằng số liệu thay vì phỏng đoán.
- **US-822** — Bắt lỗi lặp chéo segment: đo được hiện tượng đệm đuôi nuốt sang câu kế tiếp gây lặp chữ.

## Câu hỏi tồn đọng mà T-009 phải trả lời

1. **Đệm biên VAD bao nhiêu?** Đo sơ bộ (20 utterance, 1 file, metric proxy "degeneracy" = tỉ lệ token lặp — KHÔNG phải độ chính xác): pad 0.0s → 0.166, 2 utterance lặp >50%; pad 0.3s → 0.092, 0 hỏng; pad 0.6s → 0.126, 0 hỏng. Hiện `live.py:453` đệm đầu `PREROLL_S=0.32` nhưng `live.py:479` đệm đuôi chỉ `0.2` — bất đối xứng, cả hai hằng số ra đời ở commit checkpoint `0bd7370`, không tài liệu nào biện minh.
2. **Toggle nào giúp, toggle nào hại?** `denoise_enabled`, `lexicon_{bac,trung,nam,en,slang}`, `flag_words`, `live_ident`, `revise`. Hiện **tất cả đang OFF** vì không ai biết cái nào có ích.

## Edge Cases & Error Handling

- Chưa có bản chuẩn nào → script báo rõ "cần ít nhất 1 transcript đánh dấu golden", exit sạch, không chạy ASR vô ích.
- Bản chuẩn nhưng mất audio gốc (`data/recordings/*.wav` bị xoá) → bỏ qua transcript đó cho T-009, vẫn đo được T-008.
- `edited_text` rỗng hoặc chỉ khoảng trắng → không coi là bản chuẩn hợp lệ.
- Chia cho 0 khi bản chuẩn không có từ nào → WER trả 0.0 nếu hyp cũng rỗng, 1.0 nếu ngược lại.
- Chạy lại ASR chiếm `_decode_lock` — script là tác vụ offline, phải nêu rõ nó sẽ chặn phiên live đang mở.

## Test Strategy

- WER/CER: ca chuẩn (giống hệt → 0.0; rỗng; thay/xoá/thêm từng loại), ca tiếng Việt (khác dấu câu/hoa thường → 0.0 sau chuẩn hoá).
- Lặp chéo segment: fixture từ chuỗi thực địa 2026-07-20.
- Cột `golden`: migration idempotent trên DB cũ (chạy `init()` hai lần).
- API: 404 id sai, toggle bật/tắt, không đụng `edited_text`.
- Không mock hàm đang test. Không test nào chạy ASR thật (chậm) — T-009 kiểm bằng engine giả.

## Rollback

- Cột `golden` là additive, mặc định 0 → bản cũ đọc bình thường, không cần rollback dữ liệu.
- Script bench là tác vụ đọc (trừ việc ghi cờ golden qua API) → không có gì để rollback.
- Nếu T-009 kết luận đổi hằng số đệm: đổi hằng số là commit riêng, tách khỏi bộ đo, có số liệu kèm.

## Ràng buộc — đường găng

**User phải tự sửa tay 3–5 transcript làm chuẩn (~30–60 phút).** Không có nó thì T-008/T-009/T-010 không chạy được. T-007 (UI) phải xong trước để việc sửa dễ chịu; đây là lý do T-007 không nên xếp cuối.
