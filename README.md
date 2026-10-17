
# Zentinel AIOps

Real-time log anomaly detection and novelty/drift monitoring for enterprise infrastructure.

## Architecture

BGL log input → BGL parser → Frozen Drain3 templates → Event vocabulary → 100-line windows → LSTM known-event scoring + novelty checks → Three-way decisions → FastAPI → SQLite → Streamlit dashboard.

## Decision Types

- **ANOMALY_ALERT:** The LSTM score exceeds the saved threshold and novelty review does not take precedence.
- **NO_ANOMALY_DETECTED:** The score is below the threshold and the novelty-review rule is not triggered.
- **NOVELTY_DRIFT_REVIEW:** Unknown-event drift is high, or there are insufficient known-event transitions for reliable scoring.

These decisions are model outputs, not confirmed real-world incidents.

## Features

- BGL system-log parsing using Drain3.
- Sliding-window feature extraction.
- Isolation Forest baseline and LSTM next-event prediction.
- Novelty and template-drift monitoring.
- FastAPI inference service.
- Streamlit live monitoring dashboard.
- SQLite persistence for event metadata and decisions.
- Log-file replay and monitoring.

## Technology Stack

- **Language:** Python 3.11
- **Log parsing:** Drain3
- **Machine learning:** Scikit-learn, PyTorch
- **API:** FastAPI, Uvicorn
- **Dashboard:** Streamlit, Plotly
- **Storage:** SQLite
- **Dataset:** LogHub BGL

## Installation

Create and activate a virtual environment:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

Install dependencies and run tests:

```powershell
python -m pip install -r requirements.txt
python -m pytest -q
```

## Run Locally

Start the API in the first terminal:

```powershell
python -m uvicorn zentinel.api.live_api:app --app-dir src --host 127.0.0.1 --port 8001
```

Start the dashboard in a second terminal:

```powershell
python -m streamlit run dashboard/live_app.py --server.port 8502
```

Start the log-file watcher in a third terminal:

```powershell
python scripts/follow_log_file.py
```

Dashboard: http://localhost:8502

API documentation: http://127.0.0.1:8001/docs

## Dataset and Evaluation

Zentinel uses the public LogHub BGL dataset. The project uses chronological data splits and frozen Drain3 templates for event matching.

The test period previously showed substantial template drift and limited automatic decision coverage. Test data was inspected for drift diagnostics, so results should be described as exploratory rather than a completely untouched blind evaluation.

Evaluation should report precision, recall, F1-score, PR-AUC, false-positive rate, scoring coverage, detection latency, and end-to-end processing latency where measured.

## Persistence and Reliability

SQLite persists event metadata, counters, unfinished windows, completed decisions, and idempotency responses. Raw log lines are not stored in the event table.

Exactly-once ingestion across all crash scenarios, automatic operating-system log discovery, production authentication, multi-user stream isolation, and incident-management integrations are not yet guaranteed.

## IBM Z / LinuxONE Deployment

LinuxONE deployment, compatibility, and platform-specific performance must be tested before claiming them as completed. ONNX export and IBM Z AI accelerator execution must also be verified before presenting them as supported features.

## Open-Source Resources

- LogHub: https://github.com/logpai/loghub
- Drain3: https://github.com/logpai/Drain3
- PyTorch: https://github.com/pytorch/pytorch
- Scikit-learn: https://github.com/scikit-learn/scikit-learn
- FastAPI: https://github.com/fastapi/fastapi
- Streamlit: https://github.com/streamlit/streamlit

## Project Status

Zentinel is a research prototype under active development. Its performance, reliability, and deployment compatibility must be validated before production use.
