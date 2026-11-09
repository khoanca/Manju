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
# Deps ML (faster-whisper/torch) vẫn theo lockfile để không lệch với bản local —
# image to nhưng tier local không chạy ở enterprise. Tách group deps = tech debt.
RUN uv sync --frozen --no-dev --no-install-project

COPY app ./app
COPY mcp_server ./mcp_server

VOLUME ["/data"]
EXPOSE 8000
CMD ["uv", "run", "--no-sync", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips=*"]
