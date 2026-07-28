# PRD – Manju: Meeting Transcriber local-first + tổ chức

**Phiên bản:** 1.1 · **Ngày:** 2026-07-28 (v1.0: 2026-07-09) · **Trạng thái:** Approved (đang build; v1.1 đổi định hướng xử lý sang hybrid online-first — FR-10)
**Tài liệu liên quan:** [BRD.md](BRD.md) (yêu cầu nghiệp vụ gốc — transcribe, pass 2, live)

## 1. Tổng quan mô hình

Hệ thống theo mô hình **lưu local — xử lý hybrid (online-first) + org sync** (đổi định hướng 2026-07-28; trước đó: local-first toàn phần):

- **Dữ liệu của ai LƯU trên máy người đó.** Audio (nặng, riêng tư nhất) là file trong thư mục user tự chọn; text + metadata trong database local (SQLite). Không bản lưu nào tự sinh ở bên thứ ba.
- **Xử lý được phép online, offline vẫn chạy đủ.** Khi có mạng + API key, ASR live/upload ưu tiên cloud streaming STT (tier `cloud`, FR-10) và pass 2 có thể dùng cloud LLM; audio chỉ STREAM tới provider user đã bật để transcribe — không phải sync lưu trữ. Mất mạng/không key → tự rơi về engine local (mlx → cuda → cpu), không mất tính năng.
- **Tổ chức là lớp cộng tác, chỉ nhận text.** Mỗi người có tài khoản riêng trên org cloud (Supabase); user **chủ động chọn từng bản** transcript để đẩy text lên org DB. Không sync audio, không sync tự động.
- **Máy nào cũng dùng được, máy mạnh tự phát huy.** App dò năng lực máy + mạng lúc khởi động và chọn engine ASR tốt nhất khả dụng (cloud khi online, local theo tier máy); điện thoại/máy yếu dùng qua browser (PWA) làm client mỏng.

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
- Probe lúc khởi động, chọn tier đầu tiên thỏa: **cloud** (streaming STT online — mặc định khi có mạng + key, user chọn per-phiên ở start card; FR-10) → **mlx** (Mac Apple Silicon, GPU Metal) → *(dành chỗ: native ANE khi Apple thêm vi_VN — xem BRD mục 4 Phase 0/1)* → **cuda** (GPU NVIDIA, faster-whisper float16) → **cpu** (faster-whisper int8; cỡ model theo RAM/core của máy).
- Env `ASR_ENGINE` ép tier (phục vụ test/vận hành). UI hiển thị engine đang dùng.
- Interface engine thống nhất cho cả live (decode partial/final) và upload (transcribe file), là điểm cắm cho tier `cloud` (FR-10), `remote` LAN (Đợt 3) và native.

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
**PARKED một phần 2026-07-26 (FR-9/US-827):** các US đưa ngữ cảnh/bias tự động vào ĐƯỜNG LIVE bị gỡ khỏi live path — đo thực địa cho thấy bias tự động bị decoder echo lên subtitle khi im lặng/nhiễu và kéo `no_speech_prob` về 0 (vô hiệu gate chống bịa), trong khi thư viện corrections còn rỗng nên lợi ích chưa chứng minh được. Module giữ nguyên cho upload/reanalyze; khôi phục khi thư viện có dữ liệu thật (code cũ trong git history, nhánh trước commit FR-9).
- US-806 — Topic-bias ~~(live)~~ **PARKED cho live**; re-rank lexicon vẫn dùng cho upload.
- US-807 — Personal lexicon (học): mine từ đặc trưng mỗi speaker từ transcript đã diarize (bảng `speaker_terms`) — giữ (không đụng live path).
- US-808 — Personal lexicon (dùng ở live) — **PARKED**.
- US-809 — Metadata cuộc họp làm topic khởi tạo — **PARKED** (title vẫn dùng đặt tên transcript).
- US-810 — Region theo speaker: tag Bắc/Trung/Nam → ưu tiên cặp sửa đúng vùng miền — giữ cho upload/pass-2 full-text.
- US-811 — Revision: câu confidence thấp được re-decode nền bằng setting mạnh hơn, đẩy bản sửa qua WS (mlx mặc định tắt — xem plan-live-intelligence).
- US-812 — Uncertain words: word-confidence thấp báo pass 2 chỗ cần soát kỹ (opt-in) — giữ.
- US-813 — Denoise mic (noisereduce, opt-in) trước VAD; WAV lưu vẫn raw — giữ.
- US-814 — Speaker-ID realtime — **PARKED** (tranh CPU với decode; đã opt-in OFF từ 2026-07-20).
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

### FR-9 — Live Reliability: chống lặp/bịa + Stop tức thời (net-new 2026-07-26)
- Lý do: điều tra thực địa 2026-07-25 ("đăng đăng đăng" trên subtitle, Stop chờ 18-25s) — gốc rễ là decoder-born loops (greedy partial + thang temperature chấp nhận rác T=1.0), filter mù lặp ×3, và final decode dài giữ decode lock ngay lúc Stop. Tái hiện + kiểm chứng chéo trên WAV thật.
- US-824 — Chống lặp: subtitle (partial + final + bản lưu) không chứa chuỗi lặp thoái hoá; lặp nhấn mạnh thật giữ nguyên. Cơ chế: gate `compression_ratio>2.4` (chỉ số whisper trả sẵn), cycle ≥3-token lặp ≥3, run ×3 khi segment logprob<−0.5 — ngưỡng kiểm 0 false-positive trên 34 phiên.
- US-825 — Stop tức thời: bấm Stop → UI rời màn ghi <0.5s, lưu chạy nền; correction câu cuối không bị mất im lặng; audio OPFS không mồ côi ở nhánh lỗi.
- US-826 — Live decode gọn: live-final chỉ T=(0.0, 0.2) + accept-or-drop (upload giữ thang đủ); đuôi im lặng bị trim trước decode (pad 0.3s); initial_prompt CHỈ chứa glossary user gõ.
- US-827 — Park ngữ cảnh khỏi live path (xem FR-7).
- US-828 — Telemetry decode: mỗi utterance lưu temperature/compression_ratio/no_speech_prob thật/wall-time vào `raw_segments` — truy phiên lỗi không cần tái hiện.
- Chi tiết AC/task: `docs/plan-live-reliability.md`.

