# Project State

## Current Phase
- **Phase**: 2 - Product
- **Status**: Complete (đang build Đợt 1)
- **Next Step**: Chạy `/plan-feature` cho hạng mục kế tiếp của Đợt 1 (xem PRD ưu tiên), hoặc `/audit-plan` để soát tiến độ vs BRD/PRD.

## Active Feature
**Slang/Teencode Lexicon** (FR-6 mở rộng, US-815..817, branch `feat/slang-lexicon` từ `main`) — **Implemented** 2026-07-20. Plan: [docs/plan-slang-lexicon.md](plan-slang-lexicon.md), PRD stub trong FR-6. Slang/teencode MXH vào bảng `corrections` tag `slang`: **US-815** seed tự soạn 61 entry (`app/data/lexicon/slang.json` — dataset teencode công khai toàn viết tắt chat + license NOASSERTION nên chỉ opt-in qua `scripts/build_slang_seed.py --include-datasets`), toggle `lexicon_slang` mặc định OFF, ranking slang region-neutral khi có vùng active (không bị seed vùng chèn hết cap 800); **US-816** nút "Cập nhật tiếng lóng" → `POST /api/lexicon/slang-trend` → `correct.chat_once` (OpenRouter-only) → nhập `source='trend'` `status='pending'` chờ duyệt (503 thiếu key, 502 mọi nguồn fail); **US-817** web adapter best-effort (`app/slang_trend.py`): trang public không đăng nhập (mặc định wiki Tiếng lóng, thêm URL qua setting `slang_sources`), robots.txt tôn trọng, không né anti-bot — TikTok/FB/X trực tiếp NGOÀI phạm vi (user đã chốt). **US-818** (bổ sung cùng ngày, user yêu cầu): Apify adapter — caption TikTok tươi theo hashtag qua actor `clockworks~tiktok-scraper` (API verify thật), opt-in `APIFY_TOKEN` trong .env, hashtag qua setting `slang_hashtags` (ô UI chỉ hiện khi có token), lỗi/hết quota → skip nguồn. 4 PR stacked: dfca0da → 35d28ee → df14c7e → (PR4 Apify). Files mới: `app/slang_trend.py`, `app/data/lexicon/slang.json`, `scripts/build_slang_seed.py`, `tests/test_slang_trend.py`. Còn lại: chưa merge (cần review người); UI chưa soát browser thật; chưa chạy thử Apify với token thật (user tự điền .env).

### Feature trước
**Live Intelligence** (FR-7, US-806..814, branch `feat/correction-library` — stack tiếp sau correction library) — **Implemented** 2026-07-20. Plan: [docs/plan-live-intelligence.md](plan-live-intelligence.md), PRD stub FR-7. 9 hạng mục tăng độ chính xác live subtitle, triển khai song song bằng 4 agent (engines / corrections+db+main / denoise / UI) + tích hợp live.py: **US-806** topic-bias (topic từ US-805 re-rank lexicon + tiêm vào initial_prompt, `_refresh_bias` swap spec giữa phiên — prompt ASR xếp user glossary CUỐI vì Whisper cắt đầu giữ đuôi); **US-807/808** personal lexicon (bảng `speaker_terms`, mine heuristic ASCII sau diarize, start card chọn người tham dự); **US-809** title/agenda làm topic mồi + tên transcript; **US-810** cột `speakers.region` → ưu tiên cặp sửa đúng vùng; **US-811** revision (decode_scored/min_logprob < −0.6 → revise nền → WS `revise` → mới pass 2; cpu dùng model to hơn; **mlx tạm None** — mlx-whisper 0.4.3 chưa có beam search, code sẵn chờ upstream; cuda None); **US-812** flag_words opt-in (word probability < 0.5 → prompt pass 2); **US-813** denoise noisereduce opt-in (thread decode, WAV lưu raw); **US-814** speaker-ID realtime (embed utterance final ≥1.5s, thread riêng, WS `speaker`, đổi người → bias theo người nói). Mọi tính năng never-fail. Settings mới: `flag_words`, `denoise_enabled`. Dep mới: noisereduce 3.0.3 (không torch). Test: full suite 194 test + ruff + mypy + node --check xanh. **Đã merge vào main 2026-07-20** (`ed4b25b`, user yêu cầu merge trực tiếp — gộp cả speaker-layer + correction-library, 16 commit). Còn lại: smoke live trên browser thật chưa chạy; mlx revise chờ upstream beam search. **HOTFIX 2026-07-20 (bug thực địa, main `3dbfbf7`)**: subtitle live hiện "chủ đề"/"J. J. J." — Whisper nhại initial_prompt văn xuôi; đã gỡ tiêm "Chủ đề: X." khỏi prompt (topic chỉ còn re-rank term, mô tả US-806 phía trên đọc theo hotfix này) + guard `_is_token_loop` chặn segment lặp token. BÀI HỌC: initial_prompt Whisper chỉ được chứa danh sách term, cấm văn xuôi.

