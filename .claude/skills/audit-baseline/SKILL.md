---
name: audit-baseline
description: Scan a project's EXISTING files for violations of framework rules (too long, type escape hatches, bloated always-loaded docs, obvious inefficiencies). Report-only — never auto-fixes without per-item approval.
when_to_use: Right after applying the framework to an existing codebase (auto-invoked at the tail of /apply-framework and /init-project), or standalone anytime the user wants to know what existing code breaks the rules. NOT for reviewing new changes — use /review-changes for diffs.
---

## Purpose

Framework rules are forward-looking — they govern code Claude *writes*. They do NOT
retroactively scan files that already exist. This skill closes that gap: it measures
the existing codebase against the rules and reports what's out of line.

It is a **baseline debt** tool, not a cleanup bot. Default posture: **report, don't touch.**
Existing "violations" are often intentional (generated code, vendored files, contract specs).
So: surface everything, fix only what the user explicitly approves, and record the rest
as tracked debt instead of silently rewriting working code.

## Input/Output Contract

INPUT: An existing project with framework rules installed (`.claude/rules/_framework/`).
OUTPUT:
  - A severity-grouped report of violations (printed to chat).
  - `docs/tech-debt.md` written/updated with everything NOT fixed this run.
  - Only the files the user explicitly approved are modified.
GATE: **Never modify any existing project file without per-item user approval.**
  Auto-fix is opt-in, one item (or batch) at a time. Reporting and writing
  `docs/tech-debt.md` are the only non-interactive actions.

## Guardrails (read before scanning)

- Do NOT rewrite a file just because a metric trips. Generated code, fixtures,
  migrations, vendored/third-party, and contract specs legitimately break "rules."
- Exclude by default: `node_modules/`, `dist/`, `build/`, `.next/`, `vendor/`,
  `*.min.*`, lockfiles, `*.generated.*`, anything in `.gitignore`/`.claudeignore`.
- Hard violations are objective (measured). Soft flags are subjective (AI judgment) —
  present them as "worth a look," never as facts. Mark them clearly.
- If unsure whether something is intentional → it goes to the report as a flag, NOT a fix.

## Step 1: Scope the scan

1. Read `.claude/templates/stack.yml` (or detect language from file extensions) to pick
   the right escape-hatch patterns and length conventions.
2. Build the exclude list (above + `.claudeignore` patterns).
3. Pull thresholds from the installed rules so the audit stays in sync with them:
   - function length / params → `.claude/rules/_framework/code.md`
   - always-loaded token budget → `/optimize-context` (default warn at >4000 auto-loaded tokens)
   - CLAUDE.md line budget → 80 lines

## Step 2: Hard violations (objective — measured, grouped by rule)

Run detection over source files (respect excludes). Example commands — adapt to stack:

**Type escape hatches** (`code.md`: "No `any`/`Object`/`dynamic`"):
```bash
# TS/JS
rg -n --glob '!**/*.d.ts' '\bas any\b|: any\b|<any>|: Object\b' src
# Python
rg -n '#\s*type:\s*ignore|: Any\b|cast\(Any' src
# Dart/Flutter
rg -n '\bdynamic\b' lib
```

**Banned constructs** (`code.md`):
```bash
rg -n '\beval\(' src                         # eval
rg -n 'catch\s*\([^)]*\)\s*\{\s*\}' src      # silent catch (empty block)
rg -n '\?\s*[^:]+\?[^:]+:[^:]+:' src          # candidate nested ternary (verify by eye)
```

**Dangerous ops in scripts** (`code.md`: no `rm -rf`/`DROP TABLE`/force push):
```bash
rg -n 'rm -rf|DROP TABLE|push --force|push -f' --glob '*.sh' --glob '*.sql' --glob '*.yml'
```

