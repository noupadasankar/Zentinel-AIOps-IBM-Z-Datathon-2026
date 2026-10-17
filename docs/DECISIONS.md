# Architectural Decision Records (ADRs) - Zentinel AIOps

This document records architectural, ML, and engineering decisions made during the development of Zentinel AIOps, in accordance with the project rules.

---

## ADR-001: Idempotency Key Architecture and Collision Protection
- **Status:** Accepted
- **Context:** Log watchers may restart, crash, or experience file replacements/truncations. A naive idempotency key like `sha256(file_path:offset)` leads to collisions if a file is rotated, truncated, or replaced. Furthermore, if a client accidentally reuses a key for a different payload, the server could silently return mismatched results.
- **Decision:**
  1. Watcher generates keys using `sha256(file_id:offset)`, where `file_id = f"{st_dev}:{st_ino}:{sha256(first_line)}"`. This uniquely identifies file generations across rotations and reboots.
  2. The server stores `payload_sha256 = sha256(raw_line)` alongside the idempotency response in SQLite.
  3. When an existing key arrives:
     - If `payload_sha256` matches, HTTP 200 is returned with `"idempotency_replayed": true`.
     - If `payload_sha256` differs, HTTP 409 Conflict is returned immediately without reprocessing.
- **Consequences:** Eliminates ghost re-execution and prevents silent data corruption across log rotations.

---

## ADR-002: Ingestion State Machine: Two-Phase Transactional Update
- **Status:** Accepted
- **Context:** In `live_api.py`, modifying in-memory state (`STATE`, `CURRENT_WINDOW`) before SQLite persistence caused state divergence if SQLite failed or threw an exception during transaction commit. Retries would then double-count lines or create inconsistent windows.
- **Decision:** All state transitions are computed on local copies (`candidate_state`, `candidate_window`). The state is committed atomically to SQLite in a WAL transaction. Only upon successful commit are in-memory references (`STATE`, `CURRENT_WINDOW`, `RECENT_WINDOWS`) updated.
- **Consequences:** Guarantees database and in-memory synchronization even under disk full or simulated SQLite failures.

---

## ADR-003: Watcher Error Handling and Resiliency
- **Status:** Accepted
- **Context:** Watcher previously forwarded client errors (400-499) blindly or stalled on temporary network/server glitches.
- **Decision:**
  1. Dead-letter queue (`rejected_live_lines.jsonl`) records only client errors (`400 Bad Request`, `422 Unprocessable Entity`). The dead-letter payload includes the actual watched file path (`source_file`), exact byte offset, raw line, timestamp, and error detail.
  2. Transient errors (`408 Request Timeout`, `429 Too Many Requests`, `5xx Server Error`, network disconnections) trigger exponential backoff retry without advancing the cursor.
  3. File replacement is actively detected by monitoring inode/dev and first-line hash changes, resetting the cursor to 0 upon replacement.
- **Consequences:** Stream ingestion never silently drops unprocessable lines, nor does it wedge permanently on transient API hiccups.

---

## ADR-004: Lifespan Management and Concurrency Enforcement
- **Status:** Accepted
- **Context:** Zentinel's streaming windowing logic aggregates consecutive 100-line windows in memory and SQLite. Running multiple worker processes (`WEB_CONCURRENCY > 1`) without distributed IPC would shard window state unpredictably across workers.
- **Decision:** Model and Drain state loading is moved to FastAPI's async `lifespan` handler. During startup, `WEB_CONCURRENCY` is inspected; if greater than 1, startup fails immediately with an explanatory error.
- **Consequences:** Guarantees single-worker consistency for log stream windowing.

---

## ADR-005: API Security, Authentication, Rate Limiting, and CORS
- **Status:** Accepted
- **Context:** Operational endpoints require role-based access control (`ingest`, `read`, `admin`), timing-attack mitigation, rate limiting to prevent denial-of-service, and strict CORS policies.
- **Decision:**
  1. Authentication uses `X-API-Key` headers. Expected keys are stored as SHA-256 hashes in `ZENTINEL_API_KEY_SHA256` (format: `role:sha256_hash,...` or raw hash). Comparison uses `hmac.compare_digest`.
  2. Public health check `/health` requires no credentials.
  3. Role hierarchy: `admin` encompasses all permissions; `ingest` permits ingestion; `read` permits monitoring and metrics.
  4. Token-bucket rate limiting enforces requests-per-second thresholds per key.
  5. Uniform error envelope `{ "error": { "code": ..., "message": ... }, "request_id": ... }` prevents stack trace leaks.
  6. Strict CORS whitelist restricts unauthorized cross-origin browser access.
- **Consequences:** Meets enterprise security baselines without leaking internal implementation details.

---

