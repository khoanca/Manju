---
source: framework
---

## Monorepo (Turborepo / Nx / pnpm)

- Use workspace protocol: `"@myapp/ui": "workspace:*"`. Never relative `../../` cross-package imports.
- Strict import boundaries: apps import packages, never other apps. Packages never import apps.
- Named exports only in shared packages. No default exports.
- Run commands from monorepo root (`pnpm turbo build --filter=web`). Never from subdirectories.
- Turbo tasks: `dev` and `build` must depend on `^db:generate` or equivalent codegen steps.
