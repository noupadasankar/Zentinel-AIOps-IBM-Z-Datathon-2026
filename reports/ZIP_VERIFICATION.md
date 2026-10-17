# ZIP Verification Report — Zentinel AIOps (IBM Z Datathon 2026)

Generated: 2026-10-18. This file intentionally stays **outside** the archive (it documents the archive's own digest) and is the authoritative integrity record per `reports/FINAL_COMPLETION_REPORT.md` §5.

## Archive identity

- Name: `Zentinel-AIOps-IBM-Z-Datathon-2026.zip`
- Size: 19,531,891 bytes (19.53 MB compressed)
- SHA-256: `eb8db584efe2d91b3ea9f9e20c275bef169a5b0504f9d71e3f0602cef4890c40`
- File count: 193

## Build method

Explicit include-list assembly (nothing enters by accident): `src`, `dashboard`, `scripts`, `tests`, `notebooks`, `docs`, `reports`, `artifacts`, `.github`; `data/processed` whitelist (small parquets/samples/npz + exported CSVs); `data/raw` = `sample_replay.log` only; root config files (Makefile, Dockerfile, compose, pyproject, pytest.ini, requirements.*, .env.example). Pre-build gate rejects forbidden names; this report is excluded from the bundle (circular-digest rule).

## Verification steps executed (post-build)

| Step | Result |
| --- | --- |
| CRC integrity (`testzip`) of every member | **OK** |
| Extraction to clean directory | 193 files extracted |
| Forbidden-name scan (BGL.log, BGL.tar.gz, .env, .venv, venv, __pycache__, caches, .git) | **NONE found** |
| Required-path presence (README, CHANGELOG, Makefile, Dockerfile+compose, notebooks/README, artifacts/manifest.json, FINAL_COMPLETION_REPORT.md, LINUXONE_DEPLOYMENT.md, live_api.py, first/last notebooks) | **ALL present** |
| Notebook count | 13 `.ipynb` + `README.md` + `notebook_execution_log.json` |
| Fully-executed check (every code cell has execution/output) | **13/13 — NONE unexecuted** |
| Bundled artifact SHA-256 re-check vs bundled `artifacts/manifest.json` | **NO mismatch** |
| `zentinel.config.verify_manifest()` run from the extracted tree | no missing artifacts |

## Uncompressed inventory (top-level)

| Segment | Files | MB (uncompressed) |
| --- | --- | --- |
| notebooks | 15 | 53.5 |
| data/processed (whitelist) + data/raw/sample_replay.log | 18 | 23.4 |
| artifacts | 14 | 2.7 |
| reports | 45 | 0.4 |
| scripts | 36 | 0.3 |
| src | 23 | 0.1 |
| docs | 14 | 0.1 |
| dashboard | 2 | 0.1 |
| tests | 13 | 0.1 |
| root config/doc files | 12 | 0.1 |

## Deliberate exclusions (distribution rules)

`data/raw/BGL.log` (743 MB), `BGL.tar.gz`, `data/raw/bgl_parsed*` (generated from raw), `data/processed/bgl_frozen_events.parquet`/`bgl_parsed.parquet` (150 MB each, derived), runtime SQLite DBs (`zentinel_live.sqlite3*`, `nb11_scratch_live.sqlite3*`), runtime state (`log_tailer_state.json`, `data/ingest` caches), `.env` (does not exist), `.venv`, caches. Re-obtain the raw dataset via `docs/DATA_CARD.md`.
