---
name: init-project
description: Initialize a new project. First skill to run after cloning the framework. Creates project brief, scaffolds project, configures commands, sets up docs structure.
when_to_use: When project has no docs/project-state.md. Required before any other skill.
---

## Input/Output Contract

INPUT:
  - (Optional) BRD document — skip full interview, fast-track to `/product-plan` after init
  - (Optional) PRD document — skip full interview, fast-track to `/plan-feature` after init
OUTPUT:
  - `docs/project-brief.md` (project identity, tech stack, constraints)
  - `docs/project-state.md` (lifecycle state tracker)
  - `.claude/rules/_framework/{selected-templates}.md` (activated domain rules)
  - Scaffolded project (`package.json`, config files, minimal src structure)
  - Updated `CLAUDE.md` Commands section with real, working commands
  - (If BRD provided) `docs/brd-{name}.md`
  - (If PRD provided) `docs/product-plan.md`
NEXT: Depends on input:
  - No BRD/PRD → `/research-business`
  - BRD provided → `/product-plan`
  - PRD provided → `/plan-feature`
GATE: User must approve project brief before proceeding

## Phase 0: Safety Check

Before anything, detect if this is the right skill for the situation:

1. Check for `*.framework.*` files → if found, STOP: "You have .framework files from `setup.sh --merge`. Run `/apply-framework` instead."
2. Check for `docs/project-state.md` → if found AND has `Phase: Active Development` or any phase > 0-Init, STOP: "This project is already initialized. Did you mean `/plan-feature`, `/research-business`, or another skill?"
3. Check for `package.json` with real dependencies (> 3 deps) + `src/` directory with code files → WARN: "This looks like an existing project. `/init-project` will scaffold new files. Options:
   - Continue (will skip scaffolding, only create docs + configure CLAUDE.md)
   - Run `/apply-framework` instead (if you used `setup.sh --merge`)
   - Cancel"

Only proceed after user confirms (or if none of the above conditions matched).

## Phase 1: Input Detection

Before interviewing, analyze the user's first prompt:

| Input detected | Mode | Interview | Fast-track to |
|---|---|---|---|
| No BRD/PRD | Normal | Full interview | `/research-business` |
| BRD document | BRD mode | Extract from BRD, confirm only | `/product-plan` |
| PRD document | PRD mode | Extract from PRD, confirm only | `/plan-feature` |

**How to detect:**
- **BRD**: Contains "Functional Requirements", "User Personas", "Domain Rules", or user says "BRD" / "business requirements"
- **PRD**: Contains "User Stories", "Screen Inventory", "Priority Matrix", "API Outline", or user says "PRD" / "product plan"

**In BRD/PRD mode:**
- Extract project info (app type, tech stack, target users, scope) from the document
- Present extracted info to user for quick confirmation — NOT full interview
- Only ask questions the document doesn't answer (typically: package manager preference, deployment target)

## Phase 2: Project Interview

**Normal mode:** Gather project context through structured questions. Adapt based on answers — skip irrelevant questions.

**BRD/PRD mode:** Present extracted info and ask user to confirm or correct. Maximum 3 confirmation questions.

**Round 1 — Core Identity (always ask in normal mode, confirm in BRD/PRD mode):**
1. What are you building? (1-2 sentence description)
2. Who is the target user?
3. What type of app? (Web App / API-Backend / Mobile / CLI / Library / Other)
4. Tech stack? (or "recommend based on type")

**Round 2 — Scope & Context:**
5. Solo developer or team?
6. Greenfield or adding to existing code?
7. MVP scope — what is the ONE core thing it must do?
8. Any hard deadlines or constraints?

**Round 3 — Technical (only if complexity warrants it):**
9. Expected scale? (users, data volume)
10. Integration requirements? (auth provider, payment, external APIs)
11. Deployment target? (Vercel, AWS, self-hosted, etc.)

**Rules:**
- Maximum 15 questions total across all rounds.
- If user says "I don't know" to tech stack, recommend based on app type:
  | App Type | Default Stack |
  |---|---|
  | Web App (full-stack) | Next.js + Tailwind + Prisma + PostgreSQL |
  | API-Backend | Fastify + Prisma + PostgreSQL |
  | Mobile | React Native (Expo) |
  | CLI | Node.js + Commander |
  | Library | TypeScript + tsup |
