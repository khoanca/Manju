# Manju Enterprise — một image, công ty tự host (FR-11 US-906).
# Audio không chạm đĩa server; DB text ở volume /data.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    MANJU_EDITION=enterprise \
    MANJU_DATA_DIR=/data

COPY --from=ghcr.io/astral-sh/uv:0.11.32 /uv /uvx /bin/

WORKDIR /app
COPY pyproject.toml uv.lock ./
# mlx-whisper (kéo torch + CUDA, ~2,8 GB) chỉ cài trên macOS arm64 nên không vào
# image này. faster-whisper/sherpa-onnx/noisereduce vẫn theo lockfile vì app còn
# import numpy và faster_whisper.vad lúc khởi động — tách nốt = tech debt.
RUN uv sync --frozen --no-dev --no-install-project

COPY app ./app
COPY mcp_server ./mcp_server

VOLUME ["/data"]
EXPOSE 8000
CMD ["uv", "run", "--no-sync", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips=*"]
