# Plan — Live Reliability (FR-9, US-824..828)

- **Source**: FR-9 (net-new 2026-07-26, thêm vào PRD cùng đợt này — pattern net-new như FR-6/7/8)
- **Status**: Planning
- **Updated**: 2026-07-26
- **Gốc rễ**: điều tra 13-agent 2026-07-25/26, tái hiện được trên WAV thật (chi tiết: hội thoại + memory `whisper-vi-hallucination`, `live-stop-latency-rootcause`). Tóm tắt bằng chứng:
  - Lặp sinh trong decoder (1 raw segment): (a) partial greedy T=0 loop trên buffer speech+im lặng; (b) final rơi thang temperature → mlx-whisper **chấp nhận vô điều kiện kết quả T=1.0** dù fail gate.
  - Filter mù đúng vùng lỗi: lặp ×3 dưới mọi ngưỡng; `no_speech_prob` luôn 0.000 trên live (gate nsp chết); **compression_ratio có sẵn trong segment mlx (rác 4.9–40.6 vs sạch ≤1.7) mà `keep_segment` không dùng**.
  - Bias mặc định `build_bias('')` = "hình dung, kubernetes" bị echo lên màn hình (~10s) và có case được LƯU.
  - Stop chậm: final decode thang 6 nấc = 18.5–25s **bất kể buffer dài ngắn** (đo 22.4s cho buffer 2.2s) + drain pass-2 5–8s; join(8) hết giờ → **mất correction câu cuối trong im lặng**; 2 nhánh lỗi client quên `opfs.finish()`.
  - Ngưỡng siết đã kiểm 0 false-positive trên 34 phiên live: cycle p≥3 rep≥3 cắt được; p1 rep 3 chỉ cắt khi lp<−0.5; p2 giữ ≥4.

## US mới (ghi vào PRD FR-9 cùng đợt)

- **US-824 Chống lặp**: subtitle (partial + final + bản lưu) không chứa chuỗi lặp thoái hoá; lặp nhấn mạnh thật ("nó nó nó", "vâng, vâng, vâng") giữ nguyên.
- **US-825 Stop tức thời**: bấm Stop → UI rời màn ghi <0.5s, lưu chạy nền; không mất correction câu cuối; không mồ côi audio OPFS ở nhánh lỗi.
- **US-826 Live decode gọn**: live-final không leo thang temperature sinh rác; đuôi im lặng không vào decoder; initial_prompt chỉ chứa glossary user nhập.
- **US-827 Park ngữ cảnh**: gỡ ContextTracker/condense/refresh-bias/memory_filter khỏi đường live (module giữ cho upload/reanalyze); US-806/808/809/814 đánh dấu **parked** trong PRD/plan-live-intelligence (khôi phục khi thư viện corrections có dữ liệu thật).
- **US-828 Telemetry decode**: mỗi decode ghi temperature-đã-dùng, compression_ratio, nsp thật, wall-time vào `raw_segments` — phiên lỗi sau truy được không cần tái hiện.

## Approach

Giữ Whisper turbo + pass-2 (đã thắng mọi benchmark thay thế), sửa 3 tầng theo đúng gốc rễ: (1) không đưa rác vào decoder (trim đuôi im lặng, bỏ bias mặc định), (2) không cho decoder leo thang sinh rác ở live (accept-or-drop thay thang 6 nấc; upload giữ nguyên thang), (3) chặn nốt phần lọt bằng filter siết theo số liệu 0-FP + gate compression_ratio sẵn có. Stop nhanh là hệ quả trực tiếp của (2) + tách "dừng UI" khỏi "chốt & lưu". Không đổi engine, không migration DB (telemetry là key JSON additive).

## Tasks

