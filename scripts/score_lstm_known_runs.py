"""Score LSTM sequences using only contiguous runs of known events.

Unknown-event novelty is measured separately. Sequences without a
known-to-known transition are marked unscorable, not normal.
"""

import json
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import torch
from sklearn.metrics import (
    average_precision_score,
    confusion_matrix,
    f1_score,
    precision_recall_curve,
    precision_score,
    recall_score,
    roc_auc_score,
)
from torch import nn

ROOT = Path(__file__).resolve().parents[1]

SEQUENCE_PATH = ROOT / "data" / "processed" / "bgl_event_sequences_v2.npz"
VOCAB_PATH = ROOT / "artifacts" / "event_token_vocabulary_v2.json"
MODEL_PATH = ROOT / "artifacts" / "lstm_next_event.pt"

OUTPUT_PATH = ROOT / "data" / "processed" / "lstm_known_run_validation_scores.parquet"
THRESHOLD_PATH = ROOT / "artifacts" / "lstm_known_run_threshold.json"
REPORT_PATH = ROOT / "reports" / "lstm_known_run_validation_report.json"

DEVICE = torch.device("cpu")
BATCH_SIZE = 512

PAD_TOKEN = 0
UNKNOWN_TOKEN = 1
VALIDATION_CODE = 1


class LSTMNextEvent(nn.Module):
    """Architecture used by the trained next-event predictor."""

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
            padding_idx=PAD_TOKEN,
        )

        self.lstm = nn.LSTM(
            embedding_dim,
            hidden_dim,
            batch_first=True,
        )

        self.output = nn.Linear(hidden_dim, vocab_size)

    def forward(self, tokens):
        embedded = self.embedding(tokens)
        hidden, _ = self.lstm(embedded)
        return self.output(hidden)


def collect_known_runs(sequences, lengths, window_indices):
    """Return runs of consecutive known events, length at least two."""
    run_windows = []
    runs = []

    for window_index in window_indices:
        length = int(lengths[window_index])
        sequence = sequences[window_index, :length]

        start = None

        for position in range(length + 1):
            is_known = position < length and int(sequence[position]) >= 2

            if is_known and start is None:
                start = position

            elif not is_known and start is not None:
                run = sequence[start:position]

                # At least two known events are needed for a prediction.
                if len(run) >= 2:
                    run_windows.append(int(window_index))
                    runs.append(run.astype(np.int64, copy=True))

                start = None

    return run_windows, runs


def score_known_runs(model, runs, run_windows, total_windows):
    """Aggregate prediction losses across known-only event transitions."""
    model.eval()

    score_sums = np.zeros(total_windows, dtype=np.float64)
    transition_counts = np.zeros(total_windows, dtype=np.int64)

    total_batches = (len(runs) + BATCH_SIZE - 1) // BATCH_SIZE
    loss_function = nn.CrossEntropyLoss(reduction="none")

    print(f"Known runs to score: {len(runs):,}")
    print(f"Scoring batches: {total_batches:,}\n")

    with torch.no_grad():
        for batch_number, offset in enumerate(
            range(0, len(runs), BATCH_SIZE),
            start=1,
        ):
            batch_runs = runs[offset : offset + BATCH_SIZE]
            batch_windows = run_windows[offset : offset + BATCH_SIZE]

            max_steps = max(len(run) - 1 for run in batch_runs)
            batch_size = len(batch_runs)

            inputs = np.zeros(
                (batch_size, max_steps),
                dtype=np.int64,
            )
            targets = np.zeros_like(inputs)
            valid_lengths = np.zeros(batch_size, dtype=np.int64)

            for row_index, run in enumerate(batch_runs):
                inputs[row_index, : len(run) - 1] = run[:-1]
                targets[row_index, : len(run) - 1] = run[1:]
                valid_lengths[row_index] = len(run) - 1

            input_tensor = torch.as_tensor(inputs, device=DEVICE)
            target_tensor = torch.as_tensor(targets, device=DEVICE)

            logits = model(input_tensor)

            losses = loss_function(
                logits.reshape(-1, logits.shape[-1]),
                target_tensor.reshape(-1),
            ).reshape(batch_size, max_steps)

            positions = torch.arange(
                max_steps,
                device=DEVICE,
            ).unsqueeze(0)

            valid_mask = positions < torch.as_tensor(
                valid_lengths,
                device=DEVICE,
            ).unsqueeze(1)

            masked_losses = losses * valid_mask.float()
            run_loss_sums = masked_losses.sum(dim=1).cpu().numpy()

            for row_index, window_index in enumerate(batch_windows):
                score_sums[window_index] += run_loss_sums[row_index]
                transition_counts[window_index] += valid_lengths[row_index]

            if batch_number % 10 == 0 or batch_number == total_batches:
                print(f"Scored batch {batch_number:,}/{total_batches:,}")

    scores = np.full(total_windows, np.nan, dtype=np.float64)

    scorable = transition_counts > 0
    scores[scorable] = score_sums[scorable] / transition_counts[scorable]

    return scores, transition_counts


