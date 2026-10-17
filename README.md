# Zentinel AIOps: Enterprise Log Anomaly Detection Platform

[![CI](https://github.com/zentinel-aiops/zentinel/actions/workflows/ci.yml/badge.svg)](.github/workflows/ci.yml)
[![Python 3.11](https://img.shields.io/badge/python-3.11-blue.svg)](https://www.python.org/)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![ONNX Opset 17](https://img.shields.io/badge/ONNX-Opset_17-orange.svg)](reports/onnx_parity_report.json)
[![Artifact Integrity: Verified](https://img.shields.io/badge/Artifacts-SHA256_Verified-green.svg)](artifacts/manifest.json)

> **Zentinel AIOps** is a log anomaly detection and observability platform designed for distributed infrastructure. Built on **LogHub BlueGene/L**, Zentinel couples **frozen Drain3 template mining** with a **known-run next-event LSTM** to deliver bounded **three-way operational decisions**: `NO_ANOMALY_DETECTED`, `ANOMALY_ALERT`, and `NOVELTY_DRIFT_REVIEW`.

---

## Concepts and Data Flow

```
log file (BGL line) ──▶ FILE WATCHER ──▶ POST /api/v1/live/ingest ──▶ Drain3 frozen matcher
     (tail + cursor)        (X-API-Key)         (idempotency)          └─▶ token ─┐
                                                                                  ▼
 dashboard ◀── GET /api/v1/dashboards/summary ◀── SQLite WAL store ◀── known-run LSTM scoring
 (live UI)        (single aggregated feed)          (transactions)     └─▶ three-way decision
```

---

## 5-Command Quick Start

Spin up the complete platform (Live FastAPI Engine, Dark-Theme Operations Dashboard, and Log Watcher) in under 2 minutes:

```bash
# 1. Clone the repository
git clone https://github.com/zentinel-aiops/zentinel.git && cd zentinel

# 2. Configure environment from template
cp .env.example .env

# 3. Launch full stack via Docker Compose
docker compose up -d --build

# 4. Verify system health
curl -s http://127.0.0.1:8000/health

# 5. Open the Operations Command Center
# Navigate to: http://127.0.0.1:8501
```

*To run natively on Python instead of Docker (the `zentinel` package is a `src/`-layout package — install it editable before starting the server):*
```bash
pip install -r requirements.lock
pip install -e . --no-deps        # installs the zentinel package from src/ (also done by `make install`)
pytest -ra
python -m uvicorn zentinel.api.live_api:app --host 127.0.0.1 --port 8000 --workers 1
streamlit run dashboard/app.py --server.port 8501
```

*Optional — tail a log file through the live ingestion pipeline (same command the Docker watcher uses):*
```bash
python -m zentinel.ingestion.watcher data/raw/sample_replay.log \
    --api-url http://127.0.0.1:8000 \
    --api-key your-ingest-key \
    --from-start
```

---

## Measured Performance & Empirical Results

All metrics below originate directly from committed reports in [`reports/`](reports/):

### 1. Temporal Walk-Forward Validation Table (Test Period)
*Evaluated across 4 consecutive chronological test slices (7,122 test windows / 712,163 log lines).*
*Context: **Exploratory temporal test set** (inspected during drift diagnostics).*

| Evaluation Slice | Window ID Range | Scorable Coverage % | Precision | Recall | F1 Score | Ground-Truth Anomaly Windows | Decision Routing Insight |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Slice 1** | `W-0000` – `W-1779` | **90.67%** (1,614 / 1,780) | **81.73%** | **85.00%** | **83.33%** | 100 | High known coverage; clean anomaly capture. |
| **Slice 2** | `W-1780` – `W-3559` | **7.70%** (137 / 1,780) | 0.00% | 0.00% | 0.00% | 0 | Drift onset; unknown templates emerge. |
| **Slice 3** | `W-3560` – `W-5339` | **0.00%** (0 / 1,780) | N/A *(drift)*| N/A | N/A | 0 | **100% routed to `NOVELTY_DRIFT_REVIEW`**; 0 false alarms. |
| **Slice 4** | `W-5340` – `W-7121` | **34.51%** (615 / 1,782) | 0.00% | 0.00% | 0.00% | 0 | Partial recovery; novel templates stabilize. |
| **Overall Aggregate**| `W-0000` – `W-7121` | **33.22%** (2,366 / 7,122) | **81.73%** | **85.00%** | **83.33%** | **100** | **0.0130 False Alerts / Hour**. |

*Source: [`reports/temporal_walk_forward_evaluation.json`](reports/temporal_walk_forward_evaluation.json)*

### 2. Operational Reliability & Lead Time
- **False Alert Rate**: **0.0130 false alerts / hour** (0.31 / day), eliminating operator alert fatigue.
- **Detection Delay / Lead Time**: Median **8.50 seconds** from episode onset to alert (p90: 9,504.5 s).
- **95% Block-Bootstrap CIs** (500 resamples): F1 Score **[0.792, 0.871]** (Precision [0.758, 0.867], Recall [0.789, 0.902]).

### 3. Ingestion Throughput Benchmark
*Measured on TEST-period lines (line 4,035,801 onwards) across 3 repeated trials of 60 seconds each:*
- **Sequential (1 worker)**: **101.13 ± 1.55 events/s** | Latency: p50 = **2.21 ms**, p95 = **6.03 ms**, p99 = **21.50 ms** (Peak RAM: 290.78 MB).
- **8-way Concurrent**: **100.00 ± 0.02 events/s** | Latency: p50 = **24.11 ms**, p95 = **69.56 ms**, p99 = **106.92 ms** (Peak RAM: 328.63 MB).
- *Source: [`reports/benchmark_report.json`](reports/benchmark_report.json)*

### 4. ONNX Runtime Export & Numerical Parity (Opset 17)
- Evaluated on **1,000 real runs** and **918 test windows**:
  - **Max Logit Difference**: **$4.77 \times 10^{-6}$** (< 5e-6).
  - **Decision Agreement**: **100.00% identical window decisions** between PyTorch CPU and ONNX Runtime.
  - **PyTorch CPU Latency**: 0.501 ms mean per run.
  - **ONNX Runtime CPU Latency**: 0.645 ms mean per run.
  - *Source: [`reports/onnx_parity_report.json`](reports/onnx_parity_report.json)*

---

## Architectural Highlights

```
+-------------------------------------------------------------------------------------------------+
|                                 THREE-WAY OPERATIONAL DECISION                                  |
+------------------------------------+--------------------------------+---------------------------+
|        NOVELTY_DRIFT_REVIEW        |         ANOMALY_ALERT          |    NO_ANOMALY_DETECTED    |
| (Unknown Rate > 2% or Transitions < 2)| (Score >= 3.4791 on Known Runs)|   (Score < 3.4791 Normal) |
+------------------------------------+--------------------------------+---------------------------+
```

1. **Frozen Template Mining**: Trained strictly on the 70% chronological training split ($T_1$). `TemplateMiner.add_log_message()` is **never** called at inference (Non-Negotiable Rule 3). Unmatched logs cleanly map to `UNKNOWN_TOKEN` (1).
2. **Known-Run LSTM Scoring**: Sequence transitions are evaluated over contiguous sub-runs of known tokens ($L \ge 2$), computing conditional cross-entropy loss without allowing unmapped templates to corrupt transition probabilities.
3. **Transactional Candidate-Copy Persistence**: State updates are computed on speculative copies and committed atomically to SQLite WAL before being promoted to in-memory runtime state, eliminating state/DB divergence.
4. **Hysteresis Alert Policy**: Alerts open at $\tau = 3.4791$ and close strictly when 3 consecutive windows fall below $0.70 \times \tau$ ($2.4354$), completely eliminating alert flapping.
5. **Grounded Forensic Evidence**: Incident evidence is derived purely from physical quantities (per-event cross-entropy loss, template frequency, raw line references). **Zero hallucinated metrics; no synthetic SHAP claims.**

---

## Documentation Suite

Comprehensive engineering documentation is available in [`docs/`](docs/):

- 🏛️ [**System Architecture**](docs/ARCHITECTURE.md): End-to-end design, ingestion topology, state machine, and data flow.
- 📊 [**Dataset Card**](docs/DATA_CARD.md): LogHub BGL schema, chronological boundaries, label masking, and drift characteristics.
- 🧠 [**Model Card**](docs/MODEL_CARD.md): LSTM architecture, hyperparameters, calibration, and ONNX opset 17 specifications.
- 🔌 [**REST API Reference**](docs/API.md): Complete OpenAPI endpoint documentation, authentication, curl examples, and JSON schemas.
- 🛡️ [**Security Architecture**](docs/SECURITY.md): Threat model, constant-time key validation, SQL injection defense, and non-root container isolation.
- 🧪 [**Notebook Laboratory**](notebooks/README.md): 13 fully executed research/verification notebooks (00–12) with per-notebook JSON reports.
- 🐧 [**LinuxONE Deployment**](docs/LINUXONE_DEPLOYMENT.md): s390x portability inventory and target-side verification protocol (no on-device claims made).
- 🎯 [**Scoring Alignment**](docs/DATATHON_SCORING_ALIGNMENT.md): Requirement-by-requirement mapping to the datathon criteria with evidence pointers.
- 🚀 [**Operations & Runbook**](docs/OPERATIONS.md): Deployment, numbered migrations, retention purge, backup procedures, and troubleshooting.
- 📈 [**Evaluation Report**](docs/EVALUATION.md): Walk-forward temporal evaluation, detection delay, false alert rate, and CI methodology.
- ⚖️ [**Architectural Decisions (ADR)**](docs/DECISIONS.md): Record of design decisions (ADR-001 through ADR-011).
- 📜 [**Changelog**](CHANGELOG.md): Versioned release history adhering to Keep a Changelog.
- 🎬 [**2-Minute Demo Script**](docs/DEMO_SCRIPT.md): Step-by-step executive walkthrough script.
- 📑 [**Executive Slide Deck**](docs/SLIDES.md): 7-slide architectural presentation outline.

---

## Hardware Portability & Enterprise Readiness

> [!NOTE]
> **IBM Z / LinuxONE / s390x Statement (Rule 5)**:
> Zentinel AIOps is **portable by design** across standard x86-64, ARM64, and Linux enterprise environments.
> **Not yet validated on s390x / IBM Z accelerator.** In accordance with Non-Negotiable Rule 5, no performance claims are asserted without a measured run on that specific hardware.

---

## Platform Limitations

1. **Exploratory Test Data**: The test period was inspected during exploratory drift diagnostics to engineer the three-way decision boundary.
2. **Single-Worker Concurrency**: The live ingestion state machine requires serialized atomic transactions (`WEB_CONCURRENCY=1`). Multi-worker scaling requires horizontal stream partitioning by source ID.
3. **Coverage Boundary**: During severe template drift surges (e.g., Test Slice 3), automatic known-run scoring coverage falls to 0%, intentionally delegating windows to the Retraining Review Queue rather than emitting unreliable anomaly scores.