| ID | Task | Source | Dep | Files | Status |
|-----|------|--------|-----|-------|--------|
| T-001 | Gate `compression_ratio > 2.4` trong `keep_segment` (mlx per-segment; fw qua getattr). Lưu ý cr là per-window: window degenerate rơi cả cụm — chấp nhận | US-824 | ‖ | app/engines.py | [ ] |
| T-002 | Collapse cycle period≥3 repeat≥3 (`_CYCLE_MIN_REPEAT` tách theo period; đã kiểm 0 FP) | US-824 | ‖ | app/engines.py | [ ] |
| T-003 | Collapse run period-1 ×3..5 khi segment `avg_logprob < −0.5` (bắt "đăng"×3, "em môm cài"; giữ stutter thật khi lp tốt) — luồng lp vào `_mlx_scored`/`_fw_scored` → collapse variant | US-824 | ‖ | app/engines.py | [ ] |
| T-004 | Vá `_is_token_loop` bị token đuôi lệch đánh bại ("Hải, "×74 + "H"): ngưỡng ≥90% token trùng khi run ≥6 | US-824 | ‖ | app/engines.py | [ ] |
| T-005 | Fixture regression từ history-sweep: mọi chuỗi lặp đã lọt (verbatim) phải bị cắt; mọi stutter thật phải sống | US-824 | → T-001..004 (contract chữ ký chốt trước, viết song song được) | tests/test_engines_loops.py | [ ] |
| T-006 | Live-final: `temperature=(0.0, 0.2)` + accept-or-drop (fail gate cr/lp → utterance rỗng, KHÔNG leo T≥0.4); upload/reanalyze giữ thang đủ | US-826 | → T-001 | app/engines.py | [ ] |
| T-007 | Trim đuôi im lặng trước final decode: chỉ decode tới `spans[-1].end` + pad 0.3s (VAD spans đã có sẵn trong `_tick_open`) | US-826 | ‖ với T-006 | app/live.py | [ ] |
| T-008 | Bias: bỏ nạp lexicon nền vào initial_prompt khi user không nhập glossary (`build_bias('')` → chuỗi rỗng → `initial_prompt=None`) | US-826 | ‖ | app/live.py, app/corrections.py | [ ] |
| T-009 | Gỡ call-site ContextTracker/condense/refresh-bias/memory_filter khỏi live path (module + test module giữ); spec-sync PRD FR-7 + plan-live-intelligence đánh dấu US-806/808/809/814 parked CÙNG commit | US-827 | → T-008 | app/live.py, PRD.md, docs/plan-live-intelligence.md | [ ] |
| T-010 | Stop server: stop-final chỉ T=0 + bỏ word_timestamps; pass-2 câu cuối không kịp → lưu trước, thread nền apply correction vào DB sau `saved` (hết silent-drop); warm-up ping hủy được theo `stop_event` | US-825 | → T-006 | app/live.py, app/db.py | [ ] |
| T-011 | Stop client: rời màn ghi ngay khi bấm (giữ WS nền chờ `saved` cập nhật lịch sử); fix 2 nhánh lỗi quên `opfs.finish()` (deadline 60s + WS-chết-sau-stop, app.js:1531,1629) | US-825 | ‖ | app/static/app.js | [ ] |
| T-012 | Telemetry: `raw_segments[k]` thêm `temperature`, `compression_ratio`, `no_speech_prob` thật, `decode_wall_s` (DecodeResult mở rộng) | US-828 | → T-001 | app/engines.py, app/live.py | [ ] |
| T-013 | Verify e2e: chạy simulator (live_sim) trên `20260725-152501-live-1523.wav` + `20260721-165404-live-1653.wav` trước/sau — tiêu chí: 0 chuỗi lặp được lưu, stop-final wall <5s, câu sạch không đổi | US-824/825/826 | → tất cả | scripts hoặc tests (đánh dấu slow) | [ ] |

## Stacked PRs (ước tính tổng ~570 dòng > 400 → bắt buộc tách)

1. **PR-1 `fix/live-loop-filter-2`** — T-001..005, T-012 (engines.py + tests). Điểm tích hợp: bản lưu + hiển thị hết lặp. Deploy độc lập được.
2. **PR-2 `fix/live-decode-simplify`** — T-006..009 (live.py + spec-sync PRD). Phụ thuộc PR-1 (gate cr).
3. **PR-3 `fix/live-stop-instant`** — T-010, T-011, T-013 (live.py + app.js + verify). Phụ thuộc PR-2.

## Edge cases & error handling

- Stutter thật phải sống: "nó nó nó", "Vâng, vâng, vâng", "thì thì thì", "tự tự tự", "là là là", "cái cái cái", "nè nè nè", "chuyện gì"×3, "bỏ ra"×3 (fixture bắt buộc trong T-005).
- Window cr>2.4 chứa lẫn câu thật: mất cả window — chấp nhận (chính Whisper cũng coi window đó hỏng); telemetry T-012 cho phép theo dõi tần suất.
- Accept-or-drop ở live-final có thể drop câu thật khó nghe: đo bằng T-013 + FR-8 bench khi user có bản golden; upload không bị ảnh hưởng (giữ thang đủ).
- fw engine (cpu/cuda) có beam+vad_filter nên ít loop hơn — chỉ nhận thay đổi filter (T-001..004), không đổi ladder của fw.
- Stop khi WS đã rớt: giữ hành vi park 60s hiện tại (ngoài scope; ghi tech-debt nếu user còn than).

## Test strategy

- T-005: bảng fixture 2 chiều (phải-cắt / phải-giữ) từ history-sweep — chạy trong suite thường (pure function, nhanh).
- T-010/T-011: test luồng shutdown với fake engine (pattern test live hiện có), assert: saved trước correction muộn, correction muộn ghi DB, opfs.finish gọi ở nhánh lỗi (JS: node check hiện có).
- T-013: e2e simulator đánh dấu slow, chạy nền (full suite ~12 phút — chạy background theo memory).

## Rollback

- Không migration DB (JSON keys additive). Rollback = revert PR tương ứng; PR độc lập deploy được theo thứ tự ngược.
