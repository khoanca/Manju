# BRD – App Transcribe cuộc họp

**Phiên bản:** 1.4 · **Ngày:** 2026-07-11 · **Trạng thái:** Draft
**Tài liệu liên quan:** [PRD.md](PRD.md) — spec chi tiết mô hình local-first + tổ chức, phân đợt build.

## 1. Mục đích
App chạy **local** (riêng tư, miễn phí) để chuyển đoạn ghi âm cuộc họp / hội thoại khách hàng thành **văn bản đầy đủ** (voice-to-text).

## 2. Yêu cầu chính

### YC-1 — Transcribe voice-to-text
- Hai đường vào: **(a) Ghi âm trực tiếp** từ micro, **(b) Tải file lên** (mp3, m4a, wav, mp4...).
- App chuyển **toàn bộ giọng nói thành văn bản**, hiển thị kết quả để xem, **Copy** và **Tải .txt**.
- **Lưu lại file ghi âm gốc** kèm transcript (WAV cho phiên ghi trực tiếp, giữ nguyên file cho bản upload); trong lịch sử có thể **nghe lại và tải xuống** bản ghi.
- Transcript + file ghi âm được lưu lại để xem/nghe lại sau (lịch sử).

### YC-2 — Xử lý nội dung pha tiếng Việt + tiếng Anh cho chính xác
- Hội thoại thường **pha tiếng Việt lẫn thuật ngữ tiếng Anh** → cần nhận dạng đúng cả hai.
- App cho **chọn model** theo nhu cầu chính xác: `small` (nhanh) · `large-v3-turbo` (cân bằng — mặc định) · **`large-v3` (chính xác nhất)**. Dùng model lớn hơn cho đoạn nhiều thuật ngữ.
- App cho nhập **ô "Thuật ngữ / ngữ cảnh"** (danh sách từ tiếng Anh, tên riêng, tên dự án) để model nhận đúng thay vì phiên âm sai. Danh sách được "mồi" vào **mọi** đoạn decode (hotwords), không chỉ đoạn đầu.
- Chọn **ngôn ngữ chính** của hội thoại (Tiếng Việt / English) trước khi transcribe.

### YC-3 — Pass 2: LLM local soát lại thuật ngữ
- Sau khi có text (pass 1), app dùng **LLM chạy local qua Ollama** đọc lại transcript, tìm các cụm thuật ngữ tiếng Anh bị phiên âm sai thành âm tiết Việt (VD: "cu bơ nét" → "Kubernetes") và sửa dựa trên ô Thuật ngữ.
- **Bật/tắt được** bằng checkbox trước khi transcribe (mặc định bật).
- **Guardrail chống sửa quá tay:** LLM chỉ được sửa từ nghi vấn; đoạn nào bị đổi quá nhiều so với bản gốc thì giữ nguyên bản gốc. Ollama tắt/lỗi → app vẫn trả bản pass 1 bình thường.
- **Lưu song song 2 bản** (bản sửa là bản chính, bản Whisper gốc để đối chiếu); UI có nút xem qua lại 2 bản.

### YC-4 — Live: subtitle trực tiếp từ mic
- Người dùng bấm **Bắt đầu ghi**, app nghe qua micro và hiện **subtitle theo thời gian thực**: chữ hiện dần từng từ trong lúc nói (partial); khi dứt câu (~0.7s lặng), app decode lại cả câu rồi cho **pass 2 soát thuật ngữ** và **thay thế** câu trên màn hình (độ trễ mục tiêu 3–8s/câu).
- Chỉ transcribe (không dịch ngôn ngữ). Dùng chung ô Thuật ngữ, ngôn ngữ và checkbox pass 2 với flow upload; model live mặc định `small` (CPU mới theo kịp realtime).
- Bấm **Dừng** → transcript **và file ghi âm** phiên live lưu vào lịch sử như flow upload. Ollama tắt → subtitle vẫn chạy, chỉ không có bước sửa thuật ngữ.

### YC-5 — Đa máy, đa người dùng, tổ chức
- Hệ thống dùng được trên **nhiều loại máy**: máy mạnh tự dùng engine tốt nhất có sẵn; máy yếu/điện thoại dùng qua browser (PWA).
- **Dữ liệu local-first:** audio + text của mỗi người nằm trên máy người đó (text trong database local, audio trong thư mục user tự chọn được).
- **Tổ chức:** mỗi người một tài khoản riêng, admin tổ chức invite; user chọn từng bản transcript để đẩy **text** (không audio) lên database online của tổ chức; admin thấy tất cả và cấp quyền xem cho từng người. Chi tiết: PRD FR-5.

