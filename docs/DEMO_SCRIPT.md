# Zentinel AIOps: 2-Minute Executive Demo Script

**Target Audience:** SRE Leads, Infrastructure Architects, AIOps Engineering Teams  
**Duration:** Exactly 2 minutes (120 seconds)  
**Setup:** Run `docker compose up` or `make demo` to open `http://127.0.0.1:8501`.

---

## Timeline & Narration

### [0:00 - 0:25] The Problem & Three-Way Decisions (Screen: Overview / Home)
- **Visual Cue:** Show Zentinel Dark-Theme Dashboard Overview page. Point to the top incident banner and KPI cards.
- **Spoken:**
  > "Welcome to Zentinel AIOps. Traditional log anomaly tools drown operators in false positives or go blind during system upgrades because they force binary 0/1 decisions. Zentinel introduces a robust **three-way decision engine**:
  > 1. `NO_ANOMALY_DETECTED` for expected system behavior,
  > 2. `ANOMALY_ALERT` when event transition perplexity spikes on known run paths, and
  > 3. `NOVELTY_DRIFT_REVIEW` when unknown log signatures spike above 2%, cleanly separating distribution drift from actionable incidents."

---

### [0:25 - 0:50] Real-Time Telemetry & Hysteresis (Screen: Overview Telemetry Chart)
- **Visual Cue:** Scroll down to the dual-axis Plotly chart. Highlight the yellow validation threshold ($\tau = 3.4791$) and the orange unknown rate line.
- **Spoken:**
  > "Notice our live telemetry stream. Windows of 100 log lines are parsed by frozen Drain3 templates and scored by our next-event LSTM.
  > Unlike naive thresholding, Zentinel implements **hysteresis alerting**: an incident opens at score $\ge 3.48$, but only resolves after 3 consecutive windows fall strictly below $0.70 \times \tau$ (2.44). This eliminates alert flapping and fatigue during intermittent burst errors."

---

### [0:50 - 1:20] Explainable Forensic Evidence (Screen: INVESTIGATE -> Incidents)
- **Visual Cue:** Click on `INVESTIGATE -> Incidents` in the left sidebar. Open active incident `INC-0001`. Expand "Forensic Evidence & Physical Quantities".
- **Spoken:**
  > "When an incident fires, operators need actionable evidence immediately. Zentinel provides **zero-hallucination, SHAP-free forensics** grounded purely in physical quantities:
  > - Exact per-event cross-entropy loss ranking the top surprising templates,
  > - Verification of first-seen template rates against our 0.02 threshold,
  > - Concrete log line references directly pinpointing the underlying kernel failure.
  > An operator can acknowledge or close the incident directly, logging an immutable audit action into SQLite."

---

### [1:20 - 1:45] Retraining Curation & Drift Safety (Screen: INVESTIGATE -> Review Queue)
- **Visual Cue:** Click on `INVESTIGATE -> Review Queue`. Point to unmatched signatures and click "Export Review Queue (.CSV)".
- **Spoken:**
  > "When novel software versions introduce new log templates, Zentinel does not break or misclassify them as hardware alerts. They flow automatically into the **Retraining Review Queue**.
  > From here, ML engineers can inspect signature frequency, download a curated CSV, and schedule an offline Drain3 mining job without corrupting the frozen production weights."

---

### [1:45 - 2:00] Production Packaging & Verified Performance (Screen: OBSERVE -> Model Evaluation)
- **Visual Cue:** Navigate to `OBSERVE -> Model Evaluation`. Highlight the walk-forward evaluation table and false alert rate.
- **Spoken:**
  > "Everything you see is measured, tested, and containerized:
  > - Verified walk-forward temporal evaluation with **0.013 false alerts per hour**,
  > - Strict numerical parity with ONNX Runtime (< 5e-6 max logit difference, opset 17),
  > - End-to-end containerization ready in under 2 minutes with `docker compose up`.
  > Zentinel brings enterprise-grade predictability and mathematical rigor to log anomaly detection."
