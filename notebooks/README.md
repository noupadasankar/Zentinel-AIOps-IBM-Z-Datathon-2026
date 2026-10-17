# Zentinel AIOps — Notebooks (00–12)

Real, **executed** Jupyter notebooks (`kernel: python3`, all cells fully executed, no placeholders). Every figure and metric is computed from the repository's data/artifacts at execution time; per-notebook machine-readable results are exported to `reports/nbXX_*.json`. Execution status and durations live in `notebook_execution_log.json`.

| Notebook | Purpose |
|---|---|
| `00_environment_and_data_contracts.ipynb` | Environment probe, artifact manifest verification (SHA-256), contract checks; writes `reports/nb00_run_manifest.json`. |
| `01_data_exploration.ipynb` | Sample-level EDA over `data/processed/bgl_parsed_sample.jsonl`, class imbalance, cross-check vs `window_feature_summary.json`. |
| `02_log_parsing_and_templates.ipynb` | Frozen Drain3 state audit (615 templates), production `parse_bgl_line` probes, unknown-template scan of validation/test lines (single pass over `data/raw/BGL.log`), never refits. |
| `03_temporal_feature_engineering.ipynb` | Window features + chronological splits; leakage guards (window-id contiguity, monotonic start lines, temporal order, label-not-a-feature, zero PAD/UNK in training windows). |
| `04_isolation_forest_baseline.ipynb` | Loads the frozen Isolation Forest via `joblib`, reproduces validation scores to 1e-9, exploratory test metrics + PR curve + FP analysis. |
| `05_lstm_next_event_model.ipynb` | Loads the production next-event LSTM checkpoint; score parity vs committed validation scores; known-run scoring parity and three-way decision parity on real test windows; latency stats. |
| `06_lstm_autoencoder_experiment.ipynb` | **EXPERIMENTAL** smoke-scale reconstruction autoencoder; clearly flagged, never production-promoted. |
| `07_model_evaluation_and_comparison.ipynb` | Both models on the same 7,122 test windows: P/R/F1/PR-AUC/ROC-AUC, confusion matrices, PR curves, runtime table; exports `data/processed/metrics_summary.csv`. |
| `08_explainability_and_alerts.ipynb` | Replays all test decisions through the live `IncidentPolicy`, deep-dive evidence (real BGL lines re-parsed through the production pipeline; token parity asserted); exports `alert_evidence.csv`. |
| `09_drift_and_calibration.ipynb` | Unknown-rate/coverage timelines, validation-only threshold sensitivity grid, feature drift, clearly-labelled synthetic drift scenario; exports `drift_report.csv` + `retraining_review_queue.csv`. |
| `10_onnx_export_and_parity.ipynb` | ONNX opset 17 graph checks, PyTorch↔ONNXRuntime logit/loss/decision parity on real runs, latency microbenchmarks **on this host (x86-64)** — no s390x claims. |
| `11_streaming_replay_benchmarks.ipynb` | Launches the real FastAPI ingest server on a scratch SQLite DB and replays test-period BGL lines sequentially and 8-way concurrent; p50/p95/p99 latency + throughput. |
| `12_final_results_and_figures.ipynb` | Consolidates all committed + notebook reports into the final results view, limitations, and exact reproduction commands. |

## Re-executing

From the repository root (requires the dataset + artifacts present and the environment from `docs/IMPLEMENTATION_STATUS.md`):

```bash
jupyter nbconvert --to notebook --execute notebooks/00_environment_and_data_contracts.ipynb
```

Notebooks 02, 08 and 11 read `data/raw/BGL.log` (not shipped in archives — see ZIP verification report). No notebook mutates frozen production state: template refits, threshold reselection on test data, and production-model overwrites are guarded by explicit assertions.
