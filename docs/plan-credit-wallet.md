# Plan: Ví credit — tính phí token cho LLM cloud
- **Source**: US-601..606 (PRD FR-6 — NEW, write-back tại T-01/T-02)
- **Status**: Approved
- **Updated**: 2026-07-11


## Context

Manju hiện local-first: Whisper chạy trên máy, pass 2 sửa thuật ngữ qua Ollama (miễn phí) hoặc OpenRouter (key nằm trong `.env` user, trả tiền trực tiếp). User muốn thương mại hóa phần LLM: **mọi lần dùng OpenRouter (pass 2 file upload + sửa câu live subtitle) trừ vào ví credit của user; user nạp ví bằng thanh toán thật**. Quyết định đã chốt qua Q&A:

- Voice/audio **không rời máy user** — chỉ text đi online.
- Ví server-side trên **Supabase** (tận dụng Đợt 2 đang dở: Auth + Postgres + Edge Functions).
- Đơn vị ví = **credit trừu tượng**, markup nướng sẵn vào tỷ giá credit (bảng giá owner tự đặt).
- Hết ví → **chặn hẳn** tính năng trả phí (không âm thầm fallback), mời nạp. Ollama local vẫn luôn miễn phí và dùng được.
- Nạp ví: **PayOS (QR bank VN)**, webhook xác nhận tự động.
- Feature **chưa có trong BRD/PRD** → phải write-back spec trước (YC-6 / FR-6 / US-601..606).

## Kiến trúc

App local giữ 2 backend LLM: **Ollama (free)** và **cloud** (mới — thay thế gọi OpenRouter trực tiếp; `OPENROUTER_API_KEY` rời khỏi `.env` user, chuyển thành secret server). Backend cloud gọi Edge Function `llm-correct` kèm JWT user → function kiểm tra số dư → gọi OpenRouter bằng key CỦA SERVER → tính credit từ `usage` trong response → trừ ví atomic qua RPC SECURITY DEFINER → trả text đã sửa + credit info. 402 = hết credit → UI chặn + CTA nạp. Nạp: `payos-order` tạo đơn + link QR, `payos-webhook` verify chữ ký HMAC → cộng ví idempotent. Toàn bộ gate sau env `CLOUD_BILLING` (default off → app offline 100% như hiện tại).

**Auth đặt server-side trong FastAPI** (`app/cloud.py` mới), không dùng supabase-js: token cần trong Python (live loop + transcribe chạy server thread), app local single-user nên 1 session là đúng, và tiện thể gỡ luôn gap Đợt 2 (push đang không có nguồn access_token). `refresh_token` lưu SQLite `settings` (cùng mức tin cậy với `.env` hiện nay), `access_token` in-memory tự refresh.

## Supabase — `supabase/migrations/002_billing.sql` (additive, không đụng 001)

- `wallets(user_id PK→auth.users, balance_credits bigint ≥0)` — lưu ×1000 (milli-credit) tránh float drift khi trừ per-sentence.
- `credit_ledger` append-only: delta, balance_after, reason enum (`llm_correct|topup|adjustment|refund`), `request_id` unique (idempotent retry), model + prompt/completion_tokens.
- `pricing_rates(model PK, credits_per_1k_prompt, credits_per_1k_completion, active)` — markup nằm ở đây. Seed PLACEHOLDER.
- `topup_packages(code PK, amount_vnd, credits, active)` — seed PLACEHOLDER.
- `topup_orders(id, user_id, provider payos|sepay, provider_order_code unique, package_code, status pending|paid|failed|expired, webhook_payload)`.
- **RLS**: user chỉ SELECT dòng của mình (wallet/ledger/orders); KHÔNG có policy insert/update → chỉ RPC SECURITY DEFINER + service_role được ghi. `pricing_rates`/`topup_packages`: select cho authenticated.
- **RPC** (revoke execute khỏi anon/authenticated — chỉ service_role gọi từ Edge Function):
  - `spend_credits(user_id, credits, request_id, usage)` — idempotent theo request_id; single-statement update clamp-to-zero; ghi ledger. Race-safe.
  - `apply_topup(order_code, payload)` — guard `status='pending'` → cộng ví + ledger; webhook duplicate = no-op.
