# Zentinel AIOps — IBM Z Datathon 2026

Real-time operational-log anomaly detection and incident-investigation platform. Ingests raw log lines, parses them against a **frozen** template state, builds temporal windows, scores them with a PyTorch LSTM next-event model and an Isolation Forest, applies a deterministic three-way decision policy, groups alert windows into evidence-backed incidents, and exposes everything through a FastAPI backend and a Streamlit operations dashboard.

- **Status:** complete, 78/78 tests passing, lint clean, acceptance-verified package
- **Target platform:** IBM Z / LinuxONE (portable by design; **not yet validated on s390x** — no on-device claims made)
- **Final artifact:** `Zentinel-AIOps-IBM-Z-Datathon-2026.zip` — SHA-256 in [`reports/ZIP_VERIFICATION.md`](reports/ZIP_VERIFICATION.md)

---

## 1. Pipeline overview

```
raw log line ──► POST /api/v1/live/ingest ──► parse (frozen Drain3, 615 templates)
             ──► token 0=PAD 1=UNKNOWN 2..615 ◄── unknown → UNKNOWN, never refit
             ──► 100-line window (47,480 windows, 654 features over the dataset)
             ──► Isolation Forest (647-dim features) + LSTM known-run scoring
             ──► three-way decision ──► incident policy (hysteresis/cooldown/dedupe)
             ──► SQLite persistence (WAL) ──► API ──► dashboard ──► operator actions (audit)
```

| Module | Path | Responsibility |
|---|---|---|
| config + artifact integrity | `src/zentinel/config.py` | paths from env, `verify_manifest()` SHA-256 check at startup |
| ingestion | `src/zentinel/ingestion/` | idempotency (payload-mismatch → 409), Retry-After backoff, truncation/rotation detection, dead-letter, retention |
| parsing | `src/zentinel/parsing/drain_parser.py` | `parse_bgl_line`, frozen `TemplateMiner.match(..., never-refit guard)`, UNK mapping |
| features | `src/zentinel/features/` | window identity, template counts, entropy, unknown-rate |
| models | `src/zentinel/models/` | `InferenceEngine` (IF + LSTM + thresholds), `score_known_runs` (UNK/PAD break runs), ONNX opset-17 export |
| alerts | `src/zentinel/alerts/` | three-way policy, hysteresis, cooldown, dedupe, severity, incident lifecycle, measured evidence |
| API | `src/zentinel/api/` | FastAPI routes, schemas, auth, rate limiting, single-worker state discipline |
| dashboard | `dashboard/app.py` | 10 operator pages, live-API-first, labelled demo fallback |
| research | `notebooks/00–12.ipynb`, `scripts/` | executed laboratory + training/eval pipelines |

---

## 2. Data (LogHub BGL)

| Item | Value |
|---|---|
| Source | LogHub **BGL** telemetry, 4,747,963 labelled lines (`data/raw/BGL.log`, 743 MB — not distributed; re-obtain per the dataset card section below) |
| Chronological split 70/15/15 | train lines 1–3,323,600 (33,236 windows) · validation 3,323,601–4,035,800 (7,122) · test 4,035,801–4,747,963 (7,122; 802 anomalous) |
| Windows | 100 lines → 47,480 windows × 654 features |
| Frozen templates | **615** (training split only) |
| Shipped in ZIP | `data/processed/bgl_parsed_sample.jsonl` (14.5 MB), `bgl_window_features.parquet` (6.9 MB), sequences npz, per-window scores/decisions, `data/raw/sample_replay.log` demo fixture |

Data card (schema & semantics): BGL lines are `<label>` + `<timestamp>` + node/card/LBA fields + free-form message; a window is `target_anomaly=1` when any line carries a labelled alert class. Sequences use tokens 0=PAD, 1=UNKNOWN, 2–615 known. Regeneration chain:

```bash
python scripts/parse_bgl_full.py
python scripts/build_event_sequences_v2.py
python scripts/build_window_features.py
python scripts/train_isolation_forest.py
python scripts/train_lstm_predictor.py
python scripts/score_lstm_known_runs.py
python scripts/build_three_way_decisions.py
```

