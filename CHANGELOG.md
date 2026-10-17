# Changelog - Zentinel AIOps

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased] - Phase 2: Structure, Features, and Incident Policies

### Added
- **Incident Management Policy (`src/zentinel/alerts/policy.py`)**:
  - Hysteresis alerting opening at validation threshold ($\tau = 3.4791$) and closing only after 3 consecutive windows below $0.70 \times \tau$.
  - Dynamic severity rating (`LOW`, `MEDIUM`, `HIGH`) mapped to validation anomaly score percentiles.
  - Cooldown logic preventing alert flapping.
- **Explainable Evidence Engine (`src/zentinel/alerts/explanations.py`)**:
  - Top-$k$ templates ranked by per-event cross-entropy loss.
  - Identification of first-seen / unknown template signatures.
  - Unknown event drift vs 0.02 threshold evaluation.
  - Critical severity frequency counts (`FATAL`, `SEVERE`, `ERROR`, `WARNING`).
  - Traceable raw log line numbers without synthetic or SHAP approximations.
- **Numbered Database Migrations (`src/zentinel/api/migrations.py`)**:
  - `schema_migrations` tracking schema versioning.
  - Migration 001: Baseline state and event tables.
  - Migration 002: Metadata tracking columns (`payload_sha256`, `source_id`, `model_id`, `latency_ms`).
  - Migration 003: Operational tables (`alerts`, `alert_actions`, `model_registry`, `api_keys`).
- **REST Endpoints (`src/zentinel/api/live_api.py`)**:
  - `GET /api/v1/alerts` with filtering by status and severity.
  - `GET /api/v1/alerts/{id}` returning forensic evidence and audit actions.
  - `POST /api/v1/alerts/{id}/ack` for acknowledging alerts.
  - `POST /api/v1/alerts/{id}/close` for resolving alerts with reasons.
  - `POST /api/v1/live/ingest/batch` for high-throughput batch ingestion up to 500 lines.
  - `GET /api/v1/review` with CSV download support for retraining curation.
  - `GET /api/v1/metrics` in Prometheus text exposition format.
  - `GET /api/v1/models` querying the active model registry.
  - `GET /api/v1/drift` providing template novelty and drift rate summaries.
- **Empirical Walk-Forward Evaluation (`src/zentinel/models/evaluation.py` & `scripts/evaluate.py`)**:
  - Walk-forward test over 4 consecutive temporal slices.
  - Reporting of false alerts per hour (0.0130 / hour measured).
  - Detection delay distribution (median 8.5s, p90 9504.5s).
  - Block-bootstrap 95% confidence intervals (500 resamples).
  - Mandatory scoring coverage metrics alongside every precision and recall score.
- **Modular Package Structure**:
  - Consolidated features and sequences in `src/zentinel/features/`.
  - Replay tooling in `src/zentinel/ingestion/replay.py`.
  - Archived legacy scripts in `scripts/archive/` with documentation.

## [0.1.0] - Phase 1: Correctness, Security, and Integrity Hardening

### Added
- Artifact integrity manifest (`artifacts/manifest.json`) verifying SHA-256 digests of all frozen models, state bins, and decision thresholds at startup.
- FastAPI lifespan management checking single-worker concurrency (`WEB_CONCURRENCY <= 1`) and initializing inference resources cleanly.
- `X-API-Key` authentication with SHA-256 hash comparison via `hmac.compare_digest` and role-based permissions (`ingest`, `read`, `admin`).
- Token-bucket rate limiting per API key.
- Strict configurable CORS allow-list.
- Uniform error responses containing request IDs with stack-trace suppression.
- Two-phase transactional state persistence in `LiveStateStore` ensuring in-memory state and SQLite never diverge on failure.
- Retention cleanup job purging idempotency records older than 7 days and events older than 30 days.
- SQLite WAL checkpointing and online database backup API.
- Resilient file watcher with `sha256(file_id:offset)` idempotency keys, file replacement/inode detection, dead-letter recording for 400/422, and backoff retries for 408/429/5xx.
- Environment template `.env.example`.
- Over 50 comprehensive unit and integration tests covering parser, matcher, scoring, windowing, persistence, security, and watcher resilience.

### Changed
- Sanitized local absolute paths from committed reports (`reports/live_ingestion_benchmark.json`).
- Updated `.gitignore` to prevent secret key leaks (`*.pem`, `*.key`, `*.crt`).
