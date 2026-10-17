# Changelog - Zentinel AIOps

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased] - Phase 1: Correctness and Security
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
- Over 40 comprehensive unit and integration tests covering parser, matcher, scoring, windowing, persistence, security, and watcher resilience.

### Changed
- Sanitized local absolute paths from committed reports (`reports/live_ingestion_benchmark.json`).
- Updated `.gitignore` to prevent secret key leaks (`*.pem`, `*.key`, `*.crt`).
