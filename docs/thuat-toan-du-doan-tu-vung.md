# Thuật toán dự đoán & sửa từ vựng trong Manju

> Tổng hợp **mọi cơ chế** app đang dùng để "đoán đúng từ" (nhất là thuật ngữ
> tiếng Anh bị Whisper phiên âm thành âm tiết Việt), xếp theo thứ tự chúng chạy
> trong pipeline — cộng **3 lớp mới đề xuất** (ký ức bản dịch cũ → dự đoán ngành
> nghề → thư viện từ vựng ngành). Mỗi lớp ghi rõ: mục đích, cơ chế/ngưỡng, vị
> trí code.
>
> Trạng thái: Lớp 0–6 **đã có trong code**; Lớp A–C **đã implement** (2026-07-21).
> Code: `app/memory_filter.py` (A), `app/domain.py` (B), phần domain trong
> `app/corrections.py` + `app/slang_trend.py` + seed `app/data/lexicon/domain/*`
> (C); wiring ở `app/transcribe.py` (upload) và `app/live.py` (live). Tests:
> `tests/test_memory_filter.py`, `test_domain.py`, `test_corrections_domain.py`,
> `test_domain_research.py`.

---

## 1. Bức tranh tổng thể — pipeline nhiều lớp

Một từ đi từ âm thanh → text đúng phải qua 6 lớp hiện có. Mỗi lớp bắt một loại
lỗi khác nhau; không lớp nào một mình đủ.

```
                        ┌─────────────────────────────────────────────┐
   audio ──▶ [Lớp 0] ──▶│  Whisper decode  │──▶ [Lớp 1] ──▶ [Lớp 2] ──▶│
             bias mồi    └──────────────────┘    lọc hình     re-decode  │
             initial_prompt                       dạng câu     low-conf   │
                                                                          ▼
   text đúng ◀── [Lớp 4] ◀────────────── [Lớp 3] ◀───────────────────────┘
                 học từ bản              pass 2 LLM
                 sửa tay (loop)          sửa thuật ngữ
                    ▲                        ▲
                    │                        │
              [Lớp 5] seed/remote/trend  [Lớp 6] đo WER/CER
              lexicon (nguồn từ ngoài)   (bản chuẩn = edited_text)
```

Ba loại lỗi cần trị:
1. **Hallucination** — Whisper bịa chữ khi im lặng/nhiễu (outro YouTube, token
   lặp, chuỗi đếm). → Lớp 1.
2. **Phiên âm sai thuật ngữ Anh** — "cu bơ nét" thay vì "Kubernetes". → Lớp 0
   (phòng) + Lớp 3 (chữa).
3. **Sai vì thiếu ngữ cảnh** — nghe không rõ, đoán bừa. → Lớp 2 (re-decode) +
   Lớp 3 (context + uncertain spans).

Vòng học (Lớp 4→0/3) làm hệ tự tốt lên: user sửa tay → app trích cặp
(sai→đúng) → nạp lại làm bias mồi + few-shot cho lần sau.

---

## 2. Lớp 0 — Bias mồi ASR (phòng bệnh, trước khi decode)

**Mục đích:** đẩy xác suất Whisper nhận đúng thuật ngữ ngay từ đầu, bằng cách
nhồi danh sách term vào `initial_prompt` của decoder.

