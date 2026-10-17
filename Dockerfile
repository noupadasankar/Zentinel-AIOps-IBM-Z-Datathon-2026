# Zentinel AIOps Production Container
FROM python:3.11-slim

# Prevent Python from writing .pyc files and enable unbuffered output
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    WEB_CONCURRENCY=1

# Install runtime dependencies (curl for container healthcheck)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Create dedicated non-root application user
RUN groupadd -g 1000 zentinel && \
    useradd -u 1000 -g zentinel -m -s /bin/bash zentinel

WORKDIR /app

# Install pinned dependencies
COPY requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock

# Copy project files
COPY pyproject.toml ./
COPY artifacts/ ./artifacts/
COPY reports/ ./reports/
COPY src/ ./src/
COPY dashboard/ ./dashboard/
COPY scripts/ ./scripts/

# Create data directories with appropriate non-root ownership
RUN mkdir -p /app/data/processed /app/data/raw && \
    chown -R zentinel:zentinel /app

# Switch to non-root user
USER zentinel

# Default expose: 8000 (API), 8501 (Dashboard)
EXPOSE 8000 8501

# Container healthcheck querying open /health endpoint
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -f http://127.0.0.1:8000/health || exit 1

# Default launch command: Single-worker FastAPI Live API server
CMD ["python", "-m", "uvicorn", "zentinel.api.live_api:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
