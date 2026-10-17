"""Replay historic BGL log files into the live inference API."""

import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

from zentinel.ingestion.watcher import compute_file_id, generate_idempotency_key


def replay_log_slice(
    log_path: Path,
    api_url: str = "http://127.0.0.1:8001",
    api_key: str | None = None,
    start_line: int = 1,
    max_lines: int = 1000,
    delay_seconds: float = 0.01,
    on_event: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Replay a range of lines from a log file to the live API."""
    if not log_path.exists():
        raise FileNotFoundError(f"Log file not found: {log_path}")

    file_id = compute_file_id(log_path)
    sent = 0
    completed_windows = 0
    headers = {}
    if api_key:
        headers["X-API-Key"] = api_key

    with httpx.Client(timeout=30.0) as client, log_path.open("r", encoding="utf-8", errors="replace") as f:
        for line_idx, line in enumerate(f, 1):
            if line_idx < start_line:
                continue
            if sent >= max_lines:
                break

            line_clean = line.strip()
            if not line_clean:
                continue

            idempotency_key = generate_idempotency_key(file_id, line_idx)
            try:
                resp = client.post(
                    f"{api_url.rstrip('/')}/api/v1/live/ingest",
                    json={"raw_line": line_clean, "idempotency_key": idempotency_key},
                    headers=headers,
                )
                if resp.status_code == 200:
                    sent += 1
                    data = resp.json()
                    if data.get("window_completed"):
                        completed_windows += 1
                    if on_event:
                        on_event(data)
            except (httpx.HTTPError, OSError):
                pass

            if delay_seconds > 0:
                time.sleep(delay_seconds)

    return {
        "lines_sent": sent,
        "completed_windows": completed_windows,
    }
