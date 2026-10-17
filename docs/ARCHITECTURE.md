# Zentinel AIOps: System Architecture

## 1. Overview and Design Philosophy

Zentinel AIOps is an enterprise-grade log anomaly detection and observability platform designed for high-throughput distributed infrastructure. The platform monitors unstructured system logs, extracts structured templates via frozen Drain3 miners, evaluates sequence transition perplexity through a next-event LSTM model, and executes **three-way operational decisions**:

1. **`NO_ANOMALY_DETECTED`**: The log window represents expected operational sequence transitions under the validation baseline threshold ($\tau = 3.4791$).
2. **`ANOMALY_ALERT`**: Known-run log transitions exhibit severe conditional loss exceeding the threshold, signaling unexpected state changes, hardware degradation, or kernel failures.
3. **`NOVELTY_DRIFT_REVIEW`**: The window contains insufficient known transitions or an unknown signature rate exceeding the validation threshold ($> 2.0\%$), identifying distribution shifts, software upgrades, or unmapped templates without raising false hardware alarms.

```
+-----------------------------------------------------------------------------------+
|                                 LOG INGESTION                                     |
|  Log Watcher (device:inode:sha256 file_id) ---> POST /api/v1/live/ingest (Batch)  |
+-----------------------------------------------------------------------------------+
                                         |
                                         v
+-----------------------------------------------------------------------------------+
|                              DRAIN3 PARSER (FROZEN)                               |
|  - Strictly matches against frozen templates trained on Train Split (T1)          |
|  - Tree modification disabled at inference (add_log_message never called)         |
|  - Unmatched lines assigned UNKNOWN_TOKEN (1), matched lines -> token [2..615]    |
+-----------------------------------------------------------------------------------+
                                         |
                                         v
+-----------------------------------------------------------------------------------+
|                              KNOWN-RUN LSTM SCORER                                |
|  - Identifies contiguous runs of known tokens (length >= 2)                       |
|  - Evaluates cross-entropy perplexity per run (PyTorch CPU / ONNX Runtime)       |
|  - Computes window transition loss: Score = Sum(Loss) / Sum(Transitions)          |
+-----------------------------------------------------------------------------------+
                                         |
                                         v
+-----------------------------------------------------------------------------------+
|                            THREE-WAY DECISION ENGINE                              |
|           +---------------------------+---------------------------+               |
|           | Unknown Rate > 0.02 OR    | Score >= 3.4791           | Score < 3.4791|
|           | Transitions == 0          |                           |               |
|           +---------------------------+---------------------------+               |
|           |   NOVELTY_DRIFT_REVIEW    |       ANOMALY_ALERT       |   NO_ANOMALY  |
+-----------+---------------------------+---------------------------+---------------+
                                         |
                                         v
+-----------------------------------------------------------------------------------+
|                        TRANSACTIONAL PERSISTENCE & ALERTS                         |
|  - Candidate-copy SQLite WAL state machine                                        |
|  - Hysteresis Alert Policy (Open: tau >= 3.48; Close: 3 windows < 0.70 * tau)     |
|  - Grounded Explanations (per-event loss ranking, line numbers, no SHAP)         |
+-----------------------------------------------------------------------------------+
                                         |
                                         v
+-----------------------------------------------------------------------------------+
|                       OPERATIONAL DASHBOARD & API EXPOSITION                      |
|  - FastAPI (Prometheus /metrics, /alerts, /review, /models, /drift)               |
|  - Streamlit Executive Dark-Theme Command Center (Incidents, Queue, Replay)       |
+-----------------------------------------------------------------------------------+
```

---

## 2. Ingestion Subsystem

### 2.1 File Watcher & Rotation Resilience (`src/zentinel/ingestion/watcher.py`)
The ingestion pipeline handles live rotating log files with defensive crash resilience:
- **File Instance Identification (`file_id`)**: On POSIX and Windows, the watcher identifies log files using:
  $$\text{file\_id} = \text{st\_dev} : \text{st\_ino} : \text{SHA-256}(\text{first line of file})$$
- **Idempotency Key**: Each dispatched chunk or line is identified by:
  $$\text{idempotency\_key} = \text{SHA-256}(\text{file\_id} : \text{byte\_offset})$$
- **File Replacement Detection**: If a watched log file is rotated, replaced, or truncated, the watcher detects the change in inode or first-line hash, resets its state, and flushes pending buffers without crashing.
- **Dead-Letter Protocol**: Only irrecoverable payload syntax errors (`HTTP 400` / `422`) are dead-lettered to disk. Transient network or capacity errors (`HTTP 408`, `429`, `5xx`) undergo exponential backoff retry.

### 2.2 Ingestion API (`src/zentinel/api/live_api.py`)
- Dispatched via `POST /api/v1/live/ingest` (single event) or `POST /api/v1/live/ingest/batch` (up to 500 lines per call).
- Hashed API key authentication (`X-API-Key` via constant-time `hmac.compare_digest`).
- Token-bucket rate limiting per key.
- Lifespan context enforces single-process state integrity (`WEB_CONCURRENCY <= 1`).

