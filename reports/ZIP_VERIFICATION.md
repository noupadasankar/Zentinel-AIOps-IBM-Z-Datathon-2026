# ZIP Verification Report — Zentinel AIOps (IBM Z Datathon 2026)

Generated: 2026-10-18 (audit-hardening rebuild: dead-module removal, API/header/status fixes, functional dashboard auto-refresh, side-effect-free admin probe, 97-test verification cycle, reproducible in-repo packaging). This file intentionally stays **outside** the archive (it documents the archive's own digest) and is the authoritative integrity record per `reports/FINAL_COMPLETION_REPORT.md` §Q.

## Archive identity

- Name: `Zentinel-AIOps-IBM-Z-Datathon-2026.zip`
- Size: 19,504,146 bytes (19.50 MB compressed)
- SHA-256: `6e66b5d679726bfdbddc2567dac516d6325658a449dbb7fd09cef8bda320c320`
- File count: 185

## Build method (reproducible)

Explicit include-list assembly (nothing enters by accident) via the **in-repo builder** `make package` → `scripts/build_distribution_zip.py`: `src`, `dashboard`, `scripts`, `tests`, `notebooks`, `reports`, `artifacts`, `.github`, `linuxone-validation`, `.streamlit`; `data/processed` whitelist (small parquets/samples/npz + exported CSVs); `data/raw` = `sample_replay.log` only; root config files (Makefile, Dockerfile, compose, pyproject, pytest.ini, requirements.*, .env.example, `.gitignore`, `.dockerignore`, `start_zentinel.bat`, `run_tests.bat`). All project documentation is consolidated in the single root `README.md` (CHANGELOG intentionally retired in the same convention). Pre-build gate rejects forbidden names **and build-artifact suffixes** (`*.egg-info`, `*.dist-info`). Changes in this build: removed unreferenced modules (`features/{windows,sequences}.py` stubs + empty `features/` package, orphaned `replay_api.py`); added in-repo packaging/scanning tools (`scripts/build_distribution_zip.py`, `scripts/verify_distribution_zip.py`, `scripts/secret_scan.py`). This report is excluded from the bundle (circular-digest rule).

## Verification steps executed (post-build, `make verify-zip` → `scripts/verify_distribution_zip.py`)

| Step | Result |
| --- | --- |
| CRC integrity (`testzip`) of every member | **OK** |
| Extraction to clean directory | 185 files extracted |
| Forbidden-name + build-artifact-suffix scan (BGL.log, BGL.tar.gz, .env, .venv, venv, __pycache__, caches, .git, *.egg-info, *.dist-info) | **NONE found** |
| Required-path presence (README, Makefile, Dockerfile+compose, artifacts/manifest.json, FINAL_COMPLETION_REPORT.md, live_api.py, unified.py, validate_linuxone.sh, `.streamlit/config.toml`, `dashboard/navigation.py`, `dashboard/app.py`, both launchers, first/last notebooks) | **ALL present** |
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
| reports | 46 | 0.3 |
| scripts | 39 | 0.3 |
| src | 20 | 0.2 |
| tests | 15 | 0.1 |
| dashboard | 3 | 0.1 |
| linuxone-validation | 1 | 0.0 |
| `.github` | 1 | 0.0 |
| `.streamlit` | 1 | 0.0 |
| root config/doc files (incl. 2 launchers) | 13 | 0.0 |

## Deliberate exclusions (distribution rules)

`data/raw/BGL.log` (743 MB), `BGL.tar.gz`, `data/raw/bgl_parsed*` (generated from raw), `data/processed/bgl_frozen_events.parquet`/`bgl_parsed.parquet` (150 MB each, derived), runtime SQLite DBs (`zentinel_live.sqlite3*`, `nb11_scratch_live.sqlite3*`), runtime state (`log_tailer_state.json`, `data/ingest` caches), `.env` (does not exist), `.venv`, `*.egg-info`/`*.dist-info`, `__pycache__`/`.pytest_cache`/`.ruff_cache`/`.ipynb_checkpoints`, `.git`. Re-obtain the raw dataset via `README.md`.

## Operator note (LinuxONE transfer)

The transferred artifact's digest is recorded on-target by `linuxone-validation/validate_linuxone.sh` into `02-zip-sha256.txt`. Set `EXPECTED_ZIP_SHA256` (exported before the run, using the SHA-256 above) to make the script **fail** on any transfer corruption or wrong-file mix-up; when unset, the script records the measured digest for manual comparison.
