# Project Brief: Manju — Meeting Transcriber

## Overview
- **Type**: Local-first desktop app (FastAPI server + PWA thin client) + MCP server
- **Description**: App chạy local để chuyển ghi âm cuộc họp / hội thoại thành văn bản (voice-to-text) bằng Whisper, có pass 2 LLM soát thuật ngữ và chế độ Live subtitle từ mic. Kèm MCP server để Claude đọc transcript.
- **Target Users**: Cá nhân/nhóm cần transcribe cuộc họp riêng tư, hội thoại pha tiếng Việt + thuật ngữ tiếng Anh; máy yếu/điện thoại dùng qua PWA client mỏng.
- **Tech Stack**: Python 3.12 · uv · FastAPI + uvicorn · faster-whisper / mlx-whisper (ASR theo tier máy) · Ollama / OpenRouter (pass 2) · SQLite · PWA (vanilla JS, OPFS, service worker) · MCP (stdio)
- **Package Manager**: uv (lockfile `uv.lock` là nguồn chân lý)
- **Team**: Solo
- **Deployment**: Self-hosted / local-first (server Python chạy trên máy user); org sync qua Supabase là đợt sau

## MVP Scope
Đưa file ghi âm (hoặc mic live) vào → nhận **toàn bộ text chính xác** cho nội dung pha Việt–Anh, lưu lại transcript + audio để xem/nghe lại. Pass 2 (soát thuật ngữ) và Live subtitle là năng lực lõi kèm theo.

## Constraints
- **Timeline**: Đang build **Đợt 1** theo PRD (Approved).
- **Scale**: Local single-user per máy; `MAX_LIVE_SESSIONS` mặc định 2 phiên live đồng thời.
- **Platform**: Tối ưu Apple Silicon (mlx-whisper / Metal); fallback CUDA / CPU (faster-whisper int8).
- **Integrations**: Ollama (`gemma4:e4b`, tuỳ chọn) hoặc OpenRouter cho pass 2; MCP server cho Claude Code.
- **Privacy**: Audio không bao giờ tự rời máy; org cloud chỉ nhận text khi user chủ động push.

## Activated Rules
Không kích hoạt thêm template rule khi init (docs-only mode). Rule `_framework/` mặc định (backend, frontend, database, security, devops, testing, code, git, guardrails) áp dụng theo nguyên tắc; lưu ý các rule này viết theo giọng Node/TS — diễn giải sang Python/FastAPI khi áp dụng.

## Decisions Log
| # | Decision | Rationale | Date |
|---|----------|-----------|------|
| 1 | Python 3.12 pin qua uv | Tránh thiếu wheel ML (ctranslate2) trên 3.13/3.14 | 2026-07-09 |
| 2 | mlx-whisper large-v3-turbo cho ASR live trên Mac | Thắng benchmark: 8.4x RT, thuật ngữ Anh đúng; PhoWhisper bị loại | 2026-07-09 |
| 3 | gemma4:e4b làm model pass 2 | Thắng benchmark sửa thuật ngữ Việt–Anh (3.3GB RAM, ~5s/chunk); model thinking bị loại | 2026-07-09 |
| 4 | init-project docs-only, bỏ scaffold | Dự án Python đã có sẵn code + PRD, không scaffold Node/TS | 2026-07-09 |
