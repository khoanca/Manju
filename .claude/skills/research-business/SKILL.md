---
name: research-business
description: Clarify business requirements from vague ideas. Ask questions, analyze domain, produce a BRD. Use BEFORE plan-feature.
when_to_use: When the user describes a feature, product idea, or business need that is vague, incomplete, or lacks clear acceptance criteria. Do NOT use for pure code tasks (bug fixes, refactors, tests).
---

## Input/Output Contract

INPUT:
  - `docs/project-state.md` (current phase = 0-Complete or 1-Active)
  - `docs/project-brief.md` (project context, tech stack, constraints)
OUTPUT:
  - `docs/brd-{feature-name}.md` (approved BRD)
  - Updated `docs/project-state.md` (phase 1 marked complete)
NEXT: `/product-plan`
GATE: User must approve BRD before proceeding

## Prerequisites

Before starting:
1. Read `docs/project-state.md` — verify project is initialized (phase 0 complete).
2. Read `docs/project-brief.md` — use project type, tech stack, and constraints to inform questions.
3. If no `docs/project-state.md` exists, stop and suggest: "Run `/init-project` first."

## Goal

Transform a vague business idea into a clear BRD (Business Requirements Document) that can be handed to `/product-plan` for product specification.

## Phase 1: Understand the Domain

