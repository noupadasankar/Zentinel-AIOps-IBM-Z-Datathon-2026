# ZIP Verification Report — Zentinel AIOps (IBM Z Datathon 2026)

Generated: 2026-10-18 (rebuild including LinuxONE validation tooling + unified single-port server). This file intentionally stays **outside** the archive (it documents the archive's own digest) and is the authoritative integrity record per `reports/FINAL_COMPLETION_REPORT.md` §5.

## Archive identity

- Name: `Zentinel-AIOps-IBM-Z-Datathon-2026.zip`
- Size: 19,498,524 bytes (19.50 MB compressed)
- SHA-256: `be3e593ee01f870175c78f0210f2b3fa1224b5365d8bb95f3da501cbd7529e25`
- File count: 182

## Build method

Explicit include-list assembly (nothing enters by accident): `src`, `dashboard`, `scripts`, `tests`, `notebooks`, `reports`, `artifacts`, `.github`, `linuxone-validation`; `data/processed` whitelist (small parquets/samples/npz + exported CSVs); `data/raw` = `sample_replay.log` only; root config files (Makefile, Dockerfile, compose, pyproject, pytest.ini, requirements.*, .env.example). All project documentation is consolidated in the single root `README.md`. New in this build: `src/zentinel/api/unified.py` (single-port API+dashboard server), `tests/test_unified_server.py`, `linuxone-validation/validate_linuxone.sh` (target-side runbook automation). Pre-build gate rejects forbidden names; this report is excluded from the bundle (circular-digest rule).

## Verification steps executed (post-build)

| Step | Result |
| --- | --- |
| CRC integrity (`testzip`) of every member | **OK** |
| Extraction to clean directory | 182 files extracted |
| Forbidden-name scan (BGL.log, BGL.tar.gz, .env, .venv, venv, __pycache__, caches, .git) | **NONE found** |
| Required-path presence (README, CHANGELOG, Makefile, Dockerfile+compose, artifacts/manifest.json, FINAL_COMPLETION_REPORT.md, live_api.py, unified.py, validate_linuxone.sh, first/last notebooks) | **ALL present** |
| Notebook count | 13 `.ipynb` + `notebook_execution_log.json` |
| Fully-executed check (every code cell has execution/output) | **13/13 — NONE unexecuted** |
| Bundled artifact SHA-256 re-check vs bundled `artifacts/manifest.json` | **NO mismatch** |
| `zentinel.config.verify_manifest()` run from the extracted tree | no missing artifacts |

## Uncompressed inventory (top-level)

| Segment | Files | MB (uncompressed) |
| --- | --- | --- |
| notebooks | 14 | 53.4 |
| data/processed (whitelist) + data/raw/sample_replay.log | 18 | 23.4 |
| artifacts | 14 | 2.7 |
| reports | 46 | 0.3 |
| scripts | 36 | 0.3 |
| src | 24 | 0.2 |
| tests | 14 | 0.1 |
| dashboard | 2 | 0.1 |
| linuxone-validation | 1 | 0.0 |
| root config/doc files | 11 | 0.0 |

## Deliberate exclusions (distribution rules)

`data/raw/BGL.log` (743 MB), `BGL.tar.gz`, `data/raw/bgl_parsed*` (generated from raw), `data/processed/bgl_frozen_events.parquet`/`bgl_parsed.parquet` (150 MB each, derived), runtime SQLite DBs (`zentinel_live.sqlite3*`, `nb11_scratch_live.sqlite3*`), runtime state (`log_tailer_state.json`, `data/ingest` caches), `.env` (does not exist), `.venv`, caches. Re-obtain the raw dataset via `README.md`.
