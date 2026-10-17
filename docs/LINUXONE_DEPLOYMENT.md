# LinuxONE / IBM Z Deployment (s390x)

**Status: portable by design — NOT yet validated on z/Linux (s390x) hardware. No on-device benchmarks or parity numbers are claimed in this repository.** Every timing/parity figure committed here was produced on the Windows x86-64 development host described in `reports/nb00_run_manifest.json` and the benchmark reports.

## Why this is not yet validated on s390x

No z15 / LinuxONE machine was available to this project (development ran entirely inside a Windows x86-64 sandbox; see Blockers in `docs/IMPLEMENTATION_STATUS.md`). Fabricating on-device claims would violate the project's evidence policy, so instead this document specifies everything needed to *obtain* those numbers on a target system.

## Portability inventory (verified, host-agnostic)

| Component | Portability basis | s390x availability of dependency |
| --- | --- | --- |
| Ingestion API (FastAPI + uvicorn) | Pure Python; SQLite (default) | Published s390x wheels/builds exist via upstream distributions |
| Drain3 template matcher + frozen state | Pure Python; frozen `drain3_train_state_0_7.bin` | Pure Python — portable |
| Isolation Forest baseline | scikit-learn + `joblib.load` (STACK_GLOBAL pickle) | scikit-learn ships s390x wheels |
| LSTM next-event scorer | PyTorch CPU-only build | PyTorch publishes CPU wheels for s390x (Linux); version 2.x line — verify exact version availability on target |
| ONNX graph (`artifacts/bgl_lstm_next_event.onnx`) | Standard ops, opset 17 | onnxruntime publishes s390x builds for the Linux s390x platform — verify on target |
| Dashboard (Streamlit) | Pure Python | Ships s390x wheels |
| Notebooks | plotly-based rendering (no matplotlib/native deps) | Pure Python |

Nothing in the codebase uses x86-specific intrinsics, GPU/CUDA, or Telum/on-chip accelerators — inference is plain float32 CPU PyTorch plus an ONNX graph.

## Deployment steps (to execute on the target z/OS Linux / KVM / z/VM guest)

1. Provision Python 3.11 on the s390x guest; create `python3.11 -m venv .venv`.
2. `pip install -r requirements.txt` — pin and record the resolved s390x wheels in the run manifest.
3. Copy `data/` processed artifacts, `artifacts/` (incl. SHA-256 `manifest.json`), `notebooks/`, `scripts/`.
4. Artifact manifest verification must pass before any inference is allowed (`python -c "from zentinel.config import verify_manifest; print(verify_manifest())"` — SHA-256 per `artifacts/manifest.json`); this guards bit-rot during file transfer.
5. Smoke: `uvicorn zentinel.api.live_api:app --port 8000` then `GET /ready` (loads the frozen engine).
6. Re-run on-device parity: execute `notebooks/05_lstm_next_event_model.ipynb` and `notebooks/10_onnx_export_and_parity.ipynb` with the pointer to the committed score files; the notebooks are self-checking (assertions), so a PASS printed on the target is the first legitimate s390x parity claim — attach that notebook output to `reports/` on the target host.

## Benchmarks to collect before making any performance claim

- `scripts/benchmark.py` (60 s × 3 protocol) — sequential and 8-client concurrent ingestion latency/throughput on the target.
- nb10 latency table (PyTorch vs ONNXRuntime on the target CPU).
- Record `uname -m` (must be `s390x`), IFL/LPAR configuration, and clock speed in the report header.

## Known risks to verify on target

- PyTorch/onnxruntime wheel availability + ABI on the guest's Python build.
- SQLite performance under the guest's I/O configuration (ingest persistence is commit-per-window-batch).
- ONNX quantized-op fallback: none of the graph ops are restricted in principle, but verify the full opset-17 graph executes on the s390x runtime build.
