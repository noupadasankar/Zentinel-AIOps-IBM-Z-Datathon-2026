
"""Evaluate saved Isolation Forest and LSTM on the chronological test set."""

import json
import time
from pathlib import Path

import joblib
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import torch
from sklearn.metrics import (
    average_precision_score,
    confusion_matrix,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)
from torch import nn

ROOT = Path(__file__).resolve().parents[1]

FEATURE_PATH = ROOT / "data" / "processed" / "bgl_window_features.parquet"
MANIFEST_PATH = ROOT / "reports" / "window_feature_columns.json"

IF_MODEL_PATH = ROOT / "artifacts" / "isolation_forest_baseline.joblib"
IF_THRESHOLD_PATH = ROOT / "artifacts" / "isolation_forest_threshold.json"
IF_VALIDATION_REPORT = (
    ROOT / "reports" / "isolation_forest_validation_report.json"
)

SEQUENCE_PATH = ROOT / "data" / "processed" / "bgl_event_sequences_v2.npz"
VOCAB_PATH = ROOT / "artifacts" / "event_token_vocabulary_v2.json"
LSTM_MODEL_PATH = ROOT / "artifacts" / "lstm_next_event.pt"
LSTM_THRESHOLD_PATH = ROOT / "artifacts" / "lstm_threshold.json"
LSTM_VALIDATION_REPORT = ROOT / "reports" / "lstm_validation_report.json"

OUTPUT_PATH = ROOT / "data" / "processed" / "test_model_comparison.parquet"
REPORT_PATH = ROOT / "reports" / "test_model_comparison.json"

BATCH_SIZE = 256
DEVICE = torch.device("cpu")

METADATA_COLUMNS = [
    "window_id",
    "split",
    "start_line",
    "end_line",
    "start_timestamp",
    "end_timestamp",
    "target_anomaly",
]


class LSTMNextEvent(nn.Module):
    """Must match the architecture used during training."""

    def __init__(
        self,
        vocab_size: int,
        embedding_dim: int,
        hidden_dim: int,
    ):
        super().__init__()

        self.embedding = nn.Embedding(
            vocab_size,
            embedding_dim,
            padding_idx=0,
        )

        self.lstm = nn.LSTM(
            input_size=embedding_dim,
            hidden_size=hidden_dim,
            batch_first=True,
        )

        self.output = nn.Linear(hidden_dim, vocab_size)

    def forward(self, tokens: torch.Tensor) -> torch.Tensor:
        embedded = self.embedding(tokens)
        hidden, _ = self.lstm(embedded)
        return self.output(hidden)


def score_sequences(model, sequences, lengths, indices):
    """Compute mean next-event cross-entropy per real sequence position."""

    model.eval()
    loss_function = nn.CrossEntropyLoss(reduction="none")
    result = []

    with torch.no_grad():
        for offset in range(0, len(indices), BATCH_SIZE):
            selected = indices[offset:offset + BATCH_SIZE]

            tokens = torch.as_tensor(
                sequences[selected],
                dtype=torch.long,
                device=DEVICE,
            )

            batch_lengths = torch.as_tensor(
                lengths[selected],
                dtype=torch.long,
                device=DEVICE,
            )

            inputs = tokens[:, :-1]
            targets = tokens[:, 1:]

            logits = model(inputs)

            losses = loss_function(
                logits.reshape(-1, logits.shape[-1]),
                targets.reshape(-1),
            ).reshape_as(targets)

            positions = torch.arange(
                targets.shape[1],
                device=DEVICE,
            ).unsqueeze(0)

            valid_mask = positions < (batch_lengths.unsqueeze(1) - 1)

            summed_loss = (losses * valid_mask.float()).sum(dim=1)
            valid_count = valid_mask.sum(dim=1).clamp_min(1)

            scores = summed_loss / valid_count
            result.extend(scores.cpu().numpy().tolist())

    return np.asarray(result, dtype=np.float64)


