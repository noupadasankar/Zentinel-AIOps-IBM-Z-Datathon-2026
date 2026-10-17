FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    WEB_CONCURRENCY=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd -g 1000 zentinel && \
    useradd -u 1000 -g zentinel -m -s /bin/bash zentinel

WORKDIR /app

COPY requirements.lock ./
RUN pip install --no-cache-dir \
    --extra-index-url https://download.pytorch.org/whl/cpu \
    --default-timeout=120 \
    -r requirements.lock

COPY pyproject.toml ./
COPY artifacts/ ./artifacts/
COPY reports/ ./reports/
COPY src/ ./src/
COPY dashboard/ ./dashboard/
COPY scripts/ ./scripts/

RUN mkdir -p /app/data/processed /app/data/raw && \
    chown -R zentinel:zentinel /app

USER zentinel

EXPOSE 8000 8501

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -f http://127.0.0.1:8000/health || exit 1

CMD ["python", "-m", "uvicorn", "zentinel.api.live_api:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
