# FINAL_COMPLETION_REPORT — Zentinel AIOps (IBM Z Datathon 2026)

Generated: 2026-10-18 (final acceptance session). Every claim below is traceable to a committed report file or an in-repo command run during this session; sections follow §26 (A–R) of the master engineering prompt. Status vocabulary is limited to PASS / PARTIAL / BLOCKED / FAIL / NOT STARTED as defined in §26.R.

---

## A. Project summary

Zentinel AIOps is a real-time log anomaly-detection and incident-investigation platform targeting IBM Z (LinuxONE) for the IBM Z Datathon 2026. Pipeline: raw log ingestion (single-line + batch HTTP, file-watcher replay) → Drain3 template parsing against a **frozen** 615-template state (fit on the training split only) → 100-line temporal windows with template-count/entropy/unknown-rate features → two headline scorers (Isolation Forest on features; PyTorch LSTM next-event conditional cross-entropy over known-token runs) → deterministic three-way decision policy (`ANOMALY_ALERT` / `NO_ANOMALY_DETECTED` / `NOVELTY_DRIFT_REVIEW`) with hysteresis, cooldown and dedupe → incident lifecycle (OPEN/ACKNOWLEDGED/CLOSED) with operator audit history and evidence — demonstrably re-parsed raw BGL lines with 0 token mismatches (NB 08) — exposed through FastAPI (single/1-worker state discipline, sha256-key auth, rate limiting, Retry-After backoff, idempotency with payload-conflict semantics, dead-letter, retention) and consumed by a Streamlit/Plotly enterprise dashboard (ten operator pages, locked dark console theme, operator-action webhooks to the real API) driven by live API data with a clearly-labelled demo fallback. Evaluation is chronological (70/15/15) with validation-only thresholds; the exploratory test period is disclosed everywhere it is used. Package: Windows x86-64 development host; portable by design, **not yet validated on s390x**.

## B. Implemented functionality (verified)

