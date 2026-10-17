import json
import re
from pathlib import Path

import numpy as np
import pandas as pd
import torch
import torch.nn.functional as F
from sklearn.metrics import (
    average_precision_score,
    confusion_matrix,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)
from torch import nn
from torch.nn.utils.rnn import pad_sequence

ROOT = Path(__file__).resolve().parents[1]
SEQUENCE_PATH = ROOT / "data/processed/bgl_event_sequences_v2.npz"
MODEL_PATH = ROOT / "artifacts/lstm_next_event.pt"
THRESHOLD_PATH = ROOT / "artifacts/lstm_known_run_threshold.json"

OUTPUT_PATH = ROOT / "data/processed/lstm_known_run_test_scores.parquet"
REPORT_PATH = ROOT / "reports/lstm_known_run_test_report.json"

BATCH_SIZE = 512
TEST_SPLIT_CODE = 2
UNKNOWN_TOKEN = 1
PADDING_TOKEN = 0


def load_threshold(path):
    data = json.loads(path.read_text(encoding="utf-8"))

    def search(obj):
        if isinstance(obj, dict):
            for key, value in obj.items():
                if "threshold" in key.lower() and isinstance(
                    value, (int, float)
                ):
                    return float(value)

            for value in obj.values():
                found = search(value)
                if found is not None:
                    return found

        return None

    value = search(data)
    if value is None:
        raise ValueError(
            f"Could not find a numeric threshold in {path}. "
            f"Found keys: {list(data.keys())}"
        )
    return value


def load_state_dict(path):
    checkpoint = torch.load(
        path,
        map_location="cpu",
        weights_only=True,
    )

    if isinstance(checkpoint, dict):
        for key in ("model_state_dict", "state_dict", "model"):
            value = checkpoint.get(key)
            if isinstance(value, dict) and any(
                torch.is_tensor(v) for v in value.values()
            ):
                checkpoint = value
                break

    if not isinstance(checkpoint, dict):
        raise TypeError("Checkpoint does not contain a model state dictionary.")

    state = {
        k: v
        for k, v in checkpoint.items()
        if torch.is_tensor(v)
    }

    # Remove the common DataParallel prefix if present.
    if state and all(k.startswith("module.") for k in state):
        state = {k[len("module."):]: v for k, v in state.items()}

    return state


class ReconstructedLSTM(nn.Module):
    """Reconstruct the saved embedding, LSTM and projection layer."""

    def __init__(self, state):
        super().__init__()

        lstm_ih_key = next(
            (k for k in state if k.endswith("weight_ih_l0")), None
        )
        lstm_hh_key = next(
            (k for k in state if k.endswith("weight_hh_l0")), None
        )

        if not lstm_ih_key or not lstm_hh_key:
            raise ValueError(
                "Could not identify standard LSTM weights. "
                f"Checkpoint keys: {list(state.keys())}"
            )

        lstm_prefix = lstm_ih_key.rsplit(".", 1)[0]
        lstm_weight_ih = state[lstm_ih_key]
        lstm_weight_hh = state[lstm_hh_key]

        input_size = int(lstm_weight_ih.shape[1])
        hidden_size = int(lstm_weight_hh.shape[1])
        vocab_candidates = []

        for key, tensor in state.items():
            if (
                key.endswith(".weight")
                and tensor.ndim == 2
                and tensor.shape[1] == input_size
                and not key.startswith(lstm_prefix + ".")
                and tensor.shape[0] > tensor.shape[1]
            ):
                vocab_candidates.append((key, tensor))

        if not vocab_candidates:
            raise ValueError(
                "Could not identify the embedding matrix in the checkpoint."
            )

        # The embedding matrix has shape [vocabulary size, embedding size].
        embedding_key, embedding_weight = vocab_candidates[0]
        embedding_prefix = embedding_key.rsplit(".", 1)[0]
        vocab_size = int(embedding_weight.shape[0])
        embedding_dim = int(embedding_weight.shape[1])

        reverse = any(
            k.endswith("weight_ih_l0_reverse") for k in state
        )
        directions = 2 if reverse else 1

        layer_ids = []
        for key in state:
            match = re.search(r"weight_ih_l(\d+)$", key)
            if match and key.startswith(lstm_prefix + "."):
                layer_ids.append(int(match.group(1)))
        num_layers = max(layer_ids, default=0) + 1

        output_size = hidden_size * directions
        projection_candidates = [
            key
            for key, tensor in state.items()
            if key.endswith(".weight")
            and tuple(tensor.shape) == (vocab_size, output_size)
            and not key.startswith(lstm_prefix + ".")
            and key != embedding_key
        ]

        if not projection_candidates:
            raise ValueError(
                "Could not identify the output projection layer. "
                "Please share the checkpoint-key error."
            )

        projection_key = projection_candidates[-1]
        projection_prefix = projection_key.rsplit(".", 1)[0]

        # These checks avoid silently loading an incompatible architecture.
        names = [
            embedding_prefix.split(".")[-1],
            lstm_prefix.split(".")[-1],
            projection_prefix.split(".")[-1],
        ]
        if len(set(names)) != 3:
            raise ValueError(
                "The checkpoint uses overlapping module names; "
                "the architecture needs manual reconstruction."
            )

        self.embedding_name = names[0]
        self.lstm_name = names[1]
        self.projection_name = names[2]

        self.add_module(
            self.embedding_name,
            nn.Embedding(vocab_size, embedding_dim, padding_idx=0),
        )
        self.add_module(
            self.lstm_name,
            nn.LSTM(
                input_size=embedding_dim,
                hidden_size=hidden_size,
                num_layers=num_layers,
                batch_first=True,
                bidirectional=reverse,
            ),
        )
        self.add_module(
            self.projection_name,
            nn.Linear(output_size, vocab_size),
        )

        self.load_state_dict(state, strict=True)

    def forward(self, tokens):
        embedded = getattr(self, self.embedding_name)(tokens)
        outputs, _ = getattr(self, self.lstm_name)(embedded)
        return getattr(self, self.projection_name)(outputs)