### Feature trước (cùng branch)
**Correction Library** (branch `feat/correction-library`, stack lên `feat/speaker-layer`) — **Implemented** 2026-07-15. Plan: [docs/plan-correction-library.md](plan-correction-library.md), PRD stub FR-6. Sửa transcript + thư viện tự học (không train model): diff trích cặp sai→đúng → bảng `corrections` → mồi vào Whisper initial_prompt (build_bias, cap 800 ký tự) + few-shot pass 2; glossary về server settings (localStorage = cache, tự migrate); seed lexicon 3 miền + accent Anh (246 entry, toggle theo vùng, online update opt-in qua URL + sha256); rolling context live (ContextTracker, Ollama cũng nhận context). 5 PR stacked commit trên branch: 13d537c → 0f25607 → 0e6c38e → dc60881 → e4cbbe1. **124 test xanh** (ruff+mypy+node check), smoke test server thật đạt. Files mới: `app/corrections.py`, `app/data/lexicon/*.json`, `scripts/fetch_lexicon.py`, `tests/test_{corrections,live_context}.py`. Còn lại: chưa merge (cần review người, stack sau speaker-layer); UI chưa soát trên browser thật (chỉ node --check + API smoke).

### Feature trước nữa
**Speaker Layer** (branch `feat/speaker-layer`, từ `main`) — **Implemented** 2026-07-14. Plan: [docs/plan-speaker-layer.md](plan-speaker-layer.md). Diarization + nhận diện giọng (voiceprint) qua sherpa-onnx (offline, không token HF), giữ mlx-whisper. 4 PR: timestamp+SRT/VTT → diarize pass → đặt tên thủ công → voiceprint enroll+auto-ID. 59 test xanh (ruff+mypy). Files: `app/{diarize,subtitle}.py` (mới), `app/{engines,db,transcribe,main}.py`, `app/static/{app.js,index.html}`, `scripts/fetch_diarize_models.py`, `tests/test_{subtitle,diarize,speakers_api,voiceprint}.py`. Setting `diarize_enabled` mặc định off; model tải qua `uv run python scripts/fetch_diarize_models.py` (~33MB, gitignored). Còn lại: chưa merge vào main (cần review người); billing/credit-wallet branch tách riêng.

## Features Backlog
Nguồn: [PRD.md](../PRD.md) §2 (FR-1..). Điền chi tiết khi chạy `/plan-feature`.
- FR-1 — Engine ASR theo năng lực máy (EngineRegistry: mlx → cuda → cpu)
- YC-1 — Transcribe voice-to-text (upload + ghi trực tiếp, lưu audio + transcript)
- YC-2 — Xử lý nội dung pha Việt–Anh (chọn model, hotwords, ngôn ngữ chính)
- YC-3 — Pass 2 LLM soát thuật ngữ (Ollama/OpenRouter, guardrail chống sửa quá tay)
- YC-4 — Live subtitle realtime từ mic
- (Đợt sau) Org sync text lên Supabase

## Phase History
| Phase | Status | Date | Artifact |
|-------|--------|------|----------|
| 0 - Init | Complete | 2026-07-09 | docs/project-brief.md |
| 1 - Business | Complete | 2026-07-09 | BRD.md (v1.3) |
| 2 - Product | Complete | 2026-07-09 | PRD.md (v1.0, Approved) |

