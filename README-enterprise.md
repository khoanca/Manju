# Manju Enterprise — công ty tự host

Bản tách riêng của Manju cho một công ty: **một server, nhiều tài khoản nhân viên**,
nhận dạng giọng nói qua **Soniox** (tiếng Việt xen tiếng Anh, tách người nói).
**Audio ở lại máy nhân viên** (bộ nhớ trình duyệt — OPFS), server chỉ lưu **văn bản**.
Mỗi nhân viên cắm LLM của mình (Claude, v.v.) vào các cuộc họp qua MCP.

## Privacy — đọc trước khi deploy

| Dữ liệu | Đi đâu |
|---|---|
| Audio **ghi trực tiếp** | Trình duyệt stream thẳng tới Soniox (temp key, TTL 1h) để nhận dạng; đồng thời ghi WAV vào OPFS trên máy nhân viên. **Không bao giờ qua server Manju.** |
| Audio **file tải lên** | Đi **qua RAM server** (không ghi đĩa) → Soniox async → Manju xoá file + transcript phía Soniox ngay khi nhận kết quả. |
| Văn bản + metadata | SQLite trên server (`/data/manju.db`). Member thấy bản mình; admin thấy tất cả. |
| Soniox API key thật | Chỉ nằm trong env của server. Browser chỉ nhận temp key. |
| Glossary công ty | Gửi Soniox làm `context.terms` để nhận đúng thuật ngữ. |

Soniox: realtime không lưu audio, async lưu tới khi xoá, không dùng train
(soniox.com/docs/security-and-privacy — cam kết của hãng, tự kiểm chứng).

## Cài đặt (Docker)

```bash
git clone <repo> manju-enterprise && cd manju-enterprise
docker build -t manju-enterprise .
docker run -d --name manju -p 8000:8000 \
  -v manju-data:/data \
  -e SONIOX_API_KEY=snx_... \
  -e OPENROUTER_API_KEY=...      # tuỳ chọn: bật "AI làm sạch" pass 2
  manju-enterprise
```

Đặt sau reverse proxy có **HTTPS** (mic trong trình duyệt yêu cầu HTTPS trừ
`localhost`). Image chạy `--proxy-headers` nên cookie `Secure` tự bật khi có
`X-Forwarded-Proto: https`.

Không Docker: `MANJU_EDITION=enterprise MANJU_DATA_DIR=/data uv run uvicorn app.main:app --host 0.0.0.0`.

Env:
- `SONIOX_API_KEY` — **bắt buộc** (không có → không ghi/upload được; start card báo "admin chưa cấu hình").
- `MANJU_EDITION=enterprise`, `MANJU_DATA_DIR=/data` — Dockerfile đã set.
- `OPENROUTER_API_KEY` — tuỳ chọn, cho toggle "AI làm sạch transcript" (mặc định tắt).

## Tài khoản

1. Mở `https://manju.congty.vn` → **Đăng ký** SĐT + mật khẩu. **Người đầu tiên = admin**.
2. Nhân viên đăng ký sau → trạng thái *Chờ duyệt*. Admin vào **Settings → Quản lý user** → Duyệt / Khoá / đổi role.
3. Không OTP — chốt chặn là admin duyệt. Sai mật khẩu 5 lần → khoá 15 phút.

## Ghi họp & tải file

- **Ghi âm trực tiếp**: bấm mic → subtitle live → Stop → text lưu về server (owner = bạn). WAV nằm trong trình duyệt; tải về ở Settings → "Tải bản ghi trong trình duyệt về máy" (iOS có thể tự dọn sau ~7 ngày không dùng).
- **Tải file lên**: mp3/m4a/wav/mp4… → Soniox async → text. File không lưu trên server.
- Thuật ngữ công ty: admin sửa ở Settings → Thuật ngữ; áp cho mọi người.

## Cắm LLM qua MCP (từng nhân viên)

1. Settings → **API token cho LLM** → *Tạo token* → sao chép (chỉ hiện 1 lần).
2. Trên máy nhân viên (cần `uv` + clone repo này):
   ```bash
   claude mcp add manju -e MANJU_URL=https://manju.congty.vn -e MANJU_TOKEN=mj_... -- \
     uv --directory /path/manju-enterprise run python mcp_server/enterprise.py
   ```
3. Tool: `list_transcripts`, `search_transcripts(query)`, `read_transcript(id)`. Quyền = đúng quyền của người giữ token; xoá token trong Settings là LLM mất quyền ngay.

## Backup

Toàn bộ dữ liệu = volume `/data` (`manju.db` + `transcripts/*.txt`). Backup bằng
`sqlite3 /data/manju.db ".backup /backup/manju-$(date +%F).db"` hoặc snapshot volume.

## Giới hạn hiện tại

- Chỉ Soniox; không có đường offline (Whisper local tắt ở bản này).
- Tên người nói = nhãn Soniox (1, 2, 3…); gán tên tay trong màn chi tiết.
- Tìm kiếm MCP là `LIKE` trên tiêu đề/nội dung (chưa vector search).
- Rate-limit đăng nhập giữ trong RAM (restart server là reset).
- Ghi họp dài trên điện thoại: iOS/Android có thể ngắt mic khi khoá màn hình — dùng laptop cho họp dài.
