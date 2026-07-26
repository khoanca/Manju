# Tech Debt

## Baseline audit — 2026-07-11

Tracked debt (existing files that break framework rules; fix opportunistically).

### Hard (code.md: functions <40 lines, ≤3 params) — ✅ fixed 2026-07-11

All items resolved in the baseline-refactor commit (params grouped into dataclasses:
`db.TranscriptRecord`, `db.SyncState`, `engines.DecodeSpec`, `transcribe.JobSpec`,
`transcribe.TranscriptDraft`, `correct.LlmOpts`; long functions split). Verified:
AST re-audit clean, 27 tests pass, ruff + mypy clean.

- [x] app/db.py — `insert_transcript` 12 params → `TranscriptRecord`; `migrate_from_files` 54 dòng → tách `_legacy_row`/`_legacy_segments`; `set_sync_state` 5 params → `SyncState` (phát hiện bổ sung)
- [x] app/transcribe.py — `_run` 62 dòng/7 params → `_process` + `_maybe_correct` + `JobSpec`; `save_transcript` 8 params → `TranscriptDraft` + tách `_store_audio`
- [x] app/live.py — `handle` 64 dòng → tách `_start_session`/`_resume_session`/`_finish`; `__init__` 47 dòng → tách `_pick_model`/`_init_buffers`/`_init_recording`/`_init_workers`
- [x] app/engines.py — `decode`/`_decode`/`transcribe_file` 4–5 params → `DecodeSpec`
- [x] app/correct.py — `_correct_chunk`/`_correct_chunk_openrouter`/`correct_sentence` 5 params → `LlmOpts`
- [x] app/main.py — `api_transcribe` 5 params → Pydantic Form model `TranscribeForm` (wire format không đổi)
- [x] app/org.py — `push_transcript` 4 params → bỏ param `timeout`, dùng hằng `PUSH_TIMEOUT_S` (phát hiện bổ sung)

### Docs — ✅ fixed 2026-07-11

- [x] CLAUDE.md — 97 → 73 dòng: "What This Is" viết lại đúng bản chất project (Manju, không phải framework template); Layer 0 tách sang `.claude/rules/project/routing-layer0.md`

### Soft (review when touching the file — needs human judgment)

- [ ] app/engines.py:277 — `except ImportError: pass` in `_probe()` is an intentional engine-tier fallback (mlx → cuda → cpu) but lacks the explanatory comment that the CUDA probe below has
- [ ] app/static/app.js — 506 dòng một file; chấp nhận được với PWA vanilla, cân nhắc tách theo screen nếu tiếp tục phình

### Known gaps

- [x] Test/lint/typecheck — đã cấu hình 2026-07-11: pytest (27 tests, `tests/`), ruff, mypy (pyproject.toml)
- [ ] US-825 (FR-9): stop_wall đo 6.19/7.54s trong harness feed 3.4x realtime (backlog dồn 28s) — chiếu realtime <5s nhưng là ngoại suy. Đo lại 1 lần trên phiên live thật (telemetry decode_wall_s đã có trong raw_segments); nếu vẫn >5s: hạ MAX_UTTERANCE_S hoặc stop-final 1 nấc T=0. UI stop đã tức thời phía client, số này chỉ là lưu nền.
- [ ] US-825: nhánh `_persist_late_correction` ghi đè DB chưa được exercise trong T-013 (mọi correction trả changed=False qua OpenRouter) — sẽ tự kích hoạt khi Ollama bật lại và pass-2 thật sự sửa câu; theo dõi lần live đầu.
