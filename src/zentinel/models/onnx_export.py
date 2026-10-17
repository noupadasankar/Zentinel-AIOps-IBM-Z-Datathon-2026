"""ONNX model export, runtime inference, and numerical parity verification.

Exports the trained PyTorch LSTMNextEvent model to ONNX with opset 17 and dynamic
batch/sequence axes, and verifies numerical parity against PyTorch on real runs.
"""

from __future__ import annotations

import json
import platform
import time
from pathlib import Path
from typing import Any

import numpy as np
import onnx
import onnxruntime as ort
import torch
import torch.nn.functional as F

from zentinel.config import (
    DECISION_REPORT_PATH,
    DRAIN_STATE_PATH,
    MODEL_PATH,
    MODEL_THRESHOLD_PATH,
    ROOT_DIR,
    UNKNOWN_TOKEN,
    VOCAB_PATH,
)
from zentinel.models.inference import InferenceEngine, LSTMNextEvent

DEFAULT_ONNX_PATH = ROOT_DIR / "artifacts" / "bgl_lstm_next_event.onnx"
SEQUENCES_PATH = ROOT_DIR / "data" / "processed" / "bgl_event_sequences_v2.npz"


def export_lstm_to_onnx(
    model_path: Path = MODEL_PATH,
    output_path: Path = DEFAULT_ONNX_PATH,
    opset_version: int = 17,
) -> Path:
    """Export PyTorch LSTMNextEvent to ONNX with dynamic batch and sequence axes.

    Args:
        model_path: Path to PyTorch model checkpoint (.pt).
        output_path: Target path for the exported .onnx model.
        opset_version: ONNX operator set version (default: 17).

    Returns:
        Path to the validated ONNX model artifact.
    """
    if not model_path.is_file():
        raise FileNotFoundError(f"Source PyTorch model missing: {model_path}")

    checkpoint = torch.load(model_path, map_location="cpu", weights_only=True)
    config = checkpoint["config"]

    model = LSTMNextEvent(
        vocab_size=int(config["vocab_size"]),
        embedding_dim=int(config["embedding_dim"]),
        hidden_dim=int(config["hidden_dim"]),
    )
    model.load_state_dict(checkpoint["model_state_dict"], strict=True)
    model.eval()

    output_path.parent.mkdir(parents=True, exist_ok=True)
    dummy_input = torch.tensor([[2, 3, 4, 5]], dtype=torch.long)

    # Use legacy torchscript exporter for rock-solid opset 17 compatibility
    torch.onnx.export(
        model,
        dummy_input,
        str(output_path),
        export_params=True,
        opset_version=opset_version,
        do_constant_folding=True,
        input_names=["tokens"],
        output_names=["logits"],
        dynamic_axes={
            "tokens": {0: "batch_size", 1: "seq_len"},
            "logits": {0: "batch_size", 1: "seq_len"},
        },
        dynamo=False,
    )

    # Verify ONNX model integrity
    onnx_model = onnx.load(str(output_path))
    onnx.checker.check_model(onnx_model)
    return output_path


