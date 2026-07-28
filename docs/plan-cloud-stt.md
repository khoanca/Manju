# Plan: FR-10 — Tier `cloud`: streaming STT online-first (Soniox)

- **Source**: PRD FR-10 (Đợt 1.5) + docs/research-online-stt-stack.md
- **Status**: In-Progress
- **Updated**: 2026-07-29
- **Lưu ý**: chưa có SONIOX_API_KEY — build + test bằng mock transport; smoke thật & benchmark chốt provider chạy khi user điền key. Provider đổi được sau bench (module cloud_stt cô lập phần Soniox-specific).

## Approach

Nhánh cloud là một "bộ não decode" thay thế bên trong `LiveSession` hiện có — KHÔNG phải Engine mới trong EngineRegistry (ABC Engine là decode-theo-cửa-sổ, cloud là stream token liên tục; research doc đã chốt "interface mới cạnh Engine"). Client gửi `mode: online|offline` trong WS `start`; mode online thì `feed()` vẫn ghi buffer + file WAV như cũ (để fallback + lưu), đồng thời đẩy PCM sang `SonioxLive` (WS `wss://stt-rt.soniox.com/transcribe-websocket`, model `stt-rt-v5`, `language_hints [vi,en]`, `context.terms` = glossary + corrections, diarization bật). Token final gom thành utterance (gap ≥0.7s / >28s) → tái dùng nguyên protocol `partial`/`final` với client — UI subtitle không đổi. Provider lỗi/rớt → cắt buffer tới `end_ms` token final cuối rồi khởi động decode loop local (never-fail, phiên không đứt). Pass 2 trên nhánh cloud mặc định TẮT (setting `cloud_cleanup`); sau Stop chạy `stt-async-v5` nghe lại WAV nền (setting `cloud_relisten`, mặc định BẬT) → bản chính tốt hơn, xong XÓA file/transcription phía Soniox. Thư viện WS client: `websocket-client` (sync, hợp mô hình thread của live.py).

## Tasks

