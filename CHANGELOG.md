# Changelog - Zentinel AIOps

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.2.0] - 2026-10-18 — LinuxONE Validation Readiness

### Added
- **Target-side validation automation** (`linuxone-validation/validate_linuxone.sh`): executes the runbook steps 1–11 on an s390x host with hard stop-guards (non-s390x abort; missing/divergent artifacts abort), disposable SQLite DB for API lifecycle checks, real entry points only (`zentinel.config.verify_manifest()`, `scripts/benchmark.py` when `data/raw/BGL.log` is transferred, the shipped `sample_replay.log` replay otherwise), idempotency/409/422 checks, controlled-restart persistence check, on-device ONNX parity smoke (`tokens`→`logits`, results to `reports/linuxone_onnx_parity_smoke.json`), and `~/zentinel-validation` evidence packaging with RESULTS.md. No target results are claimed until the script is actually run on IBM Z/LinuxONE.
- **Git remote**: `origin` = `https://github.com/noupadasankar/Zentinel-AIOps-IBM-Z-Datathon-2026.git` enabling clone-on-target (runbook Step 2).
- X86-64 dry-run of all embedded validation logic: manifest verify PASS, engine threshold loaded from artifact (3.479122797648112), ONNX smoke 32/32 agreement / 4.29e-06 max logit diff (host sanity only, not a target result).

### Changed
- §17 evidence pointer updated in `reports/FINAL_COMPLETION_REPORT.md` (still BLOCKED-B: no target run executed).

## [1.1.0] - 2026-10-18 — Notebook Laboratory Complete

### Added
- **13 executed Jupyter notebooks** (`notebooks/00_environment_and_data_contracts.ipynb` … `12_final_results_and_figures.ipynb`), all cells executed with a passing, assertion-checked final state on 2026-10-18 (ledger: `notebooks/notebook_execution_log.json`). Per-notebook machine-readable outputs: `reports/nb00_run_manifest.json` … `reports/nb12_final_summary.json`.
  - nb02: frozen Drain3 state audit (615 templates), production parser probes, single-pass unknown-template scan of validation/test BGL lines, runtime no-refit guard (monkey-patch check).
  - nb03: split/leakage guards — window-id contiguity, monotonic start lines, temporal split order, label-not-a-feature, zero PAD/UNK positions in the training split.
  - nb04: frozen Isolation Forest re-scoring parity to 1e-9 against committed validation scores + exploratory test metrics/PR curve.
  - nb05: production LSTM checkpoint parity (validation scores, known-run scoring on test windows, three-way decision parity), latency measurement.
  - nb06: **experimental** smoke-scale LSTM reconstruction autoencoder, exported flagged `experimental: true` — never production-promoted.
  - nb07: same-window comparison of IF vs LSTM (precision/recall/F1/PR-AUC/ROC-AUC + confusion matrices + runtime table); exports `data/processed/metrics_summary.csv`.
  - nb08: live `IncidentPolicy` replay of all 7,122 test decisions, peak-alert forensics re-parsed from `data/raw/BGL.log` through the production pipeline (token parity asserted); exports `alert_evidence.csv`.
  - nb09: unknown-rate/coverage timelines, validation-only threshold sensitivity grid, feature-drift table, labelled synthetic drift scenario; exports `drift_report.csv`, `retraining_review_queue.csv`.
  - nb10: ONNX opset-17 graph checks + PyTorch↔ONNXRuntime logit/decision parity on real runs (x86-64 host; no s390x claims).
  - nb11: live FastAPI ingest subprocess on a scratch SQLite DB; sequential + 8-client replay of test-period lines with p50/p95/p99 latency.
  - nb12: final consolidation, limitations, and reproduction commands.
- **Documentation**: `docs/LINUXONE_DEPLOYMENT.md` (portability inventory + target verification protocol), `docs/DATATHON_SCORING_ALIGNMENT.md` (criterion → deliverable mapping), `notebooks/README.md`, updated `docs/PROJECT_AUDIT.md` and `docs/IMPLEMENTATION_STATUS.md`.
- **Engineering**: `make train` target wiring the real training chain; removed obsolete patch artifacts and backup files.

### Notes
- Notebook figures are rendered with plotly (matplotlib intentionally not used).
- All notebook numbers are recomputed from committed artifacts at execution time; test labels are used only inside explicitly-declared exploratory evaluation cells and never for fitting or threshold selection.

