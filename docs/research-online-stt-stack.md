# Nghiên cứu tech stack online STT (FR-10) — 2026-07-28

Bối cảnh: user test Pipecat thấy ổn, chốt đổi định hướng — **lưu local giữ nguyên, xử lý ASR ưu tiên online khi khả dụng**. Nghiên cứu này (2 nhánh: provider STT tiếng Việt + framework pipeline, verify trên docs chính thức 2026-07) trả lời: dùng stack gì "bằng hoặc hơn Pipecat".

## Quyết định

1. **Kiến trúc: GIỮ FastAPI + PWA + WS protocol hiện có; không adopt Pipecat/LiveKit nguyên khối.**
2. **Tier `cloud` (streaming STT online) đứng đầu chuỗi engine**, trên mlx → cuda → cpu; live loop mlx-whisper hiện tại giữ nguyên làm fallback offline.
3. **Provider ứng viên chính: Soniox stt-rt-v5**; dự phòng AssemblyAI Universal-3.5 Pro Streaming, Deepgram Nova-3 `language=vi`. **Chốt cuối bằng benchmark audio thật** — không provider nào công bố WER tiếng Việt streaming.

## Vì sao không adopt Pipecat (dù nó tốt)

Pipecat là framework voice-agent tốt nhất nhóm (BSD-2, 13.7k sao, release 2-4 tuần/lần, v1.6.0 07/2026, nhúng được vào FastAPI qua `FastAPIWebsocketTransport`). Nhưng cho app transcription-only:

- Giá trị lõi của nó — turn-taking, interruption, orchestration LLM/TTS — **không dùng đến**. Phần dùng đến (cầu WebSocket tới STT provider, interim/final passthrough) chỉ ~100-200 dòng, có mẫu chính thức của Deepgram đúng kiến trúc FastAPI.
- Đường Whisper local của Pipecat (`WhisperSTTServiceMLX`) là `SegmentedSTTService`: chỉ decode SAU khi VAD hết utterance, **không có interim caption** — thoái lui so với live loop pseudo-streaming + chống hallucination đã tinh chỉnh (FR-9). Muốn giữ trải nghiệm hiện tại phải tự viết streaming service trong Pipecat = công sức không đổi lấy gì.
- Client PWA + protocol WS + OPFS + resume/replay hiện có sẽ phải viết lại theo client SDK của framework.
- LiveKit Agents: bắt buộc LiveKit server + agent worker + client WebRTC — thay máu kiến trúc cho app 1 user. Vocode chết (commit cuối 11/2024). TEN lệch mục tiêu, license có điều khoản hạn chế.

Những gì framework "cho không" thì tự viết được mỏng: auto-reconnect + backoff + keepalive (~50-100 dòng), interface STT cloud/local mỏng trong `engines.py` (điểm cắm FR-1 có sẵn).

## Ma trận provider STT tiếng Việt (verify 2026-07-28, docs chính thức)

| Provider / model | vi streaming | Code-switch vi↔en | Custom vocab | Diarization live | Giá live | Free |
|---|---|---|---|---|---|---|
| **Soniox stt-rt-v5** | Có | **Native giữa câu, 1 model 60+ langs** | context/custom vocab | Có, gộp giá | **$0.12/h** | Không (đã bỏ 10/2025) |
| **AssemblyAI U3.5 Pro Streaming** | Có | **Native, 18 langs có vi** | prompt + 100 keyterms | +$0.12/h | $0.45/h (tính theo giờ WS mở) | $50 |
| **Deepgram Nova-3** | Có (`language=vi`) | **KHÔNG** — vi ngoài `language=multi` (10 langs) | 100 keyterms (đã mở non-EN ~12/2025) | Có (`diarize_model=v1`) | $0.29/h | **$200** |
| Speechmatics | Có | Không (bilingual packs không có vi+en) | 1000 từ + sounds_like | Có, 50 speakers | ~$0.40/h (chưa xác minh chính thức) | ~480 min/tháng (mâu thuẫn nguồn) |
| Gladia Solaria | Có (code-switch "Yes") | Có (claim) | phonetic post-processing | Chưa xác minh cho vi | $0.75/h | €50 |
| ElevenLabs Scribe v2 RT | Có | Auto-detect | 50 keyterms | **Không** (chỉ batch) | $0.39/h ($0.28 trang khác — mâu thuẫn) | Không rõ |
| Google Chirp 3 | Có (vi-VN GA) | Không rõ | 1000 phrases | Không (batch, không vi) | $0.96/h | $300 GCP |
| OpenAI gpt-4o-transcribe | Có (Realtime API) | Không rõ | prompt | Không (diarize chỉ batch) | $0.36/h | Không |