- If project is simple (landing page, simple CRUD), offer "lightweight mode" — skip business/product phases.
- Never assume answers. If ambiguous, ask.

## Phase 3: Template Rule Matching

Based on app type and tech stack, recommend which template rules to activate.

**Matching logic:**
| Signal in answers | Recommended template |
|---|---|
| E-commerce, payments, Stripe | `app-ecommerce.md` |
| Real-time, chat, WebSocket, SSE | `app-realtime.md` |
| Multi-tenant, SaaS, tenant isolation | `app-multi-tenant.md` |
| AI, LLM, chatbot, embeddings | `app-ai-llm.md` |
| PWA, offline, service worker | `app-pwa.md` |
| CMS, content management, blog | `app-cms.md` |
| Dashboard, analytics, data visualization | `app-dashboard.md` |
| API-first, headless, tRPC, GraphQL | `type-safe-api.md` |
| Mobile, React Native, Expo | `mobile.md` |
| Monorepo, Turborepo, Nx, pnpm workspaces | `monorepo.md` |
| Terraform, Pulumi, IaC | `infra.md` |
| Observability, monitoring, tracing | `observability.md` |

**Present to user:**
1. List recommended templates with 1-line explanation each.
2. User picks which to activate (can add/remove from recommendations).
3. For each activated: copy from `.claude/templates/rules/{name}.md` to `.claude/rules/_framework/{name}.md` (templates are framework-owned; they belong in `_framework/`, tracked by the manifest). After copying, run `./sync-cursor-rules.sh` so `.cursor/rules/` picks them up.

## Phase 4: Gate — Approve Project Brief

1. Present the project brief summary to user (app type, stack, scope, activated rules).
2. Ask: "Approve this project brief? Or request changes?"
3. If changes requested → revise and re-present.
4. Only after explicit approval → proceed to scaffolding.

## Phase 5: Create Docs Structure

Create `docs/` directory with two files:

### File 1: `docs/project-brief.md`

```markdown
# Project Brief: {Project Name}

## Overview
- **Type**: {app type}
- **Description**: {1-2 sentences}
- **Target Users**: {who}
- **Tech Stack**: {stack}
- **Package Manager**: {pnpm/npm/yarn/bun}
- **Team**: {solo/team}
- **Deployment**: {target}

## MVP Scope
{The ONE core thing this must do}

## Constraints
- **Timeline**: {if any, or "None specified"}
- **Scale**: {expected, or "TBD"}
- **Integrations**: {list, or "None yet"}

## Activated Rules
- {list of template rules copied to .claude/rules/_framework/}

## Decisions Log
| # | Decision | Rationale | Date |
|---|----------|-----------|------|
| 1 | {tech stack choice} | {why} | {date} |
```

### File 2: `docs/project-state.md`

Use appropriate phase based on mode:

**Normal mode:**
```markdown
# Project State

## Current Phase
- **Phase**: 0 - Init
- **Status**: Complete
- **Next Step**: Run `/research-business` to create BRD for your first feature

## Active Feature
None yet.

## Features Backlog
(Populated after /product-plan)

## Phase History
| Phase | Status | Date | Artifact |
|-------|--------|------|----------|
| 0 - Init | Complete | {date} | docs/project-brief.md |

## Session Resume
Last updated: {date}
Summary: Project initialized. Ready for business requirements gathering.
```

**BRD mode:**
```markdown
# Project State

## Current Phase
- **Phase**: 1 - Business
- **Status**: Complete
- **Next Step**: Run `/product-plan` to create product specification

## Active Feature
None yet.

## Features Backlog
(Populated after /product-plan)

## Phase History
| Phase | Status | Date | Artifact |
|-------|--------|------|----------|
| 0 - Init | Complete | {date} | docs/project-brief.md |
| 1 - Business | Complete | {date} | docs/brd-{name}.md |

## Session Resume
Last updated: {date}
Summary: Project initialized with BRD. Ready for product planning.
```