Before asking anything, analyze what the user already said:
- Extract explicit requirements (what they clearly stated).
- Extract implicit requirements (what they assume but didn't say).
- Identify the domain (e-commerce, SaaS, CMS, fintech, etc.).
- Identify the primary user personas involved.

## Phase 2: Structured Interview

Ask the user targeted questions to fill gaps. Group questions by category, max 5 questions per round. Do NOT ask everything at once.

**Round 1 — Core Intent:**
- Who is the end user? What problem are they facing?
- What does success look like? (measurable outcome)
- What existing workflow does this replace or improve?

**Round 2 — Scope & Boundaries:**
- What is explicitly OUT of scope for v1?
- Are there regulatory/compliance constraints? (GDPR, PCI-DSS, etc.)
- What systems/services does this integrate with?

**Round 3 — Edge Cases & Risks (only if needed):**
- What happens when [error scenario]?
- What are the volume/scale expectations?
- Are there time-sensitive aspects? (deadlines, events, seasons)

**Rules:**
- Never invent requirements the user didn't confirm.
- If the user says "I don't know yet", mark it as TBD in the BRD — do not guess.
- Stop interviewing when all critical fields in the BRD template are filled.
- Adapt questions based on previous answers — skip irrelevant categories.

## Phase 3: Domain Analysis

Based on gathered info, analyze:
- **User Flows**: Map the happy path and top 3 alternate/error paths.
- **Domain Rules**: Business logic that MUST be enforced (e.g., "order cannot be cancelled after shipping").
- **Data Entities**: Key objects and their relationships (not DB schema — business concepts).
- **Integration Points**: External systems, APIs, third-party services.

## Phase 3.5: Ubiquitous Language Glossary

Build a term mapping table that becomes the single source of truth for naming across the entire pipeline. This prevents semantic drift when business concepts get translated to code.

**Process:**
1. Extract all domain-specific terms from Phases 1-3 (user flows, domain rules, data entities).
2. For each term, define the canonical code name (English) and a precise definition.
3. If the user communicates in a non-English language, include the original term for traceability.

**Format (Section 10 of BRD):**

| # | Business Term | Code Name | Definition | Used In |
|---|--------------|-----------|------------|---------|
| G-001 | Đơn hàng | `Order` | A purchase request created by a customer, containing one or more line items | FR-001, DR-002 |
| G-002 | Thanh toán | `Payment` | A financial transaction that settles an Order | FR-003 |

**Rules:**
- Every Data Entity from Phase 3 MUST have a glossary entry.
- Every actor/persona MUST have a glossary entry with its code name (e.g., "Khách hàng" → `Customer`).
- Code Name must be a valid identifier: PascalCase for entities/types, camelCase for fields/actions.
- One business concept = one Code Name. No synonyms in code (e.g., don't use both `User` and `Customer` for the same concept).
- If two business terms map to the same code concept, note the distinction or merge explicitly.
- `/product-plan` and `/plan-feature` MUST use these Code Names — not invent new ones.
- Update the glossary when new terms emerge in later phases.

## Phase 3.75: Gherkin Scenarios

Write Gherkin scenarios for functional requirements. This is the strongest bridge between business semantics and testable behavior — concrete examples eliminate ambiguity that prose requirements leave open.

**Default behavior:**
- **Must Have requirements**: 1 happy path scenario + top error/alternate paths. Always write these.
- **Should Have requirements**: Happy path scenario only.
- **Skip only when**: User explicitly opts out, OR the requirement is a pure config/infra task with no user-observable behavior.

- Use business language from the Glossary (Phase 3.5), not technical terms — these are specs, not test code.
- Gherkin scenarios go in Section 11 of the BRD, not a separate file.

**Gherkin format rules:**
- Feature name = FR-NNN description.
- Scenario names must be unique and descriptive.
- Given/When/Then only — no And/But chaining beyond 2 levels.
- Use concrete example data, not placeholders: `Given a user with balance "$150.00"` not `Given a user with balance "<amount>"`.
- Tag scenarios: `@must-have`, `@should-have`, `@error-path`, `@security`.
- These scenarios become the source of truth for `/comprehensive-test` later.

## Phase 4: Output BRD

Present the BRD in this format. User must approve before moving to `/product-plan`.

```markdown
# BRD: [Feature Name]

## 1. Overview
- **Problem**: [1-2 sentences — what pain point this solves]
- **Solution**: [1-2 sentences — high-level approach]
- **Success Metric**: [measurable outcome]

## 2. User Personas
| Persona | Description | Primary Goal |
|---------|-------------|-------------|
| ...     | ...         | ...         |

## 3. Functional Requirements
### Must Have (v1)
- FR-001: [requirement] — [acceptance criteria]
- FR-002: ...

### Should Have (v1 if time permits)
- FR-010: ...

### Out of Scope (future)
- [item]: [reason deferred]

## 4. User Flows
### Happy Path
1. [step]
2. [step]
3. ...

### Alternate/Error Paths
- [scenario]: [expected behavior]

## 5. Domain Rules
- DR-001: [rule] — [reason]
- DR-002: ...

## 6. Data Entities
| Entity | Key Attributes | Relationships |
|--------|---------------|---------------|
| ...    | ...           | ...           |

## 7. Integration Points
| System | Direction | Purpose |
|--------|-----------|---------|
| ...    | in/out    | ...     |

## 8. Constraints
- **Regulatory**: [if any]
- **Performance**: [expected load/latency]
- **Timeline**: [deadline if any]

## 9. Open Questions (TBD)
- [question]: [owner — who needs to answer]

## 10. Ubiquitous Language Glossary (see Phase 3.5)

| # | Business Term | Code Name | Definition | Used In |
|---|--------------|-----------|------------|---------|
| G-001 | ... | `...` | ... | FR-NNN |

## 11. Gherkin Scenarios (see Phase 3.75)

Feature: FR-001 [requirement name]

  @must-have
  Scenario: [happy path description]
    Given [precondition with concrete data]
    When [action]
    Then [expected outcome]

  @must-have @error-path
  Scenario: [error path description]
    Given [precondition]
    When [action that triggers error]
    Then [expected error handling]
```

## Transition

After user approves the BRD:
1. Save BRD to `docs/brd-{feature-name}.md`.
2. Update `docs/project-state.md`:
   - Set Phase 1 (Business) status to Complete with date.
   - Record BRD artifact path in Phase History.
3. Suggest: "BRD approved. Run `/product-plan` to create the product specification."
4. The BRD becomes the source of truth — `/product-plan` references it, not the original vague prompt.
