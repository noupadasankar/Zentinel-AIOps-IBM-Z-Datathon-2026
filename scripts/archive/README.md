# Archived Helper and Hotfix Scripts

This directory preserves historical helper and patch scripts created during earlier exploratory development phases of the Zentinel prototype.

### Contents
- `fix_file_watcher_lock.py`: Legacy script resolving file watcher lock contention.
- `fix_idempotency_patch.py`: Initial prototype patch for log line hashing.
- `fix_live_dashboard_banner.py`: Patch for earlier dashboard status banner rendering.
- `fix_persistence_status.py`: Script testing persistence status response structure.
- `enable_idempotency.py`: Early implementation injecting idempotency tables into SQLite.
- `enable_live_persistence.py`: Early implementation of SQLite tables for live state.
- `enable_watcher_idempotency.py`: Prototype injecting hash generation into the file tailer.

### Current Status
All active functionality has been consolidated into the structured Python packages under `src/zentinel/`:
- `src/zentinel/api/`: Migrations, lifespan API, and thread-safe persistence.
- `src/zentinel/ingestion/`: Production file watcher with file ID and idempotency generation.
- `src/zentinel/models/`: Modular inference and evaluation engines.
- `src/zentinel/alerts/`: Incident grouping, hysteresis, and explanation policies.

These archived scripts are retained solely for provenance and historical auditing.
