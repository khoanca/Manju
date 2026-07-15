# PRD – Manju: Meeting Transcriber local-first + tổ chức

**Phiên bản:** 1.0 · **Ngày:** 2026-07-09 · **Trạng thái:** Approved (đang build Đợt 1)
**Tài liệu liên quan:** [BRD.md](BRD.md) (yêu cầu nghiệp vụ gốc — transcribe, pass 2, live)

## 1. Tổng quan mô hình

Hệ thống theo mô hình **local-first + org sync**:

- **Dữ liệu của ai nằm trên máy người đó.** Audio (nặng, riêng tư nhất) là file trong thư mục user tự chọn, không bao giờ tự rời máy. Text + metadata nằm trong database local (SQLite).
- **Tổ chức là lớp cộng tác, chỉ nhận text.** Mỗi người có tài khoản riêng trên org cloud (Supabase); user **chủ động chọn từng bản** transcript để đẩy text lên org DB. Không sync audio, không sync tự động.
- **Máy nào cũng dùng được, máy mạnh tự phát huy.** App dò năng lực máy lúc khởi động và chọn engine ASR tốt nhất có trên máy đó; điện thoại/máy yếu dùng qua browser (PWA) làm client mỏng.

### Hai loại client

| | App đầy đủ (personal app) | Client mỏng (PWA) |
|---|---|---|
| Chạy trên | Máy tính của user (server Python local) | Browser/điện thoại, trỏ tới app đầy đủ qua LAN |
| ASR | Local theo engine tier của máy | Dùng ASR của máy chạy app đầy đủ |
| Text | SQLite local | Lưu trong SQLite của máy chủ phiên đó |
| Audio | File trong thư mục user chọn | OPFS trên thiết bị (không lên server) |
| Org sync | Đăng nhập + push từng bản | (Đợt sau) |

## 2. Yêu cầu chức năng

### FR-1 — Engine ASR theo năng lực máy (EngineRegistry)
- Probe lúc khởi động, chọn tier đầu tiên thỏa: **mlx** (Mac Apple Silicon, GPU Metal) → *(dành chỗ: native ANE khi Apple thêm vi_VN — xem BRD mục 4 Phase 0/1)* → **cuda** (GPU NVIDIA, faster-whisper float16) → **cpu** (faster-whisper int8; cỡ model theo RAM/core của máy).
- Env `ASR_ENGINE` ép tier (phục vụ test/vận hành). UI hiển thị engine đang dùng.
- Interface engine thống nhất cho cả live (decode partial/final) và upload (transcribe file), là điểm cắm cho tier `remote` (Đợt 3) và native.

### FR-2 — Lưu trữ local
- **Text:** SQLite `data/manju.db` là nguồn chân lý (transcript, raw, segments, metadata, settings, trạng thái sync). Mỗi bản ghi vẫn xuất `{id}.txt` làm artifact đọc nhanh/grep.
- **Audio:** thư mục lưu do user cấu hình trong Settings (mặc định `data/recordings/`). Đổi thư mục chỉ ảnh hưởng bản mới; bản cũ nhớ đường dẫn riêng, không move file.
- Dữ liệu cũ dạng file JSON được migrate vào DB tự động lúc khởi động (idempotent, không xóa file gốc).
- MCP server đọc cùng DB (read-only) để trợ lý AI truy transcript.

### FR-3 — Nhiều phiên live đồng thời
- Server chịu được N phiên live song song (`MAX_LIVE_SESSIONS`, mặc định 2). Decode được serialize qua lock toàn cục để không tranh GPU; partial "bận thì bỏ tick" nên độ trễ tăng tối đa ~1 tick khi 2 phiên chồng nhau.

### FR-4 — Mobile/PWA và độ bền kết nối
- Cài được lên home screen (manifest + service worker; network-first cho HTML/API).
- Giữ màn hình sáng khi đang ghi (Wake Lock, re-acquire khi đổi tab).
- Mất mạng ngắn không mất dữ liệu: client buffer 60s PCM, server giữ phiên 60s sau khi rớt WS; reconnect + replay từ offset đã ack.
- Client mỏng (truy cập không phải localhost): audio ghi vào OPFS trên thiết bị, server không giữ WAV; có nút tải về/xóa, cảnh báo iOS có thể tự dọn storage sau 7 ngày không dùng.

### FR-5 — Tài khoản & tổ chức (Đợt 2)
- Mỗi người một tài khoản (Supabase Auth). Admin tổ chức invite qua email.
- Push text: nút "Đẩy lên tổ chức" trên từng bản ghi; push lại = cập nhật (upsert). Trạng thái sync hiển thị trong lịch sử.
- **Ma trận quyền xem text trên org:**

| Vai trò | Thấy gì |
|---|---|
| Member | Bản của chính mình |
| Member được cấp grant xem người X | + toàn bộ bản đã push của X |
| Admin | Tất cả bản trong org; quản lý member + grants |

- Thực thi quyền bằng RLS ngay trong Postgres (không tin client).

### FR-6 — Correction Library: sửa transcript + thư viện tự học (net-new 2026-07-15)
- User sửa transcript (bản máy giữ nguyên); app trích cặp (sai → đúng) vào thư viện có duyệt, tự mồi vào ASR + pass 2 các lần sau. Seed lexicon từ địa phương Bắc/Trung/Nam + accent Anh, cập nhật online opt-in. Rolling context cho live subtitle.
- Không fine-tune model (local-first, không GPU/training). Chi tiết US-801..805, AC, task: `docs/plan-correction-library.md`.

## 3. Ngoài phạm vi
- Sync 2 chiều / sửa đồng thời (local là nguồn chân lý, push là một chiều).
- Sync audio lên org.
- Realtime collab, comment, tóm tắt tự động.
- Dịch ngôn ngữ.

## 4. Phân đợt

- **Đợt 1 — nền local (đang build):** EngineRegistry; SQLite + migration; thư mục audio configurable; multi-session; tách JS + lưu settings; wake lock; WS reconnect/resume; PWA + OPFS.
- **Đợt 2 — org cloud:** Supabase project (orgs, org_members, transcripts_text, visibility_grants + RLS; Edge Function invite); app local đăng nhập + push từng bản; màn org viewer + quản lý grants. Schema/flow chi tiết: `supabase/migrations/001_org.sql` (khi build).
- **Đợt 3 — mở rộng:** tier ASR `remote` (máy yếu mượn máy mạnh); diarization FluidAudio + system audio tap (BRD mục 4 Phase 2–3); native ASR khi Apple thêm vi_VN vào SpeechTranscriber (probe sẵn: `native/bin/native-asr`).
