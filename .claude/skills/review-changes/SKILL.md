---
name: review-changes
description: Review code changes before commit. Use after implementing a feature or fix.
when_to_use: After completing implementation, before committing. Also when user asks for a code review.
---

## Input/Output Contract

INPUT:
  - `docs/project-state.md` (active feature status = "Tested")
  - `docs/product-plan.md` (acceptance criteria for verification)
  - Git diff of changes
OUTPUT:
  - Review checklist results
  - Updated `docs/project-state.md` (active feature status = "Complete")
NEXT: Next feature from backlog, or project complete
GATE: User must approve the review before committing

## Prerequisites

Before reviewing:
1. Read `docs/project-state.md` — verify active feature status is "Tested" or "Implemented".
2. Read active feature's acceptance criteria from `docs/product-plan.md`.
3. Read `docs/plan-{feature}.md` — the task ledger (`T-NNN`) for this feature, to check traceability of the diff.
4. If no tests have been run, suggest: "Run `/comprehensive-test` first."

## Checklist — Code Quality

- [ ] No TODO/FIXME/HACK left behind.
- [ ] No hardcoded values (URLs, keys, magic numbers).
- [ ] No skipped or commented-out tests.
- [ ] No `console.log` or debug statements.
- [ ] All new behavior / critical logic has tests (assert visible behavior, not every function).
- [ ] Error handling at boundaries.
- [ ] Changes match the approved plan and Feature Preview (from `/plan-feature` Step 2) — no scope creep.
- [ ] Every changed code area traces to a `T-NNN` task in `docs/plan-{feature}.md`. An untraceable change = scope creep OR an un-updated spec — resolve one of the two (drop the change, or add/update the task upstream).
- [ ] `docs/plan-{feature}.md` task checkboxes reflect what actually landed; if code diverged from the spec, the spec was updated in the same change, not left stale.
- [ ] No unrelated changes in diff.
- [ ] Naming consistent with codebase conventions.
- [ ] Changes satisfy acceptance criteria from `docs/product-plan.md` for the active feature.
- [ ] `docs/project-state.md` is updated with current progress.
- [ ] No stale feature flags (flags introduced in earlier PRs that should now be removed).
- [ ] If feature flags added: expiry date or cleanup condition documented in code comment.

## Checklist — Anti-Hallucination

Verify these with tools (Read/grep/find), not from memory:
- [ ] All imports resolve: `find` each imported file path — it exists and exports the used symbol.
- [ ] All function calls match actual signatures: `grep` or `Read` each called function's definition.
- [ ] All env vars used are defined in `.env.example` or config loader.
- [ ] All DB column/table names match the schema/migration files.
- [ ] All third-party package imports exist in `package.json` dependencies.
- [ ] All types are derived from source of truth (Zod, Prisma, API schema) — no hand-written duplicates.
- [ ] No fabricated URLs, API endpoints, or documentation links.
- [ ] Test fixtures/mock data match actual schema field names and types.

## Checklist — AI-Generated Code Failure Modes

AI-generated code is disproportionately prone to privilege-escalation paths and architectural flaws. Check for:
- [ ] Correlated blind spots: if tests and implementation were both AI-generated, verify at least one test fails on known bad input before trusting coverage.
- [ ] Hallucinated APIs: every function/method call exists in the actual dependency version installed (not a plausible-but-nonexistent API).
- [ ] Logic gaps: code is syntactically correct but logically wrong (e.g., off-by-one, wrong comparison operator, missing null check on optional chain).
- [ ] Over-abstraction: unnecessary wrappers, helpers, or indirection that add complexity without value.
- [ ] Stale patterns: AI may generate patterns from older framework versions (Pages Router, getServerSideProps, class components).
- [ ] Security blind spots: missing auth checks on new endpoints/Server Actions, overly permissive CORS, unsanitized user input passed to queries.

## Checklist — Security

- [ ] No secrets in diff (grep: `password`, `secret`, `api_key`, `token`, base64 strings).
- [ ] Input validation present on all new endpoints/forms.
- [ ] New deps pinned to exact versions and pass `npm audit`.
- [ ] Auth at resource level + parameterized queries enforced (verify against `security.md`, `database.md`).

## Checklist — Performance

- [ ] No N+1 queries introduced (check DB calls in loops).
- [ ] New dependencies don't significantly increase bundle size.
- [ ] No synchronous heavy computation on main thread.
- [ ] Frontend rules applied: next/image, virtualization >100 rows, tree-shakeable imports (see `frontend.md`).

## Checklist — Observability

For every backend PR, answer: "Will we know when this feature fails?"
- [ ] Key operations have structured log statements (success + failure paths).
- [ ] Error paths log with appropriate level (ERROR for action-required, WARN for degraded).
- [ ] New endpoints have response time tracking.
- [ ] If feature has SLO implications → alerting rule exists or is planned.

## Verify

- Run test command from CLAUDE.md — all pass.
- Run lint command from CLAUDE.md — no errors.
- Run typecheck command from CLAUDE.md — no type errors.
- Review `git diff` — only expected changes.

## Optional: hard enforcement (opt-in)

Traceability above is enforced softly here (a review gate). To make it mechanical, a project MAY add a `Stop` hook in `.claude/settings.json` that runs `/audit-plan`'s drift check and blocks on HIGH drift. Not enabled by default — it adds friction on every stop. Turn it on only when spec-rot has actually bitten.

## After Approval

1. Update `docs/project-state.md`:
   - Mark active feature as "Complete" with date.
   - Move to next feature in backlog (or mark wave complete).
   - Update Session Resume.
2. Suggest next action based on state:
   - More features in current wave → "Next feature: {name}. Run `/plan-feature` to start."
   - Wave complete → "Wave {N} complete. Proceed to Wave {N+1}? Or ship current version?"