def calculate_metrics(y_true, scores, threshold, elapsed_seconds):
    """Evaluate fixed-threshold predictions and score ranking."""

    y_true = np.asarray(y_true, dtype=np.int8)
    scores = np.asarray(scores, dtype=np.float64)

    predictions = (scores >= threshold).astype(np.int8)

    tn, fp, fn, tp = confusion_matrix(
        y_true,
        predictions,
        labels=[0, 1],
    ).ravel()

    normal_count = int((y_true == 0).sum())

    return {
        "threshold": float(threshold),
        "windows_evaluated": len(y_true),
        "actual_anomalous_windows": int(y_true.sum()),
        "predicted_anomalous_windows": int(predictions.sum()),
        "true_positives": int(tp),
        "false_positives": int(fp),
        "true_negatives": int(tn),
        "false_negatives": int(fn),
        "precision": float(
            precision_score(y_true, predictions, zero_division=0)
        ),
        "recall": float(
            recall_score(y_true, predictions, zero_division=0)
        ),
        "f1": float(
            f1_score(y_true, predictions, zero_division=0)
        ),
        "false_positive_rate": float(
            fp / (fp + tn) if (fp + tn) else 0.0
        ),
        "false_positive_windows_per_1000_normal_windows": float(
            fp / normal_count * 1000 if normal_count else 0.0
        ),
        "average_precision_pr_auc": float(
            average_precision_score(y_true, scores)
        ),
        "roc_auc": float(roc_auc_score(y_true, scores)),
        "score_min": float(scores.min()),
        "score_median": float(np.median(scores)),
        "score_max": float(scores.max()),
        "scoring_seconds": float(elapsed_seconds),
        "scoring_windows_per_second": float(
            len(y_true) / elapsed_seconds if elapsed_seconds > 0 else 0.0
        ),
    }


