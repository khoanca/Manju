---
name: apply-framework
description: Auto-merge CodingFramework into existing project. Run after setup.sh --merge. Merge is automated; rule deletions ask first.
when_to_use: After setup.sh --merge creates .framework.md files. Also when user says "apply framework" or "merge framework".
---

## Input/Output Contract

INPUT: Project with `.framework.md` / `.framework.mdc` files from `setup.sh --merge`
OUTPUT: All `.framework.*` merged and deleted, stack.yml filled, CLAUDE.md Commands updated, baseline audit run
GATE: Merge/append steps are automated. Deleting any rule file (Step 10) requires explicit user approval — never delete without confirmation.

## Ownership model (how setup.sh produced these files)

- `.claude/rules/_framework/` — framework-owned, hash-tracked in `.framework-manifest.json`.
  A `_framework/{name}.framework.md` appears ONLY when you had locally modified that framework
  rule (hash mismatch), so setup.sh refused to overwrite and left the new version beside it to merge.
  Unmodified framework rules were upgraded in place — no artifact, nothing to do.
- `.claude/rules/project/` — user-owned. setup.sh NEVER writes here, so it never produces
  `.framework.md` here. When a `project/` rule overlaps a `_framework/` rule, the project rule wins.

## Step 1: Scan

```bash
find . -name "*.framework.*" -type f | sort
```
None found → tell user to run `setup.sh --merge` first, stop.

## Step 2: Merge CLAUDE.md

If `CLAUDE.framework.md` exists:
1. Read both user's `CLAUDE.md` and `CLAUDE.framework.md`.
2. Append missing sections (check by `## Heading` match): Language, Task Classification, Routing, Session.
3. Do NOT duplicate existing sections or touch user's sections.
4. If > 80 lines after merge: extract non-essential sections to `.claude/rules/` files.
5. Delete `CLAUDE.framework.md`.

**Line budget priority** (keep in CLAUDE.md):
1. Project header + Commands (user's own)
2. Language
3. Task Classification + Routing
4. Session
5. Everything else → `.claude/rules/`

## Step 3: Merge Rules

For each `.claude/rules/_framework/{name}.framework.md` (these exist only where you had
edited a framework rule — see Ownership model):
1. Parse both files by `## Heading`.
2. Append sections from the new framework version that don't exist in your version.
3. Keep your version for sections that exist in both (your edits win).
4. Delete `{name}.framework.md`.
5. Do NOT touch `.claude/rules/project/` — those are user-owned and never have artifacts.

After merging any rule, regenerate Cursor rules: `./sync-cursor-rules.sh` (then `--check` must pass).

## Step 4: Merge Skills

For each `.claude/skills/{name}/SKILL.framework.md`:
- User's SKILL.md < 20 lines (placeholder) → replace with framework version.
- User's SKILL.md ≥ 20 lines → keep user's, append missing sections.
- Delete `SKILL.framework.md`.

## Step 5: Merge Cursor Rules

Same as Step 3 for `.cursor/rules/{name}.framework.mdc`.

## Step 6: Detect Stack & Fill stack.yml

Follow `.claude/guides/stack-detection.md`.

## Step 7: Update CLAUDE.md Commands

Follow `.claude/guides/claude-md-commands.md`.

## Step 8: Create docs/project-state.md

If missing, create with:
- Phase: Active Development, Status: In Progress
- Phase History: "Framework Applied | Complete | {today}"
- Session Resume: "CodingFramework merged. All .framework files resolved."

## Step 9: Final Verification

1. Confirm NO `.framework.*` files remain: `find . -name "*.framework.*" -type f`
2. Count CLAUDE.md lines — warn if > 80.
3. Confirm `.claude/.framework-manifest.json` exists (safe-upgrade tracking). If missing
   and `jq` is installed, note that re-running `setup.sh` will seed it.
4. Run `./sync-cursor-rules.sh --check` — must pass (rules ↔ cursor in sync).
5. Run `typecheck` and `lint` if scripts exist.

## Step 9.5: Baseline Audit (existing-code violations)

Invoke `/audit-baseline` in auto-run mode. The framework's rules only govern code written
*from now on* — this scans the EXISTING files for violations (too long, escape hatches,
bloated always-loaded docs, obvious inefficiencies), reports them, and records anything not
fixed to `docs/tech-debt.md`. **Report-only by default; fixes are opt-in per item.** Do not
block completion if the user defers — the debt is captured.

## Step 10: Context Optimization

Quick audit (~30 seconds):
1. Count tokens in always-loaded + auto-loaded files.
2. Scan for duplicate rules across files.
3. Scan for rules that conflict with stack (React rules in CLI project, etc.).
4. Present a removal proposal — list duplicates (keep in more specific file) and irrelevant rules with the reason for each. **WAIT for user approval. Delete only what the user confirms; never delete without confirmation.**
5. If > 4000 auto-loaded tokens → suggest `/optimize-context`.

## Step 11: Summary

```
=== Framework Applied ===

Merged:
  ✓ CLAUDE.md (added: ...)
  ✓ .claude/rules/... (added: ...)

Detected stack: {stack summary}
Commands configured: {list}
Context audit: {tokens} auto-loaded, {N} duplicates removed
Baseline audit: {hard} hard / {docs} docs / {soft} soft → {fixed} fixed, rest in docs/tech-debt.md

CLAUDE.md: {N}/80 lines ✓
Manifest: .framework-manifest.json ✓ (framework rules upgrade safely on re-run)
Your rules go in .claude/rules/project/ (never overwritten)
Ready: /research-codebase, /plan-feature, /debug, /review-changes
```
