# Plan: Slang/Teencode Lexicon (mở rộng FR-6)
- **Source**: US-815..817 (FR-6 Correction Library mở rộng — PRD stub cập nhật kèm plan này)
- **Status**: Implemented
- **Updated**: 2026-07-20

## Approach
Slang/teencode vào thẳng bảng `corrections` hiện có với `tag='slang'` — tái dùng toàn bộ hạ tầng US-803/804 (build_bias, top_pairs, INSERT OR IGNORE, UI duyệt). Ba nguồn theo quyết định user:
1. **US-815 — Seed tự soạn** (`app/data/lexicon/slang.json`, source=`seed`, approved): danh sách slang nói 2024–2026 tự soạn (wrong = Whisper nghe nhầm khả dĩ, right = chính tả chuẩn của từ lóng). Dataset teencode công khai (Vinorm, teencode4) đã khảo sát 2026-07-20: toàn viết tắt chat (`bme→bố mẹ`) — Whisper không bao giờ output dạng này, license NOASSERTION → **không nhúng vào seed commit**; script build hỗ trợ qua flag `--include-datasets` + filter phát âm, mặc định tắt.
2. **US-816 — LLM trend digest** (source=`trend`, **pending** chờ duyệt): nút Settings gọi OpenRouter (chat_once) liệt kê slang MXH đang hot dạng JSON cặp (nghe nhầm → chuẩn); không fallback Ollama (model 4B local không đủ kiến thức trend).
3. **US-817 — Web adapter best-effort** (source=`trend`, pending): fetch trang PUBLIC không đăng nhập (mặc định wiki Tiếng lóng — đã verify 200; user thêm URL bài báo qua setting `slang_sources`), tôn trọng robots.txt, không né anti-bot, bị chặn thì skip; text trang → cùng prompt trích xuất LLM như (2). TikTok/FB/X trực tiếp: API đóng/tường đăng nhập → ngoài phạm vi, đã báo user.

Ranking: sửa tối thiểu `_rank_rows`/`top_pairs` — khi có `regions`, tag `slang` được coi region-neutral cùng tier với vùng (nếu không sẽ bị seed vùng chèn hết cap 800 ký tự); `regions=()` hành vi giữ nguyên từng byte. Toggle `lexicon_slang` mặc định TẮT (họp trang trọng không bị nhiễm slang).

## Tasks
| ID | Task | Source | Dep | Files | Status |
|-----|------|--------|-----|-------|--------|
| T-101 | `scripts/build_slang_seed.py`: CURATED + dataset opt-in (filter phát âm, dedupe nội bộ + 4 file vùng) → sinh slang.json | US-815 | ‖ | scripts/build_slang_seed.py | [x] |
| T-102 | Sinh + commit `app/data/lexicon/slang.json` (~60 entry, schema {wrong,right}) | US-815 | → T-101 | app/data/lexicon/slang.json | [x] |
| T-103 | `SLANG_TAG`, `SEED_REGIONS += ("slang",)`, ranking slang region-neutral trong `_rank_rows` + `top_pairs` | US-815 | ‖ | app/corrections.py | [x] |
| T-104 | `lexicon_slang` vào SettingsIn + `_LEXICON_REGIONS` | US-815 | → T-103 | app/main.py | [x] |
| T-105 | Checkbox "Tiếng lóng/GenZ" + `LEX_IDS.lexSlang` | US-815 | → T-104 | app/static/index.html, app/static/app.js | [x] |
| T-106 | Tests US-815 (schema slang.json, import/toggle, ranking 2 chiều, cập nhật assert settings) | US-815 | → T-102,T-104 | tests/test_corrections.py | [x] |
| T-107 | `chat_once(system, user, timeout)` public trong correct.py (OpenRouter-only, raise khi lỗi) | US-816 | ‖ | app/correct.py | [x] |
| T-108 | `app/slang_trend.py`: prompt trích xuất, `_parse_entries` (validate lỏng, skip-not-raise), `llm_digest`, `run_trend_update` → import pending | US-816 | → T-107 | app/slang_trend.py | [x] |
| T-109 | `POST /api/lexicon/slang-trend` (503 thiếu key, 502 mọi nguồn fail, trả counts) | US-816 | → T-108 | app/main.py | [x] |
| T-110 | Nút "Cập nhật tiếng lóng" + badge pending + nguồn `trend` trong filter/label UI | US-816 | → T-109 | app/static/index.html, app/static/app.js | [x] |
| T-111 | Tests US-816 (parse garbage, import pending, 503, pending không vào bias) | US-816 | → T-109 | tests/test_slang_trend.py | [x] |
| T-112 | Web adapter: `_robots_ok` (robotparser), `_fetch_page` (httpx, UA riêng, fail→None), `_html_to_text` (stdlib HTMLParser), `web_digest` + setting `slang_sources`; wire vào `run_trend_update` | US-817 | → T-108 | app/slang_trend.py, app/main.py | [x] |
| T-113 | Tests US-817 (robots deny skip, fetch fail skip, html→text, partial failure) | US-817 | → T-112 | tests/test_slang_trend.py | [x] |
| T-114 | Apify adapter: `apify_digest` (actor `clockworks~tiktok-scraper`, endpoint run-sync-get-dataset-items — verify API 2026-07-20), `_harvest_text`, opt-in `APIFY_TOKEN` env; wire vào `run_trend_update` | US-818 | → T-108 | app/slang_trend.py | [x] |
| T-115 | Setting `slang_hashtags` + ô nhập UI (chỉ hiện khi server có APIFY_TOKEN, qua `apify_enabled` trong GET /api/settings) | US-818 | → T-114 | app/main.py, app/static/index.html, app/static/app.js | [x] |
| T-116 | Tests US-818 (hashtag setting, harvest text, gọi actor đúng input, lỗi/hết quota → skip nguồn, token rỗng không gọi) | US-818 | → T-114 | tests/test_slang_trend.py | [x] |