- Rollback: `supabase/migrations/rollback/002_billing_down.sql` (docs, không auto-run); pg_dump 3 bảng billing trước thao tác phá hủy.

## Edge Functions — `supabase/functions/` (Deno TS, region sin1)

- `_shared/billing.ts`: admin client (service_role), `computeCredits(usage, rate)`, verify chữ ký PayOS. Secrets qua `supabase secrets set`: `OPENROUTER_API_KEY`, `PAYOS_*`.
- **`llm-correct`** (verify_jwt): `POST {text ≤2200 chars, glossary?, context?, requestId}`. System prompt nằm server-side (copy TS của `_SYSTEM_PROMPT` — chống lạm dụng proxy làm LLM chùa; cross-reference comment 2 phía). Pre-flight estimate → thiếu thì **402** `{error, balanceCredits, estimatedCredits}`; đủ → gọi OpenRouter, đọc `usage` (field hiện đang bị vứt ở correct.py:125/143), `spend_credits`, trả `{text, usage, credits:{spentCredits, balanceCredits}}`. 502 upstream = transient (client trả text gốc, KHÔNG phải blocked). Chunking + `_guard`/`_clean_output` vẫn ở client Python.
- **`payos-order`** (verify_jwt): tạo `topup_orders` pending + PayOS payment-request → `{checkoutUrl, qrCode, ...}`. Poll trạng thái đơn: client đọc thẳng row qua PostgREST (RLS select) — không cần function.
- **`payos-webhook`** (no-verify-jwt): verify HMAC checksum → `apply_topup` → luôn 200 khi hợp lệ/duplicate.
- Live latency: warm-up ping `llm-correct` lúc mở phiên live (song song warm-up Ollama sẵn có ở `_correct_loop`), overhead steady-state <300ms so với budget 20s/câu.

## App local

- **`app/correct.py`**: dataclass `CorrectionResult(text, ok, blocked=False, credits_spent=0.0, balance=None)` — 2 hàm public trả kiểu này (đổi chữ ký nội bộ, sửa cả 3 caller + tests). `_correct_chunk_cloud()` gọi Edge Function kèm JWT + requestId. `llm_backend()` = cloud khi `CLOUD_BILLING` on + đã login + setting `llm_backend=cloud`, ngược lại ollama. Gỡ `_correct_chunk_openrouter` + `OPENROUTER_*` (key về server). Contract never-fail giữ nguyên cho transient; **402 → `blocked=True`** (trạng thái riêng, UI phải hiện — không im lặng). `correct_text` gặp 402 thì bỏ các chunk còn lại, cộng dồn credits_spent.
- **`app/cloud.py`** (mới ~120 LOC): `login/logout/access_token` (auto-refresh, refresh_token trong `settings` KV), `get_wallet`, `create_topup`, `get_order`.
- **`app/transcribe.py`**: `_maybe_correct` → CorrectionResult; job thêm `correction_blocked`, `credits_spent`; `TranscriptDraft`/`save_transcript` thêm `credits_spent`.
- **`app/db.py`**: cột `credits_spent REAL` trên `transcripts` — ALTER guarded bằng `PRAGMA table_info` trong `init()` (repo không có migration framework). Không mirror ledger local — Supabase là nguồn chân lý billing.
- **`app/live.py`**: gặp `blocked` → set `credit_blocked`, gửi WS `{"type":"credit_blocked", balance}` MỘT lần, ngừng enqueue câu mới vào hàng sửa (tránh 402 mỗi câu); warm-up nhánh cloud; cộng credits vào draft khi save.
- **`app/main.py`**: `POST /api/auth/login|logout`, `GET /api/auth/session`, `GET /api/wallet`, `POST /api/wallet/topup`, `GET /api/wallet/topup/{id}`, `GET /api/wallet/packages`; settings thêm `llm_backend` + `cloud_billing`. Tất cả trả 503 khi `CLOUD_BILLING` off.

