# CLAUDE.md Commands Configuration

Shared by `/init-project` and `/apply-framework`.

## Update Commands Section

Read `package.json` scripts. Map common variants:
- `dev` / `start:dev` / `serve` → Dev
- `build` / `compile` → Build
- `test` / `test:unit` → Test
- `lint` / `eslint` → Lint
- `typecheck` / `type-check` / `tsc` → Typecheck

Replace the Commands block in CLAUDE.md:

```markdown
## Commands

- Dev: `{pm} run dev`
- Build: `{pm} run build`
- Test: `{pm} test`
- Lint: `{pm} run lint`
- Typecheck: `{pm} run typecheck`
```

Where `{pm}` = detected package manager. Only include commands that exist in package.json scripts.

## Line Budget

Keep CLAUDE.md under 80 lines. If over, move non-essential sections to `.claude/rules/` files.
