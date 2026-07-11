## Routing Layer 0 — Decompose & map (every actionable prompt)

Before routing, break the goal into the smallest independent pieces. Skip ONLY for pure Q&A/explain/review (answer directly).

- For a code/feature task: split the feature into small work items. For each, mark **‖ parallel** (no dependency on another item) or **→ sequential** (depends on an item — name which). Build an execution map:
  ```
  A. schema + migration        → (blocks B, C)
  B. API endpoint              ‖ with C, after A
  C. validation/types         ‖ with B, after A
  D. UI                        → after B
  E. tests                     ‖ per-item, after each item lands
  ```
- Then ASK how to execute: **(1)** I spawn agents to run parallel items concurrently, or **(2)** you open multiple sessions yourself. Don't assume — this is a user decision.
- If user picks multiple sessions: write the map to `docs/parallel-plan.md` (items, parallel/sequential, dependencies, "session N runs X") for them to follow.
- The map sets parallelism; it does NOT skip gates. Still run the normal Layer 1/2 flow, `/scout-repos`/`/plan-feature` gates, and domain rules per item.
