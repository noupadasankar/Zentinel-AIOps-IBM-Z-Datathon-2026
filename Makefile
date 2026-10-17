# Zentinel AIOps Automation Makefile
.PHONY: help install test lint train export-onnx benchmark evaluate demo reproduce run-api run-dashboard clean

PYTHON ?= python

help:
	@echo "Zentinel AIOps Command Suite:"
	@echo "  make install        Install pinned dependencies from requirements.lock"
	@echo "  make test           Run comprehensive pytest suite"
	@echo "  make lint           Execute ruff static code analysis"
	@echo "  make train          Parse + mine templates + build features/sequences + train LSTM and IsolationForest"
	@echo "  make export-onnx    Export PyTorch LSTM to ONNX opset 17 and verify parity"
	@echo "  make benchmark      Execute sequential and 8-way concurrent live ingestion benchmarks"
	@echo "  make evaluate       Run 4-slice temporal walk-forward evaluation"
	@echo "  make demo           Launch offline Streamlit demo dashboard"
	@echo "  make run-api        Start live FastAPI ingestion server (single worker)"
	@echo "  make run-dashboard  Start Streamlit operational dashboard"
	@echo "  make reproduce      Full end-to-end audit (lint, test, onnx parity, evaluation)"
	@echo "  make clean          Clean temporary caches and artifacts"

install:
	$(PYTHON) -m pip install -r requirements.lock

test:
	$(PYTHON) -m pytest -ra -v

lint:
	$(PYTHON) -m ruff check .

export-onnx:
	$(PYTHON) scripts/export_onnx.py

# Full training workflow: requires data/raw/BGL.log (see docs/DATA_CARD.md).
# Steps mirror the frozen-template pipeline documented in docs/ARCHITECTURE.md:
# 1) chronological split  2) Drain3 fit on training split only  3) full parse
# 4) frozen-template match  5) sequences  6) window features
# 7) IsolationForest + validation threshold  8) LSTM next-event training.
# Each script fails fast if its input/outputs already exist.
train:
	@echo "WARNING: full training takes multiple CPU-hours and needs data/raw/BGL.log"
	@echo "Run individual scripts for staged execution; see --help-free CLI docs in"
	@echo "docs/ARCHITECTURE.md and scripts/*.py headers."
	$(PYTHON) scripts/plan_time_splits.py && \
	$(PYTHON) scripts/train_drain_final.py && \
	$(PYTHON) scripts/parse_bgl_full.py && \
	$(PYTHON) scripts/match_frozen_templates.py && \
	$(PYTHON) scripts/build_event_sequences_v2.py && \
	$(PYTHON) scripts/build_window_features.py && \
	$(PYTHON) scripts/train_isolation_forest.py && \
	$(PYTHON) scripts/train_lstm_predictor.py

benchmark:
	$(PYTHON) scripts/benchmark.py

evaluate:
	$(PYTHON) scripts/evaluate.py

demo:
	$(PYTHON) -m streamlit run dashboard/app.py

run-api:
	$(PYTHON) -m uvicorn zentinel.api.live_api:app --host 127.0.0.1 --port 8000 --workers 1

run-dashboard:
	$(PYTHON) -m streamlit run dashboard/app.py --server.port 8501

reproduce: lint test export-onnx evaluate
	@echo "All reproduction checks passed successfully."

clean:
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".ruff_cache" -exec rm -rf {} + 2>/dev/null || true
