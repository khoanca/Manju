# Plan: Manju Enterprise (bản tách riêng, tự host)
- **Source**: PRD FR-11, US-901..US-906
- **Status**: Approved (user duyệt preview 2026-11-09, yêu cầu "plan rồi chạy luôn")
- **Updated**: 2026-11-09

## Approach
Fork Manju thành edition `enterprise` bật bằng env `MANJU_EDITION=enterprise` (code local giữ, không chạy). Một FastAPI phục vụ cả công ty; nhân viên dùng PWA sẵn có. **Audio không chạm đĩa server**: live = server cấp Soniox *temporary API key* (`usage_type=transcribe_websocket`), browser stream PCM thẳng Soniox `stt-rt-v6` (`new WebSocket(url, ["soniox-api-key", key])` — đã xác minh docs), gom token→utterance trong JS (port `cloud_stt.SonioxLive`), mỗi câu final gửi server qua `/ws/live` dạng JSON; WAV ghi OPFS máy nhân viên. Upload = browser → server (RAM, không ghi đĩa) → Soniox async (temp key chỉ dùng được cho WebSocket — xác minh docs). Multi-user bằng bảng `users` (SĐT + mật khẩu scrypt stdlib, cookie session HttpOnly), người đầu = admin, member chờ duyệt. MCP cho LLM cá nhân = client stdio gọi HTTP API bằng API token cá nhân (không đọc SQLite trực tiếp vì DB nằm trên server). Giữ SQLite `manju.db` + `db.py` (migration additive). Chọn cách này thay vì rewrite theo schema 9 bảng ARCHITECTURE.md vì tận dụng 80% code; nối messup là đợt sau.

