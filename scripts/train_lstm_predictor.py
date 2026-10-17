
"""Train an LSTM next-event predictor for BGL log anomalies."""

import json
import random
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
from torch.utils.data import DataLoader, TensorDataset

ROOT = Path(__file__).resolve().parents[1]

SEQUENCE_PATH = ROOT / "data" / "processed" / "bgl_event_sequences_v2.npz"
VOCAB_PATH = ROOT / "artifacts" / "event_token_vocabulary_v2.json"

MODEL_PATH = ROOT / "artifacts" / "lstm_next_event.pt"
THRESHOLD_PATH = ROOT / "artifacts" / "lstm_threshold.json"

VALIDATION_SCORES_PATH = (
    ROOT / "data" / "processed" / "lstm_validation_scores.parquet"
)
REPORT_PATH = ROOT / "reports" / "lstm_validation_report.json"

SEED = 42
EMBEDDING_DIM = 32
HIDDEN_DIM = 64
BATCH_SIZE = 256
EPOCHS = 8
LEARNING_RATE = 1e-3

SPLIT_CODES = {
    "train": 0,
    "validation": 1,
    "test": 2,
}

METADATA_COLUMNS = [
    "window_id",
    "split",
    "start_line",
    "end_line",
    "start_timestamp",
    "end_timestamp",
    "target_anomaly",
]


def set_seed(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)


class LSTMNextEvent(nn.Module):
    """Predict the next event token at every sequence position."""

    def __init__(
        self,
        vocab_size: int,
        embedding_dim: int = EMBEDDING_DIM,
        hidden_dim: int = HIDDEN_DIM,
    ):
        super().__init__()

        self.embedding = nn.Embedding(
            num_embeddings=vocab_size,
            embedding_dim=embedding_dim,
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


def calculate_window_scores(
    model: nn.Module,
    sequences: np.ndarray,
    lengths: np.ndarray,
    indices: np.ndarray,
    device: torch.device,
    batch_size: int = BATCH_SIZE,
) -> np.ndarray:
    """Compute average next-event loss for each selected window."""

    model.eval()
    scores = []

    loss_function = nn.CrossEntropyLoss(reduction="none")

    with torch.no_grad():
        for offset in range(0, len(indices), batch_size):
            selected = indices[offset:offset + batch_size]

            token_batch = torch.as_tensor(
                sequences[selected],
                dtype=torch.long,
                device=device,
            )

            length_batch = torch.as_tensor(
                lengths[selected],
                dtype=torch.long,
                device=device,
            )

            inputs = token_batch[:, :-1]
            targets = token_batch[:, 1:]

            logits = model(inputs)

            token_losses = loss_function(
                logits.reshape(-1, logits.shape[-1]),
                targets.reshape(-1),
            ).reshape_as(targets)

            positions = torch.arange(
                targets.shape[1],
                device=device,
            ).unsqueeze(0)

            # A sequence of length L has L-1 next-event targets.
            # Do not score targets at padding positions.
            valid_mask = positions < (length_batch.unsqueeze(1) - 1)

            summed_loss = (
                token_losses * valid_mask.float()
            ).sum(dim=1)

            valid_counts = valid_mask.sum(dim=1).clamp_min(1)

            mean_loss = summed_loss / valid_counts

            scores.extend(mean_loss.cpu().numpy().tolist())

    return np.asarray(scores, dtype=np.float64)


def evaluate_scores(
    y_true: np.ndarray,
    scores: np.ndarray,
    threshold: float,
) -> dict:
    predictions = (scores >= threshold).astype(np.int8)

    tn, fp, fn, tp = confusion_matrix(
        y_true,
        predictions,
        labels=[0, 1],
    ).ravel()

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
            fp / (fp + tn) * 1000 if (fp + tn) else 0.0
        ),
        "average_precision_pr_auc": float(
            average_precision_score(y_true, scores)
        ),
        "roc_auc": float(roc_auc_score(y_true, scores)),
        "score_min": float(scores.min()),
        "score_median": float(np.median(scores)),
        "score_max": float(scores.max()),
    }