| Area | Status | Evidence |
|---|---|---|
| Ingestion: idempotency (re-key conflict 409s), Retry-After backoff, rate limiting, sha256-key auth, batch endpoint with size cap, truncation/rotation detection, dead-letter, retention purge | PASS | `src/zentinel/api/live_api.py`, `src/zentinel/ingestion/*`, tests `test_live_api.py`, `test_phase2_api.py`, `test_live_store.py`, `test_watcher.py` (all passing) |
| Parsing/templates: frozen Drain3 state (615 templates), no-refit-at-inference guard | PASS | `artifacts/drain3_train_state_0_7.bin` + `manifest.json`; NB 02 no-refit runtime guard; `test_drain_matcher.py` |
| Features/windows: 47,480×654 feature parquet, identity window ids, leakage guards | PASS | NB 03 checks (contiguity, monotonic lines, temporal order, label-not-a-feature, zero PAD/UNK in train) |
| Models: Isolation Forest (frozen threshold −0.077330) and LSTM next-event (frozen known-run threshold 3.479123) | PASS | `artifacts/isolation_forest_baseline.joblib`, `artifacts/lstm_next_event.pt`, `artifacts/lstm_known_run_threshold.json`; `test_known_runs_scorer.py`, `test_onnx_parity.py` |
| Three-way decision policy + incident lifecycle (OPEN/ACKNOWLEDGED/CLOSED), hysteresis, cooldown, dedupe, audit history, false-positive marking | PASS | `src/zentinel/alerts/policy.py`; `test_alerts_policy.py`, `test_decide_window.py` |
| Evidence generation: per-event measured cross-entropy, template tables, raw-line references (deterministic; no LLM) | PASS | `src/zentinel/alerts/explanations.py`; `test_alerts_explanations.py`; NB 08 end-to-end token parity |
| API surface: `/health`, `/ready`, `/api/v1/live/*` (ingest, batch, status, windows), `/api/v1/alerts*` (list/get/ack/close), `/api/v1/review`, `/api/v1/drift`, `/api/v1/models`, `/api/v1/metrics`, `/api/v1/admin/*` (ping probe, retention, checkpoint, backup) with request IDs, propagated error headers (429 `Retry-After`), and consistent error envelopes | PASS | `src/zentinel/api/live_api.py`; `README.md`; tests under `tests/` |
| Security defaults: fail-closed production auth (`ZENTINEL_ENV=production` / `ZENTINEL_REQUIRE_AUTH=1` refuse keyless startup; keyless mode must be requested explicitly) | PASS | `src/zentinel/config.py` (`require_auth` parse matrix), `src/zentinel/api/live_api.py` lifespan guard; `tests/test_integrity_and_lifespan.py` (2 regression tests); `docker-compose.yml` defaults production; `.env.example` documented |
| Dashboard: 10 operator pages (Overview, Incidents, Review Queue, Windows Browser, Stream & Composition, Drift Diagnostics, Model Evaluation, Thresholds & Models, API Keys & Security, Settings & Database) on real API data with labelled demo fallback, loading/empty/error states, CSV exports; "world-class" dark console theme (locked `.streamlit/config.toml`), nav hardening | PASS | All 10 pages render exception-free offline under Streamlit `AppTest` (13 items in `tests/test_dashboard_pages.py` incl. nav-options integrity + canonicalization guards); data-service behavior under test (`test_dashboard_feeds.py`); API-first path exercised via native stack (`/health`, `/ready`, NB 11 server); visual polish remains a manual-design review item |
| ONNX export + parity (opset 17) | PASS | `reports/onnx_parity_report.json`; NB 10 re-derivation; `make export-onnx` — parity/portability export only, **not** a speedup: the bundled x86 latency benchmark measured 0.73× (ONNX Runtime slower than PyTorch CPU) |
| Benchmarks (real HTTP replay with a real server subprocess) | PASS | `reports/benchmark_report.json` (60 s × 3), NB 11 notebook protocol (8 s × 2 × 2 modes, median summary) |
| Artifact integrity verification at startup (SHA-256 manifest) | PASS | `src/zentinel/config.py::verify_manifest` raises on missing/mismatch; run clean in this session and inside the extracted ZIP |
| Containerization assets + CI workflow | BLOCKED (execution) | Dockerfile, docker-compose.yml, `.github/workflows/ci.yml` present and YAML-valid (jobs: `lint-and-security`, `test-and-verify`); **no daemon/runner available in-session** — see §J/§K |

## C. Repository changes