def evaluate_at_threshold(y_true, scores, threshold):
    predictions = (scores >= threshold).astype(np.int8)

    tn, fp, fn, tp = confusion_matrix(
        y_true,
        predictions,
        labels=[0, 1],
    ).ravel()

    normal_count = int((y_true == 0).sum())

    return {
        "threshold": float(threshold),
        "scored_windows": len(y_true),
        "actual_anomalous_windows": int(y_true.sum()),
        "predicted_anomalous_windows": int(predictions.sum()),
        "true_positives": int(tp),
        "false_positives": int(fp),
        "true_negatives": int(tn),
        "false_negatives": int(fn),
        "precision": float(precision_score(y_true, predictions, zero_division=0)),
        "recall": float(recall_score(y_true, predictions, zero_division=0)),
        "f1": float(f1_score(y_true, predictions, zero_division=0)),
        "false_positive_rate": float(fp / (fp + tn) if (fp + tn) else 0.0),
        "false_positives_per_1000_normal_windows": float(fp / normal_count * 1000 if normal_count else 0.0),
        "pr_auc_average_precision": float(average_precision_score(y_true, scores)),
        "roc_auc": float(roc_auc_score(y_true, scores)),
        "score_min": float(scores.min()),
        "score_median": float(np.median(scores)),
        "score_max": float(scores.max()),
    }


