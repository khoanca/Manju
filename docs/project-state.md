# Project State

## Current Phase
- **Phase**: 2 - Product
- **Status**: Complete (đang build Đợt 1)
- **Next Step**: Chạy `/plan-feature` cho hạng mục kế tiếp của Đợt 1 (xem PRD ưu tiên), hoặc `/audit-plan` để soát tiến độ vs BRD/PRD.

## Active Feature
**Ví credit — tính phí token cho LLM cloud** (PRD FR-6 / BRD YC-6, US-601..606). Status: **Implemented** (T-01..T-18,T-20 xong; T-19 E2E chờ Supabase project thật + PayOS merchant — checklist trong plan). Plan: [docs/plan-credit-wallet.md](plan-credit-wallet.md). Branch: `feat/credit-wallet` (9 commit, chưa merge main).

Files mới: `supabase/migrations/002_billing.sql` (+ rollback + pgTAP), `supabase/functions/{_shared,llm-correct,payos-order,payos-webhook}/`, `supabase/config.toml` (port +10 — máy chạy nhiều project Supabase), `app/cloud.py`, `tests/test_cloud.py`. Sửa: `app/correct.py` (CorrectionResult, gỡ OpenRouter direct — key về server), `app/transcribe.py`, `app/db.py` (cột `credits_spent`), `app/live.py` (WS `credit_blocked`), `app/main.py` (auth + wallet endpoints), `app/static/` (UI billing), BRD v1.4, PRD v1.1, README.

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
Last updated: 2026-07-11 (chiều)
Summary: Build xong **FR-6 ví credit** trên branch `feat/credit-wallet`: Supabase billing schema (RLS + RPC atomic, pgTAP 18/18 trên local stack port 54331+), Edge Functions llm-correct/payos-order/payos-webhook (deno test 13/13), app local backend cloud (CorrectionResult, 402→blocked không fallback), UI ví/nạp/402. Gates: 42 pytest + ruff + mypy xanh. **Việc còn lại:** T-19 E2E thật (owner cần: Supabase project, PayOS merchant, đặt giá thật thay PLACEHOLDER, rotate OPENROUTER_API_KEY trong .env — app không còn đọc key này). Lưu ý kỹ thuật: image Postgres local segfault khi authenticated gọi hàm bị revoke → pgTAP dùng has_function_privilege; service_role phải được GRANT execute tường minh sau revoke PUBLIC.

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