def main():
    required_files = [
        FEATURE_PATH,
        MANIFEST_PATH,
        IF_MODEL_PATH,
        IF_THRESHOLD_PATH,
        IF_VALIDATION_REPORT,
        SEQUENCE_PATH,
        VOCAB_PATH,
        LSTM_MODEL_PATH,
        LSTM_THRESHOLD_PATH,
        LSTM_VALIDATION_REPORT,
    ]

    for path in required_files:
        if not path.is_file():
            raise FileNotFoundError(f"Required file missing: {path}")

    if OUTPUT_PATH.exists() or REPORT_PATH.exists():
        raise SystemExit(
            "Test evaluation output already exists. Inspect it before "
            "rerunning to avoid overwriting results."
        )

    with MANIFEST_PATH.open("r", encoding="utf-8") as file:
        feature_manifest = json.load(file)

    with IF_THRESHOLD_PATH.open("r", encoding="utf-8") as file:
        if_threshold_info = json.load(file)

    with IF_VALIDATION_REPORT.open("r", encoding="utf-8") as file:
        if_validation_report = json.load(file)

    with LSTM_THRESHOLD_PATH.open("r", encoding="utf-8") as file:
        lstm_threshold_info = json.load(file)

    with LSTM_VALIDATION_REPORT.open("r", encoding="utf-8") as file:
        lstm_validation_report = json.load(file)

    feature_columns = feature_manifest["feature_columns"]
    if_threshold = float(if_threshold_info["threshold"])
    lstm_threshold = float(lstm_threshold_info["threshold"])

    print("========== TEST-SET MODEL EVALUATION ==========")
    print("Using existing saved models and validation thresholds.")
    print(f"Isolation Forest threshold: {if_threshold:.8f}")
    print(f"LSTM threshold: {lstm_threshold:.6f}")
    print("No model fitting or threshold tuning will occur.\n")

    # ---------- Isolation Forest ----------
    feature_table = pq.read_table(
        FEATURE_PATH,
        columns=METADATA_COLUMNS + feature_columns,
    )
    feature_data = feature_table.to_pandas()

    test_frame = (
        feature_data.loc[feature_data["split"] == "test"]
        .sort_values("window_id")
        .reset_index(drop=True)
    )

    if test_frame.empty:
        raise RuntimeError("No test windows found in feature dataset.")

    X_test = test_frame[feature_columns].astype(np.float32)

    if not np.isfinite(X_test.to_numpy()).all():
        raise ValueError("Test features contain NaN or infinite values.")

    if_model = joblib.load(IF_MODEL_PATH)

    start = time.perf_counter()
    if_scores = -if_model.decision_function(X_test)
    if_elapsed = time.perf_counter() - start

    y_test = test_frame["target_anomaly"].to_numpy(dtype=np.int8)

    if_metrics = calculate_metrics(
        y_test,
        if_scores,
        if_threshold,
        if_elapsed,
    )

    # ---------- LSTM next-event predictor ----------
    with np.load(SEQUENCE_PATH) as data:
        sequences = data["sequences"].copy()
        lengths = data["lengths"].copy()
        targets = data["targets"].copy()
        split_codes = data["split_codes"].copy()
        sequence_window_ids = data["window_ids"].copy()
        sequence_start_lines = data["start_lines"].copy()
        sequence_end_lines = data["end_lines"].copy()
        sequence_start_ts = data["start_timestamps"].copy()
        sequence_end_ts = data["end_timestamps"].copy()

    with VOCAB_PATH.open("r", encoding="utf-8") as file:
        vocabulary = json.load(file)

    checkpoint = torch.load(
        LSTM_MODEL_PATH,
        map_location=DEVICE,
        weights_only=True,
    )

    model_config = checkpoint["config"]

    if model_config["vocab_size"] != vocabulary["vocabulary_size"]:
        raise ValueError("LSTM model and vocabulary sizes do not match.")

    if vocabulary["padding_token"] != 0 or vocabulary["unknown_token"] != 1:
        raise ValueError("Unexpected LSTM PAD/UNK token configuration.")

    lstm_model = LSTMNextEvent(
        vocab_size=model_config["vocab_size"],
        embedding_dim=model_config["embedding_dim"],
        hidden_dim=model_config["hidden_dim"],
    ).to(DEVICE)

    lstm_model.load_state_dict(checkpoint["model_state_dict"])

    test_indices = np.flatnonzero(split_codes == 2)

    expected_window_ids = sequence_window_ids[test_indices]
    observed_window_ids = test_frame["window_id"].to_numpy()

    if not np.array_equal(expected_window_ids, observed_window_ids):
        raise RuntimeError(
            "LSTM test sequences and feature test windows do not align."
        )

    if not np.array_equal(targets[test_indices], y_test):
        raise RuntimeError(
            "Test targets disagree between the sequence and feature datasets."
        )

    start = time.perf_counter()
    lstm_scores = score_sequences(
        lstm_model,
        sequences,
        lengths,
        test_indices,
    )
    lstm_elapsed = time.perf_counter() - start

    lstm_test_targets = targets[test_indices].astype(np.int8)

    lstm_metrics = calculate_metrics(
        lstm_test_targets,
        lstm_scores,
        lstm_threshold,
        lstm_elapsed,
    )

    # ---------- Save side-by-side results ----------
    results = test_frame[METADATA_COLUMNS].copy()

    results["isolation_forest_score"] = if_scores
    results["isolation_forest_prediction"] = (
        if_scores >= if_threshold
    ).astype(np.int8)

    results["lstm_score"] = lstm_scores
    results["lstm_prediction"] = (
        lstm_scores >= lstm_threshold
    ).astype(np.int8)

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    pq.write_table(
        pa.Table.from_pandas(results, preserve_index=False),
        OUTPUT_PATH,
        compression="zstd",
    )

    report = {
        "evaluation": "Chronological held-out test period",
        "test_period_previously_inspected_for_unknown_event_diagnostics": True,
        "test_labels_used_for_model_fitting": False,
        "test_labels_used_for_threshold_selection": False,
        "test_labels_used_for_exploratory_diagnostics": True,
        "thresholds_selected_on_validation_only": True,
        "test_windows": len(y_test),
        "test_anomalous_windows": int(y_test.sum()),
        "isolation_forest": {
            "selected_validation_threshold": if_threshold,
            "previous_validation_metrics": (
                if_validation_report["validation_metrics_at_selected_threshold"]
            ),
            "test_metrics": if_metrics,
        },
        "lstm_next_event": {
            "selected_validation_threshold": lstm_threshold,
            "previous_validation_metrics": (
                lstm_validation_report["validation_metrics"]
            ),
            "test_metrics": lstm_metrics,
        },
        "scored_test_windows_file": str(OUTPUT_PATH.relative_to(ROOT)),
    }

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)

    with REPORT_PATH.open("w", encoding="utf-8") as file:
        json.dump(report, file, indent=2, ensure_ascii=False)

    for model_name, metrics in [
        ("ISOLATION FOREST", if_metrics),
        ("LSTM NEXT-EVENT", lstm_metrics),
    ]:
        print(f"\n========== {model_name} TEST RESULTS ==========")
        print(f"Precision: {metrics['precision']:.4f}")
        print(f"Recall:    {metrics['recall']:.4f}")
        print(f"F1-score:  {metrics['f1']:.4f}")
        print(f"PR-AUC:    {metrics['average_precision_pr_auc']:.4f}")
        print(f"ROC-AUC:   {metrics['roc_auc']:.4f}")
        print(f"False positives: {metrics['false_positives']:,}")
        print(f"False negatives: {metrics['false_negatives']:,}")
        print(
            "False-positive windows per 1,000 normal windows: "
            f"{metrics['false_positive_windows_per_1000_normal_windows']:.2f}"
        )
        print(
            f"Scoring throughput: "
            f"{metrics['scoring_windows_per_second']:.2f} windows/second"
        )

    print(f"\nComparison dataset: {OUTPUT_PATH}")
    print(f"Report: {REPORT_PATH}")
    print("\nSUCCESS: Test evaluation completed without fitting or retuning.")


if __name__ == "__main__":
    main()