## [1.0.0] - Phase 4: Packaging, Proof, and Production Readiness

### Added
- **Turnkey Containerization Ecosystem**:
  - Multi-stage `Dockerfile` running unprivileged non-root user `zentinel` (UID 1000) with container healthcheck.
  - Multi-service `docker-compose.yml` orchestrating `api`, `dashboard`, `watcher`, persistent SQLite volume, read-only artifacts mount, and localhost-bound ports (`127.0.0.1:8000`, `127.0.0.1:8501`).
  - Comprehensive automation `Makefile` (`install`, `test`, `lint`, `export-onnx`, `benchmark`, `evaluate`, `demo`, `reproduce`).
- **ONNX Export and Runtime Parity (`src/zentinel/models/onnx_export.py` & `scripts/export_onnx.py`)**:
  - Exported `LSTMNextEvent` to ONNX opset 17 with dynamic sequence and batch dimensions.
  - Evaluated numerical parity over 1,000 real runs: maximum logit difference $4.77 \times 10^{-6}$, 100.0% identical window decisions.
  - Saved empirical report to `reports/onnx_parity_report.json`.
- **Live Ingestion Benchmarking Suite (`scripts/benchmark.py`)**:
  - Measured sequential (101.13 ± 1.55 events/s, p50 2.21ms) and 8-way concurrent (100.00 ± 0.02 events/s, p50 24.11ms) performance on TEST-period lines over 60s sustained trials across 3 repeats.
  - Continuous CPU and RSS memory telemetry monitoring with full system metadata logging.
  - Saved empirical reports to `reports/benchmark_report.json` and `reports/live_ingestion_benchmark.json`.
- **Dependency Pinning & CI/CD Hardening**:
  - Pinned lockfile `requirements.lock` with exact transitive dependency tree.
  - GitHub Actions workflow (`.github/workflows/ci.yml`) integrating `ruff`, `pytest`, `pip-audit`, and `gitleaks`.
- **Comprehensive Documentation Suite**:
  - Authored `docs/ARCHITECTURE.md`, `docs/DATA_CARD.md`, `docs/MODEL_CARD.md`, `docs/API.md`, `docs/SECURITY.md`, `docs/OPERATIONS.md`, and `docs/EVALUATION.md`.
  - Authored executive demo script (`docs/DEMO_SCRIPT.md`) and 7-slide outline (`docs/SLIDES.md`).
  - Extracted sample replay log (`data/raw/sample_replay.log`).
  - Updated `README.md` with walk-forward results table with scorable coverage, 5-command quickstart, limitations, and LinuxONE portability statement.

## [0.3.0] - Phase 3: Streamlit Operations Dashboard and Visualizations

### Added
- **Dark-Theme Operations Dashboard (`dashboard/app.py` & `dashboard/live_app.py`)**:
  - Implemented command center layout matching operational design specification.
  - Left navigation grouped into `HOME` (Overview), `INVESTIGATE` (Incidents, Review Queue, Windows), `OBSERVE` (Sources/Components, Templates, Drift, Model Evaluation), and `CONFIGURE` (Thresholds, API Keys, Settings).
  - Open incidents and review queue status badges in navigation.
  - Real-time incident banner highlighting active severity with immediate ACK/CLOSE actions.
  - 4 Executive KPI cards: Active Anomaly Windows, Open Incidents, Windows Scored/min, and Scorable Coverage % with unknown rate.
  - Dual-axis Plotly telemetry chart tracking LSTM anomaly score vs validation threshold (3.4791) alongside novelty unknown rate (2.0% threshold).
  - Source/component health distribution grid with alert ratios.
  - Top surprising templates ranked by mean cross-entropy loss with trend arrows.
  - Live activity feed showing incident lifecycle, drift surges, and model registry events.
  - Interactive Incident Management page with forensic evidence drilldown, raw log inspection, and operator action dispatch.
  - Retraining review queue with CSV export for active template curation.
  - Model evaluation page presenting empirical comparison tables (LSTM vs IF vs Three-Way), confusion matrix, PR curves, and detection delay metrics.
  - Offline demo mode providing zero-dependency demonstration using committed Parquet decisions.

## [0.2.0] - Phase 2: Structure, Features, and Incident Policies

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
