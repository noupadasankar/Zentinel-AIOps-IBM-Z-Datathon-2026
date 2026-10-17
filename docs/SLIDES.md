# Zentinel AIOps: Executive Slide Deck Outline (7 Slides)

---

## Slide 1: Title & Vision
### **Zentinel AIOps: Enterprise Log Anomaly Detection with Three-Way Decision Bounds**
- **Subtitle:** Closing the Reliability Gap Between Deep Learning Models and Critical Infrastructure Operations
- **Key Takeaways:**
  - Built for high-volume supercomputer and enterprise logs (LogHub BlueGene/L benchmark).
  - Uncompromising operational integrity: zero invented metrics, frozen template mining, transactional state.
  - Deploys as a self-contained microservice ecosystem with sub-millisecond scoring latency.
- **Presenter Note:** Frame the project around operational predictability and defensive software engineering.

---

## Slide 2: The Core Problem
### **Why Existing Log Anomaly Detectors Fail in Production**
- **The Binary Decision Trap:** Binary classifiers (0 = normal, 1 = anomaly) conflate *system failure* with *harmless software novelty*, creating crippling alert storms during deployments.
- **Temporal Data Leakage:** K-fold cross-validation inflates benchmark scores by leaking future template transitions into the past.
- **State Divergence & Flapping:** Asynchronous ingestion queues lose state alignment with ACID stores during network partitions, while raw thresholding causes incessant alert flapping.
- **Presenter Note:** Emphasize that log anomaly detection is an operational systems problem as much as an ML problem.

---

## Slide 3: Core Architectural Innovation
### **Three-Way Decisions & The Known-Run Scoring Model**
- **Component 1: Frozen Template Parsing:**
  - Drain3 miner trained strictly on training split ($T_1$) with depth 4 and similarity 0.70.
  - Frozen at inference: no runtime tree modification; unparseable lines cleanly mapped to `UNKNOWN_TOKEN`.
- **Component 2: Known-Run LSTM Scoring:**
  - Evaluates contiguous runs of known event tokens ($\ge 2$) via next-event conditional perplexity.
  - Novel tokens trigger drift review rather than corrupting sequence transitions.
- **Component 3: Three-Way Decision Bounds:**
  - `NO_ANOMALY_DETECTED`: Score below validation threshold ($\tau = 3.4791$).
  - `ANOMALY_ALERT`: Known-run transition loss $\ge \tau$, signalling unexpected sequence transitions.
  - `NOVELTY_DRIFT_REVIEW`: Unknown event rate $> 2.0\%$ or unscoreable window, signalling distribution shift.

---

## Slide 4: Engineering Rigor & Defensive Security
### **Hardening Correctness, Security, and State Synchronization**
- **Robust Ingestion Idempotency:**
  - Watcher key = $\text{SHA-256}(\text{file\_id} : \text{offset})$, where $\text{file\_id} = \text{device} : \text{inode} : \text{SHA-256}(\text{first line})$.
  - Detects file truncation, rotation, and replacement across OS restarts.
  - Server verifies payload digest and returns HTTP 409 on key collision.
- **Candidate Copy Transactional Pattern:**
  - State machine computes speculative updates on candidate copies.
  - Commits atomically to SQLite WAL before updating in-memory inference state.
- **Cryptographic Artifact Manifest:**
  - `manifest.json` tracks SHA-256 digests of all frozen models and thresholds; verifies at startup.
- **Defensive API Security:**
  - Constant-time API key verification (`hmac.compare_digest`), per-key token bucket rate limiting, and strict single-worker enforcement (`WEB_CONCURRENCY <= 1`).

---

## Slide 5: Explainable Forensics & Incident Lifecycle
### **Incident Management Grounded in Real Physical Quantities**
- **Hysteresis Alerting Policy:**
  - Incidents open at threshold $\tau = 3.4791$; close strictly when 3 consecutive windows fall below $0.70 \times \tau$ (2.4354).
  - Eliminates alert flickering during burst anomalies.
- **Grounded Evidence (Zero SHAP Claims):**
  - Top-$k$ templates ranked by per-event cross-entropy loss.
  - Unknown event drift vs 2.0% validation threshold.
  - Critical keyword frequency (`FATAL`, `SEVERE`, `ERROR`).
  - Concrete log line numbers for auditability.
- **Operator Workflow:**
  - In-app ACK / Close actions recorded with timestamps and operator notes in `alert_actions` SQLite audit log.
  - Retraining review queue with single-click CSV export.

---

## Slide 6: Empirical Walk-Forward Evaluation
### **Rigorous Validation Across Consecutive Temporal Slices**
- **Temporal Slicing Methodology:**
  - Evaluated across 4 consecutive test slices over 7,122 test windows (712,163 log lines).
  - Coverage reported explicitly alongside every metric (Rule 1).
- **Key Measured Results (Exploratory Test Set):**
  - **F1 Score:** $83.33\%$ (Precision: $81.73\%$, Recall: $85.00\%$) on scorable windows.
  - **False Alert Rate:** **0.0130 false alerts / hour** (defensible operational signal-to-noise).
  - **Detection Delay:** Median $8.50$ seconds from episode onset to alert (p90 $9,504.5$ s).
  - **95% Confidence Intervals:** F1 $[0.792, 0.871]$ via 500 block-bootstrap resamplings.
- **Presenter Note:** Highlight Slice 3: 100% novelty surge correctly routed to `NOVELTY_DRIFT_REVIEW` rather than false alerts.

---

## Slide 7: Production Packaging, ONNX & Portability
### **Turnkey Deployment and Hardware Readiness**
- **Full Container Ecosystem:**
  - Non-root `Dockerfile`, `docker-compose.yml` (API, Streamlit Dashboard, Watcher, SQLite volume).
  - Artifacts mounted read-only; localhost binding (`127.0.0.1`).
  - Pinned lockfile (`requirements.lock`), comprehensive CI pipeline (`ruff`, `pytest`, `pip-audit`, `gitleaks`).
- **ONNX Runtime Export (Opset 17):**
  - Dynamic sequence/batch dimensions.
  - Verified numerical parity on 1,000 real runs: **Max logit difference $4.77 \times 10^{-6}$**.
  - **100.0% identical window decisions** between PyTorch and ONNX Runtime.
- **Portability & Enterprise Hardware Statement:**
  - Portable Python / ONNX architecture by design.
  - Transparent hardware claim: *"Portable by design; not yet validated on s390x / IBM Z accelerator."*
