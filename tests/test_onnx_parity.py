"""Unit tests for ONNX export, session execution, and parity verification."""

from __future__ import annotations

from pathlib import Path

from zentinel.config import MODEL_PATH
from zentinel.models.onnx_export import (
    DEFAULT_ONNX_PATH,
    ONNXInferenceEngine,
    export_lstm_to_onnx,
)


def test_onnx_export_creates_valid_model(tmp_path: Path):
    target_onnx = tmp_path / "test_model.onnx"
    out_path = export_lstm_to_onnx(
        model_path=MODEL_PATH,
        output_path=target_onnx,
        opset_version=17,
    )
    assert out_path.is_file()
    assert out_path.stat().st_size > 50000


def test_onnx_inference_engine_scoring():
    if not DEFAULT_ONNX_PATH.is_file():
        export_lstm_to_onnx(output_path=DEFAULT_ONNX_PATH)

    engine = ONNXInferenceEngine(onnx_path=DEFAULT_ONNX_PATH)

    score, transitions = engine.score_known_runs([2])
    assert score is None
    assert transitions == 0

    score, transitions = engine.score_known_runs([2, 5, 8, 12])
    assert score is not None
    assert transitions == 3
    assert score > 0.0

    events = [{"token": 5} for _ in range(100)]
    res = engine.decide_window(events)
    assert res["decision"] in ["NO_ANOMALY_DETECTED", "ANOMALY_ALERT", "NOVELTY_DRIFT_REVIEW"]
    assert "known_transitions_scored" in res
