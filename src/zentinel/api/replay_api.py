import json
from pathlib import Path

import numpy as np
import pandas as pd
from fastapi import FastAPI, HTTPException, Query

ROOT = Path(__file__).resolve().parents[3]

DATA_PATH = ROOT / "data/processed/lstm_three_way_test_decisions.parquet"
REPORT_PATH = ROOT / "reports/lstm_three_way_test_decision_report.json"

ALLOWED_DECISIONS = {
    "ANOMALY_ALERT",
    "NO_ANOMALY_DETECTED",
    "NOVELTY_DRIFT_REVIEW",
}

PUBLIC_COLUMNS = [
    "window_index",
    "unknown_event_count",
    "known_event_count",
    "observed_event_count",
    "unknown_event_rate",
    "model_scorable",
    "lstm_anomaly_score",
    "lstm_would_alert",
    "high_unknown_rate",
    "decision",
    "decision_reason",
]

app = FastAPI(
    title="Zentinel AIOps API",
    description=(
        "Offline replay API for log anomaly detection and novelty/drift "
        "decisions. This service exposes previously generated evaluation "
        "results; it does not ingest or score live logs."
    ),
    version="0.1.0",
)

if not DATA_PATH.exists():
    raise FileNotFoundError(
        f"Decision dataset not found: {DATA_PATH}"
    )

if not REPORT_PATH.exists():
    raise FileNotFoundError(
        f"Decision report not found: {REPORT_PATH}"
    )

DECISIONS = pd.read_parquet(DATA_PATH)

with REPORT_PATH.open("r", encoding="utf-8") as file:
    EVALUATION_REPORT = json.load(file)

required_columns = set(PUBLIC_COLUMNS + ["target_anomaly"])
missing_columns = required_columns - set(DECISIONS.columns)

if missing_columns:
    raise ValueError(
        f"Decision dataset is missing columns: {sorted(missing_columns)}"
    )

if DECISIONS["window_index"].duplicated().any():
    raise ValueError("Duplicate window_index values found.")

DECISIONS = DECISIONS.sort_values("window_index").reset_index(drop=True)


def json_safe(value):
    """Convert Pandas/NumPy scalar values into JSON-compatible values."""
    if value is None or value is pd.NA:
        return None

    if isinstance(value, (np.integer,)):
        return int(value)

    if isinstance(value, (np.floating,)):
        value = float(value)

    if isinstance(value, (np.bool_,)):
        return bool(value)

    if isinstance(value, float) and not np.isfinite(value):
        return None

    if pd.isna(value):
        return None

    if isinstance(value, pd.Timestamp):
        return value.isoformat()

    return value


def public_record(row):
    """Expose decisions without leaking evaluation labels to consumers."""
    return {
        column: json_safe(row[column])
        for column in PUBLIC_COLUMNS
    }


@app.get("/", tags=["System"])
def root():
    return {
        "service": "Zentinel AIOps API",
        "mode": "offline_replay",
        "documentation": "/docs",
        "health": "/health",
    }


@app.get("/health", tags=["System"])
def health():
    return {
        "status": "ok",
        "mode": "offline_replay",
        "decision_dataset_loaded": True,
        "windows_available": len(DECISIONS),
    }


@app.get("/api/v1/summary", tags=["Decisions"])
def summary():
    counts = (
        DECISIONS["decision"]
        .value_counts()
        .to_dict()
    )

    return {
        "service": "Zentinel AIOps",
        "mode": "offline_replay",
        "total_windows": len(DECISIONS),
        "decisions": {
            decision: int(counts.get(decision, 0))
            for decision in sorted(ALLOWED_DECISIONS)
        },
        "automatic_decision_coverage": float(
            (DECISIONS["decision"] != "NOVELTY_DRIFT_REVIEW").mean()
        ),
        "review_rate": float(
            (DECISIONS["decision"] == "NOVELTY_DRIFT_REVIEW").mean()
        ),
        "lstm_threshold": EVALUATION_REPORT.get(
            "lstm_validation_threshold"
        ),
        "novelty_threshold": EVALUATION_REPORT.get(
            "novelty_unknown_rate_threshold"
        ),
        "warning": (
            "These are historical evaluation results, not live predictions."
        ),
    }


@app.get("/api/v1/decisions", tags=["Decisions"])
def list_decisions(
    decision: str | None = Query(
        default=None,
        description="Filter by a decision category.",
    ),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
):
    filtered = DECISIONS

    if decision is not None:
        if decision not in ALLOWED_DECISIONS:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "Invalid decision filter.",
                    "allowed_values": sorted(ALLOWED_DECISIONS),
                },
            )

        filtered = filtered[filtered["decision"] == decision]

    total = len(filtered)
    page = filtered.iloc[offset:offset + limit]

    return {
        "mode": "offline_replay",
        "total_matching": total,
        "offset": offset,
        "limit": limit,
        "returned": len(page),
        "items": [
            public_record(row)
            for _, row in page.iterrows()
        ],
    }


@app.get("/api/v1/decisions/{window_index}", tags=["Decisions"])
def get_decision(window_index: int):
    matches = DECISIONS[
        DECISIONS["window_index"] == window_index
    ]

    if matches.empty:
        raise HTTPException(
            status_code=404,
            detail=f"Window {window_index} was not found.",
        )

    return public_record(matches.iloc[0])


@app.get("/api/v1/evaluation", tags=["Evaluation"])
def evaluation():
    """Return retrospective evaluation metrics, separate from live decisions."""
    return {
        "evaluation_type": "exploratory_temporal_test",
        "metrics": {
            "alert_precision": EVALUATION_REPORT.get("alert_precision"),
            "alert_recall_over_all_test_labelled_anomalies": (
                EVALUATION_REPORT.get(
                    "alert_recall_over_all_test_labelled_anomalies"
                )
            ),
            "alert_true_positives": EVALUATION_REPORT.get(
                "alert_true_positives"
            ),
            "alert_false_positives": EVALUATION_REPORT.get(
                "alert_false_positives"
            ),
            "review_labelled_anomalies": EVALUATION_REPORT.get(
                "review_labelled_anomalies"
            ),
        },
        "caveat": EVALUATION_REPORT.get("caveat"),
    }