**Function length / params** (`code.md`: <40 lines, ≤3 params): identify functions
exceeding the threshold (use the language's tooling, or scan brace/indent spans).
Report file + symbol + measured size. Do not auto-split — that's a refactor, not a fix.

**Auto-loaded context budget**:
```bash
# Rules WITHOUT `paths:` load every turn — sum their size
for f in $(rg -L --files .claude/rules); do
  head -5 "$f" | rg -q '^paths:' || wc -l "$f"
done
```
Flag if total always-loaded (rules-without-paths + CLAUDE.md) is large; defer detail to /optimize-context.

**Duplicate rules** across `_framework/` and `project/`: same directive in two files →
flag (keep in the more specific one).

## Step 3: Markdown / docs bloat

- `CLAUDE.md` > 80 lines → flag (AI compliance drops; tail rules get ignored).
- Any rule file WITHOUT `paths:` and large (>~150 lines) → always-loaded bloat.
- Project docs `.md` (BRD/PRD/guides) > ~500 lines → suggest splitting so tasks load
  only the relevant `FR-`/`US-` slice (per the Session rule). Report-only.
```bash
find . -name '*.md' -not -path '*/node_modules/*' -not -path './.git/*' \
  | xargs wc -l | sort -rn | head -20
```

## Step 4: Soft efficiency flags (subjective — FLAG ONLY, never assert)

Read (don't grep) suspicious hotspots and note *candidates*, each with a one-line reason:
- N+1 query shapes (DB call inside a loop).
- Obvious O(n²) over large collections; repeated work that could be hoisted.
- Dead code / unreferenced exports.
- Heavy synchronous work on a hot path.

Each soft flag MUST say "needs human judgment" and cite why. No fixes proposed unless asked.

## Step 5: Report

Group by severity. Keep it scannable:
```
=== Baseline Audit — {project} ({N} files scanned, {M} excluded) ===

HARD (objective, breaks a rule)
  1. src/legacy/payment.ts:88    fn `process` is 180 lines        code.md (<40)
  2. src/api/user.ts:12          `as any` on req.body             code.md (no escape hatch)
  3. CLAUDE.md                   112 lines                        init-project (<80)

DOCS (always-loaded / bloat)
  4. .claude/rules/project/biz.md  220 lines, no paths: → loads every turn
  5. docs/PRD.md                   1400 lines → split by US- slice

SOFT (needs human judgment — NOT auto-fixed)
  6. src/orders/list.ts:40       DB query inside .map() — possible N+1
  7. src/utils/calc.ts:8         nested loop over `items` — O(n²)?

Summary: {hard} hard, {docs} docs, {soft} soft.
```

## Step 6: Opt-in fix (per item or batch) — GATE

Ask: **"Fix which? (none / item numbers / 'all hard' / 'all docs')"**. Then:
- Fix ONLY approved items. Apply surgical changes per `code.md` (minimum diff, match style).
- After each fix: typecheck → lint → test the touched area (per `testing.md`/guardrails).
- Soft flags are NOT auto-fixed even under "all" — they require an explicit per-item yes.
- Never batch-rewrite generated/vendored/migration files even if approved by "all" —
  re-confirm those individually.

## Step 7: Record remaining as tracked debt

Write everything NOT fixed to `docs/tech-debt.md` (create if missing; append/update a dated
section, don't duplicate existing entries):
```markdown
## Baseline audit — {YYYY-MM-DD}

Tracked debt (existing files that break framework rules; fix opportunistically).

### Hard
- [ ] src/legacy/payment.ts:88 — fn `process` 180 lines (code.md <40)

### Docs
- [ ] docs/PRD.md — 1400 lines, split by US- slice

### Soft (review when touching the file)
- [ ] src/orders/list.ts:40 — possible N+1 in `.map()`
```
This keeps going-forward enforcement strict while letting the team pay down old debt
deliberately, not all at once.

## Step 8: Summary

```
=== Baseline Audit Done ===
Fixed: {n} (verified: typecheck/lint/test)
Tracked in docs/tech-debt.md: {m}
Re-run /audit-baseline anytime, or after paying down debt.
```

## Auto-run mode

When invoked at the tail of `/apply-framework` or `/init-project`, run Steps 1–5 and 7
automatically (report + write `docs/tech-debt.md`), then offer Step 6 (opt-in fix). Do not
block setup completion — if the user defers, everything is already captured as tracked debt.
