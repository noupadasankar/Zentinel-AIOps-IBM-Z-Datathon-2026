
"""Analyze model scores against unknown-event rates and temporal drift."""

from pathlib import Path
import json

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
from scipy.stats import spearmanr
from sklearn.metrics import (
    average_precision_score,
    f1_score,
    precision_score,
    recall_score,
)


ROOT = Path(__file__).resolve().parents[1]

FEATURES = ROOT / "data" / "processed" / "bgl_window_features.parquet"

IF_VALIDATION = (
    ROOT / "data" / "processed" / "isolation_forest_validation_scores.parquet"
)
LSTM_VALIDATION = (
    ROOT / "data" / "processed" / "lstm_validation_scores.parquet"
)

TEST_SCORES = (
    ROOT / "data" / "processed" / "test_model_comparison.parquet"
)

IF_THRESHOLD_FILE = ROOT / "artifacts" / "isolation_forest_threshold.json"
LSTM_THRESHOLD_FILE = ROOT / "artifacts" / "lstm_threshold.json"

REPORT_PATH = ROOT / "reports" / "model_drift_analysis.json"

FEATURE_COLUMNS = [
    "window_id",
    "split",
    "target_anomaly",
    "unknown_event_rate",
]

CHUNK_SIZE_WINDOWS = 500


def require_files(paths):
    missing = [str(path) for path in paths if not path.is_file()]
    if missing:
        raise FileNotFoundError(
            "Required files are missing:\n" + "\n".join(missing)
        )


def get_metrics(frame, score_column, prediction_column):
    """Summarize a fixed-threshold prediction, without tuning it."""
    if frame.empty:
        return {"windows": 0}

    y = frame["target_anomaly"].to_numpy(dtype=np.int8)
    predictions = frame[prediction_column].to_numpy(dtype=np.int8)
    scores = frame[score_column].to_numpy(dtype=np.float64)

    result = {
        "windows": int(len(frame)),
        "unknown_windows": int((frame["unknown_event_rate"] > 0).sum()),
        "unknown_window_percentage": float(
            100 * (frame["unknown_event_rate"] > 0).mean()
        ),
        "mean_unknown_event_rate": float(
            frame["unknown_event_rate"].mean()
        ),
        "actual_anomaly_percentage": float(100 * y.mean()),
        "predicted_anomalies": int(predictions.sum()),
        "false_positives": int(((y == 0) & (predictions == 1)).sum()),
        "false_negatives": int(((y == 1) & (predictions == 0)).sum()),
        "precision": float(
            precision_score(y, predictions, zero_division=0)
        ),
        "recall": float(
            recall_score(y, predictions, zero_division=0)
        ),
        "f1": float(f1_score(y, predictions, zero_division=0)),
        "mean_score": float(scores.mean()),
        "median_score": float(np.median(scores)),
        "score_p95": float(np.quantile(scores, 0.95)),
    }

    if len(np.unique(y)) == 2:
        result["pr_auc"] = float(average_precision_score(y, scores))
    else:
        result["pr_auc"] = None

    if frame["unknown_event_rate"].nunique() > 1:
        correlation, p_value = spearmanr(
            frame["unknown_event_rate"].to_numpy(),
            scores,
        )
        result["score_unknown_rate_spearman"] = (
            float(correlation) if np.isfinite(correlation) else None
        )
        result["spearman_p_value"] = (
            float(p_value) if np.isfinite(p_value) else None
        )
    else:
        result["score_unknown_rate_spearman"] = None
        result["spearman_p_value"] = None

    return result