class ONNXInferenceEngine:
    """Inference engine matching InferenceEngine but backed by ONNX Runtime."""

    def __init__(
        self,
        onnx_path: Path = DEFAULT_ONNX_PATH,
        drain_state_path: Path = DRAIN_STATE_PATH,
        vocab_path: Path = VOCAB_PATH,
        model_threshold_path: Path = MODEL_THRESHOLD_PATH,
        decision_report_path: Path = DECISION_REPORT_PATH,
    ):
        self.onnx_path = onnx_path
        self.drain_state_path = drain_state_path
        self.vocab_path = vocab_path
        self.model_threshold_path = model_threshold_path
        self.decision_report_path = decision_report_path

        # Verify existence
        for path in [
            self.onnx_path,
            self.vocab_path,
            self.model_threshold_path,
            self.decision_report_path,
        ]:
            if not path.is_file():
                raise FileNotFoundError(f"Required inference asset missing: {path}")

        self.vocab = json.loads(self.vocab_path.read_text(encoding="utf-8"))
        self.vocab_size = int(self.vocab["vocabulary_size"])

        threshold_config = json.loads(self.model_threshold_path.read_text(encoding="utf-8"))
        self.lstm_threshold = float(threshold_config["threshold"])

        decision_report = json.loads(self.decision_report_path.read_text(encoding="utf-8"))
        self.novelty_threshold = float(decision_report["novelty_unknown_rate_threshold"])

        # Setup ONNX Runtime session
        session_options = ort.SessionOptions()
        session_options.inter_op_num_threads = 1
        session_options.intra_op_num_threads = 1
        self.session = ort.InferenceSession(
            str(self.onnx_path),
            sess_options=session_options,
            providers=["CPUExecutionProvider"],
        )

    def _score_single_run(self, run: list[int]) -> tuple[float, int]:
        """Score a single known run using ONNX Runtime."""
        if len(run) < 2:
            return 0.0, 0

        tokens_in = np.asarray([run[:-1]], dtype=np.int64)
        targets = np.asarray(run[1:], dtype=np.int64)

        # Run ONNX inference
        logits = self.session.run(None, {"tokens": tokens_in})[0]  # shape: (1, seq_len, vocab_size)
        logits_2d = logits[0]  # shape: (seq_len, vocab_size)

        # Numerically stable cross-entropy loss computation
        max_logits = np.max(logits_2d, axis=-1, keepdims=True)
        exp_logits = np.exp(logits_2d - max_logits)
        log_softmax = (logits_2d - max_logits) - np.log(np.sum(exp_logits, axis=-1, keepdims=True))

        # Gather target loss: -log_softmax[i, targets[i]]
        loss_sum = float(-np.sum(log_softmax[np.arange(len(targets)), targets]))
        return loss_sum, len(run) - 1

    def score_known_runs(self, tokens: list[int]) -> tuple[float | None, int]:
        """Compute window anomaly score across all contiguous runs of known tokens."""
        total_loss = 0.0
        total_transitions = 0
        current_run: list[int] = []

        for token in [*tokens, UNKNOWN_TOKEN]:
            if token >= 2:
                current_run.append(token)
            else:
                if len(current_run) >= 2:
                    run_loss, transitions = self._score_single_run(current_run)
                    total_loss += run_loss
                    total_transitions += transitions
                current_run = []

        if total_transitions == 0:
            return None, 0
        return total_loss / total_transitions, total_transitions

    def decide_window(self, events: list[dict[str, Any]]) -> dict[str, Any]:
        """Evaluate a window of events and return decision and diagnostic counters."""
        tokens = [int(event["token"]) for event in events]

        unknown_count = sum(token == UNKNOWN_TOKEN for token in tokens)
        known_count = sum(token >= 2 for token in tokens)
        observed_count = len(tokens)
        unknown_rate = unknown_count / observed_count if observed_count else 0.0

        lstm_score, transition_count = self.score_known_runs(tokens)
        scorable = lstm_score is not None

        lstm_would_alert = bool(lstm_score >= self.lstm_threshold) if scorable else None

        high_unknown_rate = unknown_rate > self.novelty_threshold

        if not scorable:
            decision = "NOVELTY_DRIFT_REVIEW"
            reason = "INSUFFICIENT_KNOWN_TRANSITIONS"
        elif high_unknown_rate:
            decision = "NOVELTY_DRIFT_REVIEW"
            reason = "UNKNOWN_RATE_ABOVE_VALIDATION_THRESHOLD"
        elif lstm_would_alert:
            decision = "ANOMALY_ALERT"
            reason = "LSTM_SCORE_ABOVE_VALIDATION_THRESHOLD"
        else:
            decision = "NO_ANOMALY_DETECTED"
            reason = "BELOW_ANOMALY_AND_NOVELTY_THRESHOLDS"

        return {
            "unknown_event_count": int(unknown_count),
            "known_event_count": int(known_count),
            "unknown_event_rate": float(unknown_rate),
            "known_transitions_scored": int(transition_count),
            "model_scorable": bool(scorable),
            "lstm_anomaly_score": float(lstm_score) if scorable else None,
            "lstm_would_alert": lstm_would_alert,
            "high_unknown_rate": bool(high_unknown_rate),
            "decision": decision,
            "decision_reason": reason,
        }


def extract_known_runs_from_dataset(
    sequences_npz_path: Path = SEQUENCES_PATH,
    split_code: int = 2,  # 2 = test split
    max_runs: int = 1000,
) -> tuple[list[list[int]], list[list[int]]]:
    """Extract real known runs and whole windows from dataset for parity testing.

    Returns:
        (runs, windows) where runs has len == max_runs.
    """
    data = np.load(sequences_npz_path)
    sequences = data["sequences"]
    split_codes = data["split_codes"]

    test_indices = np.where(split_codes == split_code)[0]
    collected_runs: list[list[int]] = []
    collected_windows: list[list[int]] = []

    for idx in test_indices:
        seq = sequences[idx].tolist()
        collected_windows.append(seq)

        current_run: list[int] = []
        for token in [*seq, UNKNOWN_TOKEN]:
            if token >= 2:
                current_run.append(token)
            else:
                if len(current_run) >= 2:
                    collected_runs.append(list(current_run))
                    if len(collected_runs) >= max_runs:
                        break
                current_run = []

        if len(collected_runs) >= max_runs:
            break

    return collected_runs[:max_runs], collected_windows


