---
name: product-plan
description: Transform approved BRD into a detailed product specification with user stories, screens, data model, API outline, and priority matrix. Bridges business requirements to implementation.
when_to_use: When an approved BRD exists in docs/ and the project needs a product specification before implementation begins. Do NOT use for simple well-defined single-feature tasks.
---

## Input/Output Contract

INPUT:
  - `docs/project-state.md` (current phase = 1 with BRD approved)
  - `docs/brd-{name}.md` (approved BRD)
  - `docs/project-brief.md` (project context, tech stack, constraints)
OUTPUT:
  - `docs/product-plan.md` (user stories, screens, data model, API outline, priority matrix)
  - Updated `docs/project-state.md` (phase 2 complete, features backlog populated)
NEXT: `/scout-repos` (fork-or-build decision) → `/plan-feature` (for first feature in Wave 1)
GATE: User must approve product plan before any implementation begins

## Prerequisites

Before starting:
1. Read `docs/project-state.md` — verify phase 1 (Business) is complete with BRD approved.
2. Read the approved BRD file (path listed in project-state.md Phase History).
3. Read `docs/project-brief.md` for tech stack and constraints.
4. Load the **Ubiquitous Language Glossary** (BRD Section 10). All entity names, field names, and type names in this plan MUST use the Code Names from the glossary — do not invent new terms.
5. If BRD includes Gherkin scenarios (Section 11 from `/research-business`), use those as source material for acceptance criteria — they're pre-vetted specs.
6. If no approved BRD exists, stop and suggest: "Run `/research-business` first to create a BRD."

## Step 1: User Stories

For each functional requirement in the BRD, create user stories.

**Format:**
```
### US-{NNN}: {Short title}
- **As a** {persona from BRD}
- **I want to** {action}
- **So that** {benefit}
- **Source**: FR-{NNN} from BRD
- **Priority**: Must / Should / Could

**Acceptance Criteria:**
- [ ] {ref BRD Gherkin scenario by @tag/name if one exists, e.g. "FR-001 → Scenario: successful checkout"}
- [ ] {prose criterion ONLY for behavior Gherkin doesn't cover — testable: "returns results within 200ms", not "works well"}
```

**Rules:**
- Every FR in the BRD must map to at least one US.
- If the BRD has a Gherkin scenario (§11) for this FR, **reference it as the acceptance source — do not restate it**. Gherkin is the single source of truth for behavior. Add prose criteria only for what Gherkin omits (UI states, non-functional limits, edge data).
- Each US must have 2-5 acceptance criteria (references + prose combined).
- Acceptance criteria must be testable (measurable, observable).
- Group by persona when multiple personas exist.
- Priority derived from BRD's Must Have / Should Have / Out of Scope.

## Step 2: Screen/Page Inventory

List every screen or page the app needs, based on user stories and BRD user flows.

**Format:**
| Screen | Route/Path | User Stories | Key Components | States |
|--------|-----------|-------------|----------------|--------|
| {name} | /path | US-001, US-003 | {list} | loading, empty, error, success |

**Rules:**
- Derive from BRD's User Flows (happy path + alternates).
- Include error states, empty states, loading states for each screen.
- For API-only projects, list endpoint groups instead of screens.
- Mark which screens are MVP (Wave 1) vs future.
- Note responsive/mobile considerations where relevant.

## Step 3: Conceptual Data Model

Expand BRD's Data Entities into a conceptual model. This is business-level, NOT a database schema.

**Format:**
```
### {Entity Name}
- **Description**: {what it represents in the domain}
- **Key Attributes**: {name, type concept (text/number/date/enum/boolean), required?}
- **Relationships**: {Entity} has many {Entity}, {Entity} belongs to {Entity}
- **Business Rules**: {from BRD's Domain Rules that apply to this entity}
- **Source**: DR-{NNN}, FR-{NNN}
```

**Rules:**
- Every Data Entity from BRD must appear here with expanded detail.
- Entity names and attribute names MUST match the Glossary Code Names (BRD Section 10). If a new term emerges, add it to the glossary first.
- Every Domain Rule from BRD must be attached to at least one entity.
- Identify soft-delete vs hard-delete per entity.
- Note audit requirements (who changed what, when) where needed.
- Do NOT define database columns, indexes, or ORM schemas — that's for /plan-feature.

## Step 4: API Outline

Define the API surface based on user stories and data model.

**Format:**
| Method | Endpoint | Description | Auth | User Stories |
|--------|----------|-------------|------|-------------|
| POST | /api/... | ... | required/public | US-001 |

**Rules:**
- RESTful by default. GraphQL/tRPC if project-brief.md specifies.
- Every user story involving data must have at least one API endpoint.
- Note which endpoints are public vs authenticated.
- Group by resource/domain.
- **Do NOT specify field-level request/response shapes or error envelopes here** — those drift before code exists. They belong in `/plan-feature`, derived from the data model + Glossary at implementation time.
- This is a surface outline, not a spec.

## Step 5: Priority Matrix

Organize features into implementation waves.

### Wave 1: Must Have (MVP)
| # | Feature | User Stories | Complexity | Dependencies |
|---|---------|-------------|------------|-------------|
| 1 | {name} | US-001, US-002 | S/M/L | None |
| 2 | {name} | US-003 | M | Feature 1 |

### Wave 2: Should Have (v1 if time permits)
| # | Feature | User Stories | Complexity | Dependencies |
|---|---------|-------------|------------|-------------|

### Wave 3: Could Have (future)
| # | Feature | User Stories | Complexity | Dependencies |
|---|---------|-------------|------------|-------------|

**Rules:**
- Wave 1 = BRD's "Must Have". Wave 2 = "Should Have". Wave 3 = "Out of Scope / Future".
- Sequence by dependency order within each wave.
- Complexity: S (1-2 files, <1 day), M (3-5 files, 1-3 days), L (6+ files, 3+ days).
- Each feature becomes one cycle of: /plan-feature → code → /comprehensive-test → /review-changes.

## Step 6: Compile and Present

Save all sections above to `docs/product-plan.md`.

**Present summary to user:**
- Total user stories: {count}
- Screens/pages: {count}
- Data entities: {count}
- API endpoints: {count}
- Wave 1 features: {count} (estimated complexity breakdown: S/M/L)

**Ask:** "Approve this product plan? This becomes the implementation roadmap. Or request changes?"

Note: Each feature will go through a **Feature Preview gate** in `/plan-feature` Step 2 before detailed implementation planning — the user validates workflow, UI/UX, and data touchpoints at that stage.

## After Approval

1. Update `docs/project-state.md`:
   - Set Phase 2 (Product) to Complete with date
   - Populate Features Backlog from Wave 1 priority matrix
   - Set first feature (by dependency order) as Active Feature with status "Ready for Planning"
2. Suggest: "Product plan approved. Run `/plan-feature` to start implementing Feature 1: {name}."
