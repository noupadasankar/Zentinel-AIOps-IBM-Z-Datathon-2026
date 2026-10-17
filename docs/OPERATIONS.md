# Zentinel AIOps: Operations Guide and Production Runbook

This guide covers deployment, lifecycle management, database maintenance, retention policies, backup procedures, and troubleshooting for the Zentinel AIOps platform.

---

## 1. System Requirements

- **Operating System**: Linux (Ubuntu 22.04+, RHEL 9+, Debian 12+) or Windows 10/11 / macOS (x86_64, ARM64).
- **Python**: Version 3.11+ (tested on Python 3.11.9).
- **Hardware Minimums**:
  - CPU: 2 physical cores (4 vCPUs recommended).
  - RAM: 4 GB minimum (8 GB recommended for large batch buffers).
  - Storage: 10 GB fast NVMe or SSD for SQLite WAL storage.
- **Docker**: Docker Engine 24.0+ and Docker Compose v2.20+.

---

## 2. Configuration & Environment Variables

Create a local `.env` configuration file from the provided template:
```bash
cp .env.example .env
```

| Variable | Default Value | Description |
| :--- | :--- | :--- |
| `WEB_CONCURRENCY` | `1` | **Mandatory 1**: Server enforces single worker to preserve transactional state. |
| `ZENTINEL_API_KEY_SHA256` | *(empty)* | Optional SHA-256 hash of operator API key for authentication. |
| `ZENTINEL_DB_PATH` | `data/processed/zentinel_live.sqlite3` | Filesystem location for operational SQLite database. |
| `ZENTINEL_CORS_ORIGINS` | `http://localhost:8501,http://127.0.0.1:8501` | Allowed CORS origins for browser dashboards. |
| `API_BASE_URL` | `http://127.0.0.1:8000` | Target API URL used by the Streamlit operational dashboard. |

---

## 3. Quickstart Deployments

### Option A: Turnkey Docker Compose (Recommended)
Launch the entire platform (FastAPI, Streamlit Dashboard, File Watcher, and Persistent Volume):
```bash
docker compose up -d --build
```
- API is available at: `http://127.0.0.1:8000`
- Dashboard is available at: `http://127.0.0.1:8501`
- Watcher automatically tails `data/raw/sample_replay.log`.

To check service health and logs:
```bash
docker compose ps
docker compose logs -f api
```

To tear down cleanly:
```bash
docker compose down
```

### Option B: Local Python Execution
```bash
# 1. Install dependencies from lockfile
pip install -r requirements.lock
# 1b. Install the zentinel package itself (src/ layout) - REQUIRED before uvicorn
pip install -e . --no-deps      # same as `make install`

# 2. Start Live API in background or separate shell
python -m uvicorn zentinel.api.live_api:app --host 127.0.0.1 --port 8000 --workers 1

# 3. Start Operations Dashboard
streamlit run dashboard/app.py --server.port 8501
```

---

## 4. Database Maintenance & Operations

### 4.1 SQLite Configuration & WAL Mode
Zentinel operates SQLite in Write-Ahead Logging mode with strict integrity pragmas:
```sql
PRAGMA journal_mode = WAL;
PRAGMA busy_timeout = 5000;
PRAGMA foreign_keys = ON;
PRAGMA synchronous = NORMAL;
```

### 4.2 Numbered Migrations
Database schemas are version-controlled via `src/zentinel/api/migrations.py`.
Migrations run automatically upon application startup. To inspect current version:
```bash
sqlite3 data/processed/zentinel_live.sqlite3 "SELECT * FROM schema_migrations;"
```

### 4.3 Retention Purge Job
To keep database size bounded in production:
- Ingestion idempotency records older than **7 days** are purged.
- Ingested event rows older than **30 days** are purged.
- Incidents, audit actions, and completed windows are permanently preserved.

Execute the maintenance purge and WAL checkpoint:
```bash
python -c "
from zentinel.api.live_store import LiveStore
store = LiveStore()
store.purge_retention(idempotency_days=7, events_days=30)
store.checkpoint_wal()
print('Retention purge and WAL checkpoint complete.')
"
```

### 4.4 Online Non-Blocking Backups
Zentinel uses the native `sqlite3_backup` API to create consistent snapshots without blocking concurrent writers:
```bash
python -c "
from pathlib import Path
from zentinel.api.live_store import LiveStore
store = LiveStore()
backup_file = Path('data/processed/zentinel_backup.sqlite3')
store.backup_to_file(backup_file)
print(f'Backup written to {backup_file} ({backup_file.stat().st_size} bytes)')
"
```

---

## 5. Observability and Prometheus Monitoring

### 5.1 Prometheus Metrics Endpoint
Scrape `GET /api/v1/metrics` every 15 seconds. Key exposed metrics:
- `zentinel_events_ingested_total`: Total count of log lines ingested.
- `zentinel_windows_completed_total`: Total 100-line windows processed.
- `zentinel_anomalies_detected_total`: Total windows flagged `ANOMALY_ALERT`.
- `zentinel_novelty_reviews_total`: Total windows flagged `NOVELTY_DRIFT_REVIEW`.
- `zentinel_open_incidents`: Gauge tracking currently active unresolved incidents.
- `zentinel_ingestion_latency_seconds`: Latency histogram of the ingestion handler.

### 5.2 Health Checks
The `/health` endpoint is open and responds with `HTTP 200 OK` when models and templates are loaded:
```bash
curl -s http://127.0.0.1:8000/health | jq .
```

---

## 6. Troubleshooting Runbook

### Issue 1: Server refuses to start with `WEB_CONCURRENCY > 1`
- **Cause**: Ingestion state synchronization requires single-process transactional serialization.
- **Resolution**: Set `WEB_CONCURRENCY=1` in your environment or launch command.

### Issue 2: Startup error `Artifact manifest missing` or `checksum does not match`
- **Cause**: An artifact in `artifacts/` was modified or corrupted, violating Non-Negotiable Rule 2.
- **Resolution**: Restore canonical artifacts from git (`git checkout artifacts/`) or verify `artifacts/manifest.json`.

### Issue 3: Ingestion returns `HTTP 409 Conflict`
- **Cause**: An idempotency key collision occurred: the watcher resent the same file/offset key with a differing payload.
- **Resolution**: Verify log rotation scripts are not writing conflicting data to the same offset. Reset watcher state if a log file was overwritten in place.

### Issue 4: Watcher writes records to `.dead_letter` file
- **Cause**: Malformed log lines failed schema validation (`HTTP 400` or `HTTP 422`).
- **Resolution**: Inspect `<log_name>.dead_letter` to examine the rejected payload and reason code.