## ADR-006: Artifact Integrity Verification
- **Status:** Accepted
- **Context:** Machine learning inference depends on exact frozen weights, Drain cluster states, and vocabulary files. Any corruption or tampering must prevent the server from issuing untrustworthy anomaly decisions.
- **Decision:** An `artifacts/manifest.json` file records cryptographic SHA-256 hashes of all required model artifacts and validation thresholds. At startup during the lifespan phase, each asset is validated. Any hash mismatch aborts server launch.
- **Consequences:** Enforces Rule 2 (never modify frozen artifacts) with cryptographic verification.

---

## ADR-007: Incident Grouping Policy, Hysteresis, and Severity Bands
- **Status:** Accepted
- **Context:** A raw anomaly detector triggers an alert on every individual window above threshold, creating alert storms and notification fatigue for operators.
- **Decision:**
  1. Consecutive anomalous windows are coalesced into a single persistent incident (`Incident` object).
  2. Hysteresis: Incidents open immediately upon reaching the threshold ($\tau = 3.4791$). They do not close until 3 consecutive windows fall strictly below $0.70 \times \tau = 2.4354$. Windows in the intermediate band keep the incident active.
  3. Severity is mapped from score percentiles against the validation anomaly distribution:
     - LOW: $\tau \le \text{score} < 1.35 \times \tau$
     - MEDIUM: $1.35 \times \tau \le \text{score} < 2.2 \times \tau$
     - HIGH: $\text{score} \ge 2.2 \times \tau$
  4. Cooldown window mechanism suppresses fluttering re-triggering.
- **Consequences:** Dramatically reduces alert noise and provides actionable lifecycle states (`OPEN`, `ACKNOWLEDGED`, `CLOSED`).

---

## ADR-008: Alert Explanations Grounded Exclusively in Measured Quantities
- **Status:** Accepted
- **Context:** Explanations in production AIOps often rely on unverified attribution approximations (like SHAP on non-linear sequential deep models) which hallucinate or fail reproducibility tests.
- **Decision:** All incident evidence must be computed strictly from physical quantities measured during inference:
  1. Top-$k$ templates ranked by mean cross-entropy loss over their constituent transitions.
  2. First-seen / unmatched log patterns with exact counts.
  3. Window unknown event rate contrasted with the 0.02 validation threshold.
  4. Repeated high-severity keywords (`FATAL`, `SEVERE`, `ERROR`, `WARNING`).
  5. Concrete line number references pointing directly to the responsible log events.
- **Consequences:** Guarantees 100% auditability and compliance with Non-Negotiable Rule 1.

---

## ADR-009: Numbered Database Migrations and Audit Trail
- **Status:** Accepted
- **Context:** SQLite schema changes must be versioned, idempotent, and reversible without manual table manipulation.
- **Decision:** Implemented a lightweight migration runner (`src/zentinel/api/migrations.py`) tracking applied versions in `schema_migrations`.
  - Migration 001: Initial baseline schema.
  - Migration 002: Add metadata columns (`payload_sha256`, `source_id`, `model_id`, `latency_ms`).
  - Migration 003: Tables for `alerts`, `alert_actions` (audit log for ACK/CLOSE), `model_registry`, and `api_keys`.
- **Consequences:** Seamless database upgrades across environments with immutable operator audit trails.

---

## ADR-010: Walk-Forward Temporal Evaluation and Block-Bootstrap CIs
- **Status:** Accepted
- **Context:** Standard k-fold cross validation leaks temporal order in log time series. Single test scores can mask localized distribution shifts and temporal autocorrelation.
- **Decision:**
  1. 4 consecutive temporal slices evaluate walk-forward performance over the exploratory test dataset.
  2. Scoring coverage is reported alongside every metric (highlighting the drift surge in Slice 3 where novelty review properly captured 100% of drift).
  3. Block-bootstrap resamplings (500 resamples, 50-window blocks) establish 95% confidence intervals while preserving autocorrelation.
  4. Detection delay / lead time is measured in seconds from episode onset to first alert.
- **Consequences:** Transparent, defensible evaluation adhering strictly to Rule 5 and Rule 6.

---

## ADR-011: Streamlit Dashboard Design and Layout Fidelity
- **Status:** Accepted
- **Context:** The operations team requires an intuitive dark-themed command center matching an executive reference layout (sidebar grouping, alert banner, KPI cards, dual-axis telemetry charts, source health, template patterns, incident drill-down, review queue, and offline demo mode).
- **Decision:**
  1. Built the unified dashboard using Streamlit + Plotly with custom CSS styles achieving full visual fidelity without incurring React/Node frontend operational overhead.
  2. Integrated offline demo replay capability reading from committed `reports/` and `data/processed/` Parquet datasets so the dashboard operates self-contained without a live API server if desired.
  3. Strict XSS mitigation: all raw log lines and template texts are escaped (`html.escape` and pure Streamlit text/code components) rather than passed via raw unsafe HTML.
  4. Cached API calls with short TTLs (`st.cache_data(ttl=5)`) to keep telemetry fluid and prevent network/database saturation.
- **Consequences:** Immediate deployability in a single container or Python process, zero frontend build toolchain required, high performance.

