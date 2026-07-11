---
name: audit-plan
description: Audit project health — verify implementation aligns with BRD/PRD, detect drift, score progress.
when_to_use: When user wants to check if the project is on track, verify alignment with BRD/PRD, detect scope creep, or get a project health report. Also when resuming after a long break or before a major decision.
---

## Input/Output Contract

INPUT:
  - `docs/project-state.md` (current phase, active features, progress)
  - `docs/product-plan.md` (user stories, acceptance criteria, data model, API outline, priority matrix)
  - `docs/brd-*.md` (per-feature BRDs from /research-business — business requirements, glossary, success metrics)
  - `docs/project-brief.md` (tech stack, constraints)
  - Codebase (read-only scan)
  - Git history (commit log, branch state)
OUTPUT:
  - Health report presented to user (not saved to file unless requested)
  - Actionable recommendations with severity levels
NEXT: Fix drift → `/plan-feature` | Continue building → next feature | Ship → `/deploy`
GATE: None (read-only analysis). User decides what to act on.

## Prerequisites

Before auditing:
1. Read `docs/project-state.md` — verify project is past Phase 0 (Init).
2. Read `docs/product-plan.md` — this is the source of truth for what should exist.
3. Read any `docs/brd-*.md` files if they exist — business context and glossary.
4. If no product plan exists: "No plan to audit against. Run `/product-plan` first."

## Step 1: Extract Requirements Baseline

Parse `docs/product-plan.md` and any `docs/brd-*.md` to build a requirements inventory:

- **User Stories**: Extract all US-{NNN} with acceptance criteria.
- **Data Model**: Extract all entities, fields, relationships.
- **API Endpoints**: Extract all planned routes with methods.
- **Screens/Pages**: Extract all planned UI surfaces (if frontend).
- **Success Metrics**: Extract measurable outcomes from BRD.
- **Glossary Terms**: Extract Code Names for semantic validation.

Output: structured list of requirements with IDs for traceability.

## Step 2: Requirements Traceability Scan

For each requirement from Step 1, search the codebase to determine implementation status.

Use parallel sub-agents for efficiency:
- **Schema Tracer**: Match data model entities → schema/migration files, types.
- **API Tracer**: Match planned endpoints → actual route files, handlers.
- **UI Tracer**: Match planned screens → actual page/component files (skip if no frontend).
- **Test Tracer**: Match acceptance criteria → test files covering that behavior.

Classification per requirement:
- **Implemented + Tested**: Code exists AND test covers acceptance criteria.
- **Implemented, No Tests**: Code exists but no test coverage for this requirement.
- **Partial**: Some acceptance criteria met, others missing.
- **Not Started**: No matching code found.
- **Drifted**: Implementation exists but diverges from spec (wrong naming, different behavior, extra scope).

## Step 3: Drift Detection

### 3a. Structural Drift
- Compare planned file/module structure against actual project layout.
- Detect unexpected modules/files not traceable to any requirement (potential scope creep).
- Detect planned modules that don't exist yet (gaps).

### 3b. Semantic Drift (Glossary Cross-Check)
If BRD has a Glossary:
- Grep codebase for synonyms of Glossary terms (e.g., `Customer` vs `User` when Glossary says `User`).
- Check route names match Glossary Code Names.
- Check DB column/table names match Glossary Code Names.
- Flag inconsistencies with specific file:line references.

### 3c. Behavioral Drift
- Compare acceptance criteria descriptions against actual implementation logic.
- Flag where implementation does something different from what the spec says.
- Use git log to detect commits touching areas outside current planned wave/phase.

### 3d. Dependency Drift
- Compare planned tech stack (from `docs/project-brief.md`) against actual `package.json`.
- Flag unplanned dependencies added without plan update.
- Flag planned dependencies not yet installed.

### 3e. Dependency Health
- `npm audit` for HIGH/CRITICAL vulnerabilities.
- Check outdated major versions.
- Flag abandoned packages (<100 downloads or >12 months stale).

### 3f. Documentation Currency
- Stale docs (code changed since doc update).
- Undocumented features.
- Removed features still in docs.

## Step 4: Architectural Conformance

Check structural rules against the codebase:

