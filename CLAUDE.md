# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

**Manju — Meeting Transcriber**: app transcribe cuộc họp local-first (tiếng Việt xen thuật ngữ tiếng Anh), Whisper chạy trên máy user.

- `app/` — FastAPI + PWA vanilla JS (`app/static/`): upload file hoặc live subtitle qua WebSocket (`app/live.py`); pass 2 sửa thuật ngữ bằng LLM (`app/correct.py` — Ollama local, fallback OpenRouter)
- `app/engines.py` — chọn tier ASR theo máy: mlx (Apple Silicon) → cuda → cpu
- `mcp_server/` — MCP server (stdio) cho Claude đọc transcript
- Data: SQLite `data/manju.db` (nguồn chân lý) + artifact `data/transcripts/*.txt`
- Rules: `.claude/rules/_framework/` do framework quản (không sửa tay — upgrade qua `setup.sh`); rule riêng đặt ở `.claude/rules/project/` (thắng khi trùng). `.cursor/rules/` generate bằng `./sync-cursor-rules.sh` (`--check` là CI guard).
- Docs: `docs/project-state.md` (trạng thái + Session Resume), `docs/tech-debt.md` (nợ baseline), BRD.md, PRD.md, `.claude/templates/stack.yml` (stack đã detect)

## Language

- Code in English. Respond in Vietnamese.

## Commands

- Install: `uv sync`
- Dev: `uv run uvicorn app.main:app --reload` (http://localhost:8000)
- MCP server: `uv run python mcp_server/server.py`
- Test: `uv run pytest`
- Lint: `uv run ruff check app mcp_server tests` (auto-fix: `ruff check --fix`)
- Typecheck: `uv run mypy`

## Routing

Two-layer routing. Layer 1 handles 80%+ of prompts. Only escalate to Layer 2 when needed.

**Layer 0 — Decompose & map** (every actionable prompt): break the goal into work items, mark ‖ parallel / → sequential, ASK user how to execute. Full rule: `.claude/rules/project/routing-layer0.md` (auto-loaded).

### Layer 1 — Intent (always runs first)

Understand what the user wants. If actionable without deep planning, execute immediately.

| Intent | Action |
|--------|--------|
| Question / explain / review | Answer directly. No planning. |
| Explicit skill (`/plan-feature`, etc.) | Run skill. Don't bypass its safety checks. |
| `.framework.*` files exist | `/apply-framework` |
| No `docs/project-state.md` | `/init-project` |
| Product plan / vague idea without clear scope | `/research-business` → `/product-plan` → `/scout-repos` → `/plan-feature` |
| Bug / error | Reproduce first. Single-file obvious fix → fix directly. Multi-file or unclear root cause → `/debug`. Production emergency → `/debug` fast-track. |
| Test / write tests / coverage | `/comprehensive-test` to set strategy (WHAT to test, by app type), then `/gen-test` to derive cases methodically (HOW) and fill gaps. Use comprehensive-test first. |
| Simple task (single file, clear scope) | Code directly. No plan needed. |
| **None of the above** | → Layer 2 |

### Layer 2 — Scope & depth (only for implementation tasks)

Assess scope to decide planning level. When in doubt, plan more — over-planning is cheaper than rework.

| Scope | Action |
|-------|--------|
| 2-3 files, existing patterns clear | `/research-codebase` → brief plan in chat → code |
| Modify/enhance existing feature | `/research-codebase` → assess impact → code (≤3 files) or `/plan-feature` (>3 files) |
| 3+ files, cross-module, or major dep upgrade | `/plan-feature` (MUST plan before coding) |

**Scope traps**: "Add a field" sounds simple but often spans schema + API + UI + validation + tests. Refactoring ≥ 3 files always needs a plan.

**Utility skills** (invoke when relevant, no routing needed): `/deploy`, `/audit-plan`, `/optimize-context`, `/audit-skills`, `/review-changes`, `/audit-baseline` (scan EXISTING project files for framework-rule violations — too long, escape hatches, bloated docs; report-only, opt-in fixes). Runs automatically at the tail of `/apply-framework` and `/init-project`; callable standalone anytime.

**Gates**: NEVER skip a gate without user approval. Before `/plan-feature` on a new product or Wave-1 feature, `/scout-repos` (build-vs-fork) must have run — or the user explicitly skipped it. Domain rules auto-load by path scope (`paths` frontmatter) when touching matching files.

## Session

- Start: read `docs/project-state.md` Session Resume section.
- When reading large BRD/PRD docs, load only the relevant `FR-`/`US-` slice for the task at hand — not the whole file. Keep context lean; avoid context rot.
- End: update `docs/project-state.md` after milestones.
- `/compact` at >40% context. `/clear` between unrelated tasks.
- On compaction preserve: phase, active feature, modified files, test results, decisions, errors.
