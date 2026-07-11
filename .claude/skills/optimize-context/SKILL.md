---
name: optimize-context
description: Audit and compress framework files (rules, skills, CLAUDE.md) to reduce token usage. Shortens verbose rules, splits large documents, deduplicates, moves rarely-used content to on-demand loading.
when_to_use: When context window feels tight, token costs are a concern, or after adding many rules/skills. Also when user says "optimize", "compress", "reduce tokens", or "slim down".
---

## Input/Output Contract

INPUT:
  - `.claude/rules/**/*.md` (auto-loaded rules, recursive: `_framework/` + `project/`)
  - `.claude/skills/*/SKILL.md` (on-demand skills)
  - `CLAUDE.md` (always-loaded project instructions)
  - `.claude/templates/rules/*.md` (optional, if activated)
SCOPE NOTE: `_framework/` rules are framework-owned (tracked by `.framework-manifest.json`).
  Compressing them is fine, but it makes them "modified" — `setup.sh` will then stop
  auto-upgrading them (drops a `.framework.md` instead). Prefer compressing `project/`
  rules and CLAUDE.md; only touch `_framework/` when the savings are worth losing clean upgrades.
OUTPUT:
  - Compressed versions of above files
  - Report: before/after token counts, savings percentage
  - (Optional) New files split from large originals
GATE: User must approve changes before overwriting any file

## Why This Matters

- **Rules** (`.claude/rules/**`, recursive across `_framework/` + `project/`) with `paths` frontmatter load only on matching files; rules without `paths` cost tokens on EVERY conversation turn
- **CLAUDE.md** always loads → every line costs tokens on every turn
- **Skills** load only when called → less urgent, but large skills eat into working context
- Claude already knows many "best practices" → rules that repeat common knowledge waste tokens
- Redundancy across files compounds the waste

## Phase 1: Audit

Measure current token usage. Use character count as proxy (~4 chars ≈ 1 token for English, ~2-3 chars for Vietnamese).

**Step 1: Scan and categorize**

```
Category                         | Load behavior          | Priority to optimize
---------------------------------|------------------------|---------------------
CLAUDE.md                        | Always                 | CRITICAL
Rules without `paths` frontmatter| Always (every turn)    | CRITICAL
Rules with `paths: [...]`        | Auto (matching files)  | HIGH
Active template rules            | Auto (by path scope)   | HIGH
.claude/skills/*/SKILL.md        | On-demand (slash cmd)  | MEDIUM
```

**Step 2: Generate token report**

For each file, calculate:
- Character count → estimated tokens
- Category (always / auto / on-demand)
- Effective cost = tokens × load frequency (always=10x, auto=5x, on-demand=1x)

Sort by effective cost descending. Present top offenders.

**Step 3: Cross-file redundancy scan**

Grep for duplicate or near-duplicate rules across files:
- Same concept stated in different files (e.g., "parameterized queries" in both `backend.md` and `database.md`)
- Rules that restate Claude's default behavior (e.g., "use TypeScript strict mode" when `tsconfig.json` already has `strict: true`)
- Overlapping bullets between `CLAUDE.md` and rule files

## Phase 2: Classify Rules

For each rule/bullet, classify into one of:

| Class | Description | Action |
|-------|-------------|--------|
| **Redundant** | Claude already knows this without being told (common best practice, language default) | DELETE |
| **Duplicate** | Same concept exists in another loaded file | MERGE (keep in most specific file) |
| **Verbose** | Correct rule but too many words | COMPRESS (same meaning, fewer tokens) |
| **Conditional** | Only relevant for specific app types or rare scenarios | MOVE to template rule or skill |
| **Essential** | Unique, non-obvious, must stay | KEEP |

**Classification heuristics:**

Rules likely **redundant** (Claude knows these):
- "Use async/await" (JS default pattern)
- "Don't use `eval()`" (universal security knowledge)
- "Use semantic HTML" (basic web knowledge)
- Generic advice like "write clean code", "follow best practices"

Rules likely **essential** (Claude might NOT do these without instruction):
- Project-specific conventions (file naming, import order)
- Non-obvious architectural decisions (why X over Y)
- Safety guardrails that override Claude's default behavior
- Domain-specific constraints (compliance, regulatory)

**When uncertain, KEEP the rule.** False negatives (removing a needed rule) are worse than false positives (keeping a redundant one).

## Phase 3: Compression Techniques

Apply these in order, from least to most invasive:

### 3.0 Path-scope optimization (highest ROI)

Rule not loading = 0 tokens. This is far more effective than compressing content.

> Claude Code uses the `paths` frontmatter field (NOT Cursor's `globs:`/`alwaysApply:`) for path-scoped rules. A rule with `paths:` loads only when editing matching files; a rule WITHOUT `paths:` loads every turn. Never write `globs:` or `alwaysApply:` in `.claude/rules/*.md` — Claude Code silently ignores them and the rule loads unconditionally. (Cursor's `.cursor/rules/*.mdc` correctly uses `globs:`/`alwaysApply:`.)

**Step 1: Audit path coverage**

For each rule file, check frontmatter:
- Has `paths: [...]` → conditional load (good — only on matching files)
- No `paths:` → always loaded (intentional for guardrails, code style, git, testing; a leak for domain-specific rules)
- Has `globs:` or `alwaysApply:` → **BUG** — Claude Code ignores these, rule loads always. Convert to `paths:` (or remove frontmatter for always-load rules).

**Step 2: Add missing paths**

If a domain-specific rule loads every turn but only applies to certain files:
- Add a `paths:` list of matching patterns (YAML block list, one pattern per line)
- Reference `stack.yml` to build accurate paths (e.g., if stack uses `src/app/` for Next.js routes, use that in frontend.md paths)
- Keep always-load (no `paths:`) only for cross-cutting rules: code style, guardrails, git, testing.

