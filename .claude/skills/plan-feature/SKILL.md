---
name: plan-feature
description: Create implementation plan before writing code. Use when task requires more than 3 files.
when_to_use: When the user asks to build a feature, refactor a large section, or any task touching 3+ files. Skip for single-file obvious changes.
---

## Input/Output Contract

INPUT:
  - `docs/project-state.md` (current phase = 2-Complete or 3-Active, active feature identified)
  - `docs/product-plan.md` (approved product spec — user stories, data model, API outline)
  - `docs/project-brief.md` (tech stack, constraints)
OUTPUT:
  - `docs/plan-{feature}.md` (durable implementation plan — numbered tasks T-NNN traced to US-NNN; presented to user for approval)
  - Updated `docs/project-state.md` (active feature status = "Plan Approved", plan artifact path recorded)
NEXT: Code implementation → `/comprehensive-test` → `/review-changes`
GATE: User must approve implementation plan before coding begins

## Prerequisites

Before starting:
1. Read `docs/project-state.md` — identify the active feature and its status.
2. Read ONLY the active feature's slice of `docs/product-plan.md` — its US-{NNN}, acceptance criteria, and the screen/API/data rows that reference them. Don't load the whole plan into context; pull by ID.
3. Read `docs/project-brief.md` for tech stack constraints.
4. Load the **Ubiquitous Language Glossary** from the BRD (Section 10). All entity names, routes, types, and variables in the plan MUST use Glossary Code Names.
5. If no product plan exists, suggest: "Run `/product-plan` first."
6. Build-vs-fork gate: if this is the first feature of a new product (Wave 1) and `docs/project-state.md` records no `/scout-repos` decision, stop and suggest: "Run `/scout-repos` first to decide fork-vs-build — or confirm you want to build from scratch." Skip this check for later features once a fork-vs-build decision exists.

## When to Plan vs Start Coding

Plan when: 3+ files affected, unfamiliar code, unclear approach, cross-module changes.
Skip when: single-file change describable in one sentence.
Work in a **plan → act → verify loop**: plan enough to start safely, then implement and verify in tight cycles. Re-plan the remainder when reality diverges (lazy replanning, Step 8) — don't front-load an exhaustive plan that drifts before code exists.

## Step 1: Research Codebase

Run `/research-codebase` — it already orchestrates the Locator / Analyzer / Pattern-Finder sub-agents. Don't redefine that work here.

Output needed before planning: files identified, patterns/conventions found, constraints, risks.

## Step 2: Feature Preview (GATE — user must approve before detailed planning)

Before technical planning, present a consolidated picture of THIS feature so the user validates direction early — catching spec errors here is 10x cheaper than after implementation.

**This is extraction, not authoring.** Pull the relevant slice from `product-plan.md` and the BRD by ID and quote it — only synthesize where the docs leave a gap, and flag that gap explicitly. Do NOT re-derive content that already exists upstream.

Cover, for this feature only:

### 2a. Who & Why
- The persona + benefit from the feature's US-{NNN} ("As a… so that…") and the BRD success metric. Quote, don't rewrite.

### 2b. Workflow
- The happy path + top alternate/error paths from the BRD User Flows (§4) that apply to this feature. Cite the flow; mark decision points.

### 2c. UI/UX (skip if no frontend)
- The rows from product-plan Screen Inventory touched by this feature: name, route, key states (empty/loading/populated/error/edge).
- Add an ASCII wireframe or bullet layout only where the inventory doesn't already make intent clear.

### 2d. Data & Integration Touchpoints
- Entities created/read/updated/deleted (from the data model) and external systems (from BRD Integration Points) this feature touches. Validation rules the user should confirm.

**Present as a single summary. Ask:**
> "Does this match what you have in mind? Anything to add, remove, or change before I plan the implementation?"

**Rules:**
- Anything you surface that is NOT in product-plan/BRD is a gap → flag it explicitly; if the user accepts it, add to the Glossary/plan before proceeding.
- If user changes scope → update the preview, re-confirm. No stale assumptions.
- If user says "skip" or "looks good" → proceed to Step 3.
- Pure backend/CLI feature → skip 2c, focus on 2b and 2d.
- Keep preview under 30 lines — this is alignment, not documentation.

## Step 3: Tech Stack Evaluation

Only when proposing new libraries/tools/patterns. Follow `.claude/guides/tech-stack-evaluation.md`.

## Step 4: Write Plan

Reference the active feature from `docs/product-plan.md` and the approved Feature Preview from Step 2:
- Problem statement from user stories (US-{NNN}).
- Acceptance criteria become test cases.
- Data model entities inform schema design.
- API endpoints inform route structure.
- Priority matrix dependencies inform implementation sequence.