### YC-6 — Tính phí token: ví credit cho LLM cloud
- Transcribe (pass 1) và pass 2 qua **Ollama local luôn miễn phí** — "miễn phí" ở mục 1 vẫn đúng cho toàn bộ đường local. Tính phí chỉ áp dụng khi user chọn **sửa thuật ngữ qua LLM cloud** (chất lượng cao hơn, không cần cài Ollama).
- Hai hành động trả phí: **(a)** pass 2 cho bản upload/ghi âm, **(b)** sửa từng câu trong live subtitle — khi backend LLM là cloud.
- **Ví credit** của mỗi tài khoản nằm server-side (Supabase — dùng chung tài khoản YC-5); đơn vị là **credit trừu tượng**, giá bán (markup trên chi phí LLM) do chủ hệ thống đặt trong bảng giá. Mỗi lần gọi trừ credit theo token thực tế mà LLM báo về.
- **Voice/audio không bao giờ rời máy user** — chỉ đoạn text cần sửa được gửi lên. API key LLM nằm ở server, không phát cho user.
- **Hết credit → chặn hẳn tính năng trả phí** (không âm thầm chuyển backend), hiện thông báo + lời mời nạp; transcript vẫn ra bản chưa sửa. Ollama local vẫn dùng được bình thường.
- **Nạp credit:** chọn gói → thanh toán QR ngân hàng (PayOS) → hệ thống tự cộng ví khi nhận webhook xác nhận (idempotent — webhook trùng không cộng đôi).
- Lịch sử giao dịch (nạp/trừ) xem được trong app; mỗi transcript hiển thị số credit đã tốn.

## 3. Ngoài phạm vi (chưa làm)
Tách người nói (đã có lộ trình — mục 4 Phase 2) · dịch · tóm tắt tự động · sync 2 chiều/sửa đồng thời · sync audio lên tổ chức · hoàn tiền/refund tự động và hóa đơn VAT (điều chỉnh ví thủ công qua admin).

## 4. Lộ trình nâng cấp: ASR native macOS + phân biệt người nói

Mục tiêu: tận dụng engine on-device của macOS 26 (`SpeechTranscriber`, chạy Apple Neural Engine, hỗ trợ vi_VN) để live subtitle nhẹ và streaming thật (không re-decode), giải phóng RAM/GPU cho pass 2; thêm phân biệt người nói cho record cuộc họp.

- **Phase 0 — Benchmark gate: ĐÃ CHẠY 2026-07-08, KHÔNG ĐẠT.** CLI Swift `native/asr` (build: `swiftc -O native/asr/main.swift -o native/bin/native-asr`) chạy trên 4 file `data/recordings/`. Phát hiện: `SpeechTranscriber` (model mới) **chưa hỗ trợ vi_VN** trên macOS 26.5 — chỉ có `DictationTranscriber` (model dictation bàn phím). Tốc độ/RAM xuất sắc (17–37x realtime, ~19MB, chạy ANE) nhưng chất lượng fail đúng chỗ hiểm: thuật ngữ Anh bị thay bằng từ Việt sai nghĩa ("approve"→"điều hòa", "kanban checklist"→"can tre") — pass 2 không cứu được vì mất dấu vết phiên âm; và **rớt hẳn nhiều đoạn audio** (file 2143 mất nửa câu giữa, 2200 mất 5s cuối). Kết luận: giữ mlx-whisper làm pass 1.
- **Phase 1 — Live native: TẠM DỪNG** chờ Apple thêm vi_VN vào `SpeechTranscriber` (kiểm tra lại `supportedLocales` mỗi bản macOS mới — chạy lại `native/bin/native-asr` là biết). Khi có: helper Swift streaming (PCM16 16kHz qua stdin → JSON lines volatile/final); `live.py` thay vòng decode + Silero VAD + lọc hallucination bằng việc đọc event từ helper; giữ nguyên WebSocket protocol, pass 2, flow lưu; máy không đạt điều kiện → fallback mlx-whisper.
- **Phase 2 — Diarization (không phụ thuộc Phase 1, làm được ngay):** FluidAudio (pyannote convert CoreML, chạy ANE) trong helper Swift riêng: speaker embedding + cluster online theo utterance → nhãn "Người 1/2..." trên subtitle live; sau khi Dừng chạy lại diarization trên WAV đầy đủ để chốt nhãn trong transcript lưu. Giới hạn chấp nhận: overlap 2 người trong 1 utterance có thể gán sai ở bản live.
- **Phase 3 — Họp online:** helper bắt thêm system audio (Core Audio process tap): kênh mic = "Bạn", kênh system = phía bên kia (diarize tiếp trong kênh này); bản ghi WAV gộp đủ 2 chiều cuộc họp. Cần user cấp quyền Audio Recording một lần.
- **Phase 4 (tuỳ chọn):** enrollment gán tên thật theo giọng (FluidAudio speaker ID); nâng pyannote community-1 chạy offline sau phiên nếu FluidAudio chưa đủ chính xác; mồi glossary vào engine qua `AnalysisContext`.

## 5. Cách hoạt động (tóm tắt kỹ thuật)
Pass 1: Whisper (`faster-whisper`) chạy trên CPU. Pass 2: LLM local qua **Ollama** (mặc định `gemma4:e4b`, đổi bằng env `OLLAMA_MODEL`). Live: mic → AudioWorklet (PCM 16kHz) → WebSocket `/ws/live`; server buffer theo utterance, re-transcribe định kỳ (partial greedy) + Silero VAD ngắt câu (final beam search) + pass 2 từng câu. Kèm **MCP server** để trợ lý AI (Claude) đọc lại transcript đã lưu.