## Stacked PRs (≤400 LOC mỗi PR)
- **PR 1** = T-101..106 (US-815) — điểm tích hợp user-visible đầu tiên (toggle dùng được ngay).
- **PR 2** = T-107..111 (US-816) — nút trend + flow duyệt.
- **PR 3** = T-112..113 (US-817) — mở rộng nguồn web, phụ thuộc PR 2.
- **PR 4** = T-114..116 (US-818, bổ sung 2026-07-20 theo yêu cầu user "quất Apify") — caption MXH tươi qua Apify, phụ thuộc PR 2.

### US-818 — Apify adapter (bổ sung sau khi plan gốc Implemented)
Scrape MXH qua dịch vụ bên thứ ba thay vì tự scrape: actor Apify (mặc định `clockworks~tiktok-scraper`, đổi qua env `APIFY_ACTOR`) chạy trên hạ tầng Apify, app chỉ gọi REST `run-sync-get-dataset-items` rồi gom caption (`_harvest_text`, key `text/desc/description/title`) → cùng đường trích xuất LLM → pending. Opt-in `APIFY_TOKEN` (.env, như OPENROUTER_API_KEY); hashtag quét qua setting `slang_hashtags` (mặc định `xuhuong`). ToS nền tảng MXH: trách nhiệm chuyển cho lựa chọn dùng Apify của user — app không tự né anti-bot. Lỗi/hết quota → skip nguồn như mọi nguồn khác.

## Edge Cases & Error Handling
- LLM trả garbage/không phải JSON/lặp cặp → `_parse_entries` skip từng entry (đếm `skipped`), UNIQUE(wrong,right) + INSERT OR IGNORE chống trùng khi bấm lại.
- Thiếu OPENROUTER_API_KEY → 503 message tiếng Việt rõ ràng, DB không đổi.
- robots.txt cấm / 403 / timeout / non-200 → skip nguồn đó, đếm `sources_skipped`; mọi nguồn fail → 502.
- Slang nhiễm cuộc họp trang trọng → toggle seed mặc định OFF; trend luôn pending → chỉ vào bias sau khi user duyệt tay.
- Tắt toggle chỉ gỡ `source='seed', tag='slang'` — entry trend user đã duyệt là dữ liệu user, giữ lại.
- Cặp wrong không được là từ chuẩn thông dụng (không bao giờ "không"→"khum") — tránh pass 2 sửa bậy lời nói bình thường.

## Test Strategy
- US-815: schema + cross-file uniqueness (test hiện có tự phủ khi thêm vào SEED_REGIONS); import idempotent; toggle PUT/GET; ranking slang cùng tier vùng khi regions active, không đổi khi regions=().
- US-816/817: mock tại boundary (`correct.chat_once`, `httpx`) — không gọi mạng thật; endpoint counts; pending không vào build_bias tới khi approve.
- Full gate: `uv run pytest` + `ruff check app mcp_server tests` + `mypy` + `node --check app/static/app.js`.

## Rollback
- Không migration DB (tái dùng bảng/cột hiện có) → rollback = revert commit; tắt toggle `lexicon_slang` gỡ sạch seed slang khỏi bảng.