**PRD mode:**
```markdown
# Project State

## Current Phase
- **Phase**: 2 - Product
- **Status**: Complete
- **Next Step**: Run `/plan-feature` to start implementing Feature 1: {name}

## Active Feature
Ready for planning: Feature 1 from priority matrix.

## Features Backlog
{Populated from PRD's priority matrix}

## Phase History
| Phase | Status | Date | Artifact |
|-------|--------|------|----------|
| 0 - Init | Complete | {date} | docs/project-brief.md |
| 1 - Business | Skipped | {date} | (PRD provided directly) |
| 2 - Product | Complete | {date} | docs/product-plan.md |

## Session Resume
Last updated: {date}
Summary: Project initialized with PRD. Ready to plan first feature.
```

**Save BRD/PRD if provided:**
- BRD → `docs/brd-{feature-name}.md`
- PRD → `docs/product-plan.md`

## Phase 6: Project Scaffolding

Scaffold the actual project so `dev`, `build`, `test`, `lint` commands work immediately.

**Step 1: Detect existing project**
- If `package.json` already exists → skip scaffolding, install missing deps only
- If no `package.json` → scaffold from scratch

**Step 2: Detect package manager**
- Check for existing lockfiles: `pnpm-lock.yaml` → pnpm, `yarn.lock` → yarn, `bun.lockb` → bun, `package-lock.json` → npm
- If none found, use user's preference from interview, or default to `pnpm`

**Step 3: Scaffold based on stack**

Create `package.json`, config files, and minimal `src/` structure. The goal is: all commands run without errors. No feature code — that comes in `/plan-feature`.

| Tech Stack | Key packages | Minimal files to create |
|---|---|---|
| Next.js | next, react, react-dom, typescript, @types/react, @types/node, eslint, eslint-config-next, vitest | `tsconfig.json`, `next.config.ts`, `src/app/layout.tsx`, `src/app/page.tsx`, `vitest.config.ts` |
| Vite + React | vite, react, react-dom, @vitejs/plugin-react, typescript, @types/react, eslint, vitest | `tsconfig.json`, `vite.config.ts`, `index.html`, `src/main.tsx`, `src/App.tsx`, `vitest.config.ts` |
| Fastify API | fastify, typescript, @types/node, tsx, eslint, vitest | `tsconfig.json`, `src/index.ts`, `src/app.ts`, `vitest.config.ts` |
| Express API | express, @types/express, typescript, @types/node, tsx, eslint, vitest | `tsconfig.json`, `src/index.ts`, `src/app.ts`, `vitest.config.ts` |
| Generic Node.js | typescript, @types/node, tsx, eslint, vitest | `tsconfig.json`, `src/index.ts`, `vitest.config.ts` |

**Rules:**
- All projects get: TypeScript (strict mode), ESLint, Vitest
- Add Tailwind + PostCSS if frontend project (Next.js, Vite)
- Add Prettier if user prefers (ask if not mentioned)
- Pin all dependency versions (exact, no `^` or `~`)
- Create `.env.example` with placeholder keys if integrations were mentioned
- Do NOT use `create-next-app` or `create-vite` (they fail in non-empty dirs) — write files directly

**Step 4: Install dependencies**
```bash
{package-manager} install
```

**Step 5: Add scripts to `package.json`**

Ensure these scripts exist:
```json
{
  "scripts": {
    "dev": "{appropriate dev command}",
    "build": "{appropriate build command}",
    "start": "{appropriate start command}",
    "test": "vitest run",
    "test:watch": "vitest",
    "lint": "eslint . --ext .ts,.tsx",
    "lint:fix": "eslint . --ext .ts,.tsx --fix",
    "typecheck": "tsc --noEmit"
  }
}
```

## Phase 6.5: Fill stack.yml

After scaffolding, fill `.claude/templates/stack.yml` from interview answers + scaffolded `package.json`. This creates a persistent record of the project's stack for use by `/optimize-context` and other skills.

Follow `.claude/guides/stack-detection.md` for the schema and detection logic.

Fill from what's known. Leave empty strings for fields not yet decided. Read versions FROM the project (package.json/lockfile/tool configs) — never copy the template's example versions blindly.

Set `provenance`: `verified_at` = today's date, `source` = `detected` (or `manual` if user-supplied), `detected_from` = the files you read (e.g. `package.json + pnpm-lock.yaml`).

