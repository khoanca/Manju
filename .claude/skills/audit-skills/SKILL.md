---
name: audit-skills
description: Audit all skill files for length, duplication, verbosity, and optimization opportunities. Applies compression techniques to reduce tokens, hallucination, and complexity.
when_to_use: After adding/modifying skills, when token costs are high, or when user says "audit skills", "check skills", "optimize skills". Complements /optimize-context (rules+CLAUDE.md) with skill-specific deep analysis.
---

## Input/Output Contract

INPUT: `.claude/skills/*/SKILL.md`, `.claude/guides/*.md` (if exists)
OUTPUT:
  - Audit report (per-skill metrics, issues found, recommendations)
  - Optimized skill files (after user approval)
  - Shared guides extracted from duplicated content (if any)
GATE: User must approve changes before overwriting

## Phase 1: Inventory & Metrics

### 1a. Scan all skills

```bash
wc -l .claude/skills/*/SKILL.md | sort -rn
```

Per skill, collect:
- **Line count** — proxy for token cost
- **Has frontmatter** (name, description, when_to_use) — required
- **Has Language boilerplate** — redundant if CLAUDE.md has `## Language`
- **Has Input/Output Contract** — required for skill clarity
- **Load type** — on-demand (slash command) = low priority for compression

### 1b. Classify by size

| Size | Lines | Action |
|---|---|---|
| Oversized | > 200 | MUST compress or split |
| Large | 150-200 | Review for compression |
| Normal | 80-150 | Light review |
| Compact | < 80 | Skip unless quality issues |

### 1c. Baseline report

```
=== Skill Inventory ===
Total skills: {N}
Total lines: {N}
Average: {N} lines/skill

| Rank | Skill | Lines | Size Class |
|---|---|---|---|
| 1 | {name} | {lines} | Oversized |
```

### 1d. Stray artifact check

```bash
find . -name "*.framework.*" -not -path "./.git/*"
```

`.framework.md` / `.framework.mdc` are merge artifacts from `setup.sh --merge`, meant to be reviewed then deleted by `/apply-framework`. Any left behind are stale clutter — flag each with its path and recommend deletion after the user confirms the merge was applied. They are gitignored, so they won't show in `git status`.

## Phase 2: Quality Analysis

Run these checks on every skill. Use parallel sub-agents for skills > 10.

### 2a. Duplication Detection

**Cross-skill duplication:**
- Extract all `## Heading` sections from every skill.
- Compare section content between skills — flag >70% similarity.
- Common duplicates: Language boilerplate, Prerequisites patterns, stack detection, CLAUDE.md commands config.

**Intra-skill duplication:**
- Flag repeated instructions within same skill (same concept stated twice).

### 2b. Overlap Analysis

Compare skill descriptions and `when_to_use` triggers:
- Two skills with similar descriptions → users won't know which to pick.
- Overlapping triggers → wrong skill may activate.
- Flag and recommend: merge, rename, or clarify boundaries.

### 2c. Verbosity Scan

Per skill, classify each section:

| Class | Signal | Action |
|---|---|---|
| **Redundant** | Claude knows without instruction (common best practice) | DELETE |
| **Verbose** | Correct but too many words, prose where bullet suffices | COMPRESS |
| **Template-heavy** | Large embedded output templates (>20 lines) | EXTRACT to guide |
| **Example-heavy** | Code examples that could be shorter | TRIM examples |
| **Essential** | Unique, non-obvious, would cause errors if removed | KEEP |

Heuristics for redundant content:
- "Use async/await", "Don't use eval()", "Use semantic HTML" — Claude knows
- Explanations of WHY a technique works (Claude knows the technique)
- "Rules:" sections that repeat what preceding steps already say

Heuristics for essential content:
- Project-specific conventions, naming rules
- Safety guardrails, gates, approval requirements
- Non-obvious architectural decisions
- Anti-hallucination checks

### 2d. Structural Quality

Per skill check:
- [ ] Frontmatter complete (name, description, when_to_use)
- [ ] Input/Output Contract defined
- [ ] GATE defined (what needs user approval)
- [ ] NEXT defined (what skill follows)
- [ ] Steps numbered and sequential
- [ ] No orphan sections (content unreachable by any step)
- [ ] Description has both positive ("use when") and negative ("don't use when") triggers
- [ ] No Language boilerplate (should be in CLAUDE.md only)

## Phase 3: Optimization Techniques

Apply techniques from `.claude/guides/compression-techniques.md`, highest ROI first.

## Phase 4: Present Findings

Report format:
```
=== Skill Audit Report ===
Summary: {N} skills, {before}→{after} lines ({pct}% saved), {critical}/{medium}/{low} issues

Per skill: name, lines before→after, size class, issues table (type, severity, detail, action)
Cross-skill issues: type, skills involved, detail, action
Recommendations: prioritized list [HIGH/MEDIUM/LOW]
```

Show per-skill diff preview (modified sections only). Offer: "Apply all" | "Apply selectively" | "Show {skill}" | "Skip {skill}" | "Report only".

## Phase 5: Apply & Verify

After approval:
1. Create shared guides (if any). Edit skill files.
2. Verify: heading counts unchanged, frontmatter intact, GATE/NEXT valid, cross-references resolve.
3. Final line count comparison: per-skill before/after table + total reduction.

## Guardrails

- **Never trade token savings for feature quality.** If compression changes the meaning, accuracy, or completeness of a rule/instruction, keep the original. Accuracy > token savings.
- **Never delete essential content** — gates, anti-hallucination rules, safety checks, approval requirements.
- **Preserve all code snippets and specific values** — header values, config options, command examples are high-value tokens.
- **When uncertain, compress rather than delete** — shorter rule > missing rule.
- **Don't merge skills that serve different purposes** — even if they share some content. Extract shared parts to guides instead.
- **Test after changes** — if project has working commands, run typecheck + lint to verify nothing broke.
- **Semantic validation** — after compression, re-read each skill to verify the compressed version conveys the same instructions. Ambiguity introduced by compression is worse than verbosity.
