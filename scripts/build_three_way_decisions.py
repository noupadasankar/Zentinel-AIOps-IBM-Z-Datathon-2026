import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]

SEQUENCE_PATH = ROOT / "data/processed/bgl_event_sequences_v2.npz"
SCORES_PATH = ROOT / "data/processed/lstm_known_run_test_scores.parquet"
MODEL_THRESHOLD_PATH = ROOT / "artifacts/lstm_known_run_threshold.json"

OUTPUT_PATH = ROOT / "data/processed/lstm_three_way_test_decisions.parquet"
REPORT_PATH = ROOT / "reports/lstm_three_way_test_decision_report.json"

VALIDATION_SPLIT = 1
TEST_SPLIT = 2
UNKNOWN_TOKEN = 1
PADDING_TOKEN = 0
DRIFT_QUANTILE = 0.95


def find_threshold(obj):
    if isinstance(obj, dict):
        for key, value in obj.items():
            if "threshold" in key.lower() and isinstance(value, (int, float)):
                return float(value)

        for value in obj.values():
            result = find_threshold(value)
            if result is not None:
                return result

    return None


def unknown_rate(sequence):
    observed = sequence != PADDING_TOKEN
    observed_count = int(observed.sum())
    unknown_count = int((sequence == UNKNOWN_TOKEN).sum())
    known_count = int((sequence >= 2).sum())

    rate = unknown_count / observed_count if observed_count > 0 else 0.0

    return unknown_count, known_count, observed_count, float(rate)


