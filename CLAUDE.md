# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

This is a **development framework template** — not a runnable application. It provides Claude Code skills, rules, and Cursor rules that get copied into target projects via `setup.sh`.

**New project**: `setup.sh` → `/init-project`
**Existing project**: `setup.sh --merge` → `/apply-framework`

**Lifecycle**: `/init-project` → `/research-business` (BRD) → `/product-plan` (PRD) → `/scout-repos` (fork-or-build) → `/plan-feature` → code → `/comprehensive-test` → `/review-changes` → `/deploy`
**Health check**: `/audit-plan` (verify implementation aligns with BRD/PRD, detect drift, score progress — run anytime after Phase 1)
**Maintenance**: `/optimize-context` (reduce token usage by compressing rules/skills/docs)

**Key structure**:
- `.claude/rules/` — loaded recursively. Split by ownership:
  - `_framework/` — **framework-owned** (backend, frontend, database, security, devops, code, git, guardrails, testing). Each carries `source: framework`. Tracked by hash in `.claude/.framework-manifest.json`; `setup.sh` upgrades these only if unmodified. **Do not hand-edit** — your edits become merge conflicts on upgrade.
  - `project/` — **user-owned**. The framework never writes here. Put business/domain rules here. When a `project/` rule and a `_framework/` rule overlap, the project rule wins (be explicit about the override).
  - Path scope: a rule with `paths` frontmatter loads only on matching files; without `paths` it loads every turn.
- `.claude/skills/` — slash command definitions with SKILL.md specs
- `.claude/guides/` — shared reference docs used by multiple skills (stack detection, commands config, tech evaluation)
- `.claude/templates/` — app-type rule templates + stack.yml + .claudeignore template
- `.cursor/rules/` — Cursor rules (.mdc), **generated** from `.claude/rules/**` by `sync-cursor-rules.sh`. Never hand-edit; edit `.claude/rules/` (source of truth) then run `./sync-cursor-rules.sh`. `--check` fails if out of sync (CI guard).
- `setup.sh` — copies framework files into a target project; maintains `.framework-manifest.json` for safe upgrades
- `sync-cursor-rules.sh` — regenerates `.cursor/rules/*.mdc` from `.claude/rules/**/*.md` (anti-drift; single source of truth)

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

### Layer 0 — Decompose & map (every actionable prompt)

Before routing, break the goal into the smallest independent pieces. Skip ONLY for pure Q&A/explain/review (answer directly).

- For a code/feature task: split the feature into small work items. For each, mark **‖ parallel** (no dependency on another item) or **→ sequential** (depends on an item — name which). Build an execution map:
  ```
  A. schema + migration        → (blocks B, C)
  B. API endpoint              ‖ with C, after A
  C. validation/types          ‖ with B, after A
  D. UI                        → after B
  E. tests                     ‖ per-item, after each item lands
  ```
- Then ASK how to execute: **(1)** I spawn agents to run parallel items concurrently, or **(2)** you open multiple sessions yourself. Don't assume — this is a user decision.
- If user picks multiple sessions: write the map to `docs/parallel-plan.md` (items, parallel/sequential, dependencies, "session N runs X") for them to follow.
- The map sets parallelism; it does NOT skip gates. Still run the normal Layer 1/2 flow, `/scout-repos`/`/plan-feature` gates, and domain rules per item.

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
