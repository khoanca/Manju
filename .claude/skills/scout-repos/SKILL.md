---
name: scout-repos
description: Search GitHub for forkable repos matching product plan. Evaluate quality, social reviews, security — then fork or proceed to build from scratch.
when_to_use: After /product-plan, BEFORE /plan-feature. This is the build-vs-fork decision point. Also when user asks "is there an existing repo for X?"
---

## Input/Output Contract

INPUT:
  - `docs/product-plan.md` (features/user stories to match against)
  - `docs/project-brief.md` (tech stack preferences, constraints)
OUTPUT (one of two paths):
  - **Fork path**: Forked repo + tech stack report → `/plan-feature` (customize on top)
  - **Build path**: No suitable repo found → `/plan-feature` (build from scratch)
GATE: User must approve before forking. User must confirm "build from scratch" if no repo fits.

## Prerequisites

Before starting:
1. Read `docs/product-plan.md` — extract features, user stories, data model.
2. Read `docs/project-brief.md` — understand tech stack preferences.
3. Product plan is traceable to an approved BRD (from `/research-business`). Use BRD business intent — not just plan feature names — to shape search criteria.
4. If neither exists, ask: "Run `/product-plan` first to define what we're building."

## Step 1: Extract Search Criteria

From the product plan, identify:
- **Project type**: SaaS boilerplate, e-commerce, CMS, dashboard, auth system, etc.
- **Key features**: auth, payments, multi-tenancy, real-time, i18n, etc.
- **Tech stack preferences**: from project-brief.md

Formulate 3-5 search queries combining project type + key features.

## Step 2: Search GitHub (by stars, tiered)

Use WebSearch with multiple queries:
- `site:github.com {project-type} {key-features} stars:>5000`
- `site:github.com {tech-stack} starter boilerplate stars:>2000`
- `"awesome-{domain}"` curated lists

For each candidate, collect via GitHub API (`gh` CLI):
```bash
gh repo view {owner}/{repo} --json stargazersCount,licenseInfo,pushedAt,description,primaryLanguage
gh api repos/{owner}/{repo}/contributors --jq 'length'
```

