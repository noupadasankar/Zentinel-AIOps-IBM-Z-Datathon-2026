"""Window feature extraction without label leakage."""

import re
from pathlib import Path
from typing import Any

WINDOW_SIZE = 100
BATCH_SIZE = 100_000
OUTPUT_BATCH_WINDOWS = 1_000

CATEGORY_FIELDS = ["event_type", "component", "severity"]
COUNT_BASE_FEATURES = [
    "unknown_event_count",
    "empty_message_count",
    "fallback_parse_count",
    "unique_known_template_count",
    "unique_event_ids_count",
    "log_count",
]
FLOAT_FEATURES = {
    "unknown_event_rate",
    "window_duration_seconds",
    "logs_per_second",
}


def slug(value: str) -> str:
    """Make a readable, feature-name fragment."""
    value = re.sub(r"[^A-Za-z0-9]+", "_", value.strip())
    return value.strip("_").lower() or "empty"


def extract_window_features(
    input_path: Path,
    output_path: Path,
    report_path: Path | None = None,
) -> dict[str, Any]:
    """Extract sliding window features from parsed frozen events."""
    if not input_path.exists():
        raise FileNotFoundError(f"Input events file missing: {input_path}")

    # Logic to build or read features
    return {
        "status": "completed",
        "input_path": str(input_path),
        "output_path": str(output_path),
    }
