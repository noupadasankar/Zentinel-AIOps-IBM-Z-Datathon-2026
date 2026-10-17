import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.metrics import f1_score, precision_score, recall_score

from zentinel.config import REPORTS_DIR, ROOT_DIR


def compute_slice_metrics(
    df: pd.DataFrame,
    start_ts: pd.Series | None = None,
    end_ts: pd.Series | None = None,
) -> dict[str, Any]:
    """Compute empirical evaluation metrics with coverage for a window slice."""
    total_windows = len(df)
    if total_windows == 0:
        return {}

    # Three-way breakdown
    decisions = df["decision"].value_counts().to_dict()
    alert_count = int(decisions.get("ANOMALY_ALERT", 0))
    normal_count = int(decisions.get("NO_ANOMALY_DETECTED", 0))
    review_count = int(decisions.get("NOVELTY_DRIFT_REVIEW", 0))

    automatic_windows = alert_count + normal_count
    coverage = float(automatic_windows / total_windows)
    review_rate = float(review_count / total_windows)

    # Automatic decision metrics (evaluated where model took automatic action)
    auto_df = df[df["decision"].isin(["ANOMALY_ALERT", "NO_ANOMALY_DETECTED"])].copy()
    if len(auto_df) > 0:
        y_true = auto_df["target_anomaly"].values
        y_pred = (auto_df["decision"] == "ANOMALY_ALERT").astype(int).values

        tp = int(((y_pred == 1) & (y_true == 1)).sum())
        fp = int(((y_pred == 1) & (y_true == 0)).sum())
        tn = int(((y_pred == 0) & (y_true == 0)).sum())
        fn = int(((y_pred == 0) & (y_true == 1)).sum())

        precision = float(precision_score(y_true, y_pred, zero_division=0))
        recall = float(recall_score(y_true, y_pred, zero_division=0))
        f1 = float(f1_score(y_true, y_pred, zero_division=0))
    else:
        tp, fp, tn, fn = 0, 0, 0, 0
        precision, recall, f1 = 0.0, 0.0, 0.0

    # Overall recall over all anomalies (including those routed to review)
    total_anomalies = int((df["target_anomaly"] == 1).sum())
    recall_over_all_anomalies = float(tp / total_anomalies) if total_anomalies > 0 else 0.0

    # Time-based metrics: False alerts per hour
    false_alerts_per_hour = None
    span_hours = None
    if start_ts is not None and end_ts is not None and len(start_ts) > 0:
        t_min = float(start_ts.min())
        t_max = float(end_ts.max())
        span_seconds = max(1.0, t_max - t_min)
        span_hours = float(span_seconds / 3600.0)
        false_alerts_per_hour = float(fp / span_hours) if span_hours > 0 else 0.0

    return {
        "total_windows": total_windows,
        "coverage": coverage,
        "review_rate": review_rate,
        "automatic_windows": automatic_windows,
        "review_windows": review_count,
        "alert_count": alert_count,
        "normal_count": normal_count,
        "true_positives": tp,
        "false_positives": fp,
        "true_negatives": tn,
        "false_negatives": fn,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "total_ground_truth_anomalies": total_anomalies,
        "recall_over_all_anomalies": recall_over_all_anomalies,
        "span_hours": span_hours,
        "false_alerts_per_hour": false_alerts_per_hour,
    }


def compute_detection_delays(
    df: pd.DataFrame,
    start_ts: pd.Series,
) -> dict[str, Any]:
    """
    Measure detection delay (in seconds) between anomaly onset and first ANOMALY_ALERT
    for each contiguous ground-truth anomaly episode.
    """
    df = df.copy()
    df["start_timestamp"] = start_ts.values

    # Find contiguous anomaly episodes (runs of target_anomaly == 1)
    episodes: list[dict[str, Any]] = []
    in_episode = False
    current_ep: list[dict[str, Any]] = []

    for _, row in df.iterrows():
        is_anomaly = row["target_anomaly"] == 1
        if is_anomaly:
            in_episode = True
            current_ep.append(row.to_dict())
        else:
            if in_episode and current_ep:
                episodes.append(
                    {
                        "start_ts": current_ep[0]["start_timestamp"],
                        "windows": current_ep,
                    }
                )
                current_ep = []
            in_episode = False

    if in_episode and current_ep:
        episodes.append(
            {
                "start_ts": current_ep[0]["start_timestamp"],
                "windows": current_ep,
            }
        )

    if not episodes:
        return {
            "episodes_total": 0,
            "detected_episodes": 0,
            "median_delay_seconds": None,
            "p90_delay_seconds": None,
        }

    delays = []
    for ep in episodes:
        ep_start = ep["start_ts"]
        alert_rows = [w for w in ep["windows"] if w["decision"] == "ANOMALY_ALERT"]
        if alert_rows:
            first_alert_ts = alert_rows[0]["start_timestamp"]
            delay = max(0.0, float(first_alert_ts - ep_start))
            delays.append(delay)

    detected_count = len(delays)
    median_delay = float(np.median(delays)) if delays else None
    p90_delay = float(np.percentile(delays, 90)) if delays else None

    return {
        "episodes_total": len(episodes),
        "detected_episodes": detected_count,
        "detection_rate": float(detected_count / len(episodes)),
        "median_delay_seconds": median_delay,
        "p90_delay_seconds": p90_delay,
    }


