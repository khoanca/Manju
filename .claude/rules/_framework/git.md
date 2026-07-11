---
source: framework
---

## Branching

- One branch per task: `feat/`, `fix/`, `chore/` prefix.
- Hotfix: `hotfix/` prefix. Fast-track: reproduce → fix → test critical path → review → deploy with rollback.

## Commits

- Conventional Commits: `type(scope): description`. Body explains WHY.
- Checkpoint commit before destructive agent operations.
- Commit lockfiles. Lockfile is the source of truth for dependency versions.
- Never rebase commits already pushed to shared branches.

## Pull Requests

- AI-generated PRs require at least one human review.
- PR size: aim for < 400 lines changed. Split larger into stacked PRs.