### FR-10 — Tier `cloud`: streaming STT online-first (net-new 2026-07-28)
- Định hướng user chốt 2026-07-28: lưu local giữ nguyên, xử lý ASR ưu tiên online khi khả dụng. Nghiên cứu stack: `docs/research-online-stt-stack.md`.
- Kiến trúc: GIỮ FastAPI + PWA + WS protocol hiện có, KHÔNG adopt Pipecat/LiveKit nguyên khối (app transcription-only không dùng turn-taking/TTS; đường Whisper local của Pipecat là segmented — thoái lui so với live loop hiện tại). Thêm đường streaming cloud trong `live.py`/`engines.py`: đẩy PCM liên tục tới provider, nhận interim/final passthrough; mlx-whisper large-v3-turbo + live loop hiện tại giữ nguyên làm fallback offline.
- Provider: ứng viên chính **Soniox stt-rt-v5** (code-switch vi↔en native giữa câu, diarization gộp, $0.12/h); dự phòng AssemblyAI Universal-3.5 Pro Streaming ($0.45/h, $50 free), Deepgram Nova-3 `language=vi` ($0.29/h, $200 free). Không provider nào công bố WER vi streaming → chốt cuối bằng benchmark audio thật (`scripts/bench_cloud_stt.py`, thêm realtime legs) TRƯỚC khi build tích hợp.
- Nhánh cloud (tinh chỉnh 2026-07-29): transcript Soniox thô là NGUỒN CHÂN LÝ (`raw_text`); glossary + tên người + cặp sửa đã duyệt đẩy vào context của provider; **pass 2 LLM thành hậu xử lý TÙY CHỌN, mặc định TẮT** — Whisper cần LLM vì phiên âm sai thuật ngữ, Soniox code-switch native có thể không cần; chỉ bật mặc định nếu benchmark chứng minh WER giảm mà không sinh substitution mới. Sau Stop: `stt-async-v5` nghe lại toàn bộ audio (~+$0.10/h) tạo bản lưu chuẩn hơn; job async phải XÓA file/transcript phía Soniox sau khi nhận kết quả (privacy: realtime không bị lưu, async lưu tới khi xóa, không dùng train — soniox.com/docs/security-and-privacy).
- Nhánh local fallback: pass 2 giữ nguyên (gemma4:e4b local / OpenRouter); sau Stop cân nhắc re-transcribe WAV bằng large-v3-full nền (hạ tầng reanalyze sẵn có) để bản lưu vượt subtitle "an toàn" của live loop.
- UX chế độ (chốt 2026-07-29): **chọn Online/Offline per-PHIÊN ở start card** (không phải cổng chặn khi vào site — người xem lịch sử không bị hỏi). Option Online kèm hint: chính xác thuật ngữ hơn + tách người nói, dịch vụ trả phí (~$0.12–0.22/h), audio stream tới provider để nhận dạng (không lưu ở đó); Offline: chạy hoàn toàn trên máy. Nhớ lựa chọn lần trước; mặc định online khi có key + mạng; upload chọn tương tự. Đang ghi online mà rớt mạng → phiên TỰ rơi về mlx không đứt (never-fail); badge engine đang dùng (FR-1). Client gửi `mode` trong message `start` của WS. Lưu ý tài nguyên: phiên online không chiếm `_decode_lock` → chỉ phiên offline tính vào giới hạn decode đồng thời.
- AC/task chi tiết: `/plan-feature` khi bắt đầu build.

## 3. Ngoài phạm vi
- Sync 2 chiều / sửa đồng thời (local là nguồn chân lý, push là một chiều).
- Sync audio lên org.
- Realtime collab, comment, tóm tắt tự động.
- Dịch ngôn ngữ.

## 4. Phân đợt

- **Đợt 1 — nền local (đang build):** EngineRegistry; SQLite + migration; thư mục audio configurable; multi-session; tách JS + lưu settings; wake lock; WS reconnect/resume; PWA + OPFS.
- **Đợt 1.5 — cloud STT online-first (net-new 2026-07-28):** FR-10 — benchmark provider bằng audio thật → tier `cloud` streaming cho live + upload, fallback local nguyên trạng. Ưu tiên trước phần còn lại của Đợt 2.
- **Đợt 2 — org cloud:** Supabase project (orgs, org_members, transcripts_text, visibility_grants + RLS; Edge Function invite); app local đăng nhập + push từng bản; màn org viewer + quản lý grants. Schema/flow chi tiết: `supabase/migrations/001_org.sql` (khi build).
- **Đợt 3 — mở rộng:** tier ASR `remote` (máy yếu mượn máy mạnh); diarization FluidAudio + system audio tap (BRD mục 4 Phase 2–3); native ASR khi Apple thêm vi_VN vào SpeechTranscriber (probe sẵn: `native/bin/native-asr`).
