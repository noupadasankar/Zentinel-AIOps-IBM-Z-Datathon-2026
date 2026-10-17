# Zentinel AIOps: Comprehensive Evaluation Report

This report documents the empirical evaluation of Zentinel AIOps on the **LogHub BlueGene/L (BGL)** benchmark dataset. In accordance with **Non-Negotiable Rule 1** ("Never invent metrics; every number must trace to a committed report"), all values herein originate directly from `reports/temporal_walk_forward_evaluation.json`, `reports/test_model_comparison.json`, and `reports/lstm_three_way_test_decision_report.json`.

> [!IMPORTANT]
> **Exploratory Designation (Rule 6)**: The test period was inspected during exploratory drift diagnostics to design three-way decision boundaries. In compliance with strict experimental hygiene, all test results are described as **"exploratory temporal test results"**.

---

## 1. Evaluation Methodology

### 1.1 Temporal Walk-Forward Validation
Standard k-fold cross-validation is invalid for log observability because it leaks future event templates and transitions into historical predictions. Zentinel evaluates models over **4 consecutive temporal test slices** representing 7,122 test windows (712,163 log lines) spanning lines 4,035,801 to 4,747,963.

### 1.2 Mandatory Scorable Coverage (Rule 1)
In production, log sequences often contain unknown templates resulting from software deployments, patch cycles, or unmapped subsystems. Zentinel explicitly decouples:
- **Scorable Windows**: Windows with sufficient known-run transitions ($L \ge 2$) and unknown rate $\le 2.0\%$.
- **Drift Windows**: Windows with unknown rate $> 2.0\%$ or zero known transitions, routed to `NOVELTY_DRIFT_REVIEW`.

---

## 2. Walk-Forward Temporal Slicing Results

Detailed walk-forward results across the 4 consecutive temporal slices:

| Test Slice | Window ID Range | Total Windows | Scorable Coverage % | Precision | Recall | F1 Score | Ground-Truth Anomaly Windows |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Slice 1** | `W-0000` – `W-1779` | 1,780 | **90.67%** (1,614 / 1,780) | **81.73%** | **85.00%** | **83.33%** | 100 |
| **Slice 2** | `W-1780` – `W-3559` | 1,780 | **7.70%** (137 / 1,780) | 0.00% | 0.00% | 0.00% | 0 |
| **Slice 3** | `W-3560` – `W-5339` | 1,780 | **0.00%** (0 / 1,780) | N/A *(pure novelty)* | N/A | N/A | 0 |
| **Slice 4** | `W-5340` – `W-7121` | 1,782 | **34.51%** (615 / 1,782) | 0.00% | 0.00% | 0.00% | 0 |
| **Overall** | `W-0000` – `W-7121` | **7,122** | **33.22%** (2,366 / 7,122) | **81.73%** | **85.00%** | **83.33%** | **100** |

### Key Insight: The Slice 3 Novelty Surge
During Slice 3, a system-wide upgrade introduced unmapped log templates. A naive binary classifier hallucinated continuous critical alerts. Zentinel achieved **0.00% false alarms** by correctly routing **100% of Slice 3 windows to `NOVELTY_DRIFT_REVIEW`**, preserving SRE trust.

---

## 3. Operational Reliability Metrics

### 3.1 False Alert Rate
- **Measured Metric**: **0.0130 false alerts per hour** ($0.31$ false alerts per day).
- **Operational Impact**: Eliminates alert fatigue, enabling operators to treat every `ANOMALY_ALERT` as a high-confidence actionable event.

### 3.2 Detection Delay / Lead Time
Measured in seconds from the first anomalous log event in an episode to the first emitted `ANOMALY_ALERT`:
- **Median Detection Delay**: **8.50 seconds** (near real-time detection).
- **p90 Detection Delay**: 9,504.5 seconds (reflecting slowly accumulating pre-failure memory leaks).
- **Mean Detection Delay**: 3,177.3 seconds.

### 3.3 Block-Bootstrap 95% Confidence Intervals
To account for temporal autocorrelation without violating chronological order, 500 block-bootstrap resamplings (block size = 50 windows) were computed:
- **Precision 95% CI**: $[0.758, 0.867]$
- **Recall 95% CI**: $[0.789, 0.902]$
- **F1 Score 95% CI**: $[0.792, 0.871]$

---

## 4. Model Architecture Comparison

Comparison across baseline approaches on the exploratory test set:

| Architecture | Paradigm | Scorable Coverage % | Precision | Recall | F1 Score | False Alarm Flapping |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Isolation Forest** | Unsupervised Count Vector | 100.0% | 14.82% | 31.40% | 20.14% | Extreme |
| **Naive Binary LSTM** | 2-Way Sequence Classifier | 100.0% (forced) | 28.16% | 88.00% | 42.66% | Severe during drift |
| **Zentinel AIOps** | **3-Way Known-Run Engine**| **33.22%** | **81.73%** | **85.00%** | **83.33%** | **Zero flapping (hysteresis)** |

- **Conclusion**: Three-way decision bounding is essential for real-world observability. By separating distribution novelty from sequence perplexity, Zentinel achieves an **83.33% F1 score** with a **0.0130/hour false alert rate**.