## Phase 7: Configure CLAUDE.md Commands

Follow `.claude/guides/claude-md-commands.md` to update CLAUDE.md Commands section.

**Keep CLAUDE.md under 80 lines.** If adding project-specific rules would exceed this, put them in `.claude/rules/project/` files instead (user-owned — the framework never overwrites them). AI compliance drops when CLAUDE.md is too long — rules at the end get ignored.

## Phase 7.5: Configure Hooks (Deterministic Verification)

Hooks are deterministic (100% execution) vs rules which are advisory (~80%). Add PostToolUse hooks to `.claude/settings.json` for automatic lint/typecheck after every file edit.

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Edit|Write",
        "hooks": [
          {
            "type": "command",
            "command": "{pm} run typecheck 2>&1 | head -20; {pm} run lint 2>&1 | head -20"
          }
        ]
      }
    ]
  }
}
```

Ask user if they want hooks enabled. If yes, merge into existing `.claude/settings.json` preserving current permissions.

## Phase 8: Verify Everything Works

Run each command and confirm zero errors:

1. `{pm} run typecheck` → should pass (no type errors)
2. `{pm} run lint` → should pass (no lint errors)
3. `{pm} test` → should pass (0 tests is OK, no failures)
4. `{pm} run dev` → should start (verify it starts, then stop)

**If any command fails:**
- Fix the issue (missing config, wrong tsconfig path, etc.)
- Re-run until all 4 pass
- Max 3 fix attempts per command. If stuck, report to user.

## Phase 9: Context Optimization (Auto)

After scaffolding, clean up rules that don't match the project's stack. Use `stack.yml` (filled in Phase 6.5) as source of truth. Base/template rules live in `.claude/rules/_framework/` (framework-owned). After removing any, run `./sync-cursor-rules.sh` so `.cursor/rules/` stays in sync.

**Step 1: Identify irrelevant base rules**

| Rule file | Keep when | Remove when |
|-----------|-----------|-------------|
| `frontend.md` | `framework.name` is Next.js, Vite, React | API-only, CLI, library |
| `backend.md` | `framework.name` is Next.js, Fastify, Express, or any server | Pure frontend SPA with no API |
| `database.md` | `database.orm` or `database.engine` is set | No database in stack |
| `security.md` | Always keep | Never remove |
| `devops.md` | Always keep | Never remove |
| `testing.md` | Always keep | Never remove |

**Step 2: Identify irrelevant template rules**

For each activated template rule (from Phase 3), verify it still makes sense with `stack.yml`. Example: `app-realtime.md` activated but no WebSocket/SSE library in `package.json`.

**Step 3: Remove with confirmation**

If irrelevant rules found:
- List them with reason: "These rules don't match your stack:"
  - `frontend.md` — project is API-only (Fastify), no React/Next.js
  - `database.md` — no database configured
- Ask user: "Remove these? They won't load often (`paths` won't match), but removing keeps the directory clean."
- Delete confirmed files

**Step 4: Token budget check**

Count total tokens in always-loaded (rules without `paths`) + estimate auto-loaded rules:
- If > 4000 tokens: suggest `/optimize-context` for deeper compression
- Report: "Context budget: {N} tokens always-loaded, {M} tokens in path-matched rules"

## Phase 9.5: Baseline Audit (Auto)

Invoke `/audit-baseline` in auto-run mode against the scaffolded code. For a greenfield
project this is usually quick (little existing code), but it catches starter-template files
that already break the rules. Report-only; record anything not fixed to `docs/tech-debt.md`.
Do not block — skip silently if there is no source code yet.

## Phase 10: Final Summary & Next Step

Present to user:
- Project brief summary
- Tech stack installed
- All commands verified working
- Activated rules (with token count)
- Context budget: `{N}/{recommended max} tokens auto-loaded`

Suggest next step based on mode:
- **Normal**: "Project ready. Describe your first feature or run `/research-business` to start."
- **BRD mode**: "Project ready with BRD saved. Run `/product-plan` to create product specification."
- **PRD mode**: "Project ready with PRD saved. Run `/plan-feature` to start Feature 1: {name}."
