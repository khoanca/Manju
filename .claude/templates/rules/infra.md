---
source: framework
paths:
  - "**/*.tf"
  - "**/*.tofu"
  - "terraform/**"
  - "pulumi/**"
  - "infra/**"
---

## Terraform / Pulumi

- Remote state with locking (S3+DynamoDB, TF Cloud). Never local state for team projects.
- Separate state per environment. Single state across envs = dev apply destroys prod.
- Never store secrets in TF outputs. Write to secrets manager, retrieve at runtime.
- Scheduled drift detection (`terraform plan -refresh-only`) at least daily.
- Plan-approval workflow for prod: reviewed plan → explicit approval → apply.
