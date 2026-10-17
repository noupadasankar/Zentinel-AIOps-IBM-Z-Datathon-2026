
"""Train and validate the Isolation Forest anomaly detection baseline."""

from pathlib import Path
import json

import joblib
import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from sklearn.ensemble import IsolationForest
from sklearn.metrics import (
    average_precision_score,
    confusion_matrix,
    f1_score,
    precision_recall_curve,
    precision_score,
    recall_score,
    roc_auc_score,
)


ROOT = Path(__file__).resolve().parents[1]

FEATURE_PATH = ROOT / "data" / "processed" / "bgl_window_features.parquet"
MANIFEST_PATH = ROOT / "reports" / "window_feature_columns.json"

MODEL_PATH = ROOT / "artifacts" / "isolation_forest_baseline.joblib"
THRESHOLD_PATH = ROOT / "artifacts" / "isolation_forest_threshold.json"

VALIDATION_SCORES_PATH = (
    ROOT / "data" / "processed" / "isolation_forest_validation_scores.parquet"
)
REPORT_PATH = ROOT / "reports" / "isolation_forest_validation_report.json"

RANDOM_STATE = 42
N_ESTIMATORS = 200
MAX_SAMPLES = "auto"

METADATA_COLUMNS = [
    "window_id",
    "split",
    "start_line",
    "end_line",
    "start_timestamp",
    "end_timestamp",
    "target_anomaly",
]


def evaluate_scores(y_true, scores, threshold):
    """Evaluate predictions where higher scores mean more anomalous."""
    y_true = np.asarray(y_true, dtype=np.int8)
    scores = np.asarray(scores, dtype=np.float64)
    predictions = (scores >= threshold).astype(np.int8)

    tn, fp, fn, tp = confusion_matrix(
        y_true,
        predictions,
        labels=[0, 1],
    ).ravel()

    return {
        "threshold": float(threshold),
        "windows_evaluated": int(len(y_true)),
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
        "roc_auc": float(
            roc_auc_score(y_true, scores)
        ),
        "score_min": float(np.min(scores)),
        "score_median": float(np.median(scores)),
        "score_max": float(np.max(scores)),
    }


