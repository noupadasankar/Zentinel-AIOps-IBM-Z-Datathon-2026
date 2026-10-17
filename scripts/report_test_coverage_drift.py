import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]

SEQUENCE_PATH = ROOT / "data/processed/bgl_event_sequences_v2.npz"
SCORES_PATH = ROOT / "data/processed/lstm_known_run_test_scores.parquet"
FROZEN_EVENTS_PATH = ROOT / "data/processed/bgl_frozen_events.parquet"

OUTPUT_PATH = ROOT / "reports/test_coverage_drift_report.json"
WINDOW_SIZE = 100


def main():
    data = np.load(SEQUENCE_PATH)
    split_codes = data["split_codes"]
    targets = data["targets"].astype(int)
    sequences = data["sequences"]

    test_indices = np.flatnonzero(split_codes == 2)
    scores = pd.read_parquet(SCORES_PATH)

    scored_indices = set(scores["window_index"].astype(int))
    all_test_indices = set(test_indices.tolist())
    unscorable_indices = sorted(all_test_indices - scored_indices)

    unknown_rate_by_window = []
    for index in test_indices:
        seq = sequences[index]
        known_count = int(np.sum(seq >= 2))
        unknown_count = int(np.sum(seq == 1))
        non_padding_count = int(np.sum(seq != 0))
        unknown_rate = unknown_count / non_padding_count if non_padding_count else 0.0
        unknown_rate_by_window.append(
            {
                "window_index": int(index),
                "unknown_count": unknown_count,
                "known_count": known_count,
                "unknown_rate": float(unknown_rate),
                "target_anomaly": int(targets[index]),
                "scorable": int(index in scored_indices),
            }
        )

    df = pd.DataFrame(unknown_rate_by_window)
    scored_df = scores.copy()
    unscorable_df = df[~df["scorable"].astype(bool)]

    scored_anomalous = int((scored_df["target_anomaly"] == 1).sum())
    scored_normal = int((scored_df["target_anomaly"] == 0).sum())
    unscorable_anomalous = int((unscorable_df["target_anomaly"] == 1).sum())
    unscorable_normal = int((unscorable_df["target_anomaly"] == 0).sum())

    report = {
        "purpose": ("Quantify scoring coverage and unknown-event prevalence for the exploratory test period."),
        "test_windows": len(test_indices),
        "scorable_windows": len(scored_df),
        "unscorable_windows": len(unscorable_df),
        "scorable_coverage": float(len(scored_df) / len(test_indices)),
        "unscorable_rate": float(len(unscorable_df) / len(test_indices)),
        "scorable_anomalous_windows": scored_anomalous,
        "scorable_normal_windows": scored_normal,
        "unscorable_anomalous_windows": unscorable_anomalous,
        "unscorable_normal_windows": unscorable_normal,
        "mean_unknown_rate_all_test_windows": float(df["unknown_rate"].mean()),
        "median_unknown_rate_all_test_windows": float(df["unknown_rate"].median()),
        "mean_unknown_rate_scorable_windows": float(df.loc[df["scorable"] == 1, "unknown_rate"].mean()),
        "mean_unknown_rate_unscorable_windows": float(df.loc[df["scorable"] == 0, "unknown_rate"].mean()),
        "interpretation": (
            "Unscorable windows require a separate novelty/drift review. "
            "Do not classify them as normal merely because the LSTM "
            "cannot produce a valid known-run score."
        ),
        "caveat": (
            "Test data was previously inspected during drift diagnostics; "
            "this is exploratory analysis, not a pristine blind test."
        ),
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print("========== TEST COVERAGE AND DRIFT ==========")
    for key, value in report.items():
        print(f"{key}: {value}")
    print(f"\nReport saved: {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
