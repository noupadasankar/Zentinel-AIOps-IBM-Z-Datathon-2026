# Zentinel AIOps: Data Card (LogHub BlueGene/L)

## 1. Dataset Summary
- **Dataset Name**: BlueGene/L (BGL) Supercomputer Event Log
- **Collection Source**: Lawrence Livermore National Laboratory (LLNL), BlueGene/L supercomputer (131,072 processors, 64 cabinets).
- **Public Reference**: LogHub benchmark suite (He et al., IEEE TDSC 2020 / ISSRE 2016).
- **Total Log Lines**: 4,747,963 log records.
- **Total Evaluation Windows**: 47,480 consecutive non-overlapping windows of 100 log lines ($W = 100$).
- **Overall Anomaly Ratio**: 7.34% of log lines are labeled anomalous ($348,460 / 4,747,963$); 10.13% of windows contain at least one anomaly ($4,803 / 47,480$).

---

## 2. Chronological Split Boundaries

In distributed systems, random cross-validation leaks future template sequences into past predictions. Zentinel strictly enforces a chronological time split across the source log:

| Dataset Split | Line Number Range | Windows ($W=100$) | Log Lines | Normal Lines | Anomaly Lines | Anomaly Window % |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Train ($T_1$)** | Lines `1` to `3,323,600` | 33,236 | 3,323,600 | 3,061,147 | 262,453 | 10.14% |
| **Validation ($T_2$)**| Lines `3,323,601` to `4,035,800` | 7,122 | 712,200 | 672,471 | 39,729 | 8.86% |
| **Test ($T_3$)** | Lines `4,035,801` to `4,747,963` | 7,122 | 712,163 | 665,885 | 46,278 | 11.26% |

> [!IMPORTANT]
> **Exploratory Designation (Rule 6)**: The test period was inspected during exploratory drift diagnostics to design the three-way decision boundaries. In compliance with strict experimental hygiene, all reported test results are explicitly designated **"exploratory temporal test results"**.

---

## 3. Label Definition and Input Sanitization

### 3.1 Labeling Standard
Each raw BGL log line begins with an alert category tag in the first field:
- `-`: Non-failure (Normal system event).
- Non-dash strings (e.g., `FATAL`, `SEVERE`, `ERROR`, `WARNING`, `FAILURE`): Labeled failure event.
- **Window Label Rule**: A window of 100 lines is labeled anomalous ($Y=1$) if and only if **at least one** log record in that window contains an anomaly tag.

### 3.2 Non-Negotiable Input Separation (Rule 4)
- **Labels are NEVER Model Inputs**: The alert tag in the first column is completely stripped prior to template parsing and tokenization.
- The Drain3 parser and LSTM model see exclusively the timestamp, component ID, and log message body.
- Ground-truth labels are stored strictly in evaluation target vectors to score precision and recall.

---

## 4. Unstructured Schema & Field Extraction

Raw BGL logs conform to the following whitespace-delimited structure:
```
<AlertTag> <Timestamp> <Date> <Node> <PreciseTime> <NodeRepeat> <Component> <Level> <MessageBody>
```

Example raw record:
```
- 1117838570 2005.06.03 R02-M1-N0-C:J12-U11 2005-06-03-15.42.50.363779 R02-M1-N0-C:J12-U11 RAS KERNEL INFO 1445763 double-hummer alignment exceptions
```

- **Extracted Fields**:
  - `AlertTag`: `-` (Normal) vs other (Anomaly target only).
  - `Timestamp`: POSIX epoch timestamp in seconds.
  - `Node`: Hardware node identifier (e.g., `R02-M1-N0-C:J12-U11`).
  - `Component`: Subsystem component (e.g., `KERNEL`, `APP`, `BGLMASTER`, `DISCOVERY`).
  - `Level`: Log level (e.g., `INFO`, `WARNING`, `SEVERE`, `FATAL`).
  - `MessageBody`: Unstructured text processed by Drain3 template miner.

---

## 5. Distribution Shifts and Drift Characteristics

Analysis of the chronological test split reveals significant real-world concept and covariate drift:
1. **Novelty Surge (Test Slice 3, Windows 3,561–5,341)**: A scheduled software maintenance window introduced dozens of previously unseen log templates. Naive binary detectors hallucinate false alarms; Zentinel's three-way decision routes 100% of these windows to `NOVELTY_DRIFT_REVIEW`.
2. **Known-Run Coverage Variance**:
   - Test Slice 1: 90.67% scorable coverage.
   - Test Slice 2: 7.70% scorable coverage.
   - Test Slice 3: 0.00% scorable coverage (pure novelty drift).
   - Test Slice 4: 34.51% scorable coverage.
   - Overall Test Coverage: **33.22% scorable coverage**.

---

## 6. Data Integrity and Privacy
- **Public Domain**: Data originates from the public LogHub repository maintained by CUHK.
- **No PII / Customer Secrets**: Contains only machine hardware diagnostic messages, memory addresses, and kernel registers. No customer data or credentials are present.
