# Plan: Noise Reduction mở rộng

- **Source**: US-813 (Live Intelligence, FR-7) — **mở rộng**. Feature gốc chỉ là toggle on/off spectral gating stationary, live-only. Các hạng mục dưới đây là **gap** ngoài product-plan/BRD (chưa có US riêng) — flag để user duyệt; nếu chấp nhận sẽ ghi làm US-813 mở rộng.
- **Status**: Implemented
- **Updated**: 2026-07-21

## Approach

Nâng `app/denoise.py` từ 1 chế độ (spectral gating stationary, prop_decrease=1.0, chỉ streaming) thành engine cấu hình được, tái dùng `noisereduce` + `scipy.signal` (**không thêm dependency** — cả hai đã có). Giữ nguyên hợp đồng **never-fail** (mọi lỗi → trả audio gốc / `None`) và bất biến "WAV lưu là raw, denoise chỉ ảnh hưởng artifact ASR". Prefilter (high-pass + notch hum) chạy TRƯỚC spectral gating vì lọc rumble/hum tuyến tính rẻ và làm sạch phổ nền cho gating. Nhánh upload gọi engine bằng **path**, nên thêm `reduce_file(path)` batch (load→filter→gate→ghi WAV tạm) thay vì đụng chữ ký `transcribe_file`. Mọi setting mặc định GIỮ hành vi cũ (strength=100, stationary, filter off, upload off) → không ai bị đổi kết quả ngoài ý muốn.

## Tasks

| ID | Task | Source | Dep | Files | Status |
|-----|------|--------|-----|-------|--------|
| T-001 | Core `denoise.py`: `_prefilter(y,sr,highpass_hz,hum_hz)` (scipy butter high-pass + iirnotch hum & harmonics); `StreamDenoiser.__init__` nhận `prop_decrease/stationary/highpass_hz/hum_hz`; `reduce_file(in_path)→Path\|None` batch (load audio 16k mono qua ffmpeg → prefilter → `reduce_noise` non/stationary → ghi WAV tạm). Prefilter dùng overlap tail như gating trong streaming. | US-813+ | ‖ (blocks tất cả) | `app/denoise.py` | [x] |
| T-002 | Settings: `SettingsIn` + GET/PUT `/api/settings` thêm `denoise_strength`(0-100), `denoise_mode`(stationary\|nonstationary), `denoise_highpass`(Hz, "0"=off), `denoise_hum`(off\|50\|60), `denoise_upload_enabled`(0/1). Default giữ hành vi cũ. Helper `denoise_params()` đọc→dtype. | US-813+ | → T-001 (chốt tên param) | `app/main.py` | [x] |
| T-003 | Live nạp param: `_init_buffers` dựng `StreamDenoiser` với 4 param từ settings (thay vì mặc định). | US-813+ | → T-001,T-002 | `app/live.py` | [x] |
| T-004 | Upload denoise: `_process` gọi `reduce_file` khi `denoise_upload_enabled`, transcribe path đã khử ồn, `finally` xoá WAV tạm; file lưu vẫn là gốc. | US-813+ | → T-001,T-002 | `app/transcribe.py` | [x] |
| T-005 | UI: card "Khử ồn micro" mở rộng — slider cường độ, chọn mode, toggle lọc rumble, chọn hum off/50/60, toggle khử ồn file upload; wiring `app.js` (load + PUT). | US-813+ | → T-002 | `app/static/index.html`, `app/static/app.js` | [x] |
| T-006 | Tests: mở rộng `test_denoise.py` — prop_decrease giảm cường độ khử, non-stationary same-length/dtype, high-pass hạ band <cutoff, notch hạ đúng 50/60Hz, `reduce_file` ra WAV 16k & never-fail khi ffmpeg/file lỗi; settings roundtrip trong `test_accuracy_api`/mới. | US-813+ | ‖ per-item sau mỗi item land | `tests/test_denoise.py`, `tests/test_*` | [x] |

## Edge Cases & Error Handling

- ffmpeg thiếu / file hỏng / codec lạ trong `reduce_file` → trả `None` → transcribe file gốc (không chặn job).
- `denoise_strength=0` → prop_decrease 0 (noisereduce = no-op) hoặc short-circuit passthrough.
- Non-stationary trên chunk streaming ngắn: dựa overlap tail; lỗi nội bộ → passthrough (đã có guard).
- Notch hum: lọc cả sóng hài (50/100/150 hoặc 60/120/180) trong dải Nyquist; cutoff > Nyquist → bỏ qua hài đó.
- High-pass cutoff ≥ Nyquist hoặc ≤0 → bỏ qua (không lọc), never-fail.
- WAV tạm luôn xoá trong `finally` kể cả job lỗi (không rác `data/uploads`).

## Test Strategy

- Prefilter đo bằng năng lượng băng tần (`np.fft.rfft`, như test hiện có): high-pass hạ band dưới cutoff; notch hạ đúng bin 50/60Hz mà giữ band thoại 400Hz.
- Strength: prop_decrease thấp → noise_ratio cao hơn prop_decrease cao (khử nhẹ hơn).
- `reduce_file`: tạo WAV nhiễu tạm → chạy → assert file ra tồn tại, 16k mono, ngắn hơn/bằng; monkeypatch ffmpeg lỗi → `None`.
- Backward-compat: default settings → output ≈ StreamDenoiser cũ (regression guard trên test SNR sẵn có).
- Sau implement: full `uv run pytest` + ruff + mypy + `node --check app/static/app.js`.

## Rollback

- Không có migration DB (settings là key-value trong bảng `settings` sẵn có, thêm key = additive, thiếu key → default). Revert = git revert; setting thừa vô hại.
