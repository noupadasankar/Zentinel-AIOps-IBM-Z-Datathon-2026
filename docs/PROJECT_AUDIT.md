# Zentinel AIOps — Project Audit (Phase 0/1 Baseline)

Generated: 2026-10-17 · Baseline commit before this phase: `7017d10` ("refactor: harden ingestion, add dashboard feeds, decision-column migration, watcher CLI") · Checkpoint commit: `351fffa` (preserves WIP: readiness probe, Retry-After backoff, Dockerfile torch-cpu fix, baseline audit).

Raw machine-readable audit: [`reports/project_audit.json`](../reports/project_audit.json) (generator: `scripts/generate_project_audit.py`).

## 1. Repository state at audit time

- Git branch `main`, working tree was dirty: docs 10 files + `CHANGELOG.md` deleted in the working tree (all restored from HEAD because they are valuable engineering documentation); `live_api.py`, `watcher.py`, `test_live_api.py`, `test_watcher.py`, `Dockerfile` carried genuine WIP improvements (readiness probe `/ready`, Retry-After-aware bounded backoff, torch-cpu wheel-index fix). Those were **kept**, not discarded.
- Untracked baseline audit files (`reports/project_audit.json`, `scripts/generate_project_audit.py`) were committed.
- Runtime: Python 3.11.9 on Windows 10 (x86-64), project `.venv` with pinned `requirements.lock` (torch 2.14.1+cpu, sklearn 1.9.1, fastapi 0.143.3, pandas 3.0.6, drain3 0.9.11, onnx 1.23.2 + onnxruntime 1.31.0, streamlit 1.66.0, plotly 6.9.0).

## 2. Existing implementation (verified by inspection, not assumption)

| Area | Status | Evidence |
| --- | --- | --- |
| Parse: frozen Drain3 matcher (train-state `drain3_train_state_0_7.bin`, 170 training templates, `match()` only, never re-fits at inference) | Complete | `src/zentinel/parsing/drain_parser.py`, `src/zentinel/models/inference.py:130-193` |
| LSTM next-event model + known-run scoring (known→known transitions only; UNK/PAD break runs; runs < 2 contribute none) | Complete | `src/zentinel/models/inference.py:195-243` |
| Three-way decision policy (`ANOMALY_ALERT` / `NO_ANOMALY_DETECTED` / `NOVELTY_DRIFT_REVIEW`) with explicit reasons; threshold 3.4791 (validation-selected), novelty unknown-rate threshold 0.02 (validation 95th pct) | Complete | `inference.py:245-285`, `reports/lstm_three_way_test_decision_report.json` |
| Incident policy: hysteresis (3 consecutive windows < 0.7·τ), severity bands, cooldown, deterministic IDs (`INC-NNNN` restored from DB after restart) | Complete | `src/zentinel/alerts/policy.py` |
| Evidence engine: per-event cross-entropy top-k templates, first-seen/unknown signatures, raw-line references | Complete | `src/zentinel/alerts/explanations.py` |
| API: FastAPI with lifespan (single-worker guard, SHA-256 manifest verification, state restore), `/health`, `/ready`, ingest single+batch (500 max, per-line validation, partial failure explicit), status, windows, dashboard summary, alerts CRUD (ack/close w/ roles), review queue (JSON+CSV), drift, models registry, Prometheus metrics, admin retention/backup/checkpoint, request-ID middleware, uniform error handlers (no stack traces) | Complete | `src/zentinel/api/live_api.py` (940 lines) |
| Security: role API keys stored as SHA-256 (`role:hash`), `hmac.compare_digest` verification, token-bucket rate limiter, strict CORS | Complete | `src/zentinel/api/security.py`, tests `test_phase2_api.py`, `test_live_api.py` |
| Persistence: SQLite WAL store, numbered migrations, atomic event+state+idempotency commit (candidate-copy then publish), rollback-safe, incident save/update, review-window store, drift summary, retention purge, online backup, checkpoint | Complete | `src/zentinel/api/live_store.py` (559 lines), `migrations.py` |
| Ingestion: file watcher with file-identity (`dev:inode:first-line-hash`), truncation/rotation detection, cursor persistence with atomic replace, dead-letter queue, idempotency keys, 429/408/5xx + Retry-After backoff with jitter and bounded escalation | Complete | `src/zentinel/ingestion/watcher.py` (339 lines) + 12+ tests |
| Offline replay API exposing committed test-period decisions without label leakage (`PUBLIC_COLUMNS`) | Complete | `src/zentinel/api/replay_api.py` |
| ONNX export opset 17 + parity harness (1000 runs, max logit diff 4.77e-6, 100% window-decision parity) | Complete | `src/zentinel/models/onnx_export.py`, `reports/onnx_parity_report.json` |
| Dashboard: 1,653-line Streamlit command center, dark navy theme, 10 pages (Overview, Incidents w/ ack-close actions, Review Queue w/ CSV export, Windows, Stream, Drift, Model Evaluation, Thresholds, Security, Settings), Plotly charts, live-API-first with labelled offline demo fallbacks | Complete | `dashboard/app.py` |
| CI: ruff + pip-audit + gitleaks + full pytest + ONNX parity + smoke test | Complete | `.github/workflows/ci.yml` |
| Containers: non-root multi-stage-ish Dockerfile (torch-cpu index fix applied), compose api+dashboard+watcher with read-only artifact mounts and healthchecks | Complete | `Dockerfile`, `docker-compose.yml` |
| Data products: 4.75 M-line parsed/frozen parquet (150 MB ×2), window features 47,480×654, sequences v2 npz, test decisions/comparison parquet | Complete | `data/processed/` |
| Reports: 26 committed JSON/JSON reports covering temporal walk-forward (4 slices, bootstrap CIs, detection delay), model comparison, drift, unknown diagnostics, benchmarks | Complete | `reports/` |
| Tests: 78 passing (unit, API, security, idempotency, watcher, ONNX parity, dashboard feeds) | Complete | `tests/` (13 files), executed 2026-10-17: **78 passed** |
| Model artifacts + SHA-256 manifest, verified at API startup and in tests | Complete | `artifacts/manifest.json`, `config.verify_manifest()` |

