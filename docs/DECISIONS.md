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