**Cơ chế** — `build_bias()` trong [corrections.py](../app/corrections.py#L116):
xây một chuỗi glossary có thứ tự ưu tiên, cap ~800 ký tự (≈224 token — trần
thực tế Whisper ăn được, phần thừa bị bỏ đuôi):

1. **Glossary user** đứng trước — **KHÔNG BAO GIỜ bị cắt** (user-first).
2. **Term cá nhân theo người nói** (`_add_personal`) — sub-cap riêng 240 ký tự
   để không nuốt hết chỗ của thư viện.
3. **Term `right` approved từ thư viện**, re-rank bằng `_rank_rows()`:
   - tier 1: tag khớp **vùng miền đang active** (slang tính là khớp mọi vùng);
   - tier 2: term xuất hiện trong **topic** đang bàn;
   - tier 3: còn lại theo `count DESC, updated_at DESC` từ DB.
4. Dedup casefold xuyên suốt, dừng khi vượt `BIAS_CAP_CHARS`.

**Cơ chế nền:** `initial_prompt` là "văn cảnh mồi" — Whisper coi như câu trước đó
đã nói, nên nghiêng token theo phân phối của các từ trong prompt. Vì vậy prompt
**chỉ được chứa danh sách term**, TUYỆT ĐỐI không văn xuôi (văn xuôi bị echo
thẳng vào subtitle — xem cảnh báo [live.py:236](../app/live.py#L236)).

**Never-fail:** DB hỏng → trả về `user + personal` (bias vẫn chạy).

---

## 3. Lớp 1 — Bộ lọc hình dạng segment (chống hallucination)

**Mục đích:** vứt segment Whisper bịa ra khi không có tiếng nói thật. Whisper
train trên phụ đề YouTube nên khi gặp im lặng hay "đoán" ra câu outro quen thuộc,
hoặc kẹt vòng lặp token.

**Cơ chế** — `keep_segment()` [engines.py:137](../app/engines.py#L137), chạy
trên mọi backend. Trả `False` (drop) nếu trúng **bất kỳ** điều kiện:

| Bộ lọc | Điều kiện | Bắt lỗi gì |
|--------|-----------|------------|
| Whisper-internal | `no_speech_prob > 0.6` **AND** `avg_logprob < -1.0` | segment vừa "im" vừa "run" |
| `_is_token_loop` | cả segment là **1 token ngắn (≤4 ký tự)** lặp ≥3 lần | "ừ ừ ừ ừ", "J. J. J." |
| `_is_digit_loop` | ≥8 token, ≥6 chữ số, **tỉ lệ số > 0.7** | "là 4 1 1 2 2 3 4 5 5 6 6 7" |
| `_HALLUCINATION_RE` | regex khớp cụm outro | "ghiền mì gõ", "subscribe", "cảm ơn đã xem" |

**Lưu ý quan trọng** (comment trong code): điều kiện AND của bộ lọc đầu **không
bắt được hallucination "tự tin"** (`nsp=0.000, logprob=-0.98`) — đó là lý do phải
có thêm các bộ lọc theo **hình dạng text**, không chỉ dựa điểm tin cậy.

**`collapse_loops()`** [engines.py:105](../app/engines.py#L105) — bổ trợ, KHÔNG
drop cả segment: khi Whisper decode đúng vài từ đầu rồi kẹt lặp tới hết cửa sổ
("là em bán" + 224 lần "để"), nó thu run ≥6 token ngắn giống nhau về 1 lần,
**giữ phần câu thật đứng trước**. Ngưỡng đặt cao (≥6) vì hội thoại thật có thể
lặp vài lần ("không không không được đâu").

---

## 4. Lớp 2 — Re-decode câu độ tin cậy thấp (live, US-811/812)

**Mục đích:** câu nghe không chắc → decode lại bằng setting mạnh hơn thay vì
chấp nhận bản đoán bừa.

**Cơ chế** — [live.py:503](../app/live.py#L503):
- `res.min_logprob < REVISE_LOGPROB` (−0.6) → câu "giữ lại nhưng run" → reroute
  qua `revision_q`, thread nền re-decode (`Engine.revise`, chỉ khi
  `supports_revise=True` để không tốn decode-lock vô ích).
- Đồng thời đánh dấu `low_conf` → chuyển các cụm word-confidence thấp thành
  danh sách `uncertain` gửi cho Lớp 3 (pass 2 soát kỹ đúng chỗ).

Đây là cầu nối: ASR **tự báo chỗ nó không chắc** để LLM tập trung soát.

---

## 5. Lớp 3 — Pass 2 LLM sửa thuật ngữ (chữa bệnh)

**Mục đích:** đọc lại text, chỉ thay các cụm bị phiên âm Anh→Việt bằng đúng từ
gốc, giữ nguyên phần còn lại. Đây là lớp "thông minh" nhất — hiểu ngữ cảnh.

**Cơ chế** — [correct.py](../app/correct.py):
- **Chunk** theo ranh giới câu, mỗi chunk ~1800 ký tự (`_split_chunks`).
- **Prompt** (`_prompt_for`) ghép nhiều tín hiệu, mỗi tín hiệu là một "lớp
  dự đoán" đưa vào LLM:
  1. **Few-shot cặp đã biết** (`pairs` ← `top_pairs()`): "cu bơ nét → Kubernetes"
     — LLM sửa nhất quán lỗi hay gặp.
  2. **Glossary** thuật ngữ/tên riêng cần nhận đúng.
  3. **Context** = topic + các câu ngay trước (chỉ để hiểu, không đưa vào kết
     quả) — LLM dựa mạch cuộc họp đoán đúng thuật ngữ (US-805).
  4. **Uncertain spans** từ Lớp 2 — "các cụm nghe không rõ, ưu tiên soát kỹ".
- **Backend 2 tầng:** có `OPENROUTER_API_KEY` → Claude Haiku 4.5 (hiểu ngữ cảnh
  tốt, sửa được cả câu nát); lỗi/hết credit/không key → fallback Ollama
  `gemma4:e4b` local.
- **Guard chống "sửa quá tay"** (`_guard`): nếu bản sửa khác gốc quá mức
  (`SequenceMatcher.ratio() < 0.6`) hoặc rỗng → **giữ bản gốc**. Chặn LLM tóm
  tắt/viết lại thay vì sửa từ.
- **Never-fail:** mọi lỗi (server tắt, timeout) → trả text gốc + cờ `False`.
  Pass 2 không bao giờ làm fail job.

---

## 6. Lớp 4 — Vòng học từ bản sửa tay (feedback loop)

**Mục đích:** biến mỗi lần user sửa transcript thành dữ liệu để lần sau tự đúng.
Đây là thứ làm hệ **tự tốt lên theo thời gian** — và là nền tảng của Lớp A mới.

### 6a. Trích cặp (sai → đúng) — `extract_pairs()` [corrections.py:49](../app/corrections.py#L49)
- Diff **bản máy** vs **bản user sửa** bằng `SequenceMatcher`, chỉ lấy op
  `replace`, token hoá theo từ.
- Lọc văn phong bằng `_is_noise()`: mỗi bên ≤4 từ (`MAX_SPAN` — dài hơn coi là
  user viết lại câu); nếu lệch số từ thì hai cụm phải giống nhau ≥0.3
  (`MIN_RATIO`, ký tự lowercase) — cặp phiên âm chuẩn "cu bơ nét→Kubernetes"
  đạt 0.42, qua; "rét đít→redis" chỉ 0.17, chấp nhận bỏ sót còn hơn nhận nhầm.
- Cặp approved → nạp vào `build_bias` (Lớp 0) + `top_pairs` (Lớp 3).

### 6b. Mine từ vựng theo người nói — `mine_speaker_terms()` [corrections.py:209](../app/corrections.py#L209)
- Gom text theo `speaker_map`, đếm **term đáng bias** (`_salient_terms`): ASCII
  thuần ≥3 ký tự (thuật ngữ Anh không dấu), Viết Hoa ≥2 ký tự, hoặc đã có trong
  thư viện approved. Bỏ stopword filler ("ok", "uh"...).
- **Chống vòng tự khuếch đại** (`_attested_vocab`): chỉ giữ term **được chứng
  thực** trong `raw_text` (Whisper thật nghe được) hoặc `edited_text` (user sửa).
  `segments` là bản SAU pass 2 — LLM đôi khi bịa thuật ngữ ("doanh thu"→"budget");
  không được để cụm bịa đó bootstrap vào bias phiên sau.
- Giữ term count ≥2 hoặc đã-known, top 30/người → `speaker_terms` table.

**Lưu trữ** (SQLite `data/manju.db`, [db.py](../app/db.py#L87)):
```sql
corrections(id, wrong, right, tag, source, count, status, ..., UNIQUE(wrong,right))
--   tag    = vùng miền/accent/slang        (bac|trung|nam|en_accent|slang|...)
--   source = user | seed | remote | trend
--   status = pending | approved | rejected  ← chỉ 'approved' mới vào bias/pass 2
transcripts(..., raw_text, edited_text, golden)   -- golden=1: bản chuẩn đo WER
speaker_terms(transcript_id, speaker_id, term, count, ...)
```

---

## 7. Lớp 5 — Nguồn từ vựng ngoài (seed / remote / trend)

**Mục đích:** không chờ user gõ đủ — bơm sẵn từ vựng từ nguồn ngoài. Đây là
**tiền thân trực tiếp của Lớp C** (thư viện từ vựng ngành).

- **Seed vùng miền** — `import_seed()` [corrections.py:266](../app/corrections.py#L266):
  nạp `data/lexicon/{bac,trung,nam,en_accent,slang}.json` (source='seed',
  approved). `INSERT OR IGNORE` — chạy lại không nhân đôi.
- **Remote opt-in** — `fetch_remote_lexicon()`: tải 2 file (data JSON +
  `.sha256`), verify checksum mới nhận. Chống nguồn bị sửa.
- **Trend slang** — [slang_trend.py](../app/slang_trend.py), 3 nguồn cùng một
  đường trích LLM, nhập **pending** (chờ user duyệt):
  1. `llm_digest()` — hỏi thẳng Claude "slang đang hot 2024–2026";
  2. `web_digest()` — đọc trang public (tôn trọng robots.txt, không né anti-bot);
  3. `apify_digest()` — caption TikTok qua Apify (opt-in `APIFY_TOKEN`).
  - **Prompt trích xuất** (`_EXTRACT_SYSTEM`) là mẫu tái dùng được cho Lớp C:
    `right` = chính tả chuẩn của từ được **nói thành tiếng**; `wrong` = cách
    Whisper dễ nghe nhầm (âm tiết Việt gần giống). Cấm viết-tắt-khi-gõ, cấm cặp
    mà `wrong` là từ Việt chuẩn thông dụng.

---

## 8. Lớp 6 — Đo độ chính xác (đóng vòng đánh giá, FR-8)

**Mục đích:** biết từng lớp có thật sự cải thiện không. Không có số đo thì mọi
thay đổi ở trên là đoán mò.

**Cơ chế** — [accuracy.py](../app/accuracy.py):
- **Bản chuẩn** = `transcripts.edited_text` của transcript có cờ `golden=1` —
  tái dùng bản user đã sửa (FR-6), không cần dataset riêng.
- `normalize()`: gộp hoa-thường + bỏ dấu câu, **GIỮ dấu thanh và số** (phiên âm
  sai thanh điệu LÀ lỗi, không được chuẩn hoá cho biến mất).
- `edit_counts()` (Levenshtein tự cài): đếm hits/subs/dels/ins → `wer()`, `cer()`.
  `ins` chính là loop hallucination (ASR bịa thêm).
- `cross_segment_repeat()` / `max_cross_repeat()`: đo lặp xuyên segment.

---

## 9. LỚP MỚI ĐỀ XUẤT

Ba lớp bổ sung nối tiếp vòng học hiện có. Nguyên tắc: **tái dùng hạ tầng đã có**
(bảng `corrections`, `build_bias`, prompt trích xuất của `slang_trend`) thay vì
dựng mới.

### 9a. Lớp A — Bộ lọc từ ký ức bản dịch cũ (correction memory / retrieval)

**Ý tưởng của bạn:** "filter từ ngữ dựa vào memory những đoạn dịch cũ."

**Vấn đề đang có:** Lớp 4 đã trích cặp (sai→đúng) nhưng chúng chỉ được nhồi
**nguyên khối** vào prompt (few-shot ≤20 cặp, bias cap 800 ký tự). Khi thư viện
lớn dần, phần lớn cặp không lọt được vào prompt → LLM không thấy → sửa lại từ
đầu, đôi khi khác kết quả lần trước (thiếu nhất quán).

**Thuật toán đề xuất — retrieval trước LLM:**

1. **Xây index ký ức** từ toàn bộ cặp approved + lịch sử `(raw_text → edited_text)`
   của các transcript golden. Key chuẩn hoá bằng `normalize()` (đã có). Value:
   `right`, `count`, tag/domain, ngữ cảnh gặp gần nhất.
2. **Truy hồi khi có transcript mới:** với mỗi span nghi vấn (ưu tiên các cụm
   `uncertain` từ Lớp 2), tìm cặp gần nhất trong ký ức bằng độ tương tự:
   - `SequenceMatcher.ratio()` trên ký tự (đã dùng ở `_is_noise`), hoặc
   - **khớp ngữ âm** (phonetic): rất hợp tiếng Việt vì lỗi là "đọc-giống-nhau".
     Có thể bổ sung một hàm mã hoá âm tiết đơn giản (bỏ dấu thanh + gom phụ âm
     đồng âm) — nhẹ, không thêm dependency.
3. **Áp dụng theo ngưỡng tin cậy** (deterministic, không cần LLM):
   - `ratio ≥ 0.85` **và** `count ≥ N` → thay thẳng (nhanh, nhất quán, rẻ).
   - `0.6 ≤ ratio < 0.85` → **không tự sửa**, chỉ bơm cặp đó vào few-shot của
     Lớp 3 như gợi ý ("có thể là …").
   - `< 0.6` → bỏ qua.
4. **Ghi ngược:** mỗi lần memory-hit được user giữ lại (không sửa tiếp) → `count++`,
   củng cố. Bị user sửa khác → hạ ưu tiên (chống ký ức sai đóng băng).

**Vì sao đặt TRƯỚC pass 2:** biến các lỗi lặp-đi-lặp-lại thành tra-cứu O(1),
vừa nhanh vừa nhất quán, để LLM chỉ lo phần thật sự mới. Vẫn giữ `_guard` &
never-fail: memory sai không được làm hỏng câu.

**Chống vòng tự khuếch đại:** như Lớp 4, chỉ nạp vào ký ức các cặp **được chứng
thực** trong `raw_text`/`edited_text`, không lấy từ bản sau pass 2.

---

### 9b. Lớp B — Dự đoán ngành nghề (domain classification)

**Ý tưởng của bạn:** "dự đoán ra ngành nghề."

**Đầu vào:** topic summary (đã có, `summarize_topic`) + salient terms của
transcript (đã có, `_salient_terms`).

**Thuật toán — chọn 1 hoặc kết hợp (đi từ rẻ đến chính xác):**

1. **Khớp từ khoá / TF-IDF (rẻ, local, không mạng):** mỗi domain có một *seed
   term set*. Tính điểm overlap có trọng số:
   ```
   score(domain) = Σ_term  tf(term, transcript) · idf(term) · [term ∈ seed(domain)]
   ```
   `idf` hạ trọng số term phổ biến ("meeting", "deploy" xuất hiện mọi ngành),
   nâng term đặc trưng ("reconcile", "coupon rate" → tài chính). Trả top-K domain
   kèm điểm chuẩn hoá.
2. **LLM zero-shot (chính xác, cần OpenRouter):** đưa topic + top salient terms,
   hỏi "cuộc họp này thuộc ngành nào?" với danh sách ngành cho trước → trả
   `{domain, confidence}`. Tái dùng `chat_once()` như `slang_trend`.
3. **Kết hợp:** chạy (1) trước làm ứng viên rẻ; chỉ gọi (2) khi (1) mập mờ
   (điểm top1 ≈ top2). Cache theo transcript — ngành ít đổi giữa các phiên của
   cùng nhóm người.

**Đầu ra:** `transcript.domain` (+ confidence) — kích hoạt Lớp C. Ngưỡng
confidence thấp → **không** ép domain (tránh nạp nhầm lexicon gây hại hơn không
có). Cho phép user override trong Settings (giống duyệt thư viện).

---

### 9c. Lớp C — Thư viện từ vựng ngành + research bổ sung

**Ý tưởng của bạn:** "set ra các lib từ vựng ngành có liên quan, research xem
ngành đó có từ gì rồi bổ sung vào lib."

**Mô hình dữ liệu:** tái dùng bảng `corrections` — thêm tag domain
(`tag='fin'|'med'|'legal'|'devops'|...`) song song tag vùng miền. `_rank_rows`
của Lớp 0 đã re-rank theo tag khớp; chỉ cần **thêm domain đang active** vào tập
`eligible` → term ngành tự nổi lên đầu bias khi domain được đoán ở Lớp B.

**Hai giai đoạn:**

1. **Seed tĩnh** — `data/lexicon/domain/{fin,med,...}.json`, nạp bằng chính
   `import_seed()` (đã có, chỉ mở rộng `SEED_REGIONS`). Cặp `{wrong, right, tag}`
   với `wrong` = cách Whisper dễ phiên âm sai thuật ngữ ngành.

2. **Research động bổ sung** — tái dùng nguyên đường `slang_trend`:
   - Prompt cho LLM: "liệt kê thuật ngữ tiếng Anh **ngành {domain}** người Việt
     hay nói xen tiếng Việt trong họp, kèm cách Whisper dễ nghe nhầm" — cùng
     schema `[{wrong, right}]`, gắn `tag=domain`, `source='trend'`,
     `status='pending'`.
   - Nguồn web: trang thuật ngữ/từ điển ngành public thay cho trang slang.
   - Nhập **pending** → user duyệt trong Settings → mới vào bias/pass 2. Giữ
     nguyên kỷ luật "nguồn ngoài không tự tin dùng".
   - `INSERT OR IGNORE` + validate lỏng (skip-not-raise) như `run_trend_update`.

**Luồng khép kín:** Lớp B đoán domain → Lớp C nạp lexicon domain vào Lớp 0
(bias mồi) + Lớp 3 (few-shot) → Whisper/LLM nhận đúng thuật ngữ ngành ngay lần
đầu → Lớp 6 đo WER giảm để xác nhận có ích.

---

## 10. Bảng tổng hợp thuật toán

| # | Lớp | Thuật toán cốt lõi | Bắt/giải quyết | Vị trí |
|---|-----|--------------------|----------------|--------|
| 0 | Bias mồi ASR | ranking + cap chuỗi glossary (`build_bias`) | phòng phiên âm sai | `corrections.py:116` |
| 1 | Lọc hình dạng | ngưỡng logprob + phát hiện lặp/đếm + regex | hallucination | `engines.py:137` |
| 2 | Re-decode | ngưỡng min_logprob → decode lại + gắn uncertain | câu đoán bừa | `live.py:503` |
| 3 | Pass 2 LLM | prompt đa tín hiệu + guard similarity | chữa phiên âm sai | `correct.py` |
| 4 | Học từ bản sửa | diff SequenceMatcher + lọc noise + mine salient | tự tốt lên | `corrections.py:49,209` |
| 5 | Nguồn ngoài | seed/checksum-remote/LLM-web-trend digest | bơm từ vựng sẵn | `slang_trend.py` |
| 6 | Đo lường | Levenshtein WER/CER + cross-repeat | đánh giá | `accuracy.py` |
| **A** | **Ký ức bản dịch** | **retrieval theo ratio/ngữ âm + ngưỡng tin cậy** | **nhất quán, nhanh** | *đề xuất* |
| **B** | **Dự đoán ngành** | **TF-IDF overlap và/hoặc LLM zero-shot** | **chọn đúng lexicon** | *đề xuất* |
| **C** | **Lexicon ngành** | **tag domain trong `corrections` + research digest** | **thuật ngữ ngành** | *đề xuất* |

---

## 11. Nguyên tắc xuyên suốt (áp cho cả lớp mới)

- **Never-fail:** mọi lớp phụ trợ lỗi → trả kết quả gần nhất, không sập
  transcribe/API.
- **Chống vòng tự khuếch đại:** chỉ học từ nguồn *chứng thực* (`raw_text` /
  `edited_text`), không học từ bản sau pass 2 (LLM có thể bịa).
- **Nguồn ngoài luôn `pending`:** seed/remote/trend/research chỉ vào bias sau khi
  user duyệt.
- **User-first:** glossary/sửa tay của user không bao giờ bị cắt hay ghi đè.
- **Đo trước khi tin:** thêm lớp nào cũng phải chứng minh qua WER/CER giảm trên
  bộ transcript golden (Lớp 6).