## Session Resume
Last updated: 2026-07-20
Summary: Slang/Teencode Lexicon (US-815..817) implemented trên branch `feat/slang-lexicon` (3 commit stacked, chưa merge — cần review người). Điểm cần nhớ: (1) trend entry luôn `pending` — không vào bias tới khi user duyệt; (2) tắt toggle `lexicon_slang` chỉ gỡ `source='seed'`, entry trend đã duyệt giữ lại (chủ đích); (3) `chat_once` OpenRouter-only, KHÔNG fallback Ollama; (4) không scrape TikTok/FB/X trực tiếp — đã chốt với user (ToS/anti-bot), thay bằng LLM digest + trang public qua `slang_sources`. Feature trước cùng ngày: Live Intelligence (FR-7, US-806..814) — xem mục dưới. Điểm cần nhớ khi resume: (1) mlx revise trả None cho tới khi mlx-whisper có beam search — nếu nâng mlx-whisper, bỏ comment check trong `MlxEngine.revise` là tự chạy; (2) prompt ASR giờ xếp user glossary CUỐI (khác bản pass 2 user-first) — đừng "sửa" lại tưởng là bug; (3) speaker-ID live cần voiceprint đã enroll + models diarize, không thì tự tắt; (4) ĐÃ merge vào main 2026-07-20 (`ed4b25b`) theo yêu cầu user — main giờ chứa speaker-layer + correction-library + live-intelligence; các branch feat/* giữ lại làm mốc lịch sử. Đợt 2 (org sync) vẫn dở — xem mục bên dưới.

### Trạng thái Đợt 1
Kiểm tra 2026-07-11: **Đợt 1 hoàn chỉnh** — FR-1..FR-4 đều đã cài đặt (EngineRegistry, SQLite+migration, audio dir configurable, multi-session, wake lock, WS reconnect/resume+replay, PWA+OPFS). Code compile + import sạch.

### Tech debt / gaps
- ✅ ĐÃ XONG (2026-07-11): cấu hình test/lint/typecheck. Thêm pytest+ruff+mypy, `tests/` (27 test, pass), cấu hình trong pyproject.toml. Gate `uv run pytest / ruff check / mypy` đều xanh.
- ✅ ĐÃ XONG (2026-07-11): git init + checkpoint. `.env` được ignore đúng; `data/manju.db*` đã gỡ khỏi tracking; `uv.lock` chuyển sang tracked (git.md: commit lockfile).
- ✅ ĐÃ XONG (2026-07-11): baseline refactor theo /audit-baseline — mọi hàm đạt chuẩn code.md (<40 dòng, ≤3 params) qua dataclass specs (`TranscriptRecord`, `SyncState`, `DecodeSpec`, `JobSpec`, `TranscriptDraft`, `LlmOpts`, `TranscribeForm`). Chi tiết: docs/tech-debt.md.
- `.env` chứa OPENROUTER_API_KEY thật (đã gitignore). Cân nhắc rotate nếu từng lộ.
### Đợt 2 — org sync (đang build, kickoff 2026-07-11)
Đã build phần nền không cần Supabase project thật:
- ✅ Schema + RLS: [supabase/migrations/001_org.sql](../supabase/migrations/001_org.sql) — orgs, org_members, visibility_grants, transcripts_text (text-only, owner_id default auth.uid()), 3 helper SECURITY DEFINER + policy thực thi ma trận quyền PRD FR-5.
- ✅ Push module: [app/org.py](../app/org.py) — upsert qua PostgREST bằng access_token user (RLS enforced, không service_role).
- ✅ Endpoint: `POST /api/transcripts/{id}/push` + `GET /api/transcripts/{id}/sync` ([main.py](../app/main.py)); sync_state helpers + JOIN vào list ([db.py](../app/db.py)).
- ✅ Test: [tests/test_org.py](../tests/test_org.py) + sync_state trong test_db.py.

**Chưa làm (Đợt 2 còn lại):**
- Chạy migration trên một Supabase project thật (chưa có project → SQL chưa được kiểm tra trên DB sống; `auth.users`/`auth.uid()` chỉ tồn tại trên Supabase).
- Đăng nhập Supabase Auth ở client (lấy access_token) + UI nút "Đẩy lên tổ chức" + hiển thị sync status trong lịch sử.
- Edge Function invite (bootstrap admin/member đầu tiên — hiện phải seed qua service_role).
- Màn org viewer + quản lý grants (admin).
