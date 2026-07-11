# PRD – Manju: Meeting Transcriber local-first + tổ chức

**Phiên bản:** 1.1 · **Ngày:** 2026-07-11 · **Trạng thái:** Approved (Đợt 1 xong; Đợt 2 + FR-6 đang build)
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

### FR-6 — Ví credit & LLM proxy (BRD YC-6)

Backend LLM pass 2 có 2 lựa chọn trong Settings: **Local (Ollama, miễn phí)** và **Cloud (trả credit)**. Backend cloud gọi Supabase Edge Function `llm-correct` kèm JWT user; function kiểm tra số dư → gọi LLM bằng key server → trừ credit atomic theo `usage` thực tế → trả text đã sửa. Toàn bộ tính năng gate sau env `CLOUD_BILLING` (mặc định off → app offline nguyên trạng).

User stories:

- **US-601 — Đăng nhập:** là user, tôi đăng nhập/đăng xuất tài khoản (Supabase Auth — cùng tài khoản FR-5) ngay trong Settings để dùng tính năng cloud. Session giữ qua các lần mở app (refresh token lưu local).
- **US-602 — Chọn backend LLM:** là user, tôi chọn backend pass 2 (Local/Cloud) trong Settings; chưa đăng nhập hoặc `CLOUD_BILLING` off thì chỉ có Local.
- **US-603 — Xem ví:** là user, tôi xem số dư credit và lịch sử giao dịch gần nhất (nạp/trừ, model, token) trong Settings.
- **US-604 — Nạp credit:** là user, tôi chọn gói nạp (bảng `topup_packages`), thanh toán QR PayOS; ví tự cộng khi webhook xác nhận. AC: webhook trùng lặp không cộng đôi (idempotent theo `provider_order_code`); đơn quá 10 phút chưa trả → hiện "kiểm tra lại sau".
- **US-605 — Trừ credit theo usage thật:** mỗi call cloud trừ credit = token in/out × đơn giá `pricing_rates` (markup nướng sẵn). AC: trừ atomic race-safe, không âm ví (clamp 0), retry cùng `request_id` không trừ đôi; mỗi transcript hiển thị tổng credit đã tốn; ledger ghi model + token.
- **US-606 — Hết credit bị chặn:** khi 402, tính năng trả phí bị chặn hẳn — upload ra bản chưa sửa + banner "Hết credit" + CTA nạp; live ngừng gửi câu đi sửa (sub vẫn chạy raw) + pill thông báo. Không âm thầm fallback sang Ollama.

Ma trận trách nhiệm: số dư/ledger/giá chỉ đọc từ client (RLS); mọi mutation qua RPC SECURITY DEFINER do Edge Function (service_role) gọi. Chi tiết kỹ thuật: [docs/plan-credit-wallet.md](docs/plan-credit-wallet.md).

## 3. Ngoài phạm vi
- Sync 2 chiều / sửa đồng thời (local là nguồn chân lý, push là một chiều).
- Sync audio lên org.
- Realtime collab, comment, tóm tắt tự động.
- Dịch ngôn ngữ.
- Hoàn tiền/refund tự động, hóa đơn VAT (điều chỉnh ví thủ công qua service_role — ledger reason `adjustment`/`refund`).

## 4. Phân đợt

- **Đợt 1 — nền local (đang build):** EngineRegistry; SQLite + migration; thư mục audio configurable; multi-session; tách JS + lưu settings; wake lock; WS reconnect/resume; PWA + OPFS.
- **Đợt 2 — org cloud:** Supabase project (orgs, org_members, transcripts_text, visibility_grants + RLS; Edge Function invite); app local đăng nhập + push từng bản; màn org viewer + quản lý grants. Schema/flow chi tiết: `supabase/migrations/001_org.sql` (khi build).
- **Đợt 2b — ví credit (FR-6, đang build):** dùng chung Supabase project với Đợt 2 (001+002 apply cùng lúc); billing schema `supabase/migrations/002_billing.sql`, Edge Functions `llm-correct`/`payos-order`/`payos-webhook`; app local: backend cloud trong pass 2, UI ví + nạp PayOS. Đăng nhập Supabase Auth làm chung ở đợt này (trả nợ Đợt 2). Plan: [docs/plan-credit-wallet.md](docs/plan-credit-wallet.md).
- **Đợt 3 — mở rộng:** tier ASR `remote` (máy yếu mượn máy mạnh); diarization FluidAudio + system audio tap (BRD mục 4 Phase 2–3); native ASR khi Apple thêm vi_VN vào SpeechTranscriber (probe sẵn: `native/bin/native-asr`).