- **Circular dependencies**: Trace import chains, flag cycles.
- **Layer violations**: If architecture specifies layers (e.g., routes → services → repositories), verify no layer skipping.
- **Pattern consistency**: Check that similar features follow the same patterns (e.g., all API routes use the same validation approach).
- **Security boundaries**: Verify auth checks exist on all endpoints that require them per plan.

### 4a. ADR Compliance
If `docs/decisions/` exists: verify code follows accepted ADRs. Flag violations and stale ADRs.

### 4b. AI-Generated Code Correlation Check
- Flag tests created in same commit as code (correlated blind spots).
- Check for hallucinated APIs, happy-path-only tests, "tests that test nothing."

## Step 5: Quantitative Health Scoring

Calculate metrics and present as a scorecard:

| Metric | Formula | Target |
|--------|---------|--------|
| **Requirements Coverage** | implemented / total requirements | P0: 100%, P1: 90%+ |
| **Test Coverage per Requirement** | requirements with tests / implemented requirements | 80%+ |
| **Scope Creep Index** | unplanned items / total planned items | < 0.25 |
| **Glossary Conformance** | consistent terms / total term usages | 100% |
| **Architectural Conformance** | rules passing / total rules checked | 100% |
| **Wave Progress** | completed features in wave / total features in wave | per-wave |
| **Dependency Health** | no HIGH/CRITICAL audit findings | pass/fail |
| **Orphan Code Ratio** | files not traced to requirements / total files | < 0.10 |

Overall Health Score: weighted average (Requirements Coverage 25%, Test Coverage 20%, Scope Creep 15%, Architecture 15%, Glossary 10%, Wave Progress 5%, Deps 5%, AI Correlation 5%).

Thresholds:
- **90-100%**: On track. Minor adjustments only.
- **70-89%**: Attention needed. Specific areas drifting.
- **50-69%**: Significant drift. Recommend re-planning affected areas.
- **Below 50%**: Major misalignment. Recommend `/plan-feature` review for all active work.

## Step 6: Generate Report

Present to user in this structure:

```
## Project Health Report

### Overall Score: {score}% — {status}

### Requirements Status
| ID | Requirement | Status | Test | Notes |
|----|------------|--------|------|-------|
| US-001 | ... | ✅ Implemented + Tested | ✅ | |
| US-002 | ... | ⚠️ Partial | ❌ | Missing: {criteria} |
| US-003 | ... | ❌ Not Started | — | Planned for Wave 2 |

### Drift Findings
| Severity | Type | Location | Detail |
|----------|------|----------|--------|
| 🔴 HIGH | Behavioral | path:line | Spec says X, code does Y |
| 🟡 MEDIUM | Semantic | path:line | Uses "Customer" instead of "User" |
| 🔵 LOW | Structural | path/ | Unplanned utility module |

Severity legend: 🔴 HIGH = correctness/security. 🟡 MEDIUM = inconsistency. 🔵 LOW = style.

### Scope Creep
- Unplanned additions: {list with file paths}
- Impact: {assessment}

### Metrics Scorecard
| Metric | Score | Target | Status |
|--------|-------|--------|--------|
| Requirements Coverage | 75% | 100% | ⚠️ |
| ... | ... | ... | ... |

### Recommendations
1. [HIGH] {action} — {why}
2. [MEDIUM] {action} — {why}
3. [LOW] {action} — {why}

### Suggested Next Action
{Based on findings: fix drift, continue building, or ship}
```

### Trend Comparison (optional)
If previous audit exists, compare scores (↑↓→). Save to `docs/audit-history.md` only when user says "save". Max 10 entries.

## Execution Guidelines

- **Read-only**: This skill NEVER modifies code or docs. Analysis only.
- **Verify, don't assume**: Use `grep`, `find`, `Read` to check every claim. Never state "file exists" without verification.
- **Be specific**: Every finding must include file path and line number. No vague "some files may have issues."
- **Parallel agents**: Use sub-agents for Step 2 tracers to minimize time. Single agent for small projects (<20 files).
- **Severity levels**: HIGH = blocks correctness or security. MEDIUM = inconsistency or missing coverage. LOW = style or minor drift.
- **No false urgency**: If project is on track, say so. Don't manufacture problems.
- **Scope awareness**: Only audit against what's planned for current and completed waves. Don't flag Wave 3 features as "missing" when project is in Wave 1.
