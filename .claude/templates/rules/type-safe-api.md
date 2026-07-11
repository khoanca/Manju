---
source: framework
paths:
  - "**/*.graphql"
  - "**/*.proto"
  - "**/schema/**"
  - "**/trpc/**"
  - "**/resolvers/**"
  - "**/openapi*"
---

## tRPC / GraphQL / OpenAPI

- Never write API types by hand. Generate from schema (graphql-codegen, z.infer, openapi-typescript).
- Commit generated SDL/OpenAPI artifact. Validate in CI. Schema drift = production incidents.
- Use DataLoader/batching for every field resolver crossing a relation. Raw query in resolver = N+1.
- GraphQL: enforce depth limit (7-10), complexity cap (~1000), disable introspection in production.
- Model expected errors as typed unions (`Success | ValidationError`). Only throw for system errors.
- Use `.strict()` on Zod request schemas to reject unknown fields.