**Step 3: Tighten overly broad paths**

Check for path patterns that match too many files:
- `"*.yml"` in devops.md → matches app config files too, not just CI. Tighten to `".github/**/*.yml"`, `"docker-compose*.yml"`
- `"*.config.*"` in security.md → matches vitest.config.ts, tailwind.config.ts. Tighten to specific security-relevant configs

**Step 4: Remove rules for missing stack components**

Cross-reference rule files against `stack.yml`:
- `frontend.md` exists but `stack.yml` has no frontend framework → delete file
- `database.md` exists but `stack.yml` has no database/ORM → delete file
- Template rules (e.g., `app-realtime.md`) but no matching tech in `stack.yml` → delete file

Present removals to user for approval before deleting.

### 3.1 Bullet compression

Merge related bullets into one when they share the same topic. Cut explanatory clauses that state the obvious. Keep the actionable part — drop the rationale unless non-obvious. Preserve code snippets and specific values.

Before:
```
- Set `X-Content-Type-Options: nosniff` to prevent MIME type sniffing attacks.
- Set `X-Frame-Options: DENY` to prevent clickjacking (or use CSP `frame-ancestors` for granular control).
```

After:
```
- Headers: `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY` (or CSP `frame-ancestors`).
```

### 3.2 Section compression

If a section has 5+ bullets on the same subtopic → compress to a table or compact list.

Before:
```
## Auth
- Tokens in httpOnly+Secure+SameSite cookies. Never localStorage.
- Use refresh token rotation (single-use, invalidate family on replay).
- Regenerate session ID after login and privilege escalation.
- PKCE for all OAuth authorization code flows.
- Rate limit auth endpoints with progressive delays.
- JWT access tokens: short-lived (≤15min). Verify iss, aud, exp, nbf.
```

After:
```
## Auth
- Tokens: httpOnly+Secure+SameSite cookies. Never localStorage. JWT ≤15min, verify iss/aud/exp/nbf.
- Session: regenerate ID after login/privilege change. Refresh token rotation (single-use, family invalidation).
- OAuth: PKCE required. Rate limit auth endpoints with progressive delays.
```

### 3.3 Move conditional content

If a rule only applies to specific app types → move to `.claude/templates/rules/`.

Example: Redis rules → only relevant for projects using Redis → move to a `cache.md` template rule, activated during `/init-project` only when caching is in the stack.

### 3.4 Split large skills

If a SKILL.md > 250 lines AND contains an embedded template:
1. Extract the template to `guides/template-{name}.md`
2. Replace in SKILL.md with: `Read and use template from guides/template-{name}.md`
3. Saves tokens when skill loads for re-reading/editing (doesn't need the full template every time)

### 3.5 CLAUDE.md diet

CLAUDE.md should stay under 80 lines. If over:
- Move routing table to `.claude/rules/routing.md` (auto-loads always, but separates concerns)
- Move session management to `.claude/rules/session.md`
- Keep only: project identity, commands, task classification, and pointers to rules

## Phase 4: Apply Changes

**IMPORTANT: Never apply changes without user approval.**

**Step 1: Present the plan**

Show a summary table:

```
File                    | Before | After  | Saved  | Changes
------------------------|--------|--------|--------|--------
CLAUDE.md               | 890 tk | 620 tk | 30%    | Moved routing to rules/
.claude/rules/backend.md| 610 tk | 380 tk | 38%    | Merged auth bullets, removed 3 redundant
.claude/rules/security.md| 570 tk| 390 tk | 32%    | Compressed headers section, moved Redis
...                     |        |        |        |
TOTAL (always+auto)     |4500 tk |3100 tk | 31%    |
```

**Step 2: Show diffs for each file**

For each file with changes, show before/after for the modified sections. NOT the full file — only changed parts.

**Step 3: Ask for approval**

Options:
- "Apply all" → apply everything
- "Apply selectively" → go file by file
- "Show me [specific file]" → show full before/after for one file
- "Skip [file]" → exclude from changes

**Step 4: Apply approved changes**

Edit files. Then verify:
- No rule was accidentally deleted (compare rule count before/after)
- Files are valid markdown
- No broken references between files

## Phase 5: Report

Present final summary:

```
=== Context Optimization Complete ===

Path-scope coverage:
  Rules with paths:            {N}/{total} (fixed {M} missing)
  Rules removed (wrong stack): {count}
  Paths tightened:             {count}

Token savings:
  Always-loaded (CLAUDE.md + no-paths rules):    {before} → {after} tokens ({pct}% saved)
  Auto-loaded (path-scoped rules):               {before} → {after} tokens ({pct}% saved)
  On-demand (skills):                            {before} → {after} tokens ({pct}% saved)

Changes:
  Rules removed (redundant):   {count}
  Rules merged (duplicate):    {count}
  Rules compressed:            {count}
  Rules moved (conditional):   {count}
  Skills split:                {count}
  Files modified total:        {count}
```

## Guardrails

- **Never trade token savings for feature quality.** If compression changes the meaning, accuracy, or completeness of a rule/instruction, keep the original. Accuracy > token savings.
- **Never delete a rule without showing it to the user first.** The user may know context that makes a "redundant" rule actually essential.
- **Preserve all code snippets and specific values.** These are the highest-value tokens — never compress away a header value, a config option, or a command example.
- **When in doubt, compress rather than delete.** A shorter rule is better than a missing rule.
- **Don't optimize skills aggressively.** They only load on-demand — the ROI of compressing a skill is much lower than compressing an auto-loaded rule.
- **Keep CLAUDE.md readable.** It's the first thing a new developer sees. Don't sacrifice clarity for token savings.
- **Run this skill periodically** — after adding new rules/skills, or when context window warnings appear.
