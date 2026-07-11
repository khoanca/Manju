---
name: research-codebase
description: Read-only codebase exploration. Use before planning any feature or debugging.
when_to_use: When starting a new task, investigating a bug, or needing to understand existing patterns before making changes. This is the Layer 2 scope entry point in CLAUDE.md routing — use when a task touches 2-3 files and existing code context matters before coding.
context: fork
---

## Strategy

Use parallel sub-agents to explore efficiently:
- **Locator**: Find relevant files/directories by name and structure (no deep reading).
- **Analyzer**: Read key files in detail, understand data flow and dependencies.
- **Pattern Finder**: Identify conventions, naming styles, architectural patterns already in use.

Single-agent is sufficient for small codebases (< 20 files). Use sub-agents for larger ones.

## Process (read-only, no edits)

1. Map relevant files and directories.
2. Identify patterns, conventions, and naming styles.
3. Find existing similar implementations.
4. Document constraints and dependencies.
5. Note potential risks or conflicts.
6. If proposing external tools/libs: verify they exist (WebFetch to registry).

## Output Format

### Files Identified
- [path]: [purpose]

### Patterns Found
- [pattern]: [where used]

### Constraints
- [constraint]: [why it matters]

### Risks
- [risk]: [mitigation]

### External Dependencies (if any)
- [package]: [verified: yes/no] [last publish] [weekly downloads]