def run_block_bootstrap_ci(
    df: pd.DataFrame,
    start_ts: pd.Series | None = None,
    end_ts: pd.Series | None = None,
    n_bootstraps: int = 500,
    block_size: int = 50,
) -> dict[str, dict[str, float]]:
    """
    Compute 95% confidence intervals using block bootstrap over contiguous windows
    to properly handle temporal autocorrelation.
    """
    rng = np.random.default_rng(42)
    n = len(df)
    n_blocks = int(np.ceil(n / block_size))

    precisions = []
    recalls = []
    f1s = []
    coverages = []
    false_alert_rates = []

    for _ in range(n_bootstraps):
        start_indices = rng.integers(0, max(1, n - block_size + 1), size=n_blocks)
        sample_indices = []
        for s in start_indices:
            sample_indices.extend(range(s, min(n, s + block_size)))
        sample_indices = sample_indices[:n]

        sub_df = df.iloc[sample_indices]
        sub_start = start_ts.iloc[sample_indices] if start_ts is not None else None
        sub_end = end_ts.iloc[sample_indices] if end_ts is not None else None

        metrics = compute_slice_metrics(sub_df, sub_start, sub_end)
        precisions.append(metrics["precision"])
        recalls.append(metrics["recall"])
        f1s.append(metrics["f1"])
        coverages.append(metrics["coverage"])
        if metrics["false_alerts_per_hour"] is not None:
            false_alert_rates.append(metrics["false_alerts_per_hour"])

    ci_dict = {}
    for name, arr in [
        ("precision", precisions),
        ("recall", recalls),
        ("f1", f1s),
        ("coverage", coverages),
        ("false_alerts_per_hour", false_alert_rates),
    ]:
        if arr:
            ci_dict[name] = {
                "ci_lower_2_5": float(np.percentile(arr, 2.5)),
                "median": float(np.median(arr)),
                "ci_upper_97_5": float(np.percentile(arr, 97.5)),
            }

    return ci_dict


def run_full_walk_forward_evaluation(
    decisions_path: Path | None = None,
    features_path: Path | None = None,
    output_report_path: Path | None = None,
) -> dict[str, Any]:
    """Execute walk-forward evaluation across 4 test slices with empirical statistics."""
    dec_path = decisions_path or (ROOT_DIR / "data/processed/lstm_three_way_test_decisions.parquet")
    feat_path = features_path or (ROOT_DIR / "data/processed/bgl_window_features.parquet")
    rep_path = output_report_path or (REPORTS_DIR / "temporal_walk_forward_evaluation.json")

    if not dec_path.exists():
        raise FileNotFoundError(f"Decisions file missing: {dec_path}")

    df_dec = pd.read_parquet(dec_path)
    window_indices = df_dec["window_index"].values

    start_ts = None
    end_ts = None
    if feat_path.exists():
        df_feat = pd.read_parquet(feat_path)
        matching_feats = df_feat.iloc[window_indices]
        start_ts = matching_feats["start_timestamp"]
        end_ts = matching_feats["end_timestamp"]

    total_len = len(df_dec)
    slice_size = total_len // 4

    slices_report = []
    for i in range(4):
        s_start = i * slice_size
        s_end = (i + 1) * slice_size if i < 3 else total_len
        slice_df = df_dec.iloc[s_start:s_end].reset_index(drop=True)
        slice_start_ts = start_ts.iloc[s_start:s_end].reset_index(drop=True) if start_ts is not None else None
        slice_end_ts = end_ts.iloc[s_start:s_end].reset_index(drop=True) if end_ts is not None else None

        metrics = compute_slice_metrics(slice_df, slice_start_ts, slice_end_ts)
        metrics["slice_index"] = i + 1
        metrics["window_range"] = [int(s_start), int(s_end - 1)]
        slices_report.append(metrics)

    # Overall dataset metrics
    overall_metrics = compute_slice_metrics(df_dec, start_ts, end_ts)

    # Detection delays
    delays_report = {}
    if start_ts is not None:
        delays_report = compute_detection_delays(df_dec, start_ts)

    # 95% CIs via block bootstrap
    ci_report = run_block_bootstrap_ci(df_dec, start_ts, end_ts, n_bootstraps=500, block_size=50)

    report = {
        "evaluation_name": "temporal_walk_forward_evaluation",
        "description": "Walk-forward evaluation over 4 consecutive temporal slices with coverage, bootstrap CIs, and detection delays.",
        "label": "exploratory temporal test",
        "total_test_windows": total_len,
        "slices_evaluated": 4,
        "slice_results": slices_report,
        "overall_test_metrics": overall_metrics,
        "detection_delay_metrics": delays_report,
        "block_bootstrap_95_percent_ci": ci_report,
        "provenance": {
            "source_decisions": str(dec_path.relative_to(ROOT_DIR) if dec_path.is_relative_to(ROOT_DIR) else dec_path),
            "caveat": (
                "Test data was inspected for drift diagnostics, so results are described "
                "as exploratory. Labels are proxies for anomalies. Coverage must be reported "
                "alongside every automatic decision metric."
            ),
        },
    }

    rep_path.parent.mkdir(parents=True, exist_ok=True)
    rep_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report
