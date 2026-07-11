# Meeting Transcriber

App đơn giản: đưa file ghi âm cuộc họp vào → nhận **toàn bộ text**. Chạy local bằng
Whisper (`faster-whisper`), miễn phí & riêng tư. Có **pass 2**: LLM local (Ollama)
soát lại thuật ngữ tiếng Anh bị phiên âm sai. Có **chế độ Live**: subtitle trực tiếp
từ mic trong lúc họp. Kèm **MCP server** để Claude đọc các transcript đã có.

## Yêu cầu
- `uv` (đã cài), `ffmpeg` (đã cài). Môi trường tự pin Python 3.12.
- **Ollama** (cho pass 2, tuỳ chọn): cần model `gemma4:e4b` (`ollama pull gemma4:e4b`).
  Không có Ollama thì bỏ tick pass 2 — app vẫn transcribe bình thường.

## Cài đặt
```bash
cd /Users/m/Manju
uv sync
```

## Bước 1 — Chạy app
```bash
uv run uvicorn app.main:app --reload
```
Mở http://localhost:8000

App có **2 chế độ tạo bản ghi** (menu bên trái), dùng chung card **Cấu hình chung**:
chọn ngôn ngữ (**Tiếng Việt / English**), nhập **Thuật ngữ / ngữ cảnh** (từ tiếng
Anh, tên riêng — giúp cả 2 pass nhận đúng), và bật/tắt **pass 2** (AI soát thuật ngữ).

#### 🎙️ Ghi âm trực tiếp — subtitle realtime + tự lưu file ghi âm
Bấm nút micro (trình duyệt sẽ xin quyền):

- Chữ hiện dần **từng từ** trong lúc bạn nói (dòng nghiêng, màu mờ).
- Dứt câu (~0.7s im lặng) → câu được decode lại kỹ hơn, rồi AI soát thuật ngữ
  (pass 2) và **thay câu trên màn hình** (nháy sáng nhẹ). Trễ ~3–8s/câu.
- Bấm nút một lần nữa để **Dừng** → app tự lưu **cả văn bản lẫn file ghi âm** (WAV)
  vào lịch sử (`live-HHMM`); mở lại trong Lịch sử để **nghe/tải** bản ghi.
- Lần đầu bấm ghi hơi lâu (load model + warm-up); tiếng nói trong lúc chờ không
  mất — app buffer lại và hiện ngay khi sẵn sàng.
- Lưu ý: trình duyệt bật khử echo nên tiếng phát ra từ loa máy (họp online) có
  thể bị lọc bớt — muốn thu rõ thì để mic gần loa.

#### 📤 Tải file lên
1. Chọn model (`small` / `large-v3-turbo` / `large-v3`).
2. Kéo-thả hoặc chọn file ghi âm (mp3, m4a, wav, mp4...).
3. Bấm **Transcribe** — text hiện dần (pass 1), sau đó AI soát thuật ngữ (pass 2).
   Bấm **Copy**, **Tải .txt**, **Xem bản gốc** (đối chiếu trước/sau pass 2), hoặc
   nghe/tải lại **file gốc** ngay trên card kết quả.

Text + metadata lưu trong SQLite `data/manju.db` (nguồn chân lý; bản ghi cũ dạng
file được migrate tự động lúc khởi động). Mỗi bản ghi vẫn xuất `data/transcripts/{id}.txt`
để đọc nhanh/grep. File ghi âm gốc lưu ở thư mục cấu hình trong Settings
(mặc định `data/recordings/`) để nghe/tải lại. Mở app từ máy khác (điện thoại,
PWA): bật "Lưu audio trên thiết bị này" thì audio nằm trong bộ nhớ trình duyệt
(OPFS), không gửi lên server.

> Lần transcribe đầu tiên sẽ tải model Whisper (large-v3-turbo ~1.6GB) — chờ một chút.
> Env tuỳ chọn:
> - `ASR_ENGINE` — ép engine ASR (`mlx` / `cuda` / `cpu`); bỏ trống thì app tự dò máy.
> - `MAX_LIVE_SESSIONS` — số phiên live đồng thời (mặc định 2).
> - `WHISPER_MODEL` — model upload mặc định cho tier CPU.
> - `OLLAMA_URL` (mặc định `http://localhost:11434`) · `OLLAMA_MODEL` (mặc định
>   `gemma4:e4b` — thắng benchmark trên máy này: 3.3GB RAM, ~5s/chunk; thay thế:
>   `qwen3.5:latest` chậm hơn, `qwen2.5:7b-instruct-q4_K_M` bảo thủ hơn. Tránh
>   model thinking như `qwen3:4b`: xả reasoning vào kết quả → hỏng).
> - **Cloud billing (FR-6, tuỳ chọn):** `CLOUD_BILLING=on` + `SUPABASE_URL` +
>   `SUPABASE_ANON_KEY` — bật backend pass 2 qua cloud (đăng nhập + trả credit,
>   xem `docs/plan-credit-wallet.md`). `CLOUD_LLM_MODEL` chỉ là tên hiển thị
>   metadata — model thật đặt bằng secret `OPENROUTER_MODEL` của Edge Function.
>   `OPENROUTER_API_KEY` KHÔNG còn dùng ở app local (key nằm server-side:
>   `supabase secrets set OPENROUTER_API_KEY=...`) — nếu từng để trong `.env`,
>   xoá đi và cân nhắc rotate key.

## Bước 2 — MCP cho Claude
Server đọc cùng thư mục `data/transcripts/`. Đăng ký vào Claude Code:

```bash
claude mcp add meeting-transcripts -- uv --directory /Users/m/Manju run python mcp_server/server.py
```

Tools cung cấp cho Claude:
- `list_transcripts()` — liệt kê các cuộc họp đã transcribe.
- `read_transcript(transcript_id)` — đọc toàn bộ text 1 cuộc họp.

Sau khi thêm, hỏi Claude: *"liệt kê các cuộc họp đã có"* để kiểm tra kết nối.

### Test MCP nhanh (tuỳ chọn)
```bash
npx @modelcontextprotocol/inspector uv run python mcp_server/server.py
```