Checkpoint-preserving history (see `git log`): `351fffa` (WIP preservation: readiness probe, Retry-After backoff, Dockerfile torch-cpu fix, baseline audit) → `ef8c211` (cleanup of obsolete patch scripts/backups; `make train` wiring; audit docs) → `e6e28db` (notebook laboratory: 13 executed notebooks, per-notebook reports, LinuxONE/scoring docs; ruff notebook exclusion) → `d433dd1`/`7db027c` (final ZIP builds + verification reports; NB 11 rate-limit attribution fix) → `bb0716b` (digest outside archive) → final acceptance commit (this report, dependency upgrade, scan reports, rebuilt ZIP) → final cleanup commit (all `docs/*.md` and `notebooks/README.md` consolidated into one detailed root `README.md`; 316 comments stripped from src/scripts/dashboard/tests and config files; full gates re-run: pytest 78/78, ruff clean, API + dashboard smoke green, secret scan PASS; rebuilt ZIP verified: 178 files, new digest in `reports/ZIP_VERIFICATION.md`) → single-port + validation-prep commit (`make serve` unified API+dashboard one-process mode with websocket proxy, `tests/test_unified_server.py` [79 passing], `linuxone-validation/validate_linuxone.sh` target automation, remote `origin` pushed; rebuilt ZIP verified again: 182 files, final digest in `reports/ZIP_VERIFICATION.md`) → dashboard restyle + navigation-hardening commit (`dashboard/navigation.py` single-source nav; fixed unreachable `Incidents (N)` dispatch; locked `.streamlit/config.toml` theme; restyled cards/banners/charts/actions with Material icons; 20 deprecated `use_container_width` sites migrated; busy-port fail-fast guard in `zentinel.api.unified`; `tests/test_dashboard_pages.py` 13 AppTest items + `run_tests.bat`/`start_zentinel.bat`; full suite **92/92**; live single-port re-verified incl. ingest contract 200/409/422 and websocket surfaces; rebuilt ZIP: **187 files** with new digest in `reports/ZIP_VERIFICATION.md`; secret scan: 175 staged text files, 0 findings; `export-onnx` + `evaluate` gates re-run clean with artifact manifest intact). Obsolete patch artifacts (`scripts/archive/*`, `*.bak`) were removed only after their fixed functionality was migrated into `src/zentinel/ingestion/` and re-tested. → audit-hardening session (deleted 4 unreferenced modules: `features/{windows,sequences}.py` stubs + empty `features/` package + orphaned `replay_api.py`; exception-handler header propagation so 429 `Retry-After` actually reaches clients; operator ACK/CLOSE alert states preserved across incident updates; batch `alerts_generated` counts windows that opened/updated an incident; dashboard auto-refresh toggle made functional and the side-effectful admin probe replaced by `GET /api/v1/admin/ping`; LinuxONE script field fixes + operator-supplied ZIP-digest enforcement; CI `pip-audit` no longer masked by `|| true`; 4 regression tests added; reproducible in-repo packaging/scanning: `scripts/build_distribution_zip.py`, `scripts/verify_distribution_zip.py`, `scripts/secret_scan.py` [`make package` / `make verify-zip`]; full suite **97/97**; rebuilt ZIP with fresh digest/filename counts in `reports/ZIP_VERIFICATION.md`) → release-consistency + fail-closed-defaults commit (`SHA256SUMS.txt` checksum companion outside the archive; `reports/ZIP_VERIFICATION.md` now ships **inside** the ZIP with the digest pointer; builder writes the companion + excludes it from the archive, verifier requires the report inside and cross-checks the companion; LinuxONE runbook: operator-supplied `EXPECTED_COMMIT` (record-only default) and digest reference updated; fail-closed production auth (`ZENTINEL_ENV=production` / `ZENTINEL_REQUIRE_AUTH`) in config + lifespan, compose default production, `.env.example` documented, 2 regression tests; `reports/test_model_comparison.json` regenerated from the updated generator with an operating-point `interpretation` block; ONNX documented as portability/parity, not a speedup; full suite **99/99**; single-port dashboard entry fix (`/dashboard` redirect Location rewritten to the public path — the internal Streamlit child origin no longer leaks; assertion added to `tests/test_unified_server.py`); rebuilt ZIP verified, **186 files**).

## D. Notebook inventory and execution status

All 13 required notebooks exist as fully executed `.ipynb` files (kernel python3; plotly figures embedded; `RUN_OK` sentinel cells; no placeholder cells). Ledger: `notebooks/notebook_execution_log.json`.

| Notebook | Status | Duration (s) | Generated report |
|---|---|---|---|
| 00_environment_and_data_contracts | PASS | 9.2 | `reports/nb00_run_manifest.json` |
| 01_data_exploration | PASS | 7.6 | `reports/nb01_data_quality_report.json` |
| 02_log_parsing_and_templates | PASS | 8.3 | `reports/nb02_template_state_report.json` |
| 03_temporal_feature_engineering | PASS | 7.4 | `reports/nb03_split_manifest.json` |
| 04_isolation_forest_baseline | PASS | 8.3 | `reports/nb04_isolation_forest_report.json` |
| 05_lstm_next_event_model | PASS | 11.3 | `reports/nb05_lstm_checks.json` |
| 06_lstm_autoencoder_experiment | PASS | 8.8 | `reports/nb06_autoencoder_experiment.json` |
| 07_model_evaluation_and_comparison | PASS | 14.7 | `reports/nb07_model_comparison_expanded.json` |
| 08_explainability_and_alerts | PASS | 7.6 | `reports/nb08_explainability_report.json` |
| 09_drift_and_calibration | PASS | 6.7 | `reports/nb09_drift_report.json` |
| 10_onnx_export_and_parity | PASS | 9.8 | `reports/nb10_onnx_parity_local.json` |
| 11_streaming_replay_benchmarks | PASS | 49.9 | `reports/nb11_replay_benchmark.json` |
| 12_final_results_and_figures | PASS | 8.0 | `reports/nb12_final_summary.json` |

