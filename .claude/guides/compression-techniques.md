# Compression Techniques for Skills

Apply in order from highest to lowest ROI.

## 1. Extract shared content → guides

If same content appears in 2+ skills:
1. Create `.claude/guides/{topic}.md` with shared content.
2. Replace in each skill with: `Follow .claude/guides/{topic}.md.`
3. Guides load on-demand when skill references them — zero cost when unused.

Common extraction targets:
- Stack detection logic
- CLAUDE.md commands configuration
- Tech stack evaluation / package verification
- Prerequisites checking patterns

## 2. Remove redundant content

Delete content Claude already knows:
- Language boilerplate (in CLAUDE.md)
- Generic best practices ("validate input", "handle errors")
- Explanations of standard techniques ("BVA tests boundary values because...")
- Repeated rules from `.claude/rules/` (already auto-loaded by path scope)

## 3. Telegraphic compression

Convert verbose prose → imperative bullets:

Before: "You should always make sure to validate all input data before processing it to prevent security vulnerabilities."
After: "Validate all input before processing."

Rules:
- Drop subject ("You should" → imperative verb)
- Drop obvious rationale ("to prevent X" when X is obvious)
- Merge related bullets sharing same topic
- Use symbols: `→` (leads to), `≥` (at least), `+` (and), `|` (or)
- One concept per bullet, max 2 sentences

## 4. Table compression

5+ bullets on same subtopic → table or compact list:

Before:
```
- Stars ≥ 10,000
- Last commit within 6 months
- License allows commercial forking
- Has documentation
- Issues are responded to
```

After:
```
Filter: stars ≥10k, commit <6mo, license MIT/Apache/BSD, has docs, issues responsive.
```

## 5. Template extraction

Embedded output template > 20 lines → extract to `.claude/guides/template-{name}.md`, reference from skill.

## 6. Split oversized skills

If skill > 250 lines after compression AND has distinct phases:
1. Identify natural split points (independent phases).
2. Extract phase to a guide or sub-skill.
3. Main skill references extracted content.

Rule: never split if phases are tightly coupled (output of one = input of next).
