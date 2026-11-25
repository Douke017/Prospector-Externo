FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PROSPECTOR_BACKEND=mongo \
    PROSPECTOR_SEED_ON_BOOT=1 \
    PROSPECTOR_DISABLE_YAML_SYNC=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
COPY src ./src
COPY apps ./apps
COPY config ./config
COPY docs ./docs

ARG INSTALL_PLAYWRIGHT=false

RUN mkdir -p /app/output/state /app/output/reports && \
    pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -e ".[mongo]" && \
    if [ "$INSTALL_PLAYWRIGHT" = "true" ]; then \
        pip install --no-cache-dir -e ".[browser]" && \
        playwright install --with-deps chromium; \
    fi

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/health || exit 1

CMD ["uvicorn", "apps.catalog_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