def main():
    for path in (SEQUENCE_PATH, SCORES_PATH, MODEL_THRESHOLD_PATH):
        if not path.exists():
            raise FileNotFoundError(f"Missing required file: {path}")

    archive = np.load(SEQUENCE_PATH)
    sequences = archive["sequences"]
    targets = archive["targets"].astype(int)
    splits = archive["split_codes"]

    threshold_data = json.loads(MODEL_THRESHOLD_PATH.read_text(encoding="utf-8"))
    model_threshold = find_threshold(threshold_data)

    if model_threshold is None:
        raise ValueError("Could not find the saved LSTM threshold.")

    scores = pd.read_parquet(SCORES_PATH)
    if not {"window_index", "anomaly_score"}.issubset(scores.columns):
        raise ValueError("Test score file must contain window_index and anomaly_score.")

    if scores["window_index"].duplicated().any():
        raise ValueError("Duplicate window_index values in test scores.")

    score_lookup = scores.set_index("window_index")["anomaly_score"].to_dict()

    validation_indices = np.flatnonzero(splits == VALIDATION_SPLIT)
    test_indices = np.flatnonzero(splits == TEST_SPLIT)

    if len(validation_indices) == 0 or len(test_indices) == 0:
        raise ValueError("Validation or test split is empty.")

    validation_rates = np.array([unknown_rate(sequences[i])[3] for i in validation_indices])

    drift_threshold = float(np.quantile(validation_rates, DRIFT_QUANTILE))

    print("========== THREE-WAY POLICY ==========")
    print(f"LSTM threshold (validation): {model_threshold:.6f}")
    print(f"Unknown-rate threshold (validation {DRIFT_QUANTILE:.0%} quantile): {drift_threshold:.6f}")
    print("Test labels are not used to select either threshold.")

    rows = []

    for index in test_indices:
        index = int(index)
        sequence = sequences[index]

        unknown_count, known_count, observed_count, rate = unknown_rate(sequence)

        has_score = index in score_lookup
        score = float(score_lookup[index]) if has_score else None

        lstm_would_alert = bool(score >= model_threshold) if has_score else None

        insufficient_known_events = not has_score
        high_unknown_rate = rate > drift_threshold

        if insufficient_known_events:
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

        rows.append(
            {
                "window_index": index,
                "target_anomaly": int(targets[index]),
                "unknown_event_count": unknown_count,
                "known_event_count": known_count,
                "observed_event_count": observed_count,
                "unknown_event_rate": rate,
                "model_scorable": bool(has_score),
                "lstm_anomaly_score": score,
                "lstm_would_alert": lstm_would_alert,
                "high_unknown_rate": bool(high_unknown_rate),
                "decision": decision,
                "decision_reason": reason,
            }
        )

    result = pd.DataFrame(rows)

    total = len(result)
    decision_counts = result["decision"].value_counts().to_dict()

    review_mask = result["decision"] == "NOVELTY_DRIFT_REVIEW"
    alert_mask = result["decision"] == "ANOMALY_ALERT"
    normal_mask = result["decision"] == "NO_ANOMALY_DETECTED"

    tp = int((alert_mask & (result["target_anomaly"] == 1)).sum())
    fp = int((alert_mask & (result["target_anomaly"] == 0)).sum())
    test_anomalies = int(result["target_anomaly"].sum())

    report = {
        "policy": "three_way_anomaly_and_novelty_decision",
        "total_test_windows": int(total),
        "lstm_validation_threshold": float(model_threshold),
        "novelty_threshold_source": ("95th percentile of unknown-event rate on validation windows"),
        "novelty_quantile": DRIFT_QUANTILE,
        "novelty_unknown_rate_threshold": drift_threshold,
        "decision_counts": {key: int(value) for key, value in decision_counts.items()},
        "review_windows": int(review_mask.sum()),
        "review_rate": float(review_mask.mean()),
        "automatic_decision_windows": int((~review_mask).sum()),
        "automatic_decision_coverage": float((~review_mask).mean()),
        "anomaly_alerts": int(alert_mask.sum()),
        "no_anomaly_detected": int(normal_mask.sum()),
        "alert_true_positives": tp,
        "alert_false_positives": fp,
        "alert_precision": (float(tp / (tp + fp)) if (tp + fp) else None),
        "total_dataset_labelled_anomalous_windows": test_anomalies,
        "alert_recall_over_all_test_labelled_anomalies": (float(tp / test_anomalies) if test_anomalies else None),
        "review_labelled_anomalies": int((review_mask & (result["target_anomaly"] == 1)).sum()),
        "review_labelled_normal_windows": int((review_mask & (result["target_anomaly"] == 0)).sum()),
        "mean_unknown_rate_review_windows": (
            float(result.loc[review_mask, "unknown_event_rate"].mean()) if review_mask.any() else None
        ),
        "caveat": (
            "Test-period diagnostics were previously inspected, so this is "
            "exploratory temporal evaluation. Dataset labels are proxies "
            "for anomalies, not independently verified incident ground truth. "
            "Review decisions are not automatically normal or anomalous."
        ),
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)

    result.to_parquet(OUTPUT_PATH, index=False)
    REPORT_PATH.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print("\n========== THREE-WAY TEST DECISIONS ==========")
    print(f"Total test windows: {total:,}")
    print(f"Anomaly alerts: {report['anomaly_alerts']:,}")
    print(f"No anomaly detected: {report['no_anomaly_detected']:,}")
    print(f"Novelty/drift review: {report['review_windows']:,}")
    print(f"Review rate: {report['review_rate']:.2%}")
    print(f"Automatic decision coverage: {report['automatic_decision_coverage']:.2%}")
    print(f"Alert precision: {report['alert_precision']}")
    print(
        "Alert recall over all labelled anomalous test windows: "
        f"{report['alert_recall_over_all_test_labelled_anomalies']}"
    )
    print(f"Review-labelled anomalies: {report['review_labelled_anomalies']}")
    print(f"Review-labelled normal windows: {report['review_labelled_normal_windows']}")
    print(f"\nDecision dataset: {OUTPUT_PATH}")
    print(f"Report: {REPORT_PATH}")
    print("\nSUCCESS: Three-way decision policy completed.")


if __name__ == "__main__":
    main()