## Tasks
| ID | Task | Source | Dep | Files | Status |
|-----|------|--------|-----|-------|--------|
| T-001 | Edition flag `app/edition.py` (`is_enterprise()`); db migration: bảng `users`, `sessions`, `api_tokens`, cột `transcripts.owner_id`; `list/read_transcripts` nhận `owner_id` filter | US-901 (AC: member chỉ thấy bản mình) | ‖ | app/edition.py, app/db.py | [ ] |
| T-002 | `app/auth.py`: hash scrypt, register (người đầu = admin active, sau = member pending), login (rate-limit 5 sai/15 phút), session cookie, `current_user` dependency (HTTP + WS cookie), admin guard; routes `/api/auth/{register,login,logout,me}`, `/api/users` (admin list/duyệt/khoá/role), `/api/auth/tokens` (API token cá nhân) | US-901, US-902 | → T-001 | app/auth.py, app/main.py | [ ] |
| T-003 | Enterprise middleware: mọi `/api/*` + `/ws/live` cần session (trừ `/api/auth/*`); transcript scope theo owner/admin ở list/read/subtitle/review-fix/speaker-map; `PUT /api/settings` admin-only; `/api/settings` trả `edition`, ẩn engine local | US-901, US-906 | → T-002 | app/main.py | [ ] |
| T-004 | `POST /api/cloud/session`: gọi Soniox `POST /v1/auth/temporary-api-key` (expires 3600, `client_reference_id`=user) → trả `{api_key, ws_url, config}` (model v6, pcm_s16le 16k, language_hints, diarization, endpoint, context.terms từ glossary+corrections) | US-903 (AC: key thật không rời server) | → T-002 | app/cloud_stt.py, app/main.py | [ ] |
| T-005 | `app/live_direct.py`: `DirectSession` nhận JSON utterance từ client (`{type:"utt", utt, text, start_ms, end_ms, speaker}`), gom thành transcript, pass 2 optional (`cloud_cleanup`), resume token 60s, Stop → `save_transcript(audio_path=None, owner_id)`; `live.handle` rẽ nhánh khi `start.mode == "direct"` | US-903 | → T-001 | app/live_direct.py, app/live.py, app/transcribe.py | [ ] |
| T-006 | `app/static/direct-stt.js`: class `SonioxDirect` — connect subprotocol, config, feed PCM, keepalive 15s, gom token→utterance (gap 700ms / `<end>` / 28s), callbacks partial/final/error/finished, đổi key khi hết hạn | US-903 | ‖ | app/static/direct-stt.js | [ ] |
| T-007 | app.js: edition enterprise → `startLive` dùng SonioxDirect + LiveConn(mode direct) (PCM → Soniox + OPFS, KHÔNG gửi server); màn Đăng nhập/Đăng ký; màn Quản lý user (admin); ẩn mục settings local (`.local-only`); 401 → về màn login | US-901..903, US-906 | → T-002, T-005, T-006 | app/static/app.js, app/static/index.html | [ ] |
| T-008 | Upload enterprise: `/api/transcribe` giữ bytes trong RAM → `cloud_stt.transcribe_bytes_async` → `save_transcript(audio_path=None, owner_id)`; không ffmpeg/denoise/diarize; lỗi cloud → báo lỗi (không fallback local) | US-904 (AC: server không ghi file audio) | → T-003 | app/transcribe.py, app/cloud_stt.py, app/main.py | [ ] |
| T-009 | MCP enterprise: `GET /api/transcripts?q=` (LIKE title/text, scope owner); `mcp_server/enterprise.py` stdio gọi HTTP bằng `MANJU_URL` + `MANJU_TOKEN` — tools `list_transcripts`, `read_transcript`, `search_transcripts` | US-905 | → T-002 | mcp_server/enterprise.py, app/main.py, app/db.py | [ ] |
| T-010 | Dockerfile (python 3.12 + uv, `MANJU_EDITION=enterprise`, volume `/data`), `MANJU_DATA_DIR` env cho db/transcribe, README-enterprise (cài, tạo admin, MCP, privacy: upload đi qua RAM server) | US-906 | → T-008 | Dockerfile, app/db.py, app/transcribe.py, README-enterprise.md | [ ] |
| T-011 | Tests: auth (register/login/role/pending/rate-limit), scope owner, cloud session mock, DirectSession (utt→transcript, resume, save không audio), upload bytes mock, MCP client mock, edition gating | mọi US | ‖ theo item | tests/test_enterprise_*.py | [ ] |
| T-012 | Docs write-back: PRD FR-11, project-state, .env.example | spec-precedence | → hết | docs/*, PRD.md, .env.example | [ ] |

## Edge Cases & Error Handling
- Người đầu tiên đăng ký → admin active ngay; từ người thứ 2 → `pending`, đăng nhập được nhưng API khác trả 403 "chờ admin duyệt".
- Sai mật khẩu 5 lần/SĐT → khoá 15 phút (in-memory; restart xoá — chấp nhận).
- Temp key hết hạn giữa phiên (TTL 1h): Soniox trả error → client gọi `/api/cloud/session` lấy key mới, nối lại, trim không cần (server chỉ nhận text). Mất tối đa 1 utterance đang mở.
- Mất mạng: PCM vẫn ghi OPFS; LiveConn nối lại server với token; SonioxDirect nối lại với key mới. Không nối lại được → `cleanupLive` giữ OPFS, user upload WAV sau.
- Client gửi utterance trùng `utt` (replay sau resume) → server idempotent theo `utt`.
- Thiếu `SONIOX_API_KEY` ở enterprise → `/api/settings.cloud.available=false`, start card báo "admin chưa cấu hình" — không có đường offline.
- Upload lỗi Soniox → job `error` với message, không fallback local, không có file nào trên đĩa.
- Admin cuối cùng không tự hạ role/khoá chính mình.

## Test Strategy
- Mỗi AC một test trong `tests/test_enterprise_*.py`; FastAPI TestClient với `MANJU_EDITION=enterprise` qua monkeypatch; Soniox mock bằng `httpx.MockTransport` (temp key, async job) — không mạng.
- DirectSession: bơm utt → kiểm tra text/segments/speaker, resume replay idempotent, save `audio_file=None`.
- Scope: user A không đọc được bản của B (404), admin đọc được.
- Edition offline (mặc định) không đổi hành vi: full suite cũ phải xanh.
- Browser JS: `node --check` cho app.js/direct-stt.js; smoke thật với key Soniox là việc của user.

## Rollback
- Migration additive (CREATE TABLE IF NOT EXISTS, ADD COLUMN owner_id) — rollback = bỏ `MANJU_EDITION`, cột/bảng thừa vô hại.
- Không xoá cột cũ (`audio_file`), chỉ không ghi.

## Stacked PRs
1. **PR-A nền**: T-001, T-002, T-003, T-011(auth/scope) — multi-user + gating, chưa đổi pipeline.
2. **PR-B live direct**: T-004, T-005, T-006, T-007, T-011(live) — điểm feature hiện ra với user.
3. **PR-C upload + MCP + deploy**: T-008, T-009, T-010, T-012, T-011(còn lại).
