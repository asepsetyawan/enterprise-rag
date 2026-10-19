FROM node:22-bookworm-slim AS web
WORKDIR /web
COPY frontend/package*.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim-bookworm AS python-build
RUN apt-get update && apt-get install -y --no-install-recommends libglib2.0-0 libgl1 && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir uv==0.9.5
WORKDIR /app/backend
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY backend/rag_platform ./rag_platform
RUN uv sync --frozen --no-dev --no-editable
ENV TIKTOKEN_CACHE_DIR=/opt/tiktoken
RUN .venv/bin/python -c "import tiktoken; tiktoken.get_encoding('cl100k_base'); tiktoken.get_encoding('o200k_base')"
RUN SERVICE_ROLE=admin PARSER_ARTIFACTS_PATH=/opt/docling .venv/bin/python -m rag_platform.parsing

FROM python:3.12-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends libglib2.0-0 libgl1 tesseract-ocr tesseract-ocr-eng tesseract-ocr-ind && rm -rf /var/lib/apt/lists/*
RUN groupadd --gid 10001 rag && useradd --uid 10001 --gid rag --no-create-home rag
WORKDIR /app/backend
COPY --from=python-build /app/backend/.venv /app/backend/.venv
COPY --from=python-build /opt/tiktoken /opt/tiktoken
COPY --from=python-build /opt/docling /opt/docling
COPY backend/rag_platform ./rag_platform
COPY backend/migrations ./migrations
COPY backend/alembic.ini ./
COPY --from=web /web/dist /app/frontend/dist
ENV PATH="/app/backend/.venv/bin:$PATH" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 FRONTEND_DIR=/app/frontend/dist TIKTOKEN_CACHE_DIR=/opt/tiktoken
ENV PARSER_ARTIFACTS_PATH=/opt/docling HF_HUB_OFFLINE=1
USER 10001:10001
EXPOSE 8088
CMD ["uvicorn","rag_platform.api:app","--host","0.0.0.0","--port","8088","--no-access-log","--no-proxy-headers"]
