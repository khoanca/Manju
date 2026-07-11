# Tech Stack Evaluation

Used by `/plan-feature` Step 3 when proposing new libraries/tools/patterns.

## Search (authoritative sources only)

Use WebSearch with `allowed_domains` restricted to trusted sources:
- **Tier 1** (check first): `["npmjs.com", "github.com", "developer.mozilla.org", "react.dev", "nodejs.org", "vercel.com/docs", "prisma.io/docs"]`
- **Tier 2** (vetted community): `["stackoverflow.com", "news.ycombinator.com", "web.dev", "stateofjs.com", "thoughtworks.com/radar"]`
- **Tier 3** (context only, never sole source): `["dev.to", "medium.com"]`
- **Block**: `["w3schools.com", "geeksforgeeks.org", "tutorialspoint.com"]`

## Verify Package Exists

19.7% of AI-suggested packages are fabricated. For each package:
1. WebFetch `npmjs.com/package/<name>` to confirm existence
2. Check: last publish < 12 months, weekly downloads trending up, maintainer count ≥ 2
3. Check: no suspicious install scripts, dependencies count < 20
4. Security-sensitive: OpenSSF Scorecard ≥ 7/10
5. 60-day cooldown for new production packages. Dev-only tools: 21-day minimum
6. Never recommend alpha/beta/RC for production without user approval

## Cross-Check (min 2 Tier 1/2 sources agreeing)

- Compare new vs proven alternatives already in codebase
- If new contradicts proven and both viable → prefer proven
- Only choose new when: clear measurable advantage AND 6+ months stable adoption
- Flag trade-off: "X is newer [benefit], Y is proven. Recommend Y unless [condition]."

## Present to User

Alternatives with pros/cons. Mark established vs new. Recommendation and why.
