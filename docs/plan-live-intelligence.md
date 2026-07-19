# Plan — Live Intelligence (FR-7, US-806..814)

> Plan doc này là nguồn chi tiết; PRD FR-7 chỉ là stub. Lệch nhau → plan doc thắng cho tới khi write-back.
> Nền: FR-6 correction library (US-801..805). Nguyên tắc xuyên suốt: **never-fail** — mọi tính năng lỗi thì rơi về hành vi cũ; glossary user nhập không bao giờ bị cắt.

## Kiến trúc chung

- Bias ASR = `corrections.build_bias(user_glossary, personal, topic, regions)` (user → personal cap 240 → library xếp theo region > topic-overlap > count; tổng cap 800 ký tự ≈ 224 token).
- Prompt ASR xếp NGƯỢC độ quan trọng (Whisper cắt đầu, giữ đuôi): `Chủ đề: {topic:160}. {library/personal}, {user glossary}` (`LiveSession._asr_prompt`).
- Refresh giữa phiên: `ContextTracker.on_topic` → `LiveSession._refresh_bias` swap `self.glossary` + `self.spec` (DecodeSpec frozen, gán attribute atomic dưới GIL — không lock).

## US → cơ chế → file

| US | Cơ chế | File chính |
|---|---|---|
| US-806 Topic-bias | topic condense (US-805) → re-rank lexicon (keyword overlap, không LLM/embedding thêm) + tiêm topic vào initial_prompt + refresh spec | corrections.py, live.py |
| US-807 Personal learn | bảng `speaker_terms(transcript_id, speaker_id, term, count)`; mine sau diarize/đặt tên cluster (heuristic: token ASCII ≥3 không dấu / khớp corrections.right / viết hoa; count≥2 hoặc known; top 30/người; delete-then-insert per transcript = idempotent) | db.py, corrections.py (`mine_speaker_terms`), main.py hooks |
| US-808 Personal use | start card chọn người tham dự → cfg `participants` → `db.personal_terms` vào bias (sub-cap 240) + pass 2 | live.py, app.js |
| US-809 Metadata | cfg `title`/`agenda` → `ContextTracker(initial_topic=…)` — bias từ câu đầu; title = tên transcript | live.py, app.js |
| US-810 Region | cột `speakers.region` (bac/trung/nam, nullable) → `top_pairs(regions=)` + build_bias xếp pair/term đúng vùng lên trước cap; KHÔNG tự import seed (contract opt-in US-804) | db.py, corrections.py, main.py, app.js |
| US-811 Revision | `decode_scored` trả `min_logprob`; < −0.6 → `revision_q` (max 2) → `engine.revise` nền (cpu: model to hơn, load ngoài lock; mlx: code beam-5 sẵn nhưng mlx-whisper 0.4.3 raise NotImplementedError → tạm None, tự chạy khi upstream có beam search; cuda: None) → WS `{type:"revise"}` → rồi mới pass 2. Kill-switch `MANJU_REVISE=0` | engines.py, live.py, app.js |
| US-812 Uncertain | setting `flag_words` (default off) → final decode `word_timestamps` → word prob < 0.5 (≤8 từ) → prompt pass 2 "Các cụm nghe không rõ…" | engines.py, live.py, correct.py |
| US-813 Denoise | setting `denoise_enabled` (default off) → `denoise.StreamDenoiser` (noisereduce spectral gating, overlap 0.25s) trong thread decode, chỉ phần sample mới mỗi tick; WAV lưu raw | denoise.py, live.py |
| US-814 Speaker-ID | utterance final ≥1.5s → `ident_q` (max 3) → `diarize.embed_utterance` + `best_match` (thread riêng, không đụng decode lock) → WS `{type:"speaker"}`; ĐỔI người → bias xếp term người đó lên đầu (đổi người = debounce). Tắt im lặng nếu thiếu model/voiceprint. Env `LIVE_ID_THRESHOLD` | diarize.py, live.py, app.js |

## Quyết định đã chốt (không làm lại)

- N-best thật từ ASR: **loại** — faster-whisper 1.2.1 public API trả 1 hypothesis, mlx không có → thay bằng US-812.
- Embedding model cho topic: **loại** — keyword overlap đủ (topic string đã chứa thuật ngữ).
- DeepFilterNet/RNNoise: **loại** (kéo torch / native build 48kHz) → noisereduce.
- cuda không có revise (final đã là turbo beam-5) — trả None, nói thẳng.
- Speaker-ID trên partial: **loại** (CPU); chỉ final.
- `speaker_terms` không sync lên org (Đợt 2 quyết sau).

## WS messages mới

- `{type:"revise", utt, text}` — thay text dòng (client xử lý như corrected).
- `{type:"speaker", utt, name}` — prefix "name: " (client giữ raw text trong `dataset.raw`).
- cfg `start` thêm: `participants: [speaker_id]`, `title`, `agenda` (đều optional).

## Settings mới

`flag_words` ("0"/"1", default 0), `denoise_enabled` ("0"/"1", default 0). Region per speaker: PATCH `/api/speakers/{id}` `{region}`.

## Rủi ro & guard

1. Revise tranh `_decode_lock` với partial → non-blocking acquire, 2 attempt rồi bỏ (degrade).
2. Spectral gating làm mềm onset → overlap 0.25s, default off, WAV raw.
3. Speaker-ID tag nhầm (vector 1 utterance) → gate 1.5s + chỉ swap khi đổi người + env tune ngưỡng.
4. CPU revise load model lần đầu ~10s → load NGOÀI lock rồi mới try-acquire.

## Verification (đã chạy khi implement)

`uv run pytest` + `ruff` + `mypy` + `node --check app/static/app.js`; smoke: xem `docs/project-state.md` mục Active Feature.
