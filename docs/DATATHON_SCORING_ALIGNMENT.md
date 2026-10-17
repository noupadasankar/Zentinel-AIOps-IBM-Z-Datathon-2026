# Datathon Scoring Alignment

How the Zentinel AIOps deliverables map to the evaluation criteria typically applied for the IBM Z Datathon (AI/LLM Application track is **not** applicable — this is a classical MLOps/AIOps system, not an LLM application; the mapping below is against the AIOps/systems track criteria and honest-evidence requirements).

## Requirement → deliverable matrix

| # | Scoring criterion | Where it is satisfied | Evidence type |
| --- | --- | --- | --- |
| 1 | Working codebase | `src/zentinel/*` (ingestion, parsing, features, models, alerts, API), `dashboard/`, `scripts/` | 78 passing tests (`pytest`), lint clean (`ruff`) |
| 2 | Actual model training on real data | Drain3 on training split (frozen, 615 templates), Isolation Forest on training features, LSTM next-event trained on training windows only | `artifacts/manifest.json` SHA-256s, `reports/lstm_validation_report.json`, `drain3_train_summary_0_7.json` |
| 3 | Honest evaluation methodology | Chronological split, validation-only thresholds, walk-forward evaluation, exploratory-test disclosures | `reports/chronological_split_report.json`, `temporal_walk_forward_evaluation.json`, `test_model_comparison.json` (declared caveat fields) |
| 4 | Reproducibility | Committed executed notebooks 00–12, per-notebook JSON reports, `Makefile` targets, pinned `requirements.txt` | `notebooks/notebook_execution_log.json`, `reports/nbXX_*.json` |
| 5 | Real-time ingestion & alerting demo | FastAPI `/api/v1/live/ingest` → frozen Drain3 → known-run LSTM scoring → 3-way policy with hysteresis/severity/cooldown → incidents + SQLite audit | `docs/API.md`, `notebooks/11_streaming_replay_benchmarks.ipynb` (live subprocess run) |
| 6 | Observability/dashboards | Streamlit dashboard, 5 required screens, live-API-first with labelled demo fallback | `dashboard/app.py`, `tests/test_dashboard_feeds.py` |
| 7 | Explainability | Per-event measured cross-entropy attribution, template evidence, raw-line references, unknown-rate reporting — deterministic, no invented narrative | `notebooks/08_explainability_and_alerts.ipynb`, `zentinel/alerts/explanations.py`, tests |
| 8 | Drift handling without silent refit | Unknown-template rate monitoring with novelty threshold (0.02) from validation, review routing (`NOVELTY_DRIFT_REVIEW`), retraining-review queue export; frozen state never refit at inference | `notebooks/09_drift_and_calibration.ipynb`, `reports/lstm_three_way_test_decision_report.json` |
| 9 | Deployment plan for IBM Z | Portability inventory + target verification protocol; **no s390x numbers invented** | `docs/LINUXONE_DEPLOYMENT.md` |
| 10 | Packaging/ship-ability | Final ZIP with SHA-256 verification report; secrets, `.env`, raw 743 MB log, venvs and caches excluded | ZIP verification report (see `reports/FINAL_COMPLETION_REPORT.md`) |

## Headline committed results (all machine-dated and recomputable)

- Walk-forward overall test metrics: F1 0.8333, precision 0.8173, recall 0.8500, automatic coverage 33.2%, review rate 66.8%, false alerts 0.013/hour — over 7,122 exploratory-test windows.
- Three-way decision mix on the test period: 104 `ANOMALY_ALERT`, 2,262 `NO_ANOMALY_DETECTED`, 4,756 `NOVELTY_DRIFT_REVIEW` (unknown-template drift dominates the review route).
- Walk-forward slice 1: coverage 90.7%, precision 0.9811, recall 0.8525, F1 0.9123 (slice table in `reports/temporal_walk_forward_evaluation.json`).
- ONNX parity: exact decision parity with PyTorch zero-threshold mismatch on committed runs (`reports/onnx_parity_report.json`; nb10 re-derives locally on x86-64).

## Honesty guardrails encoded in the deliverables

1. Test labels never used for fitting or threshold selection; declared explicitly in `test_model_comparison.json`.
2. Unknown templates route to review at inference; frozen template state is never updated in the serving path (guarded by a runtime monkey-patch check in nb02).
3. The experimental autoencoder (nb06) is flagged `experimental: true` and cannot enter production without retraining/parity/operator-review gates (`docs/DECISIONS.md`).
4. Every benchmark carries a host disclosure; none claims LinuxONE/s390x execution.

## Known limitations (disclosed up front, not hidden)

- Test period was inspected during unknown-event diagnostics (exploratory); walk-forward protocol reduces but does not eliminate selection risk.
- Aggregate automatic coverage on the final test slice is low (33.2%) because of template drift; the system's design intent is to fail safe to review rather than fabricate verdicts.
- No Docker engine available in the authoring environment: compose validated via native stack, not container-in-container.