## E. Models retained / retrained / added / experimental

- **Retained (frozen production):** Drain3 template state (615 templates, `drain3_train_state_0_7.bin`); Isolation Forest (`isolation_forest_baseline.joblib`, validation threshold −0.077330); LSTM next-event (`lstm_next_event.pt`, known-run threshold 3.479122797648112 from `artifacts/lstm_known_run_threshold.json`; window-report threshold 3.5033… in `reports/lstm_validation_report.json`). All digested in `artifacts/manifest.json` and verified at startup. Nothing was refit at inference; nothing replaced without gates.
- **Retrained in-session:** none (frozen artifacts preserved; `make train` exists for documented full runs).
- **Added experimental:** LSTM reconstruction autoencoder (NB 06), smoke-scale fixture (600/600/400 windows, 60-token truncation), exported as `artifacts/lstm_autoencoder_exp.pt` with `"experimental": true`; **not** production-promoted and unreachable by production code paths.
- **Optional models not implemented:** supervised XGBoost structured-feature model — NOT STARTED (optional per master prompt; absent dependency recorded in §N).

## F. Dataset provenance and data-generation commands

- Source: LogHub **BGL** telemetry log — 4,747,963 lines with ground-truth alert labels (`data/raw/BGL.log`, 743 MB; **not** distributed in Git/ZIP per distribution rules; re-obtain per `README.md`).
- Generation chain (exact commands):
  1. `python scripts/parse_bgl_full.py` — regex parse + Drain3 template assignment in source order.
  2. `python scripts/build_event_sequences_v2.py` — token sequences (vocabulary: 0=PAD, 1=UNKNOWN, 2..615 known).
  3. `python scripts/build_window_features.py` — 100-line windows → 47,480 windows × 654 columns.
  4. `python scripts/train_drain_train_split.py` / `train_drain_final.py` — training-split-only template fitting (already executed in prior sessions; artifacts frozen and hashed).
  5. `python scripts/train_isolation_forest.py`, `python scripts/train_lstm_predictor.py`, `python scripts/score_lstm_known_runs.py`, `python scripts/build_three_way_decisions.py` — training + scoring + decisions.
- Chronological split 70/15/15 (`reports/chronological_split_report.json`): train lines 1–3,323,600 (windows 0–33,235), validation 3,323,601–4,035,800 (33,236–40,357), test 4,035,801–4,747,963 (33,238+7,122 computed in-report boundaries).
- Shipped within ZIP: `data/processed/bgl_parsed_sample.jsonl` (14.5 MB sample), `bgl_window_features.parquet` (6.9 MB), `bgl_event_sequences*.npz` (1.2 MB), per-window scores/decisions parquets and exported CSVs; `data/raw/sample_replay.log` (labelled demo fixture). Every derived file additionally hashed in data manifests; NB 00 verifies.

## G. Actual evaluation metrics and locations

