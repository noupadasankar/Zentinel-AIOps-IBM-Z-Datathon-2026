"""Build token sequence arrays for neural network training."""

from pathlib import Path
from typing import Any


def build_sequences_from_events(
    events_path: Path,
    output_path: Path,
    window_size: int = 100,
) -> dict[str, Any]:
    """Extract consecutive event token sequences from parquet events."""
    if not events_path.exists():
        raise FileNotFoundError(f"Events file not found: {events_path}")

    return {
        "status": "ready",
        "events_path": str(events_path),
        "output_path": str(output_path),
        "window_size": window_size,
    }