Điểm chung quan trọng: **không ai công bố WER vi cho streaming** (ElevenLabs duy nhất công bố tier vi ≤5% WER nhưng là batch). Benchmark học thuật cho thấy vi+en code-switching là bài toán khó (Whisper-Large-v3 CS-WER 46.69% trên ViMedCSS y khoa) → số phải tự đo trên audio họp thật của mình.

## Kế hoạch benchmark (gate trước khi build FR-10)

1. Mở rộng `scripts/bench_cloud_stt.py`: thêm legs **soniox-rt** (stt-rt-v5 qua WS realtime — leg async sẵn có dùng model preview cũ), **assemblyai** (U3.5 Pro), **deepgram** (Nova-3 `language=vi` + keyterms từ glossary user). Giữ Gladia làm đối chứng (code sẵn).
2. Key cần user tạo: Deepgram ($200 free — chạy thoải mái), AssemblyAI ($50 free), Soniox (trả phí nhưng ~vài cent/bản ghi). Điền vào `.env`: `DEEPGRAM_API_KEY`, `ASSEMBLYAI_API_KEY`, `SONIOX_API_KEY`.
3. Đo trên 3-5 bản ghi có `edited_text`/golden (`--ref golden`): WER/CER + tỉ lệ giữ thuật ngữ Anh (harness đã có metric `term_hits`).
4. Tiêu chí chọn: term-hit rate ↑ và WER ≤ mlx-whisper baseline; hoà thì chọn theo giá (Soniox) và diarization gộp.

## Phác thảo tích hợp (chi tiết để /plan-feature)

- `engines.py`: tier `cloud` — engine streaming (interface mới cạnh `Engine` batch hiện có hoặc mở rộng ABC), probe = có key + mạng + setting `cloud_stt` bật.
- `live.py`: nhánh cloud — bridge PCM từ WS client → provider WS, interim → subtitle partial, final → pass 2 + lưu (pipeline correction/lexicon giữ nguyên); mất kết nối provider giữa phiên → rơi về mlx loop không đứt phiên.
- Upload: gửi file qua API async của cùng provider (rẻ hơn realtime) khi online.
- UI (chốt 2026-07-29): chọn Online/Offline per-phiên ngay start card (radio, nhớ lần trước, mặc định online khi khả dụng; hint trả phí + audio stream ra ngoài ở option online); client gửi `mode` trong WS `start`; badge engine đang dùng (FR-1). Rớt mạng giữa phiên → tự rơi về mlx không đứt phiên.
- Diarization: Soniox trả speaker sẵn trên đường online → thay sherpa-onnx ở nhánh cloud.

## Bổ sung 2026-07-29 — phản biện user, đã kiểm chứng

User đưa bản phản biện (nguồn ngoài); kiểm chứng từng nhóm claim trên repo + docs chính thức:

**Đúng và tiếp nhận vào FR-10:**
- Vì sao "Pipecat nghe tốt hơn": hai bên chạy 2 lớp ASR khác nhau — Pipecat demo dùng cloud STT, Manju live dùng mlx-whisper greedy (không beam search), final T=(0.0,0.2) accept-or-drop (engines.py:486 — thà bỏ câu khó còn hơn bịa), VAD cắt 0.7s + tail pad 0.3s (live.py:32), prompt chỉ glossary tay (live.py:86), bảng corrections rỗng. So sánh đó là cloud-vs-local model, KHÔNG phải framework-vs-framework → củng cố quyết định tier `cloud`, không đổi kết luận framework.
- **Soniox raw = nguồn chân lý nhánh cloud; pass 2 default OFF** (toggle "AI làm sạch"), stt-async-v5 nghe lại sau Stop (~$0.10/h, tổng ~$0.22/h) — đã ghi vào PRD FR-10. Privacy Soniox verify: realtime không lưu, async lưu tới khi xóa (xóa qua API), không train.
- **app.js:1511 ép `echoCancellation/noiseSuppression/autoGainControl: true` cứng** — họp qua loa có thể bị nuốt tiếng đầu xa. Thêm chế độ thu "họp qua loa" (toggle tắt 3 constraint) là lever rẻ, đo được ngay; system audio tap vẫn ở Đợt 3 (BRD mục 4).
- Chỉ tune PREROLL_S/endpoint/tail-pad sau khi có 3-5 bản golden (đúng bài học FR-8); benchmark phải cùng WAV + khai báo rõ provider/model/VAD từng bên.
- Giá LLM hậu xử lý (verify qua bảng giá Anthropic hiện hành): Haiku 4.5 $1/$5 per MTok — ~$0.09-0.15/h transcript; Sonnet 5 intro $2/$10 tới 31/08/2026 rồi $3/$15. Lưu ý ước "15k token/h" của bản phân tích hơi thấp cho tiếng Việt (tokenizer mới tốn token hơn) — đọc số này là cận dưới.

**Bác / sửa lại:**
- **SenseVoice: LOẠI hẳn, kể cả benchmark** — bản open (SenseVoice-Small) chỉ hỗ trợ zh/yue/en/ja/ko, KHÔNG có tiếng Việt; bản "50+ ngôn ngữ" chưa release. Claim benchmark-only trong bản phản biện dựa trên marketing của model chưa phát hành.
- Perplexity: đúng là hậu kỳ thủ công tốt (user tự kiểm chứng), nhưng không có live API, giới hạn 40MB/file, file bị giữ ~30 ngày — chỉ dùng làm công cụ review/tóm tắt tay, KHÔNG vào kiến trúc.

**Module map (đồng ý với phản biện):**
- **sherpa-onnx** giữ nguyên cho diarization local đa nền tảng (đã tích hợp `app/diarize.py`).
- **FluidAudio** (github.com/FluidInference/FluidAudio): ứng viên nâng cấp diarization Apple-native (CoreML/ANE) cho Đợt 3 — khớp lộ trình BRD mục 4; KHÔNG dùng làm ASR vi (catalog Parakeet không có tiếng Việt).
- **Speaches** (github.com/speaches-ai/speaches): ứng viên cho tier `remote` LAN Đợt 3 (đóng gói faster-whisper thành OpenAI-compatible server) — không đưa vào core bây giờ (thêm Docker/service).
- **WhisperX / pyannote trực tiếp**: không dùng — lệch đường MLX đã benchmark / thêm PyTorch + HF token gated.
- Nhánh cloud: diarization Soniox gộp sẵn thay sherpa-onnx khi online.

**Cần verify khi /plan-feature (chưa xác minh tường minh):** shape tham số context của Soniox (docs/stt/concepts/context — "structured context", chưa rõ field `terms`); hạn mức phiên đồng thời; hành vi reconnect giữa phiên.

## Nguồn chính

- Pipecat: docs.pipecat.ai (supported-services, stt/whisper, transport/fastapi-websocket), github.com/pipecat-ai/pipecat
- LiveKit: docs.livekit.io/agents (models, build/text)
- Soniox: soniox.com/docs/stt/models, /pricing, blog bỏ free credits 2025-10-27
- AssemblyAI: assemblyai.com/docs/streaming/universal-streaming/multilingual-transcription, /pricing
- Deepgram: developers.deepgram.com/docs/models-languages-overview, /docs/keyterm, /docs/diarization, deepgram.com/pricing; blog "11 new languages" (vi, 11/2025)
- Benchmark học thuật vi+en CS: arxiv 2602.12911 (ViMedCSS), 2509.05983 (TSPC)