def evaluate_onnx_parity(
    onnx_path: Path = DEFAULT_ONNX_PATH,
    num_runs: int = 1000,
    num_windows: int = 1000,
) -> dict[str, Any]:
    """Execute numerical parity and latency comparison between PyTorch and ONNX Runtime.

    Args:
        onnx_path: Path to exported ONNX model.
        num_runs: Number of real runs to evaluate (default: 1,000).
        num_windows: Number of real test windows to evaluate for decisions (default: 1,000).

    Returns:
        Comprehensive validation dictionary report.
    """
    # 1. Load PyTorch Engine
    pt_engine = InferenceEngine()
    pt_engine.load()
    pt_model = pt_engine.model

    # 2. Load ONNX Engine
    onnx_engine = ONNXInferenceEngine(onnx_path=onnx_path)

    # 3. Extract real runs and windows
    runs, windows = extract_known_runs_from_dataset(max_runs=num_runs)
    test_windows = windows[:num_windows]

    # --- Run-Level Parity & Latency ---
    logit_diffs: list[float] = []
    loss_diffs: list[float] = []
    pt_latencies_ms: list[float] = []
    ort_latencies_ms: list[float] = []

    for run in runs:
        tokens_in_np = np.asarray([run[:-1]], dtype=np.int64)
        targets_np = np.asarray(run[1:], dtype=np.int64)
        tokens_in_pt = torch.tensor(tokens_in_np, dtype=torch.long)
        targets_pt = torch.tensor(targets_np, dtype=torch.long)

        # PyTorch execution
        t0 = time.perf_counter()
        with torch.no_grad():
            pt_logits = pt_model(tokens_in_pt)
            pt_loss = F.cross_entropy(pt_logits.squeeze(0), targets_pt, reduction="sum").item()
        t1 = time.perf_counter()
        pt_latencies_ms.append((t1 - t0) * 1000.0)

        # ONNX Runtime execution
        t0 = time.perf_counter()
        ort_logits = onnx_engine.session.run(None, {"tokens": tokens_in_np})[0]
        ort_loss, _ = onnx_engine._score_single_run(run)
        t1 = time.perf_counter()
        ort_latencies_ms.append((t1 - t0) * 1000.0)

        # Compare outputs
        pt_logits_np = pt_logits.numpy()
        diff = float(np.max(np.abs(ort_logits - pt_logits_np)))
        logit_diffs.append(diff)
        loss_diffs.append(abs(pt_loss - ort_loss))

    # --- Window-Level Decision Parity ---
    identical_decisions = 0
    decision_mismatches: list[dict[str, Any]] = []

    for idx, window in enumerate(test_windows):
        events = [{"token": int(t)} for t in window]
        pt_res = pt_engine.decide_window(events)
        ort_res = onnx_engine.decide_window(events)

        if pt_res["decision"] == ort_res["decision"]:
            identical_decisions += 1
        else:
            decision_mismatches.append(
                {
                    "window_index": idx,
                    "pytorch_decision": pt_res["decision"],
                    "onnx_decision": ort_res["decision"],
                    "pytorch_score": pt_res["lstm_anomaly_score"],
                    "onnx_score": ort_res["lstm_anomaly_score"],
                }
            )

    pct_identical = (identical_decisions / len(test_windows)) * 100.0 if test_windows else 0.0

    pt_mean_lat = float(np.mean(pt_latencies_ms))
    ort_mean_lat = float(np.mean(ort_latencies_ms))
    speedup = pt_mean_lat / ort_mean_lat if ort_mean_lat > 0 else 1.0

    report = {
        "status": "PASS" if pct_identical == 100.0 and max(logit_diffs) < 1e-4 else "FAIL",
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "onnx_model_path": (
            onnx_path.relative_to(ROOT_DIR).as_posix() if onnx_path.is_relative_to(ROOT_DIR) else onnx_path.as_posix()
        ),
        "opset_version": 17,
        "runs_parity": {
            "runs_evaluated": len(runs),
            "max_logit_difference": float(np.max(logit_diffs)),
            "mean_logit_difference": float(np.mean(logit_diffs)),
            "p99_logit_difference": float(np.percentile(logit_diffs, 99)),
            "max_loss_difference": float(np.max(loss_diffs)),
            "mean_loss_difference": float(np.mean(loss_diffs)),
        },
        "window_decisions_parity": {
            "windows_evaluated": len(test_windows),
            "identical_decisions_count": identical_decisions,
            "identical_decisions_percentage": round(pct_identical, 4),
            "mismatch_count": len(decision_mismatches),
            "test_period_context": "exploratory temporal test set (inspected for drift)",
        },
        "cpu_latency_benchmark": {
            "pytorch_cpu_ms": {
                "mean": round(pt_mean_lat, 4),
                "p50": round(float(np.percentile(pt_latencies_ms, 50)), 4),
                "p95": round(float(np.percentile(pt_latencies_ms, 95)), 4),
                "p99": round(float(np.percentile(pt_latencies_ms, 99)), 4),
            },
            "onnxruntime_cpu_ms": {
                "mean": round(ort_mean_lat, 4),
                "p50": round(float(np.percentile(ort_latencies_ms, 50)), 4),
                "p95": round(float(np.percentile(ort_latencies_ms, 95)), 4),
                "p99": round(float(np.percentile(ort_latencies_ms, 99)), 4),
            },
            "speedup_factor": round(speedup, 2),
        },
        "environment": {
            "os": platform.platform(),
            "processor": platform.processor(),
            "python_version": platform.python_version(),
            "torch_version": torch.__version__,
            "onnx_version": onnx.__version__,
            "onnxruntime_version": ort.__version__,
        },
    }

    return report