Save the plan to `docs/plan-{feature}.md` — a durable, versioned artifact with the same standing as the BRD and product-plan. Do NOT leave it only in chat. Structure:

```markdown
# Plan: {Feature Name}
- **Source**: US-{NNN} (product-plan Wave {N}, Feature {#})
- **Status**: Planning | Approved | In-Progress | Implemented
- **Updated**: {date}

## Approach
{1 paragraph: pattern/architecture chosen + why over alternatives}

## Tasks
| ID | Task | Source | Dep | Files | Status |
|-----|------|--------|-----|-------|--------|
| T-001 | {task} | US-{NNN} (AC: {scenario/criterion}) | ‖ | {paths} | [ ] |
| T-002 | {task} | US-{NNN} | → T-001 | {paths} | [ ] |

## Edge Cases & Error Handling
- {from BRD alternate/error paths}

## Test Strategy
- {per acceptance criterion — prefer writing tests first}

## Rollback
- {per-migration rollback, tested during implementation}
```

**Task rules:**
- Every task gets a stable `T-NNN` ID and a **Source** cell tracing to its US-{NNN} (and the specific acceptance criterion / Gherkin scenario it satisfies). A task with no Source is scope creep — remove it, or add the requirement upstream first.
- **Dep** column reuses Layer 0 notation: `‖` (parallel — no dependency) or `→ T-NNN` (sequential — name the blocker). This is the ledger that lets parallel agents split work without collision.
- Completes the traceability chain: `FR → US → T → code → test`.

## Step 5: Decompose into Deliverables

If the plan estimates > 400 lines changed (mandatory), otherwise skip to Step 6:
- Split into stacked PRs, each ≤ 400 LOC, independently reviewable and deployable.
- Define explicit dependency order: PR 1 → PR 2 → PR 3.
- Each PR must: pass all tests, not break existing features, have a clear scope description.
- Typical split pattern: (1) schema/types → (2) data layer → (3) business logic → (4) API/routes → (5) UI components → (6) integration/E2E tests.
- Each stacked PR lists the `T-NNN` tasks it delivers; a task belongs to exactly one PR.
- Mark which PR is the "integration point" where the feature becomes user-visible.
- If using feature flags: code can merge behind flag before all PRs are complete.

## Step 6: Validate Plan

- No circular dependencies introduced.
- Consistent with existing patterns.
- All affected tests identified.
- If split into stacked PRs: each PR validated independently for deployability.
- Migration safety checked (if DB changes):
  - Migration has a tested rollback method.
  - Rollback tested DURING feature implementation, not deferred to deploy.
  - No data loss: verify backfill correctness, orphan handling, default values.
  - Destructive changes (column drop/rename) use 2-deploy rollout.
- No breaking changes to public APIs. If API changes: verify backward compatibility with existing clients.

### Semantic Validation (Glossary Cross-Check)

If the BRD has a Glossary (Section 10), verify naming consistency:
1. Every entity/model name in the plan matches a Glossary Code Name.
2. Every route/endpoint uses Glossary Code Names (e.g., `/api/orders` not `/api/purchases` if Glossary says `Order`).
3. Every variable/type proposed in the plan aligns with Glossary conventions (PascalCase types, camelCase fields).
4. If a new term appears that isn't in the Glossary, flag it: either add to Glossary or use existing term.
5. No synonyms: if the plan uses `User` and `Customer` interchangeably, resolve to the Glossary term.

## Step 7: Get Approval

Present plan to user. Do NOT write code until user approves.

## Step 8: Implement

- Execute in planned dependency order.
- Run tests after each completed phase or logical unit (not every file). Run the full suite after implementation.
- Checkpoint commit after each completed phase.
- On failure: fix within 3 attempts → if stuck, replan the remainder (lazy replanning), don't retry the same approach.
- Mark `T-NNN` tasks `[x]` in `docs/plan-{feature}.md` as you complete them — the plan file is the live progress ledger, not just chat.
- **Spec write-back**: if implementation reveals the spec is wrong or incomplete, update it in the SAME commit as the code — never let code silently diverge. Propagate upward by scope of change: a changed task → `docs/plan-{feature}.md`; a changed US / data model / API → `docs/product-plan.md`; a changed business rule or Glossary term → the BRD. Then continue.

## Step 9: Update Progress

After feature implementation is complete:
1. Set `docs/plan-{feature}.md` Status to "Implemented" and verify every `T-NNN` is `[x]`. Any unchecked task = incomplete or descoped — resolve (finish it, or record it as descoped) before proceeding.
2. Update `docs/project-state.md`:
   - Mark current feature status as "Implemented".
   - Record files created/modified.
   - Update Session Resume with summary.
3. Suggest: "Feature implemented. Run `/comprehensive-test` to generate tests."