---

## 3. Parsing and Matching Subsystem

### 3.1 Frozen Drain3 Template Miner (`src/zentinel/parsing/drain_parser.py`)
- Trained strictly on the 70% chronological training period ($T_1$) with depth 4 and similarity threshold 0.70.
- **Inference Guarantee (Non-Negotiable Rule 3)**: `TemplateMiner.add_log_message()` is never invoked at inference. Only `TemplateMiner.match()` is called.
- Any log message failing template matching is assigned `UNKNOWN_TOKEN` (1) with `event_status="unknown_template"`.
- Matched templates map to pre-computed vocabulary IDs `[2..615]`. `PAD_TOKEN` is reserved at `0`.

---

## 4. Anomaly Scoring & Known-Run Modeling

### 4.1 LSTM Next-Event Predictor (`src/zentinel/models/inference.py`)
- **Architecture**:
  - Input: LongTensor sequence of token IDs.
  - Embedding layer: $616 \to 32$, with `padding_idx=0`.
  - LSTM: 1 layer, hidden dimension 64, `batch_first=True`.
  - Linear projection: $64 \to 616$ output logits.
- **Known-Run Scoring**:
  - In a window of $W = 100$ logs, novel or unmapped tokens break the sequence into contiguous sub-runs of matched tokens:
    $$R_k = [t_1, t_2, \dots, t_L], \quad \forall t_i \ge 2, \quad L \ge 2$$
  - For each run, conditional cross-entropy loss is evaluated on transitions:
    $$\mathcal{L}(R_k) = \sum_{i=1}^{L-1} -\log P(t_{i+1} \mid t_1, \dots, t_i)$$
  - Window anomaly score is the transition-weighted mean:
    $$\text{Score} = \frac{\sum_k \mathcal{L}(R_k)}{\sum_k (L_k - 1)}$$
  - If a window contains no runs of length $\ge 2$, it is marked unscoreable (`score=None`).

### 4.2 ONNX Runtime Acceleration (`src/zentinel/models/onnx_export.py`)
- Exported using ONNX Opset 17 with dynamic sequence length and batch size dimensions.
- Verified exact numerical parity against PyTorch CPU:
  - Max logit difference $< 5.0 \times 10^{-6}$.
  - $100.0\%$ identical window decisions across test datasets.

---

## 5. State Management & Database Synchronization

### 5.1 Candidate-Copy Transaction Pattern (`src/zentinel/api/live_store.py`)
To prevent memory/database divergence during system crashes:
1. When a log line arrives, current window state is cloned into a speculative candidate copy.
2. Ingested events, idempotency keys, and any completed window decisions are committed to SQLite inside an atomic transaction (`BEGIN IMMEDIATE`).
3. Only upon successful commit is the speculative candidate state promoted to in-memory runtime state.
4. SQLite runs in Write-Ahead Logging (`PRAGMA journal_mode=WAL`), enforcing `PRAGMA foreign_keys=ON` and `busy_timeout=5000`.

### 5.2 Schema Migrations (`src/zentinel/api/migrations.py`)
- Numbered, idempotent schema migrations tracked in `schema_migrations`:
  - `001_initial_schema.sql`: Base tables for events, windows, and idempotency.
  - `002_metadata_tracking.sql`: Columns for latency, payload hashes, and source IDs.
  - `003_alerts_and_registry.sql`: Tables for incidents, alert actions, model registry, and API keys.

---

## 6. Incident Policy & Forensic Explanations

### 6.1 Hysteresis Incident Policy (`src/zentinel/alerts/policy.py`)
- **Opening Threshold**: An incident opens when a scorable window produces $\text{score} \ge \tau$ ($3.4791$).
- **Hysteresis Closing Threshold**: To prevent alert flapping during intermittent errors, an incident closes only when 3 consecutive windows fall strictly below $0.70 \times \tau$ ($2.4354$).
- **Severity Mapping**:
  - `LOW`: $\tau \le \text{Score} < 4.5$ (Validation 90th-95th percentile)
  - `MEDIUM`: $4.5 \le \text{Score} < 7.0$ (Validation 95th-99th percentile)
  - `HIGH`: $\text{Score} \ge 7.0$ (Validation 99th+ percentile)

### 6.2 Grounded Forensic Evidence (`src/zentinel/alerts/explanations.py`)
- Evidence is derived strictly from observable physical quantities:
  1. Top-$k$ templates ranked by per-event cross-entropy loss.
  2. First-seen and novel template frequencies.
  3. Window unknown event rate contrasted with the 0.02 threshold.
  4. Exact log line numbers linking directly to the raw payload in storage.
  5. Zero synthetic or SHAP approximations.