def main():
    required_files = [SEQUENCE_PATH, VOCAB_PATH, MODEL_PATH]
    missing = [str(path) for path in required_files if not path.is_file()]

    if missing:
        raise FileNotFoundError("Required model/data files missing:\n" + "\n".join(missing))

    outputs = [OUTPUT_PATH, THRESHOLD_PATH, REPORT_PATH]
    existing = [str(path) for path in outputs if path.exists()]

    if existing:
        raise SystemExit(
            "Corrected scoring output already exists. Inspect before "
            "rerunning to avoid overwriting:\n" + "\n".join(existing)
        )

    with np.load(SEQUENCE_PATH) as data:
        sequences = data["sequences"].copy()
        lengths = data["lengths"].copy()
        targets = data["targets"].copy()
        split_codes = data["split_codes"].copy()
        window_ids = data["window_ids"].copy()
        start_lines = data["start_lines"].copy()
        end_lines = data["end_lines"].copy()
        start_timestamps = data["start_timestamps"].copy()
        end_timestamps = data["end_timestamps"].copy()

    with VOCAB_PATH.open("r", encoding="utf-8") as file:
        vocabulary = json.load(file)

    if vocabulary["padding_token"] != PAD_TOKEN:
        raise ValueError("Unexpected padding token.")

    if vocabulary["unknown_token"] != UNKNOWN_TOKEN:
        raise ValueError("Unexpected unknown-event token.")

    checkpoint = torch.load(
        MODEL_PATH,
        map_location=DEVICE,
        weights_only=True,
    )

    config = checkpoint["config"]

    model = LSTMNextEvent(
        vocab_size=config["vocab_size"],
        embedding_dim=config["embedding_dim"],
        hidden_dim=config["hidden_dim"],
    ).to(DEVICE)

    model.load_state_dict(checkpoint["model_state_dict"])

    validation_indices = np.flatnonzero(split_codes == VALIDATION_CODE)

    if len(validation_indices) == 0:
        raise RuntimeError("No validation windows found.")

    print("========== LSTM KNOWN-RUN SCORING ==========")
    print(f"Validation windows: {len(validation_indices):,}")
    print("Unknown tokens and transitions touching them will not be scored.")
    print("Test data will not be used for threshold selection.\n")

    run_windows, runs = collect_known_runs(
        sequences,
        lengths,
        validation_indices,
    )

    scores, transition_counts = score_known_runs(
        model,
        runs,
        run_windows,
        len(sequences),
    )

    validation_scorable = validation_indices[transition_counts[validation_indices] > 0]

    validation_unscorable = validation_indices[transition_counts[validation_indices] == 0]

    y_all = targets[validation_indices].astype(np.int8)
    y_scored = targets[validation_scorable].astype(np.int8)
    scored_values = scores[validation_scorable]

    if len(np.unique(y_scored)) != 2:
        raise RuntimeError("Scored validation windows must include both target classes.")

    precisions, recalls, thresholds = precision_recall_curve(
        y_scored,
        scored_values,
    )

    if len(thresholds) == 0:
        raise RuntimeError("No validation thresholds were produced.")

    f1_values = 2 * precisions[:-1] * recalls[:-1] / (precisions[:-1] + recalls[:-1] + 1e-12)

    best_f1 = float(f1_values.max())

    tied = np.flatnonzero(np.isclose(f1_values, best_f1, rtol=1e-10, atol=1e-12))

    best_index = max(
        tied,
        key=lambda i: (precisions[i], thresholds[i]),
    )

    threshold = float(thresholds[best_index])

    metrics = evaluate_at_threshold(
        y_scored,
        scored_values,
        threshold,
    )

    normal_total = int((targets[validation_indices] == 0).sum())
    anomaly_total = int((targets[validation_indices] == 1).sum())

    normal_unscorable = int((targets[validation_unscorable] == 0).sum())
    anomaly_unscorable = int((targets[validation_unscorable] == 1).sum())

    predictions = (scored_values >= threshold).astype(np.int8)

    result_table = pa.table(
        {
            "window_id": window_ids[validation_scorable],
            "split": ["validation"] * len(validation_scorable),
            "start_line": start_lines[validation_scorable],
            "end_line": end_lines[validation_scorable],
            "start_timestamp": start_timestamps[validation_scorable],
            "end_timestamp": end_timestamps[validation_scorable],
            "target_anomaly": y_scored,
            "known_transition_count": transition_counts[validation_scorable],
            "anomaly_score": scored_values,
            "predicted_anomaly": predictions,
        }
    )

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    THRESHOLD_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)

    pq.write_table(result_table, OUTPUT_PATH, compression="zstd")

    threshold_info = {
        "model": "LSTMNextEventKnownRuns",
        "threshold": threshold,
        "scoring_method": (
            "Average next-event cross-entropy across contiguous runs of known training-vocabulary events only"
        ),
        "unknown_token_scored": False,
        "transitions_touching_unknown_events_scored": False,
        "unscorable_window_policy": (
            "Mark unscorable and route to separate novelty/drift monitoring; do not classify as normal"
        ),
        "selected_using_validation_only": True,
        "test_used_for_threshold_selection": False,
    }

    with THRESHOLD_PATH.open("w", encoding="utf-8") as file:
        json.dump(threshold_info, file, indent=2)

    report = {
        "dataset": "LogHub BGL",
        "model": "LSTM next-event predictor",
        "split_evaluated": "validation",
        "validation_windows_total": len(validation_indices),
        "scorable_windows": len(validation_scorable),
        "unscorable_windows": len(validation_unscorable),
        "scorable_window_percentage": round(
            100 * len(validation_scorable) / len(validation_indices),
            4,
        ),
        "unscorable_anomalous_windows": anomaly_unscorable,
        "unscorable_normal_windows": normal_unscorable,
        "total_anomalous_windows": anomaly_total,
        "total_normal_windows": normal_total,
        "metrics_computed_only_on_scorable_windows": metrics,
        "threshold_selection": threshold_info,
        "test_data_used": False,
        "outputs": {
            "scores": str(OUTPUT_PATH.relative_to(ROOT)),
            "threshold": str(THRESHOLD_PATH.relative_to(ROOT)),
        },
    }

    with REPORT_PATH.open("w", encoding="utf-8") as file:
        json.dump(report, file, indent=2, ensure_ascii=False)

    print("\n========== CORRECTED VALIDATION RESULTS ==========")
    print(f"Validation windows: {len(validation_indices):,}")
    print(f"Scorable windows: {len(validation_scorable):,}")
    print(f"Unscorable windows: {len(validation_unscorable):,}")
    print(f"Scorable coverage: {report['scorable_window_percentage']:.2f}%")
    print(f"Unscorable anomalous windows: {anomaly_unscorable:,}")
    print(f"Unscorable normal windows: {normal_unscorable:,}")

    print(f"\nSelected threshold: {threshold:.6f}")
    print(f"Precision: {metrics['precision']:.4f}")
    print(f"Recall: {metrics['recall']:.4f}")
    print(f"F1-score: {metrics['f1']:.4f}")
    print(f"PR-AUC: {metrics['pr_auc_average_precision']:.4f}")
    print(f"ROC-AUC: {metrics['roc_auc']:.4f}")
    print(f"False positives: {metrics['false_positives']:,}")
    print(f"False negatives: {metrics['false_negatives']:,}")

    print(f"\nScored windows: {OUTPUT_PATH}")
    print(f"Threshold: {THRESHOLD_PATH}")
    print(f"Report: {REPORT_PATH}")
    print("\nSUCCESS: Corrected validation scoring completed.")


if __name__ == "__main__":
    main()