def main() -> None:
    if not SEQUENCE_PATH.is_file():
        raise FileNotFoundError(SEQUENCE_PATH)

    if not VOCAB_PATH.is_file():
        raise FileNotFoundError(VOCAB_PATH)

    outputs = [
        MODEL_PATH,
        THRESHOLD_PATH,
        VALIDATION_SCORES_PATH,
        REPORT_PATH,
    ]

    existing = [path for path in outputs if path.exists()]

    if existing:
        raise SystemExit(
            "Existing LSTM output files found. Inspect them before "
            "rerunning; this script will not overwrite them:\n"
            + "\n".join(str(path) for path in existing)
        )

    set_seed(SEED)
    device = torch.device("cpu")

    print("========== LSTM NEXT-EVENT PREDICTOR ==========")
    print(f"PyTorch version: {torch.__version__}")
    print(f"Device: {device}")
    print(f"Epochs: {EPOCHS}")
    print(f"Batch size: {BATCH_SIZE}")
    print(f"Embedding dimension: {EMBEDDING_DIM}")
    print(f"Hidden dimension: {HIDDEN_DIM}\n")

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

    if vocabulary["padding_token"] != 0:
        raise ValueError("Expected PAD token 0.")

    if vocabulary["unknown_token"] != 1:
        raise ValueError("Expected UNK token 1.")

    vocab_size = vocabulary["vocabulary_size"]

    if int(sequences.max()) >= vocab_size:
        raise ValueError("Sequence token exceeds vocabulary size.")

    if not np.isin(split_codes, list(SPLIT_CODES.values())).all():
        raise ValueError("Invalid split codes found.")

    train_indices = np.flatnonzero(split_codes == SPLIT_CODES["train"])
    validation_indices = np.flatnonzero(
        split_codes == SPLIT_CODES["validation"]
    )

    # Learn normal sequences only. Labels are targets, never inputs.
    normal_train_indices = train_indices[targets[train_indices] == 0]

    if len(normal_train_indices) == 0:
        raise ValueError("No normal training windows are available.")

    if len(np.unique(targets[validation_indices])) != 2:
        raise ValueError("Validation must contain both target classes.")

    print(f"Total windows: {len(sequences):,}")
    print(f"Training windows: {len(train_indices):,}")
    print(f"Normal training windows used: {len(normal_train_indices):,}")
    print(
        "Anomalous training windows excluded: "
        f"{int((targets[train_indices] == 1).sum()):,}"
    )
    print(f"Validation windows: {len(validation_indices):,}")
    print(f"Vocabulary size: {vocab_size:,}")

    train_sequences = torch.as_tensor(
        sequences[normal_train_indices],
        dtype=torch.long,
    )

    train_lengths = torch.as_tensor(
        lengths[normal_train_indices],
        dtype=torch.long,
    )

    dataset = TensorDataset(train_sequences, train_lengths)

    generator = torch.Generator()
    generator.manual_seed(SEED)

    loader = DataLoader(
        dataset,
        batch_size=BATCH_SIZE,
        shuffle=True,
        num_workers=0,
        generator=generator,
    )

    model = LSTMNextEvent(vocab_size).to(device)

    optimizer = torch.optim.Adam(
        model.parameters(),
        lr=LEARNING_RATE,
    )

    criterion = nn.CrossEntropyLoss(reduction="none")

    print("\nTraining on normal training sequences...")

    for epoch in range(EPOCHS):
        model.train()

        accumulated_loss = 0.0
        accumulated_tokens = 0

        for tokens, batch_lengths in loader:
            tokens = tokens.to(device)
            batch_lengths = batch_lengths.to(device)

            inputs = tokens[:, :-1]
            next_tokens = tokens[:, 1:]

            logits = model(inputs)

            losses = criterion(
                logits.reshape(-1, vocab_size),
                next_tokens.reshape(-1),
            ).reshape_as(next_tokens)

            positions = torch.arange(
                next_tokens.shape[1],
                device=device,
            ).unsqueeze(0)

            valid_mask = positions < (batch_lengths.unsqueeze(1) - 1)
            valid_mask_float = valid_mask.float()

            valid_count = valid_mask_float.sum()

            if valid_count.item() == 0:
                continue

            loss = (losses * valid_mask_float).sum() / valid_count

            optimizer.zero_grad()
            loss.backward()

            nn.utils.clip_grad_norm_(model.parameters(), max_norm=1.0)

            optimizer.step()

            accumulated_loss += float(
                (losses.detach() * valid_mask_float).sum().item()
            )
            accumulated_tokens += int(valid_count.item())

        epoch_loss = (
            accumulated_loss / accumulated_tokens
            if accumulated_tokens
            else float("nan")
        )

        print(
            f"Epoch {epoch + 1:02d}/{EPOCHS} | "
            f"normal next-event loss={epoch_loss:.5f}"
        )

    print("\nScoring validation sequences...")

    validation_scores = calculate_window_scores(
        model,
        sequences,
        lengths,
        validation_indices,
        device,
    )

    y_validation = targets[validation_indices].astype(np.int8)

    precisions, recalls, thresholds = precision_recall_curve(
        y_validation,
        validation_scores,
    )

    if len(thresholds) == 0:
        raise RuntimeError("Unable to determine validation threshold.")

    f1_values = (
        2 * precisions[:-1] * recalls[:-1]
        / (precisions[:-1] + recalls[:-1] + 1e-12)
    )

    best_f1 = float(f1_values.max())

    candidate_indices = np.flatnonzero(
        np.isclose(f1_values, best_f1, rtol=1e-10, atol=1e-12)
    )

    best_index = max(
        candidate_indices,
        key=lambda i: (precisions[i], thresholds[i]),
    )

    selected_threshold = float(thresholds[best_index])

    metrics = evaluate_scores(
        y_validation,
        validation_scores,
        selected_threshold,
    )

    model_config = {
        "model_type": "LSTMNextEvent",
        "vocab_size": vocab_size,
        "embedding_dim": EMBEDDING_DIM,
        "hidden_dim": HIDDEN_DIM,
        "window_size": int(sequences.shape[1]),
        "padding_token": 0,
        "unknown_token": 1,
        "seed": SEED,
        "epochs": EPOCHS,
        "learning_rate": LEARNING_RATE,
        "trained_on_normal_training_windows_only": True,
    }

    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    VALIDATION_SCORES_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)

    # Save weights and architecture metadata, not the whole Python object.
    torch.save(
        {
            "model_state_dict": model.state_dict(),
            "config": model_config,
        },
        MODEL_PATH,
    )

    threshold_info = {
        "model": "LSTMNextEvent",
        "threshold": selected_threshold,
        "score_definition": "Mean next-event cross-entropy per valid sequence position",
        "higher_score_means_more_anomalous": True,
        "selected_using_validation_only": True,
        "test_used_for_threshold_selection": False,
    }

    with THRESHOLD_PATH.open("w", encoding="utf-8") as file:
        json.dump(threshold_info, file, indent=2)

    scored_validation = {
        "window_id": window_ids[validation_indices],
        "split": np.full(len(validation_indices), "validation"),
        "start_line": start_lines[validation_indices],
        "end_line": end_lines[validation_indices],
        "start_timestamp": start_timestamps[validation_indices],
        "end_timestamp": end_timestamps[validation_indices],
        "target_anomaly": y_validation,
        "anomaly_score": validation_scores,
        "predicted_anomaly": (
            validation_scores >= selected_threshold
        ).astype(np.int8),
    }

    pq.write_table(
        pa.Table.from_pydict(scored_validation),
        VALIDATION_SCORES_PATH,
        compression="zstd",
    )

    report = {
        "model_config": model_config,
        "normal_training_windows_used": len(normal_train_indices),
        "validation_windows": len(validation_indices),
        "threshold_selection": threshold_info,
        "validation_metrics": metrics,
        "test_evaluated": False,
        "outputs": {
            "model": str(MODEL_PATH.relative_to(ROOT)),
            "threshold": str(THRESHOLD_PATH.relative_to(ROOT)),
            "validation_scores": str(
                VALIDATION_SCORES_PATH.relative_to(ROOT)
            ),
        },
    }

    with REPORT_PATH.open("w", encoding="utf-8") as file:
        json.dump(report, file, indent=2, ensure_ascii=False)

    print("\n========== LSTM VALIDATION RESULTS ==========")
    print(f"Selected threshold: {selected_threshold:.6f}")
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

    print(f"\nSaved model: {MODEL_PATH}")
    print(f"Saved threshold: {THRESHOLD_PATH}")
    print(f"Validation scores: {VALIDATION_SCORES_PATH}")
    print(f"Full report: {REPORT_PATH}")
    print("\nSUCCESS: LSTM training and validation completed.")


if __name__ == "__main__":
    main()
