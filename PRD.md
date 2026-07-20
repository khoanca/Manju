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
- Mở rộng slang/teencode MXH (net-new 2026-07-20): US-815 seed slang nói tự soạn (`tag='slang'`, toggle mặc định tắt, ranking region-neutral); US-816 LLM (OpenRouter) tổng hợp slang đang hot → nhập `pending` chờ duyệt; US-817 đọc trang web public tổng hợp trend (robots.txt, không né anti-bot; app không tự scrape TikTok/FB/X — API đóng); US-818 caption MXH tươi qua Apify (opt-in `APIFY_TOKEN`, hashtag TikTok qua setting, ToS do user quyết khi chọn dùng Apify). Chi tiết: `docs/plan-slang-lexicon.md`.

### FR-7 — Live Intelligence: bias thông minh cho subtitle trực tiếp (net-new 2026-07-20)
- US-806 — Topic-bias: topic (US-805) xếp lại lexicon + tiêm vào Whisper initial_prompt, refresh glossary giữa phiên.
- US-807 — Personal lexicon (học): mine từ đặc trưng mỗi speaker từ transcript đã diarize (bảng `speaker_terms`).
- US-808 — Personal lexicon (dùng): chọn người tham dự lúc mở live → nạp từ của họ vào bias.
- US-809 — Metadata cuộc họp: title/agenda làm topic khởi tạo, bias từ câu đầu tiên.
- US-810 — Region theo speaker: tag Bắc/Trung/Nam → ưu tiên cặp sửa đúng vùng miền.
- US-811 — Revision: câu confidence thấp được re-decode nền bằng setting mạnh hơn, đẩy bản sửa qua WS.
- US-812 — Uncertain words: word-confidence thấp báo pass 2 chỗ cần soát kỹ (opt-in).
- US-813 — Denoise mic (noisereduce, opt-in) trước VAD; WAV lưu vẫn raw.
- US-814 — Speaker-ID realtime: nhận diện người nói theo utterance final, tag tên + bias theo người đang nói.
- Chi tiết AC/task: `docs/plan-live-intelligence.md` (plan doc thắng PRD khi lệch, tới khi write-back).

### FR-8 — Accuracy Bench: đo độ chính xác bằng bản sửa tay (net-new 2026-07-20)
- Lý do: repo có 228 test nhưng không phép đo độ chính xác nào → mọi lỗi chất lượng đều do user phát hiện ngoài thực địa (3 hotfix ngày 2026-07-20). Test hiện tại kiểm code chạy đúng cơ chế, không kiểm sản phẩm phiên âm có đúng không.
- Dùng lại `edited_text` mà FR-6 vốn đã thu thập làm chuẩn đo — một công sửa phục vụ hai mục đích (nuôi thư viện tự học + làm chuẩn chấm điểm), không tạo dataset riêng.
- US-819 — Đánh dấu bản chuẩn: user sửa transcript cho đúng rồi bật cờ `golden`; chặn bật cờ khi chưa có bản sửa tay (chấm điểm máy bằng output của máy là số đẹp giả).
- US-820 — Đo WER/CER bản máy thô và bản pass 2 so với chuẩn → trả lời "pass 2 giúp hay hại".
- US-821 — So sánh cấu hình: decode lại audio gốc theo từng mức đệm biên VAD rồi chấm điểm, để chốt hằng số bằng số liệu thay vì phỏng đoán (`PREROLL_S=0.32` đầu / `0.2` đuôi hiện bất đối xứng, ra đời ở commit checkpoint không tài liệu).
- US-822 — Đo lặp chữ chéo 2 segment liền nhau: cái giá phải theo dõi khi tăng đệm đuôi (nuốt sang audio câu kế tiếp).
- WER/CER tự cài bằng Levenshtein, không thêm dependency; chuẩn hoá gộp hoa thường + bỏ dấu câu nhưng GIỮ dấu thanh và số (sai thanh điệu là lỗi thật).
- Ngoài phạm vi đợt này: đo ảnh hưởng của `lexicon_*` / `revise` / `live_ident` (cần dựng lại toàn pipeline live, không chỉ cửa sổ decode).
- Chi tiết AC/task: `docs/plan-accuracy-bench.md`.

## 3. Ngoài phạm vi
- Sync 2 chiều / sửa đồng thời (local là nguồn chân lý, push là một chiều).
- Sync audio lên org.
- Realtime collab, comment, tóm tắt tự động.
- Dịch ngôn ngữ.

## 4. Phân đợt

- **Đợt 1 — nền local (đang build):** EngineRegistry; SQLite + migration; thư mục audio configurable; multi-session; tách JS + lưu settings; wake lock; WS reconnect/resume; PWA + OPFS.
- **Đợt 2 — org cloud:** Supabase project (orgs, org_members, transcripts_text, visibility_grants + RLS; Edge Function invite); app local đăng nhập + push từng bản; màn org viewer + quản lý grants. Schema/flow chi tiết: `supabase/migrations/001_org.sql` (khi build).
- **Đợt 3 — mở rộng:** tier ASR `remote` (máy yếu mượn máy mạnh); diarization FluidAudio + system audio tap (BRD mục 4 Phase 2–3); native ASR khi Apple thêm vi_VN vào SpeechTranscriber (probe sẵn: `native/bin/native-asr`).