def main():
    for path in (FEATURE_PATH, MANIFEST_PATH):
        if not path.is_file():
            raise FileNotFoundError(f"Required file missing: {path}")

    outputs = [
        MODEL_PATH,
        THRESHOLD_PATH,
        VALIDATION_SCORES_PATH,
        REPORT_PATH,
    ]

    existing = [str(path) for path in outputs if path.exists()]
    if existing:
        raise SystemExit(
            "Existing model or report files found. Inspect them before "
            "deciding whether to rerun; outputs will not be overwritten:\n"
            + "\n".join(existing)
        )

    with MANIFEST_PATH.open("r", encoding="utf-8") as file:
        manifest = json.load(file)

    feature_columns = manifest["feature_columns"]

    forbidden_columns = {
        "label",
        "target_anomaly",
        "split",
        "window_id",
        "start_line",
        "end_line",
        "start_timestamp",
        "end_timestamp",
    }

    accidental_leakage = forbidden_columns.intersection(feature_columns)
    if accidental_leakage:
        raise ValueError(
            f"Metadata/target columns appear in model features: "
            f"{sorted(accidental_leakage)}"
        )

    columns_to_read = METADATA_COLUMNS + feature_columns

    print("========== ISOLATION FOREST BASELINE ==========")
    print(f"Feature columns: {len(feature_columns):,}")
    print("Loading prepared window dataset...")

    table = pq.read_table(
        FEATURE_PATH,
        columns=columns_to_read,
    )
    data = table.to_pandas()

    if data["split"].isna().any():
        raise ValueError("Missing split assignments found.")

    feature_data = data[feature_columns].astype(np.float32)

    if not np.isfinite(feature_data.to_numpy()).all():
        raise ValueError(
            "NaN or infinite feature values found. Fix the feature data "
            "before training."
        )

    train_mask = data["split"].eq("train")
    validation_mask = data["split"].eq("validation")

    # Train on normal training windows only.
    train_normal_mask = train_mask & data["target_anomaly"].eq(0)

    if train_normal_mask.sum() == 0:
        raise ValueError("No normal training windows available.")

    X_train = feature_data.loc[train_normal_mask]
    X_validation = feature_data.loc[validation_mask]
    y_validation = data.loc[
        validation_mask, "target_anomaly"
    ].to_numpy(dtype=np.int8)

    if len(np.unique(y_validation)) != 2:
        raise ValueError(
            "Validation must contain both normal and anomalous windows."
        )

    print(f"Training windows available: {int(train_mask.sum()):,}")
    print(f"Normal training windows used: {len(X_train):,}")
    print(f"Anomalous training windows excluded: "
          f"{int((train_mask & data['target_anomaly'].eq(1)).sum()):,}")
    print(f"Validation windows: {len(X_validation):,}")
    print(f"Validation anomalous windows: {int(y_validation.sum()):,}")

    model = IsolationForest(
        n_estimators=N_ESTIMATORS,
        max_samples=MAX_SAMPLES,
        contamination="auto",
        random_state=RANDOM_STATE,
        n_jobs=-1,
    )

    print("\nFitting Isolation Forest on normal training windows...")
    model.fit(X_train)

    # decision_function is higher for normal data.
    # Negating it gives a score where higher means more anomalous.
    validation_scores = -model.decision_function(X_validation)

    precisions, recalls, thresholds = precision_recall_curve(
        y_validation,
        validation_scores,
    )

    if len(thresholds) == 0:
        raise RuntimeError("Could not derive a validation threshold.")

    f1_values = (
        2 * precisions[:-1] * recalls[:-1]
        / (precisions[:-1] + recalls[:-1] + 1e-12)
    )

    best_f1 = float(np.max(f1_values))

    # Prefer higher precision, then the higher threshold if the maximum
    # F1 is tied. This keeps the selection deterministic.
    candidate_indices = np.flatnonzero(
        np.isclose(f1_values, best_f1, rtol=1e-10, atol=1e-12)
    )

    best_index = max(
        candidate_indices,
        key=lambda i: (precisions[i], thresholds[i]),
    )
    selected_threshold = float(thresholds[best_index])

    validation_metrics = evaluate_scores(
        y_validation,
        validation_scores,
        selected_threshold,
    )

    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    VALIDATION_SCORES_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)

    joblib.dump(model, MODEL_PATH)

    threshold_info = {
        "model": "IsolationForest",
        "threshold": selected_threshold,
        "score_convention": "-model.decision_function(X); higher is more anomalous",
        "selection_method": "Maximum validation F1; ties prefer higher precision, then higher threshold",
        "validation_only": True,
        "test_used_for_threshold_selection": False,
        "feature_manifest": str(
            MANIFEST_PATH.relative_to(ROOT)
        ),
    }

    with THRESHOLD_PATH.open("w", encoding="utf-8") as file:
        json.dump(threshold_info, file, indent=2)

    scored_validation = data.loc[validation_mask, METADATA_COLUMNS].copy()
    scored_validation["anomaly_score"] = validation_scores
    scored_validation["predicted_anomaly"] = (
        validation_scores >= selected_threshold
    ).astype(np.int8)

    pq.write_table(
        pa.Table.from_pandas(
            scored_validation,
            preserve_index=False,
        ),
        VALIDATION_SCORES_PATH,
        compression="zstd",
    )

    report = {
        "model": "IsolationForest",
        "random_state": RANDOM_STATE,
        "n_estimators": N_ESTIMATORS,
        "max_samples": MAX_SAMPLES,
        "feature_count": len(feature_columns),
        "training_windows_total": int(train_mask.sum()),
        "normal_training_windows_used": int(train_normal_mask.sum()),
        "anomalous_training_windows_excluded": int(
            (train_mask & data["target_anomaly"].eq(1)).sum()
        ),
        "validation_metrics_at_selected_threshold": validation_metrics,
        "threshold_selection": threshold_info,
        "test_split_evaluated": False,
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

    print("\n========== VALIDATION RESULTS ==========")
    print(f"Selected threshold: {selected_threshold:.8f}")
    print(f"Precision: {validation_metrics['precision']:.4f}")
    print(f"Recall:    {validation_metrics['recall']:.4f}")
    print(f"F1-score:  {validation_metrics['f1']:.4f}")
    print(f"PR-AUC:    {validation_metrics['average_precision_pr_auc']:.4f}")
    print(f"ROC-AUC:   {validation_metrics['roc_auc']:.4f}")
    print(f"False positives: {validation_metrics['false_positives']:,}")
    print(f"False negatives: {validation_metrics['false_negatives']:,}")
    print(
        "False-positive windows per 1,000 normal windows: "
        f"{validation_metrics['false_positive_windows_per_1000_normal_windows']:.2f}"
    )

    print(f"\nSaved model: {MODEL_PATH}")
    print(f"Saved threshold: {THRESHOLD_PATH}")
    print(f"Validation scores: {VALIDATION_SCORES_PATH}")
    print(f"Full report: {REPORT_PATH}")
    print("\nSUCCESS: Isolation Forest training and validation completed.")


if __name__ == "__main__":
    main()