---

## 3. Models and frozen thresholds

| Model | Config | Threshold (validation-selected; test never used for selection) |
|---|---|---|
| LSTM next-event (production) | vocab 616, embed 32, hidden 64, window 100, seed 42, 8 epochs, lr 1e-3, trained on normal training windows only | known-run score ≥ **3.479122797648112** |
| Isolation Forest | 200 trees, seed 42, `joblib.load` (STACK_GLOBAL pickle — load via joblib, not plain pickle) | score ≥ **−0.077330** (max validation F1, ties → precision → higher threshold) |
| Drain3 state | frozen `.bin` (615 templates) | never refit at inference (runtime monkey-patch guard in NB 02) |
| LSTM autoencoder | **EXPERIMENTAL**, smoke fixture only, `"experimental": true` flag in artifact | never promoted |

Every artifact is hashed in `artifacts/manifest.json`; the API refuses to start on mismatch.

---

## 4. Three-way decision policy

| Decision | Condition |
|---|---|
| `ANOMALY_ALERT` | scorable known-run window with score ≥ τ and unknown rate ≤ novelty limit |
| `NO_ANOMALY_DETECTED` | scorable window, score < τ |
| `NOVELTY_DRIFT_REVIEW` | unknown-template rate > **0.02** (validation q95) or transitions < 2 — human review, never silently normal |

- Hysteresis: open at τ; close after 3 consecutive windows < 0.70·τ (2.4354).
- Cooldown suppresses flapping; dedupe suppresses re-alerts.
- Incident states OPEN → ACKNOWLEDGED → CLOSED (+ false-positive marking), validated server-side with full audit history.
- Every decision reproducible from window id + feature data + model artifact hash + frozen threshold + template-state + policy version.

---

## 5. API (FastAPI)

| Endpoint | Purpose |
|---|---|
| `GET /health`, `GET /ready` | liveness; readiness = engine + persistence |
| `POST /api/v1/live/ingest` | single line; idempotent; payload mismatch → 409 |
| `POST /api/v1/live/ingest/batch` | bounded batch, per-record validation, partial-failure semantics |
| `GET /api/v1/live/status` | counters |
| `GET /api/v1/live/windows` | paginated scored windows |
| `GET /api/v1/alerts` · `/{id}` | incident list/detail |
| `POST /api/v1/alerts/{id}/ack` · `/close` | operator actions (audit-logged) |
| `GET /api/v1/review` (`format=csv`) | novelty-review queue + export |
| `GET /api/v1/drift` · `/models` · `/metrics` | drift data, model registry, real measured counters |

Interactive OpenAPI: `http://127.0.0.1:8000/docs` — all schemas, limits, error envelopes, request IDs, auth requirements live there. Security: sha256-key auth (`ZENTINEL_API_KEY_SHA256`), 100 rps default limiter with `Retry-After`, strict CORS, bounded payloads, non-root container runtime user.

---

## 6. Dashboard (10 pages, real API data)

Dark navy theme; cyan accents; amber = review, red = alert. Sidebar groups:

- **Overview** — health banner, KPI strip (events processed, windows scored, auto-coverage, review share, open incidents), score timeline with threshold line, decision mix, top patterns, recent activity.
- **Incidents (N)** — filterable list + detail: severity/state/peak score/model+threshold versions, supporting templates, raw log-line references, related windows, evidence summary; working Acknowledge / Close / False-positive buttons (real API calls, errors surfaced); audit history; CSV export; printable report.
- **Review Queue** — unmatched/first-seen patterns, frequencies, timestamps, source filters, CSV export of retraining candidates (never mutates frozen state).
- **Windows Browser** — split filter, range slider, paginated scored-window table.
- **Stream & Composition** — live token composition and known-run charts.
- **Drift Diagnostics** — unknown-rate timeline vs 0.02 threshold, coverage metrics.
- **Model Evaluation** — model registry, comparison tables, PR curve, confusion matrix, split metadata, artifact hashes, benchmark results (test metrics labelled exploratory).
- **Thresholds & Models** — threshold lines + provenance (offline-safe registry caption).
- **API Keys & Security** — masked key administration.
- **Settings & Database** — admin/DB ops with feedback.