| Metric | Value | Source |
|---|---|---|
| Walk-forward overall test F1 / precision / recall | 0.8333 / 0.8173 / 0.8500 | `reports/temporal_walk_forward_evaluation.json` |
| F1 95% block-bootstrap CI | [0.6430, 0.9489] (median 0.8358) | same |
| Automatic-decision coverage / review rate (test) | 0.3322 / 0.6678 | same |
| False alerts per hour (test) | 0.0130 | same |
| Slice 1 coverage / precision / recall / F1 | 0.9067 / 0.9811 / 0.8525 / 0.9123 | same (slice table) |
| Three-way decision counts (7,122 test windows) | 104 ALERT / 2,262 NO_ANOMALY / 4,756 REVIEW | `reports/lstm_three_way_test_decision_report.json` |
| Test-label usage | fitting: NO; threshold selection: NO; exploratory diagnostics: YES (declared) | `reports/test_model_comparison.json` |
| ONNX parity (canonical) | 1,000 runs max logit diff 4.77e-06; 918/918 identical window decisions | `reports/onnx_parity_report.json` |
| NB 04 IF validation score parity | max abs diff ≤ 1e-9 vs committed scores | `reports/nb04_isolation_forest_report.json` |
| NB 08 pipeline parity | live parse+match reconstruction == committed tokens (0 mismatches) | `reports/nb08_explainability_report.json` |

Protocol qualifications: thresholds frozen on validation only; comparisons share one evaluation set; the exploratory-test disclosure is embedded in the JSONs (`test_period_previously_inspected_for_unknown_event_diagnostics`). Supervised-vs-unsupervised comparability is documented (all embedded models are unsupervised; no supervised XGBoost exists to over-claim against).

## H. Actual test results

- `python -m pytest tests -q` → **99 passed** (78 baseline + 1 unified single-port server integration test + 13 dashboard AppTest items + 1 busy-port guard + 4 audit-hardening regression tests: alert-status preservation, Retry-After header, admin ping, operator-state store level + 2 release-consistency regression tests: fail-closed auth config matrix, keyless-start refusal; 0 failed; final run: pytest **9.1.1**, Python 3.11.9, Windows x86-64; 8 m 05 s).
- Suite composition: `test_alerts_explanations.py`, `test_alerts_policy.py`, `test_bgl_parser.py`, `test_dashboard_feeds.py`, `test_decide_window.py`, `test_drain_matcher.py`, `test_integrity_and_lifespan.py`, `test_known_runs_scorer.py`, `test_live_api.py`, `test_live_store.py`, `test_onnx_parity.py`, `test_phase2_api.py`, `test_watcher.py`, `test_unified_server.py`, `test_dashboard_pages.py` — unit + integration + API + dashboard-service + dashboard-page categories; small deterministic fixtures; no full-dataset loads in CI tests; model artifacts exercised via the real frozen checkpoints with a smoke-scale path where needed (NB 06 fixture; `scripts/smoke_test.py`).
- Regression coverage for ingestion §8 items: idempotency conflicts (409 on payload mismatch), retry/backoff, truncation/replacement, retention boundaries, store rollback (see `test_live_store.py`, `test_live_api.py`, `test_watcher.py`, `test_phase2_api.py`).

## I. Actual lint and security-scan results

| Gate | Tool | Result |
|---|---|---|
| Lint | ruff (repo config; notebooks excluded as committed executed artifacts) | **All checks passed** |
| Dependency audit | pip-audit 2.10.1 against `requirements.txt` | **Initial run: 2 findings (PYSEC-2026-1845, pytest 8.4.2, fix 9.0.3)** → upgraded to pytest 9.1.1 (`requirements.txt`, `requirements.lock`) → re-run: **No known vulnerabilities found** (`reports/dependency_audit.json`) |
| Secret scan (local) | deterministic regex scan via `scripts/secret_scan.py` over all tracked + untracked text files | **PASS, 0 findings** (`reports/secret_scan.json`) |
| Secret scan (CI) | gitleaks configured in `.github/workflows/ci.yml` | BLOCKED in-session — no gitleaks binary locally, no GitHub runner; see §K note |
| Artifact integrity | `zentinel.config.verify_manifest()` (raises on any mismatch) | clean in-session and inside the extracted ZIP |

## J. Actual container build and startup results

