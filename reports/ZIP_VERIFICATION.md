# ZIP Verification Report — Zentinel AIOps (IBM Z Datathon 2026)

Generated: 2026-10-18 (release-consistency rebuild: checksum companion `SHA256SUMS.txt` moved outside the archive; **this report now ships inside the ZIP** with the digest pointer; fail-closed production auth defaults; operating-point `interpretation` block in `reports/test_model_comparison.json`; operator-supplied LinuxONE commit expectation; 99-test verification cycle).

## Archive identity

- Name: `Zentinel-AIOps-IBM-Z-Datathon-2026.zip`
- File count: 186
- **SHA-256 and byte-exact size**: recorded in `SHA256SUMS.txt` at the repository root — the checksum companion that necessarily lives **outside** this archive (a file cannot contain its own archive's digest). `make package` writes it automatically after every default build; `make verify-zip` cross-checks it (MATCH is required for a PASS verdict; the check is also automated in-repo by `scripts/verify_distribution_zip.py`).

## Build method (reproducible)

Explicit include-list assembly (nothing enters by accident) via the **in-repo builder** `make package` → `scripts/build_distribution_zip.py`: `src`, `dashboard`, `scripts`, `tests`, `notebooks`, `reports` (this report included), `artifacts`, `.github`, `linuxone-validation`, `.streamlit`; `data/processed` whitelist (small parquets/samples/npz + exported CSVs); `data/raw` = `sample_replay.log` only; root config files (Makefile, Dockerfile, compose, pyproject, pytest.ini, requirements.*, `.env.example`, `.gitignore`, `.dockerignore`, `start_zentinel.bat`, `run_tests.bat`). All project documentation is consolidated in the single root `README.md`. Pre-build gate rejects forbidden names **and build-artifact suffixes** (`*.egg-info`, `*.dist-info`). Changes in this build: failure-path hardening from the audit cycle (429 `Retry-After`, admin ping probe, alert-state preservation, functional dashboard auto-refresh); fail-closed production auth (`ZENTINEL_ENV=production` / `ZENTINEL_REQUIRE_AUTH` refuse keyless startup); `SHA256SUMS.txt` companion + verifier cross-check; LinuxONE runbook with operator-supplied `EXPECTED_COMMIT`/`EXPECTED_ZIP_SHA256`; regenerated `test_model_comparison.json` with an operating-point `interpretation` block.

## Verification steps executed (post-build, `make verify-zip` → `scripts/verify_distribution_zip.py`)

| Step | Result |
| --- | --- |
| CRC integrity (`testzip`) of every member | **OK** |
| Extraction to clean directory | 186 files extracted |
| Forbidden-name + build-artifact-suffix scan (BGL.log, BGL.tar.gz, .env, .venv, venv, __pycache__, caches, .git, *.egg-info, *.dist-info, SHA256SUMS.txt) | **NONE found** |
| Required-path presence (README, Makefile, Dockerfile+compose, artifacts/manifest.json, FINAL_COMPLETION_REPORT.md, **ZIP_VERIFICATION.md**, live_api.py, unified.py, validate_linuxone.sh, `.streamlit/config.toml`, `dashboard/navigation.py`, `dashboard/app.py`, both launchers, first/last notebooks) | **ALL present** |
| SHA-256 companion cross-check (archive digest vs repository-root `SHA256SUMS.txt`) | **MATCH** |
| Notebook count | 13 `.ipynb` + `notebook_execution_log.json` |
| Fully-executed check (every code cell has execution/output) | **13/13 — NONE unexecuted** |
| Bundled artifact SHA-256 re-check vs bundled `artifacts/manifest.json` | **NO mismatch** |
| `zentinel.config.verify_manifest()` run from the extracted tree | clean (`MANIFEST_CHECK None`) |
| Overall verdict | **PASS** |

## Uncompressed inventory (top-level)

| Segment | Files | MB (uncompressed) |
| --- | --- | --- |
| notebooks | 14 | 53.4 |
| data/processed (whitelist) + data/raw/sample_replay.log | 18 | 23.4 |
| artifacts | 14 | 2.7 |
| reports (incl. this file) | 47 | 0.3 |
| scripts | 39 | 0.3 |
| src | 20 | 0.2 |
| tests | 15 | 0.1 |
| dashboard | 3 | 0.1 |
| linuxone-validation | 1 | 0.0 |
| `.github` | 1 | 0.0 |
| `.streamlit` | 1 | 0.0 |
| root config/doc files (incl. 2 launchers) | 13 | 0.0 |

## Deliberate exclusions (distribution rules)

`data/raw/BGL.log` (743 MB), `BGL.tar.gz`, `data/raw/bgl_parsed*` (generated from raw), `data/processed/bgl_frozen_events.parquet`/`bgl_parsed.parquet` (150 MB each, derived), runtime SQLite DBs (`zentinel_live.sqlite3*`, `nb11_scratch_live.sqlite3*`), runtime state (`log_tailer_state.json`, `data/ingest` caches), `.env` (does not exist), `.venv`, `*.egg-info`/`*.dist-info`, `__pycache__`/`.pytest_cache`/`.ruff_cache`/`.ipynb_checkpoints`, `.git`, and **`SHA256SUMS.txt`** (the digest companion must live outside the archive). Re-obtain the raw dataset via `README.md`.

## Operator note (LinuxONE transfer)

The transferred artifact's digest is recorded on-target by `linuxone-validation/validate_linuxone.sh` into `02-zip-sha256.txt`. Export `EXPECTED_ZIP_SHA256` (using the value in `SHA256SUMS.txt` at the repository root) before the run to make the script **fail** on any transfer corruption or wrong-file mix-up; when unset, the script records the measured digest for manual comparison. The commit check is operator-supplied too (`EXPECTED_COMMIT`, record-only default) — the release identity is the ZIP digest, so no stale commit pin can drift out of date.