**Hard filters (must pass ALL):**
- Last commit within 6 months (active maintenance)
- License allows commercial forking (MIT, Apache 2.0, BSD)
- Has documentation beyond a bare README
- Issues are responded to, PRs are merged
- Feature coverage ≥ 50% of product plan (a popular repo that doesn't fit is useless)

**Stars (trust tier, not a hard cutoff):**
- ≥ 10,000 → high trust, accept as-is
- 2,000–10,000 → acceptable with extra scrutiny (check maintainer responsiveness, recent release cadence, open-vs-closed issue ratio, security advisories)
- < 2,000 → only if it uniquely fits the plan AND passes the extra scrutiny above; flag the lower community validation to the user
- Stars are a trust signal, not a quality guarantee — a well-maintained 3k-star repo that fits beats a 15k-star repo that doesn't.

**If no repos pass filters** → skip to Step 5 with "No suitable repo found" → recommend build from scratch.

Select top 3-5 candidates for deep evaluation.

## Step 3: Social Review Check

For each candidate, use WebSearch on these sources:
- **Reddit**: `site:reddit.com "{repo-name}" review OR problems OR experience`
- **Hacker News**: `site:news.ycombinator.com "{repo-name}"`
- **Twitter/X**: `"{repo-name}" review OR issue OR security`
- **Dev.to**: `site:dev.to "{repo-name}" review`
- **GitHub Issues**: `gh issue list -R {owner}/{repo} --label bug --state all --limit 50`

Collect both positive signals (adoption stories, community praise) and negative signals (recurring complaints, abandonment risk).

## Step 4: Top 10 Negative Review Audit

For each candidate, compile the **10 most critical negative reviews/issues**:

### 4a. Feature Issues (search repo issues + social)
- Breaking changes between versions
- Missing promised features
- Performance problems at scale
- Poor documentation for critical features
- Migration difficulty

### 4b. Security Issues
```bash
gh issue list -R {owner}/{repo} --search "security OR vulnerability OR CVE" --state all --limit 20
```
- Known CVEs associated with the repo or its dependencies
- Authentication/authorization bypass reports
- Data leak or injection vulnerabilities
- `npm audit` results on the repo's dependencies

### Report Format

Present for each candidate:

```markdown
## {Repo Name} — {stars}⭐ | {license}

**URL**: {url}
**Last commit**: {date} | **Contributors**: {count} | **Open issues**: {count}
**Tech stack**: {detected stack}

### Feature Match vs Product Plan
| Plan Feature (from product-plan.md) | Support | Notes |
|--------------------------------------|---------|-------|
| {feature/user story}                 | ✅/⚠️/❌ | ...   |

### Top 10 Negative Reviews
| # | Source | Category | Summary | Severity |
|---|--------|----------|---------|----------|
| 1 | GitHub #XXX | Security | ... | CRITICAL |
| 2 | Reddit | Feature | ... | MEDIUM |

### Positive Signals
- {signal 1}
- {signal 2}

### Tech Stack (what comes with this repo)
| Layer | Technology | Matches project-brief? |
|-------|-----------|----------------------|
| Runtime | ... | ✅/❌ |
| Framework | ... | ✅/❌ |
| Database | ... | ✅/❌ |
| Auth | ... | ✅/❌ |
| Deployment | ... | ✅/❌ |
| Testing | ... | ✅/❌ |

### Verdict: ✅ RECOMMEND / ⚠️ CONSIDER / ❌ AVOID
{1-2 sentence reason}
```

## Step 5: Decision Point — Fork or Build?

Present the comparison report. Two outcomes:

### If good candidates exist:
Ask user:
1. Which repo to fork? (or none)
2. Use repo's tech stack as-is or customize?
3. Any negative review that is a blocker?

**Do NOT fork until user explicitly says yes.**

### If no suitable repo found:
Report why (no matches, poor quality, wrong stack, security issues).
Recommend: "No good fork candidate. Proceed to `/plan-feature` to build from scratch."

## Step 6: Fork & Sandbox Test

### 6a. Fork
```bash
gh repo fork {owner}/{repo} --clone --remote
cd {repo}
git checkout -b feat/customize-fork
npm install
```

### 6b. Security Scan
Run before anything else — catch deal-breakers early:
```bash
# Dependency vulnerabilities
npm audit

# Hardcoded secrets (API keys, passwords, tokens in source code)
grep -rn "sk-\|password\s*=\|api_key\s*=\|secret\s*=\|-----BEGIN" --include="*.ts" --include="*.tsx" --include="*.js" --include="*.env*" .

# Check .env handling
test -f .gitignore && grep -q "\.env" .gitignore && echo "✅ .env in .gitignore" || echo "❌ .env NOT in .gitignore"

# Dangerous patterns (eval, innerHTML, raw SQL interpolation)
grep -rn "eval(\|innerHTML\s*=\|\.query(\`\|\.exec(\`" --include="*.ts" --include="*.tsx" --include="*.js" .
```

**If CRITICAL vulnerabilities or leaked secrets found** → report to user, recommend ❌ AVOID. Do NOT proceed without explicit user approval.

### 6c. Build & Run Sandbox
```bash
# Build
npm run build

# Run test suite
npm test

# Start dev server and verify it runs
npm run dev &
DEV_PID=$!
sleep 10
curl -s -o /dev/null -w "%{http_code}" http://localhost:3000 || echo "❌ App failed to start"
kill $DEV_PID
```

### 6d. Sandbox Report
Present results to user:

```markdown
## Sandbox Test Results

### Security Scan
- npm audit: {X} vulnerabilities ({critical}/{high}/{moderate})
- Hardcoded secrets: {found/none}
- .env in .gitignore: ✅/❌
- Dangerous patterns: {found/none}

### Build & Run
- npm install: ✅/❌ ({time})
- npm run build: ✅/❌ ({time}, {warnings count})
- npm test: ✅/❌ ({passed}/{failed}/{skipped})
- Dev server starts: ✅/❌ (HTTP {status code})

### Verdict: ✅ SAFE TO USE / ⚠️ ISSUES TO FIX / ❌ DO NOT USE
{summary}
```

**If verdict is ❌** → ask user: revert fork and try next candidate, or build from scratch?
**If verdict is ⚠️** → list issues, ask user if acceptable before continuing.

## Step 7: Post-Fork → Plan Customization

1. Update `docs/project-brief.md`:
   - Source repo URL and commit SHA at fork time
   - License type
   - Tech stack (from report — this becomes the project's stack)
   - Known issues to address (from negative reviews)
2. Update `docs/project-state.md`:
   - Record fork decision with rationale
3. Suggest: "Fork ready. Run `/plan-feature` to plan what to customize/add on top."

## Rules

- **Never fork repos with no license** — all rights reserved by default.
- **Warn on GPL/AGPL** — copyleft implications for commercial use.
- **If repo has CRITICAL security issues** → mark ❌ AVOID regardless of star count.
- **If repo hasn't been updated in 6+ months** → warn about maintenance risk.
- **Check dependency freshness** — major versions behind = upgrade burden.
- **Prefer repos matching user's tech stack** from `project-brief.md` over alternatives with different stack.
- **Selection order**: feature fit first (≥50% plan coverage) → then rank by stars (higher = more trusted). A repo that doesn't fit the plan is useless regardless of stars.
