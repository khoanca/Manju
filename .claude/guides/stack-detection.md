# Stack Detection & stack.yml

Shared by `/init-project` and `/apply-framework`.

## Detect Package Manager

Check lockfiles: `pnpm-lock.yaml` → pnpm, `yarn.lock` → yarn, `bun.lockb` → bun, `package-lock.json` → npm. Default: pnpm.

## Fill stack.yml

Read `package.json` + lockfiles to auto-detect. Write to `.claude/templates/stack.yml`:

```yaml
provenance:
  verified_at: # today's date (YYYY-MM-DD)
  source: # detected | manual | template-default
  detected_from: # files read, e.g. package.json + pnpm-lock.yaml

runtime:
  node: # engines.node or .nvmrc or .node-version
  package_manager: # from lockfile detection above

framework:
  name: # from deps: next→Next.js, express→Express, fastify→Fastify
  version: # from deps version

language:
  typescript: # devDependencies.typescript version

ui:
  css: # tailwindcss in deps → tailwindcss
  component_lib: # @shadcn/ui, @radix-ui, @mui, antd

validation:
  library: # zod, yup, joi, arktype, valibot

database:
  orm: # prisma, drizzle-orm, kysely, typeorm
  engine: # from DATABASE_URL in .env.example, or prisma schema
  cache: # redis, ioredis → redis

auth:
  provider: # next-auth, @clerk/nextjs, lucia

testing:
  unit: # vitest, jest
  e2e: # playwright, cypress
  component: # @testing-library/react, storybook

deployment:
  platform: # vercel.json→vercel, Dockerfile→docker, wrangler.toml→cloudflare
  ci: # .github/workflows→github-actions, .gitlab-ci.yml→gitlab-ci
```

Leave empty strings for undetected fields.