Sidebar: connection badge (`API Connected (Live)` / `Offline Demo Mode`), auto-refresh (5 s), **Stream Replay** control (file 100–5,000 lines → real `/ingest/batch`). Every page has loading/empty/error states; demo fixtures are labelled and used only when the API is down.

---

## 7. Evaluation results (from committed reports)

| Metric | Value | Source |
|---|---|---|
| Walk-forward test F1 | **0.8333** (95 % CI [0.6430, 0.9489]) | `reports/temporal_walk_forward_evaluation.json` |
| Walk-forward precision / recall | 0.8173 / 0.8500 | same |
| Automatic coverage / review share | 0.3322 / 0.6678 (slice 1: 90.7 % coverage, F1 0.9123) | same |
| False alerts | 0.0130/h | same |
| Three-way mix (test, 7,122 windows) | 104 alerts · 2,262 normal · 4,756 review | `reports/lstm_three_way_test_decision_report.json` |
| Isolation Forest (exploratory test) | P 0.3228 · R 0.0636 · ROC-AUC 0.8517 · PR-AUC 0.3501 | `reports/nb04_isolation_forest_report.json` |
| LSTM parity (production reload) | window scores ≤ 1.9e-06 diff · decisions 100 % agree · 0.469 ms/window | `reports/nb05_lstm_checks.json` |
| ONNX parity (opset 17) | 1,000 runs max logit diff 4.77e-06 · 918/918 identical decisions | `reports/onnx_parity_report.json` |
| Ingestion benchmark (60 s × 3) | sequential 101.13 ± 1.55 ev/s (p50 2.21 ms) · 8-client 100.00 ev/s (p50 24.11 ms) | `reports/benchmark_report.json` |
| NB 11 replay (8 s × 2 per mode) | seq median 110.25 ev/s · conc-8 median 100.25 ev/s · 0 hard errors | `reports/nb11_replay_benchmark.json` |

Incident replay across all 7,122 test decisions (NB 08): 53 incidents opened, 52 closed, 194 updated windows, 30 cooldowns; peak-alert forensics re-parsed live from raw BGL lines with **0 token mismatches** (`reports/nb08_explainability_report.json`, evidence export `data/processed/alert_evidence.csv`).

Label discipline: test labels used for fitting/threshold selection = **never**; exploratory diagnostics = declared inside each affected report.

---

## 8. Notebook laboratory (13/13 executed PASS)

| NB | Topic | Evidence file |
|---|---|---|
| 00 | environment, artifact manifests, contract checks | `reports/nb00_run_manifest.json` |
| 01 | data exploration, imbalance, report cross-checks | `reports/nb01_data_quality_report.json` |
| 02 | frozen template audit (615), unknown scan, no-refit guard | `reports/nb02_template_state_report.json` |
| 03 | features/splits + leakage guards (contiguity, order, label-not-a-feature) | `reports/nb03_split_manifest.json` |
| 04 | Isolation Forest baseline + parity ≤ 1e-9 | `reports/nb04_isolation_forest_report.json` |
| 05 | production LSTM + parity + latency | `reports/nb05_lstm_checks.json` |
| 06 | experimental autoencoder (flagged, non-production) | `reports/nb06_autoencoder_experiment.json` |
| 07 | model comparison + confusion + PR curves | `reports/nb07_model_comparison_expanded.json` |
| 08 | incident replay + live raw-log evidence (0 mismatches) | `reports/nb08_explainability_report.json` |
| 09 | drift timelines + validation-only threshold grid + synthetic scenario | `reports/nb09_drift_report.json` |
| 10 | ONNX export/parity re-derivation | `reports/nb10_onnx_parity_local.json` |
| 11 | live-API ingestion benchmark | `reports/nb11_replay_benchmark.json` |
| 12 | final consolidation + reproduction commands | `reports/nb12_final_summary.json` |

Re-execute any notebook standalone:

```bash
jupyter nbconvert --to notebook --execute notebooks/05_lstm_next_event_model.ipynb
```

(NB 02/08/11 additionally need `data/raw/BGL.log`; the shipped demo replay works without it.)

---

## 9. Quality gates (executed, dated 2026-10-18)

| Gate | Result |
|---|---|
| pytest (parser, drain matcher, scorer, policy, store, API, watcher, ONNX, dashboard feeds) | **78 passed** |
| ruff | clean |
| pip-audit 2.10.1 | clean after pytest 9.1.1 upgrade (PYSEC-2026-1845 fixed) |
| local secret scan (109 files) | 0 findings |
| artifact manifest verification | clean in repo and inside the ZIP |
| CI workflow (`.github/workflows/ci.yml`) | valid config (2 jobs) — not executed in-session (no runner) |
| Docker | daemon unavailable on this host — verified via the native server path (NB 11) |
| LinuxONE validation | not performed — portable-by-design; target protocol documented below |

---

## 10. LinuxONE (s390x) status

**Portable by design; not yet validated on s390x.** No host was available, so nothing is claimed. Executed-on-device evidence collection steps: record `uname -m`, install Python 3.11 + `requirements.lock` + `pip install -e . --no-deps`, verify `artifacts/manifest.json` SHA-256s, run the API, `GET /ready`, re-run notebooks 05/10 (self-checking parity), then run the 60 s × 3 benchmark protocol and archive the notebook outputs + telemetry with the run. No Telum/AI-accelerator path exists in the codebase and none is claimed.

---

## 11. Operations quick reference

```bash
make install          # requirements.lock + editable zentinel package
make lint             # ruff
make test             # pytest suite
make train            # full frozen-pipeline retrain (requires data/raw/BGL.log; CPU-hour-scale)
make evaluate         # walk-forward evaluation reports
make export-onnx      # rebuild ONNX artifact + parity report
make benchmark        # 60 s × 3 ingest benchmark against a running API
make run-api          # uvicorn (single worker enforced)
make run-dashboard    # streamlit :8501
make demo             # offline dashboard demo (labelled fixtures)
make reproduce        # lint + test + ONNX parity + evaluation
```

Native startup (verified end-to-end):

```bash
pip install -r requirements.lock
pip install -e . --no-deps
uvicorn zentinel.api.live_api:app --host 127.0.0.1 --port 8000 --workers 1   # /health, /ready, /docs
streamlit run dashboard/app.py                                              # http://localhost:8501
```

Backups: stop the worker or use SQLite `VACUUM INTO 'backup.sqlite3'` while WAL mode keeps readers consistent; copy `data/processed/zentinel_live.sqlite3*` cold otherwise. Retention purge and migrations are applied automatically at API startup (bounded batches, referenced incident rows protected).

---

## 12. Package

`Zentinel-AIOps-IBM-Z-Datathon-2026.zip` — 19.5 MB, built with an explicit include-list (application, notebooks, small data, frozen artifacts, reports, Docker/CI/Makefile, sample fixture). Excluded by construction: raw 743 MB log, DBs, caches, venvs, `.env`, egg-info. Post-build verification (CRC, required paths, forbidden-name scan, artifact SHA re-check, 13/13 notebooks executed): [`reports/ZIP_VERIFICATION.md`](reports/ZIP_VERIFICATION.md) — kept outside the archive because a file cannot contain its own archive's digest. Acceptance report: [`reports/FINAL_COMPLETION_REPORT.md`](reports/FINAL_COMPLETION_REPORT.md).

## 13. Known limitations

1. Aggregate automatic coverage on the current test slice is 33.2 % — unknown-template drift routes windows to review by design (fail-safe).
2. Exploratory test period was inspected during drift diagnostics (declared in every affected report).
3. Autoencoder is smoke-scale experimental; full training procedure documented but not executed.
4. Dashboard validation is test-driven at the data-service layer; GUI walkthrough is manual.
5. Single-worker inference state (enforced); no LLM narrative layer (deterministic evidence only).
6. LinuxONE/Docker/CI executions blocked by environment, not by code.