| ID | Task | Source | Dep | Files | Status |
|-----|------|--------|-----|-------|--------|
| T-001 | Contract: `mode` trong WS start; GET `/api/settings` thêm `cloud:{available,provider,relisten,cleanup}`; PUT settings thêm `cloud_cleanup`(def 0)/`cloud_relisten`(def 1); dep `websocket-client` | FR-10 (UX per-phiên) | ‖ | pyproject.toml, app/main.py | [ ] |
| T-002 | `app/cloud_stt.py`: `available()`, `SonioxLive` (config→binary PCM→token rx, utterance grouping, speaker, callbacks on_partial/on_final/on_error, transport injectable), `build_context()` (glossary+corrections→context.terms, cap 10k chars) | FR-10 (kiến trúc) | → T-001 | app/cloud_stt.py | [ ] |
| T-003 | Async client: `transcribe_file_async()` upload→poll→text + `delete_remote()` (xóa file + transcription sau khi nhận) — model stt-async-v5, httpx client injectable | FR-10 (re-listen + privacy xóa) | ‖ với T-002 | app/cloud_stt.py | [ ] |
| T-004 | live.py: mode online — start CloudWorker thay decode loop; map token→utt (`partial`/`final` giữ nguyên msg); raw=final tokens là nguồn chân lý; pass 2 chỉ khi `cloud_cleanup`; fallback provider-fail → trim buffer theo end_ms → start local loop; telemetry nguồn engine vào raw_segments | FR-10 | → T-002 | app/live.py | [ ] |
| T-005 | Re-listen sau Stop: phiên online + có WAV server + `cloud_relisten` → thread nền async-v5 → `db.update_live_text` (text=bản async, raw giữ bản live) → xóa remote; never-fail | FR-10 (nhánh cloud) | → T-003, T-004 | app/live.py, app/transcribe.py | [ ] |
| T-006 | Upload online: form upload thêm lựa chọn online (khi available) → `_process` nhánh cloud qua async API (bỏ qua denoise/diarize local — Soniox tự diarize), fallback local khi lỗi | FR-10 ("upload chọn tương tự") | → T-003 | app/transcribe.py, app/main.py | [ ] |
| T-007 | UI: start card radio Online/Offline (chỉ hiện khi available, nhớ lần trước, hint trả phí + audio stream ra ngoài), gửi `mode` trong start; badge engine khi ghi; settings card: toggle "AI làm sạch (cloud)" + "Nghe lại sau Stop"; upload: select engine | FR-10 (UX 2026-07-29) | → T-001 (contract) ‖ T-004 | app/static/index.html, app/static/app.js | [ ] |
| T-008 | Tests: cloud_stt (utt grouping, context cap, fake WS transport, async poll+delete, error paths), live online e2e (fake transport → partial/final/save, fallback mid-session, cloud_cleanup on/off), settings API, upload online | testing.md | → T-004..T-007 | tests/test_cloud_stt.py, tests/test_live_cloud.py | [ ] |
| T-009 | Bench legs realtime: `soniox-rt` (WS), cập nhật model async→stt-async-v5, thêm `assemblyai`/`deepgram` legs — chạy khi có key | FR-10 (gate benchmark) | ‖ | scripts/bench_cloud_stt.py | [ ] |
| T-010 | Docs write-back: plan ledger, project-state, PRD nếu lệch | guardrails spec-precedence | → hết | docs/* | [ ] |

## Edge Cases & Error Handling

- Online được chọn nhưng thiếu key/mất mạng NGAY lúc start → server trả `mode:"offline"` trong msg `session`, client hiện badge offline — phiên vẫn chạy.
- Provider chết giữa phiên (WS close/error/balance hết) → fallback local: trim buffer tới sample tương ứng end_ms token final cuối (tránh decode lại phần đã chốt), utterance đang dở mất partial nhưng audio còn trong buffer → local decode lại.
- Client mỏng (OPFS, `store_audio=False`) → không có WAV server → bỏ re-listen (chỉ có bản live).
- Resume (client rớt WS tới server) giữ nguyên cơ chế token — CloudWorker sống trong LiveSession được park như cũ; NHƯNG Soniox idle >~20s cần keepalive → CloudWorker gửi `{"type":"keepalive"}` mỗi 15s khi không có audio.
- Re-listen fail/timeout → giữ bản live, không đụng DB; xóa remote best-effort (log, không raise).
- `context` quá 10k ký tự → cắt bớt phía corrections (giữ glossary user trọn).

## Test Strategy

- Unit cloud_stt: transport fake bơm kịch bản token (multi-utt, speaker đổi, error frame) → assert utt segmentation + callbacks; context builder cap; async client với httpx.MockTransport (upload→poll→transcript→delete đủ vòng đời, kể cả delete-sau-lỗi).
- e2e live: LiveSession(mode=online) + fake transport — partial/final về client đúng thứ tự, _save ghi raw_text đúng nguồn, fallback giữa phiên ra transcript liền mạch, cloud_cleanup=0 không gọi LLM.
- Full suite + ruff + mypy + node --check sau cùng (full pytest ~12ph — chạy nền).

## Rollback

- Không migration DB (tái dùng cột sẵn có; settings là bảng key-value). Tắt tính năng = không điền SONIOX_API_KEY (start card tự ẩn option online) — hành vi offline giữ nguyên 100% (mode mặc định khi thiếu key).

## Stacked commits (mỗi cái ≤400 LOC, độc lập chạy được)

1. T-001+T-002+T-003 (+unit tests) — nền cloud_stt, chưa đụng live
2. T-004+T-005 (+e2e tests) — live online path + fallback + re-listen ← điểm tính năng thành user-visible cùng (4)
3. T-006 — upload online
4. T-007 — UI
5. T-009 — bench legs
