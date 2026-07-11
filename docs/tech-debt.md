# Tech Debt

## Baseline audit — 2026-07-11

Tracked debt (existing files that break framework rules; fix opportunistically).

### Hard (code.md: functions <40 lines, ≤3 params)

- [ ] app/db.py:66 — `insert_transcript` has 12 params → group into a dataclass/TypedDict
- [ ] app/transcribe.py:139 — `save_transcript` has 8 params (42 lines)
- [ ] app/transcribe.py:75 — `_run` is 62 lines, 7 params
- [ ] app/live.py:390 — `handle` is 64 lines
- [ ] app/db.py:194 — `migrate_from_files` is 54 lines
- [ ] app/live.py:56 — `LiveSession.__init__` is 47 lines
- [ ] 4–6 params in: app/correct.py (`_correct_chunk`, `_correct_chunk_openrouter`, `correct_sentence` — 5 each), app/engines.py (`decode`/`_decode`/`transcribe_file` — 4–5), app/main.py:47 (`api_transcribe` — 5), app/transcribe.py:183 (`start_transcription` — 6)

### Docs

- [ ] CLAUDE.md — 97 lines (budget 80). Candidate: extract "Layer 0 — Decompose & map" or "Key structure" details to `.claude/rules/`

### Soft (review when touching the file — needs human judgment)

- [ ] app/engines.py:277 — `except ImportError: pass` in `_probe()` is an intentional engine-tier fallback (mlx → cuda → cpu) but lacks the explanatory comment that line 286 has
- [ ] app/static/app.js — 506 lines in one file; acceptable for a vanilla-JS PWA, consider splitting by screen if it keeps growing

### Known gaps (already tracked in docs/project-state.md)

- [ ] No test/lint/typecheck configured (testing.md requires tests for behavior) — proposed: pytest / ruff / mypy