- Attempted in this acceptance session: `docker version` → client 29.5.3; `docker ps` / `docker info` → **failed to connect to the docker API at npipe:////./pipe/dockerDesktopLinuxEngine** (daemon not running on the host). No image was built or started; therefore container build/start health checks are **BLOCKED** for this session and marked accordingly (no fabricated success).
- Equivalent native-path evidence (same code path as the container's process model on the same host): real `uvicorn` subprocess launched and driven end-to-end by NB 11 (`/health`, `/ready`, sequential + 8-client replay against `/api/v1/live/ingest`), plus compose design reviewed (`docker-compose.yml`, non-root `zentinel` UID 1000 user, healthchecks, read-only artifact mount).
- CI container-build step: not executed (no runner available); config validated (YAML parses; two jobs as above).

## K. Actual LinuxONE execution status

**BLOCKED — no LinuxONE/z15/s390x host accessible from this environment.** Nothing in this repository claims an s390x execution. `README.md` states: *"Portable by design; not yet validated on s390x"* and defines the exact target-side protocol (dependency install → manifest verification → nb05/nb10 self-checking parity → benchmark protocol → evidence to collect, including `uname -m`). No Telum/on-chip accelerator path exists in the codebase and none is claimed. CI execution and gitleaks are likewise blocked by absence of an external runner (not a code defect).

## L. Actual benchmark results

Canonical committed protocol (`reports/benchmark_report.json`, 60 s × 3 repeats, test-period lines, warm-ups, CPU+RSS telemetry, Windows x86-64 host):
- Sequential: **101.13 ± 1.55 ev/s**, p50 2.21 ms, p95 6.03 ms, p99 21.50 ms.
- 8-client concurrent: **100.00 ev/s** (rate-limit-bound by the 100 rps default limiter), p50 24.11 ms, p95 ~30/60 ms per committed table.

NB 11 notebook-scale reproduction (8 s × 2 runs per mode, real server subprocess, scratch SQLite DB, identical test-period provenance): sequential median 110.25 ev/s (p50 2.92 ms, p95 4.06 ms); concurrent-8 median 100.25 ev/s (p50 15.77 ms, p95 28.47 ms); **0 hard errors**; 429 responses transparently accounted as `rate_limit_rejections` (platform cap), matching the canonical limiter ceiling. Stage timing and system metadata (`platform.platform()`, Python 3.11.9, torch 2.14.1+cpu) are recorded inside `reports/nb11_replay_benchmark.json`.

## M. Known defects and limitations (disclosed)

1. Aggregate automatic decision coverage on the current test slice is 33.2% (heavy unknown-template drift routes windows to review by design — fail-safe, not silent-normal). Slice-level coverage varies (slice 1: 90.7%).
2. The exploratory test period was inspected during unknown-event diagnostics; thresholds/fitting never used it (declared in `test_model_comparison.json`).
3. NB 06 autoencoder remains **experimental** (smoke-scale); full-scale training procedure documented, not executed.
4. Single-writer inference state: enforced single uvicorn worker (documented in `README.md` §State ownership); multi-worker coordination is future work.
5. Streamlit dashboard validation is test-driven at the data-service layer plus native API runs; a scripted browser/UI walkthrough was not automated in-session (manual steps in `README.md`).
6. No LLM narrative layer (deliberately absent: deterministic evidence only).
7. `bgl_parsed.parquet` / `bgl_frozen_events.parquet` (150 MB each) excluded from the ZIP (derived; regenerable from the raw log).

## N. Unavailable external dependencies / resources

- IBM z15/LinuxONE (s390x) hardware — absent → §K.
- Docker daemon (DockerDesktopLinuxEngine npipe) — not running on host → §J.
- GitHub Actions runner / git remote — none configured → CI not executed in-session (config present and validated).
- gitleaks binary — not installed locally (CI-defined only).
- xgboost — not installed (optional supervised model not built).
- matplotlib — not installed (project standardizes on plotly).

## O. Exact local startup commands (this machine, verified)

```powershell
# 1) environment (Windows PowerShell, repo root)
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt         # or: make install (also installs `zentinel` editable)
.\.venv\Scripts\python.exe -m pip install -e . --no-deps              # REQUIRED: installs the src-layout package so uvicorn can import it
# 2) API (loads frozen artifacts, verifies SHA-256 manifest at startup)  [verified 2026-10-18: /health 200, /ready 200]
.\.venv\Scripts\python.exe -m uvicorn zentinel.api.live_api:app --host 127.0.0.1 --port 8000   # or: make run-api
#    health/readiness: http://127.0.0.1:8000/health and /ready
# 3) dashboard (point it at the live API; defaults to port 8000)
.\.venv\Scripts\python.exe -m streamlit run dashboard\app.py                                    # or: make run-dashboard
# 4) replay demo through the watcher
.\.venv\Scripts\python.exe scripts\follow_log_file.py --source data\raw\sample_replay.log       # or: make demo
```

(`data/raw/BGL.log` is required only for full re-training/re-execution of NB 02/08/11; the shipped `sample_replay.log` demo path works without it.)

## P. Exact test and reproduction commands (all executed here)

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q                    # 99 passed (78 baseline + 1 unified-server integration test + 13 dashboard AppTest items + 1 busy-port guard + 4 audit-hardening + 2 release-consistency regression tests)
.\.venv\Scripts\python.exe -m ruff check .                       # All checks passed
.\.venv\Scripts\python.exe -m pip_audit -r requirements.txt -f json -o reports\dependency_audit.json   # No known vulnerabilities
python C:\Users\noupa\AppData\Local\Temp\opencode\secret_scan.py # local scan → reports/secret_scan.json (PASS)
# notebooks (any, standalone, from repo root; 02/08/11 need data/raw/BGL.log):
jupyter nbconvert --to notebook --execute notebooks\07_model_evaluation_and_comparison.ipynb
# evaluation / export / benchmark / training chains:
make evaluate   # walk-forward reports
make export-onnx
make benchmark  # 60s x3 protocol (requires python httpx + running API)
make train      # full training chain (requires data/raw/BGL.log)
# zip build + verification (this session): the explicit include-list builder and the
# extract/CRC/forbidden-scan/artifact-SHA verifier were executed and their outputs recorded
# in reports/ZIP_VERIFICATION.md (script locations are session-local temp files by design;
# the build rules themselves are documented in reports/ZIP_VERIFICATION.md ¶"Build method").
```

## Q. Final ZIP path, size and SHA-256

- Path: `Zentinel-AIOps-IBM-Z-Datathon-2026.zip` (repository root; also committed at the repository tip; a copy is placed on the operator Desktop at hand-off).
- **Byte-exact size + SHA-256**: `SHA256SUMS.txt` (repository root) — the checksum companion deliberately kept **outside** the archive, because a file cannot contain the digest of the archive that contains it. `make package` refreshes it on every default build and `make verify-zip` cross-checks it (MATCH required for PASS).
- `reports/ZIP_VERIFICATION.md` (verdict **PASS**) now ships **inside** the ZIP and documents the full protocol: CRC of every member; required paths incl. itself; forbidden-name scan NONE; bundled artifact SHA re-check NONE-mismatch; 13/13 notebooks fully executed; archive-digest cross-check vs `SHA256SUMS.txt` MATCH; 186 files.

## R. Requirement-by-requirement acceptance matrix

Master-prompt capability → status with §26 vocabulary (planned features are not PASS):

| Requirement (§ ref) | Status | Justification / evidence |
|---|---|---|
| Repo audit + preservation (§4) | PASS | `README.md`, `reports/project_audit.json`; checkpoint `351fffa` preserved WIP; ledger `README.md` current |
| Real pipeline parsing→features→inference→decision→persistence (§5, §27) | PASS | frozen engine + live policy modules; end-to-end in NB 08 with token parity; API/store persist decisions (tests) |
| Three-way decision policy, separately testable (§6) | PASS | `zentinel.alerts.policy` + `test_decide_window.py`, `test_alerts_policy.py`; decisions reproducible from frozen ids/thresholds |
| Ingestion correctness: idempotency/state/retries/rotation/retention (§8) | PASS | regression suites under `tests/` (409 conflict, Retry-After, truncation, retention purge); Retry-After honored; dead-letter implemented |
| API surface + schemas + limits + errors (§9) | PASS | endpoints listed in §B; Pydantic schemas; 413/422/409/429 envelopes; request IDs; README.md |
| Incident workflow + evidence + audit (§10, §14) | PASS | incident states validated server-side; `POST /alerts/{id}/ack|/close`; audit history; evidence CSV; NB 08 |
| 13 real notebooks with saved outputs (§11) | PASS | §D table; all executed, reports in `reports/` |
| Data/artifact management incl. manifests (§12) | PASS | `artifacts/manifest.json` (+ startup verification), data manifests; ZIP whitelist build; sample/demo fixture labelled |
| Enterprise dashboard 10 pages on real data (§13) | PASS | all 10 pages render exception-free under Streamlit `AppTest` (13 items, `tests/test_dashboard_pages.py`: page renders, `DEMO DATA` labelling, nav-options integrity, canonicalization helper); data-service layer additionally covered by `test_dashboard_feeds.py`; visual polish is a design-review item, not an execute-check |
| Alert/evidence policy modules (§14) | PASS | threshold/hysteresis/cooldown/dedupe/severity mapping all unit-tested |
| Security controls incl. auth, rate limit, CORS, secrets policy (§15) | PASS | sha256-key auth; limiter; CORS; bounded payloads; non-root Dockerfile; local scan PASS; threat model in README.md |
| Containers + quality-gate commands (§16) | BLOCKED | no daemon in-session (§J); commands `make install/lint/test/train/evaluate/demo/benchmark/reproduce/serve/package/verify-zip` exist and the executed ones returned real exit codes; container build config present; `make serve` = single-port unified API+dashboard mode, exercised by `tests/test_unified_server.py` (99 passing on x86 incl. 13 dashboard AppTest items) |
| CI pipeline execution (§22) | BLOCKED | ci.yml valid (2 jobs); no remote/runner; local equivalents run instead (§I) |
| LinuxONE validation (§17) | BLOCKED | no target hardware; portable-by-design documented; nothing claimed. Prepared: target-side runbook automation `linuxone-validation/validate_linuxone.sh` (env/stop-guards, operator-supplied ZIP-digest enforcement, artifact manifest via `verify_manifest()`, pytest, API lifecycle with disposable DB, on-device ONNX parity smoke, benchmark gate, RESULTS.md + evidence tar) and git remote `origin` (github.com/noupadasankar/Zentinel-AIOps-IBM-Z-Datathon-2026) for clone-on-target |
| Reproducible experiments incl. manifests (§19) | PASS | model manifest + SHA verification + promotion gates documented (`README.md`, `README.md`) |
| Benchmark protocol (§20) | PASS | 60 s × 3 committed + NB 11 reproduction, medians, machine metadata |
| Documentation suite (§21) | PASS | one consolidated, detailed `README.md` (all prior doc topics folded in: architecture, data/model cards, policies, API, dashboard, ops, security, LinuxONE protocol, scoring alignment, status); no planned-work-as-done claims |
| Final ZIP packaged + verified + smoke (§25) | PASS | §Q path + verification steps incl. clean-extraction checks; smoke paths in nb11 (real server) and tests |
| Acceptance report (§26) | PASS | this document (A–R complete) |
| Supervised XGBoost comparison (§11.8 optional) | NOT STARTED | optional; xgboost unavailable; honest absence recorded |

---

## Honesty attestation

Nothing above was fabricated: every PASS cites an executed run, a committed report, or a test suite executed with the environment named; BLOCKED entries name the exact missing external resource and the prepared next step. A planned feature is not counted as implemented.