def collect_known_runs(sequence_rows, global_indices):
    runs = []

    for sequence, global_index in zip(sequence_rows, global_indices):
        current = []

        def save_run():
            if len(current) >= 2:
                runs.append(
                    (int(global_index), np.asarray(current, dtype=np.int64))
                )

        for token in sequence:
            token = int(token)

            if token >= 2:
                current.append(token)
            else:
                # Both UNK (1) and PAD (0) break a known-event run.
                save_run()
                current = []

        save_run()

    return runs


def main():
    for path in (SEQUENCE_PATH, MODEL_PATH, THRESHOLD_PATH):
        if not path.exists():
            raise FileNotFoundError(f"Required file not found: {path}")

    data = np.load(SEQUENCE_PATH)
    sequences = data["sequences"]
    labels = data["targets"].astype(np.int64)
    split_codes = data["split_codes"]

    test_indices = np.flatnonzero(split_codes == TEST_SPLIT_CODE)
    if len(test_indices) == 0:
        raise ValueError("No test windows found for split code 2.")

    threshold = load_threshold(THRESHOLD_PATH)
    state = load_state_dict(MODEL_PATH)
    model = ReconstructedLSTM(state)
    model.eval()

    test_sequences = sequences[test_indices]
    test_labels = labels[test_indices]

    print("========== LSTM KNOWN-RUN TEST EVALUATION ==========")
    print(f"Test windows: {len(test_indices):,}")
    print(f"Fixed validation threshold: {threshold:.6f}")
    print("Threshold selection on test data: DISABLED")

    runs = collect_known_runs(test_sequences, test_indices)
    print(f"Known runs to score: {len(runs):,}")

    if not runs:
        raise ValueError("No known runs of at least two events were found.")

    score_sums = {}
    transition_counts = {}

    with torch.inference_mode():
        total_batches = (len(runs) + BATCH_SIZE - 1) // BATCH_SIZE

        for batch_number, offset in enumerate(
            range(0, len(runs), BATCH_SIZE), start=1
        ):
            batch = runs[offset:offset + BATCH_SIZE]

            inputs = [
                torch.tensor(run[:-1], dtype=torch.long)
                for _, run in batch
            ]
            targets = [
                torch.tensor(run[1:], dtype=torch.long)
                for _, run in batch
            ]

            padded_inputs = pad_sequence(
                inputs, batch_first=True, padding_value=PADDING_TOKEN
            )
            padded_targets = pad_sequence(
                targets, batch_first=True, padding_value=PADDING_TOKEN
            )

            logits = model(padded_inputs)
            vocab_size = logits.shape[-1]

            token_losses = F.cross_entropy(
                logits.reshape(-1, vocab_size),
                padded_targets.reshape(-1),
                reduction="none",
                ignore_index=PADDING_TOKEN,
            ).reshape_as(padded_targets)

            batch_loss_sums = token_losses.sum(dim=1).cpu().numpy()
            batch_counts = (
                padded_targets.ne(PADDING_TOKEN).sum(dim=1).cpu().numpy()
            )

            for i, (window_index, _) in enumerate(batch):
                count = int(batch_counts[i])
                if count == 0:
                    continue

                score_sums[window_index] = (
                    score_sums.get(window_index, 0.0)
                    + float(batch_loss_sums[i])
                )
                transition_counts[window_index] = (
                    transition_counts.get(window_index, 0) + count
                )

            if batch_number % 10 == 0 or batch_number == total_batches:
                print(f"Scored batches: {batch_number}/{total_batches}")

    rows = []
    for global_index, label in zip(test_indices, test_labels):
        if global_index not in transition_counts:
            continue

        score = (
            score_sums[global_index] / transition_counts[global_index]
        )
        rows.append({
            "window_index": int(global_index),
            "target_anomaly": int(label),
            "anomaly_score": float(score),
            "predicted_anomaly": int(score >= threshold),
            "scored_transitions": int(transition_counts[global_index]),
        })

    result = pd.DataFrame(rows)
    if result.empty:
        raise ValueError("No test windows were scorable.")

    y_true = result["target_anomaly"].to_numpy()
    y_score = result["anomaly_score"].to_numpy()
    y_pred = result["predicted_anomaly"].to_numpy()

    tn, fp, fn, tp = confusion_matrix(
        y_true, y_pred, labels=[0, 1]
    ).ravel()

    report = {
        "evaluation": "exploratory_temporal_test",
        "note": (
            "The test period was previously examined for unknown-event and "
            "score-drift diagnostics. Treat these results as exploratory, "
            "not as evidence from a completely untouched blind test."
        ),
        "scoring_method": (
            "Mean next-event cross-entropy over known-to-known transitions "
            "inside contiguous runs; UNK and padding break runs."
        ),
        "threshold_source": "Validation-selected threshold; fixed for test",
        "threshold": float(threshold),
        "total_test_windows": len(test_indices),
        "scorable_windows": len(result),
        "unscorable_windows": int(len(test_indices) - len(result)),
        "scorable_coverage": float(len(result) / len(test_indices)),
        "unscorable_anomalous_windows": int(
            np.sum(test_labels[
                ~np.isin(test_indices, result["window_index"].to_numpy())
            ] == 1)
        ),
        "unscorable_normal_windows": int(
            np.sum(test_labels[
                ~np.isin(test_indices, result["window_index"].to_numpy())
            ] == 0)
        ),
        "scorable_anomalous_windows": int(np.sum(y_true == 1)),
        "scorable_normal_windows": int(np.sum(y_true == 0)),
        "precision": float(precision_score(
            y_true, y_pred, zero_division=0
        )),
        "recall": float(recall_score(
            y_true, y_pred, zero_division=0
        )),
        "f1_score": float(f1_score(
            y_true, y_pred, zero_division=0
        )),
        "pr_auc": float(average_precision_score(y_true, y_score)),
        "roc_auc": float(roc_auc_score(y_true, y_score)),
        "true_positives": int(tp),
        "false_positives": int(fp),
        "true_negatives": int(tn),
        "false_negatives": int(fn),
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)

    result.to_parquet(OUTPUT_PATH, index=False)
    REPORT_PATH.write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )

    print("\n========== KNOWN-RUN TEST RESULTS ==========")
    print(f"Total test windows: {report['total_test_windows']:,}")
    print(f"Scorable windows: {report['scorable_windows']:,}")
    print(f"Unscorable windows: {report['unscorable_windows']:,}")
    print(f"Scorable coverage: {report['scorable_coverage']:.2%}")
    print(f"Fixed threshold: {threshold:.6f}")
    print(f"Precision: {report['precision']:.4f}")
    print(f"Recall: {report['recall']:.4f}")
    print(f"F1-score: {report['f1_score']:.4f}")
    print(f"PR-AUC: {report['pr_auc']:.4f}")
    print(f"ROC-AUC: {report['roc_auc']:.4f}")
    print(f"TP: {tp} | FP: {fp} | TN: {tn} | FN: {fn}")
    print(f"\nScores: {OUTPUT_PATH}")
    print(f"Report: {REPORT_PATH}")
    print("\nSUCCESS: Test evaluation completed without threshold tuning.")


if __name__ == "__main__":
    main()