## Frontend (`app/static/`)

- Settings: mục "Tài khoản & Credit" (ẩn khi flag off): form login ↔ card đã đăng nhập; picker backend (Local miễn phí / Cloud trả credit); card ví: số dư + nút Nạp + ledger gần nhất.
- Nạp: chọn gói → mở `checkoutUrl` PayOS (hosted checkout render QR, không thêm dep client) → poll đơn 3s tới `paid` → toast + refresh số dư; timeout 10 phút.
- 402 UX: upload → banner trên kết quả "Hết credit — bản chưa sửa thuật ngữ" + CTA nạp; live → pill "Hết credit — sửa thuật ngữ tạm dừng", sub vẫn chạy raw.
- Chip chi phí: `✦ AI · 1.2 cr` ở detail + history khi `credits_spent > 0`.

## Tasks

| ID | Task | Source | Dep | Files |
|---|---|---|---|---|
| T-01 | BRD write-back: YC-6 ví credit (v1.4) | NEW YC-6 | ‖ | BRD.md |
| T-02 | PRD FR-6 + US-601..606 + cập nhật Đợt/state | NEW US-601..606 | → T-01 | PRD.md, docs/project-state.md |
| T-03 | 002_billing.sql: bảng, RLS, spend_credits, apply_topup, seed | US-603/604/605 | → T-02 | supabase/migrations/002_billing.sql |
| T-04 | pgTAP test RLS + RPC (local stack) | US-603/604/605 | → T-03 | supabase/tests/002_billing_test.sql |
| T-05 | _shared/billing.ts (admin client, computeCredits, PayOS sig) | FR-6 | → T-03 | supabase/functions/_shared/billing.ts |
| T-06 | Function llm-correct (pre-flight, OpenRouter, deduct, 402) | US-605/606 | → T-05 | supabase/functions/llm-correct/index.ts |
| T-07 | Function payos-order | US-604 | ‖ T-06 | supabase/functions/payos-order/index.ts |
| T-08 | Function payos-webhook (verify sig, idempotent) | US-604 | → T-07 | supabase/functions/payos-webhook/index.ts |
| T-09 | Deno unit tests T-06..08 (fixtures dùng chung với Python) | — | → T-08 | supabase/functions/*/test.ts |
| T-10 | app/cloud.py CloudSession + auth endpoints | US-601 | → T-02 (‖ edge) | app/cloud.py, app/main.py |
| T-11 | correct.py: CorrectionResult + backend cloud, gỡ OpenRouter direct | US-602/605/606 | → T-06, T-10 | app/correct.py |
| T-12 | transcribe.py + db.py: job fields + cột credits_spent | US-605 | → T-11 | app/transcribe.py, app/db.py |
| T-13 | live.py: credit_blocked, WS msg, warm-up, cộng credits | US-605/606 | → T-11 | app/live.py |
| T-14 | main.py: endpoints wallet/topup/packages, setting llm_backend | US-603/604 | → T-10 | app/main.py |
| T-15 | UI: login + backend picker + card ví | US-601/602/603 | → T-14 | app/static/index.html, app.js |
| T-16 | UI: flow nạp (checkout + poll) | US-604 | → T-15 | app/static/app.js, index.html |
| T-17 | UI: banner/pill 402 + chip chi phí | US-605/606 | → T-13, T-16 | app/static/app.js, index.html |
| T-18 | Python tests: cloud backend, 402, wallet endpoints (mock httpx) | — | → T-14 | tests/test_correct.py, tests/test_cloud.py, tests/test_db.py |
| T-19 | E2E checklist thật (Supabase project + PayOS sandbox) | — | → T-17 | docs/plan-credit-wallet.md |
| T-20 | Cleanup: gỡ key khỏi .env docs, project-state | — | → T-19 | README.md, docs/project-state.md |

## Stacked PRs (mỗi PR ≤400 LOC)

PR-1 specs (T-01,02) → PR-2 SQL+pgTAP (T-03,04) → PR-3 llm-correct (T-05,06,09a) → PR-4 payments functions (T-07,08,09b) → PR-5 CloudSession (T-10) → **PR-6 correct.py cloud backend (T-11,12) ← integration point: sửa thuật ngữ qua cloud chạy end-to-end sau flag `CLOUD_BILLING`** → PR-7 live + wallet endpoints (T-13,14) → PR-8 frontend (T-15,16,17) → PR-9 E2E + docs (T-19,20).

## Test strategy

- **SQL/RLS**: `supabase start` + pgTAP (`supabase test db`), giả lập user qua `set local "request.jwt.claims"`. Cases: chỉ đọc được dữ liệu của mình; INSERT/UPDATE trực tiếp bị từ chối; spend_credits happy/clamp-to-zero/idempotent retry/2 session song song không âm; apply_topup pending→paid một lần, webhook duplicate no-op.
- **Edge Functions**: `deno test`, stub fetch với fixture response OpenRouter (có `usage`) + test vector chữ ký PayOS. Fixtures wire-schema check-in và **dùng chung với pytest** (2 phía test cùng contract).
- **Python**: pytest monkeypatch httpx: 200 → text + credits; 402 → blocked=True, text gốc, KHÔNG gọi Ollama; 502 → ok=False không blocked; correct_text dừng chunk khi 402; live ngừng enqueue sau credit_blocked; `init()` chạy 2 lần không lỗi ALTER; endpoints 503 khi flag off. Gates: ruff + mypy + full suite.
- **E2E manual** (checklist trong plan doc): tạo project → migration 001+002 → deploy functions + secrets → PayOS sandbox → login → nạp gói nhỏ → replay webhook kiểm idempotency → upload có sửa cloud → ledger + chip cost → xả hết ví → banner blocked (upload) + pill (live) → `CLOUD_BILLING=off` → app offline đầy đủ.

## Rollback & kill switch

- Migration 002 additive thuần; down-script trong docs; pg_dump bảng billing trước thao tác phá hủy. SQLite: cột mới nullable, rollback = bỏ qua.
- `CLOUD_BILLING=off` (default): correct.py ép Ollama, endpoints 503, UI ẩn — app như trước feature. Server-side: pause function → client rơi về transient path (text gốc).

## Risks / gaps cần user chốt (flag theo skill — ngoài spec hiện có)

1. **PayOS pháp lý**: cần onboard merchant VN (CCCD/bank); bán lại LLM usage có thể dính nghĩa vụ hóa đơn/VAT — xác nhận trước khi thu tiền thật. SePay để sẵn trong enum, chưa implement.
2. **Giá & gói**: `pricing_rates`/`topup_packages` seed PLACEHOLDER — owner đặt markup + tỷ giá thật trước launch.
3. **Key OpenRouter**: đặt hard spend-limit trên key + rate-limit per-user trong llm-correct (đếm ledger/phút) — JWT lộ sẽ đốt tiền server.
4. **Overshoot**: pre-flight là estimate; call thật có thể vượt số dư ≤1 call → clamp về 0, owner chịu (reserve/settle để sau).
5. **Đợt 2 coupling**: chưa có Supabase project — feature này ép tạo project + login Auth (trả luôn nợ Đợt 2), nhưng billing và org-sync rollout dính nhau; 001+002 apply cùng lúc.
6. **Refresh token plaintext trong SQLite**: chấp nhận cho app local single-user (ngang `.env` hiện nay) — ghi vào docs.
7. **Prompt trùng 2 nơi** (Python ↔ TS): drift risk, cross-reference comment.
8. Chưa có refund/hoàn tiền tự động — chỉnh tay qua service_role (`adjustment`/`refund`).

## Verification sau khi code xong

1. `uv run pytest` (full suite) + `uv run ruff check app mcp_server tests` + `uv run mypy` xanh.
2. `supabase test db` (pgTAP) + `deno test` các function xanh trên local stack.
3. Chạy E2E checklist T-19 trên Supabase project thật + PayOS sandbox (danh sách ở Test strategy).
4. Kiểm `CLOUD_BILLING=off`: upload + live chạy đủ bằng Ollama, không lộ UI billing.