## 3. Gaps identified (this phase addresses them)

1. **Notebook laboratory absent** — `notebooks/` did not exist; 13 required notebooks must be built from shared production modules and executed locally.
2. `docs/LINUXONE_DEPLOYMENT.md` missing (s390x statement needed).
3. `docs/DATATHON_SCORING_ALIGNMENT.md` missing.
4. `docs/PROJECT_AUDIT.md`, `docs/IMPLEMENTATION_STATUS.md` missing (only machine JSON existed).
5. Final ZIP package + its verification report missing.
6. `make train` missing from the Makefile (all other required targets existed).
7. Obsolete patch leftovers: 5 `*.bak` files plus `scripts/archive/` (8 patch scripts) whose functionality has already been migrated and is covered by tests — safe to remove.
8. Minor: `data/data_sample(s)`/`data/manifests` directory convention and a few CSV exports could be added for portability (parquet equivalents exist).

## 4. Confirmed defects found and disposition

- **Docs deletion (working tree)**: docs were deleted without an in-repo rationale → restored from HEAD (`git restore`).
- **Dockerfile torch install**: `requirements.lock` pins `torch=2.x.y+cpu` (CPU local-version builds live only on the PyTorch CPU index) → fixed in the preserved WIP commit with `--extra-index-url`; s390x wheel availability flagged as unverified.
- **Watcher backoff**: no Retry-After support → fixed in WIP (covered by 3 new tests).
- **API readiness**: health lacked a readiness probe → fixed in WIP (`/ready`).
- No test failures, no lint failures (ruff) at baseline.

## 5. Evaluation status carried by committed reports (traceable, not re-claimed here)

- Walk-forward 4-slice test evaluation, 7,122 windows, F1 0.8333 (P 0.8173 / R 0.8500), zero false alerts in review slices, 0.0130 false alerts/hour, median lead time 8.5 s (`reports/temporal_walk_forward_evaluation.json`).
- Model comparison LSTM vs Isolation-Forest on the same test windows (`reports/test_model_comparison.json`, `test_model_comparison.parquet`).
- Aggregate three-way decision coverage 33.2%, review rate 66.8% on severe drift (exploratory test caveats recorded in the report trio).

## 6. Environment-dependent items (blocked locally)

- IBM z15 / LinuxONE / s390x execution: no access from this environment — portable implementation exists; target validation must run on device (`docs/LINUXONE_DEPLOYMENT.md` states "Portable by design; not yet validated on s390x").
- Large raw dataset (743 MB BGL.log) is present locally but must **not** enter the final ZIP; notebooks therefore read committed processed parquet/sample fixtures when executing.

## 7. Risk register for the remaining work

| Risk | Mitigation |
| --- | --- |
| Notebook execution on 150 MB parquet is slow/exhaustive | Use column-projected reads and sliced windows; smoke-scale training in 06 |
| Notebook 06 (autoencoder) full training cost | Separate experimental model; train on tiny fixture, record limitation |
| HTML/JS rendering of Plotly in notebooks may bloat committed outputs | Use notebook outputs where valuable; keep heavy data re-derivable by commands |
| ZIP size | Exclude data/raw/BGL.log, .venv, caches, checkpoints; keep artifacts/ (3.9 MB total) |
