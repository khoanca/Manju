# Project State

## Current Phase
- **Phase**: 2 - Product
- **Status**: Complete (đang build Đợt 1)
- **Next Step**: Chạy `/plan-feature` cho hạng mục kế tiếp của Đợt 1 (xem PRD ưu tiên), hoặc `/audit-plan` để soát tiến độ vs BRD/PRD.

## Active Feature
**Ví credit — tính phí token cho LLM cloud** (PRD FR-6 / BRD YC-6, US-601..606). Status: **Plan Approved** — plan: [docs/plan-credit-wallet.md](plan-credit-wallet.md). Branch: `feat/credit-wallet`.

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
Last updated: 2026-07-11
Summary: /apply-framework chạy lại — không còn artifact `.framework.*`. Baseline audit + refactor hoàn tất (xem docs/tech-debt.md): hàm dài/nhiều params gom vào dataclass, CLAUDE.md 73/80 dòng (Layer 0 tách sang .claude/rules/project/routing-layer0.md), git init + checkpoint, 27 test + ruff + mypy xanh. Đợt 2 (org sync) đang dở — xem mục bên dưới.

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