def main():
    require_files([
        FEATURES,
        IF_VALIDATION,
        LSTM_VALIDATION,
        TEST_SCORES,
        IF_THRESHOLD_FILE,
        LSTM_THRESHOLD_FILE,
    ])

    with IF_THRESHOLD_FILE.open("r", encoding="utf-8") as file:
        if_threshold = float(json.load(file)["threshold"])

    with LSTM_THRESHOLD_FILE.open("r", encoding="utf-8") as file:
        lstm_threshold = float(json.load(file)["threshold"])

    print("Loading feature metadata and saved scores...")

    feature_frame = pq.read_table(
        FEATURES,
        columns=FEATURE_COLUMNS,
    ).to_pandas()

    if_val = pq.read_table(
        IF_VALIDATION,
        columns=["window_id", "anomaly_score"],
    ).to_pandas().rename(
        columns={"anomaly_score": "if_score"}
    )

    lstm_val = pq.read_table(
        LSTM_VALIDATION,
        columns=["window_id", "anomaly_score"],
    ).to_pandas().rename(
        columns={"anomaly_score": "lstm_score"}
    )

    test_scores = pq.read_table(TEST_SCORES).to_pandas()

    validation = feature_frame.loc[
        feature_frame["split"] == "validation"
    ].merge(if_val, on="window_id", validate="one_to_one").merge(
        lstm_val, on="window_id", validate="one_to_one"
    )

    validation["if_prediction"] = (
        validation["if_score"] >= if_threshold
    ).astype(np.int8)

    validation["lstm_prediction"] = (
        validation["lstm_score"] >= lstm_threshold
    ).astype(np.int8)

    test = feature_frame.loc[
        feature_frame["split"] == "test"
    ].merge(
        test_scores[[
            "window_id",
            "isolation_forest_score",
            "isolation_forest_prediction",
            "lstm_score",
            "lstm_prediction",
        ]],
        on="window_id",
        validate="one_to_one",
    )

    test = test.rename(columns={
        "isolation_forest_score": "if_score",
        "isolation_forest_prediction": "if_prediction",
    })

    split_frames = {
        "validation": validation,
        "test": test,
    }

    report = {
        "purpose": (
            "Exploratory analysis of unknown-event prevalence and "
            "saved anomaly scores. No models or thresholds are changed."
        ),
        "thresholds": {
            "isolation_forest": if_threshold,
            "lstm": lstm_threshold,
        },
        "score_diagnostics": {},
        "unknown_vs_known_windows": {},
        "temporal_chunks": {},
    }

    for split_name, frame in split_frames.items():
        print(f"\n========== {split_name.upper()} ==========")

        report["score_diagnostics"][split_name] = {
            "isolation_forest": get_metrics(
                frame, "if_score", "if_prediction"
            ),
            "lstm": get_metrics(
                frame, "lstm_score", "lstm_prediction"
            ),
        }

        report["unknown_vs_known_windows"][split_name] = {}

        for group_name, mask in (
            ("contains_unknown", frame["unknown_event_rate"] > 0),
            ("no_unknown", frame["unknown_event_rate"] == 0),
        ):
            group = frame.loc[mask]

            report["unknown_vs_known_windows"][split_name][group_name] = {
                "isolation_forest": get_metrics(
                    group, "if_score", "if_prediction"
                ),
                "lstm": get_metrics(
                    group, "lstm_score", "lstm_prediction"
                ),
            }

        for model_name, metrics in report["score_diagnostics"][split_name].items():
            print(f"\n{model_name.upper()}")
            print(f"  Windows: {metrics['windows']:,}")
            print(
                "  Windows containing unknown events: "
                f"{metrics['unknown_window_percentage']:.2f}%"
            )
            print(f"  Mean unknown-event rate: {metrics['mean_unknown_event_rate']:.4f}")
            print(f"  F1: {metrics['f1']:.4f}")
            print(f"  False positives: {metrics['false_positives']:,}")
            print(
                "  Score/unknown-rate Spearman correlation: "
                f"{metrics['score_unknown_rate_spearman']}"
            )

    # Compare the later test period in contiguous blocks.
    chunk_reports = []

    for split_name, frame in split_frames.items():
        chunk_frame = frame.copy()
        chunk_frame["chunk_id"] = (
            chunk_frame["window_id"] // CHUNK_SIZE_WINDOWS
        )

        for chunk_id, group in chunk_frame.groupby("chunk_id", sort=True):
            chunk_reports.append({
                "split": split_name,
                "chunk_id": int(chunk_id),
                "start_window": int(group["window_id"].min()),
                "end_window": int(group["window_id"].max()),
                "unknown_window_percentage": float(
                    100 * (group["unknown_event_rate"] > 0).mean()
                ),
                "mean_unknown_event_rate": float(
                    group["unknown_event_rate"].mean()
                ),
                "alert_window_percentage": float(
                    100 * group["target_anomaly"].mean()
                ),
                "if_mean_score": float(group["if_score"].mean()),
                "lstm_mean_score": float(group["lstm_score"].mean()),
                "if_false_positives": int((
                    (group["target_anomaly"] == 0)
                    & (group["if_prediction"] == 1)
                ).sum()),
                "lstm_false_positives": int((
                    (group["target_anomaly"] == 0)
                    & (group["lstm_prediction"] == 1)
                ).sum()),
            })

    report["temporal_chunks"] = chunk_reports

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)

    with REPORT_PATH.open("w", encoding="utf-8") as file:
        json.dump(report, file, indent=2)

    print("\n========== UNKNOWN VS KNOWN WINDOWS ==========")

    for split_name, groups in report["unknown_vs_known_windows"].items():
        print(f"\n{split_name.upper()}")

        for group_name, model_results in groups.items():
            print(f"  {group_name}")

            for model_name, result in model_results.items():
                print(
                    f"    {model_name}: "
                    f"n={result['windows']:,}, "
                    f"F1={result['f1']:.4f}, "
                    f"FP={result['false_positives']:,}, "
                    f"mean_score={result['mean_score']:.4f}"
                )

    high_unknown_chunks = [
        chunk for chunk in chunk_reports
        if chunk["split"] == "test"
        and chunk["unknown_window_percentage"] >= 20
    ]

    print("\nTest chunks with >=20% of windows containing unknown events:")

    for chunk in high_unknown_chunks[:20]:
        print(
            f"  Chunk {chunk['chunk_id']}: "
            f"windows {chunk['start_window']:,}-"
            f"{chunk['end_window']:,}, "
            f"unknown windows={chunk['unknown_window_percentage']:.1f}%, "
            f"IF FP={chunk['if_false_positives']:,}, "
            f"LSTM FP={chunk['lstm_false_positives']:,}"
        )

    print(f"\nDetailed report: {REPORT_PATH}")
    print("\nSUCCESS: Model drift diagnosis completed.")


if __name__ == "__main__":
    main()
