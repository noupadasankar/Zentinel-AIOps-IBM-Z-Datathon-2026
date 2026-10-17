import json
import statistics
import time
from collections import Counter
from itertools import islice
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
LOG_PATH = ROOT / "data/raw/BGL.log"
REPORT_PATH = ROOT / "reports/live_ingestion_benchmark.json"

API_URL = "http://127.0.0.1:8001"
START_LINE = 1100001
REQUESTED_LINES = 1000


def percentile(values, percent):
    if not values:
        return None

    ordered = sorted(values)
    position = (len(ordered) - 1) * percent / 100
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower

    return (
        ordered[lower] * (1 - fraction)
        + ordered[upper] * fraction
    )


def main():
    if not LOG_PATH.is_file():
        raise FileNotFoundError(f"Dataset not found: {LOG_PATH}")

    with LOG_PATH.open(
        "r",
        encoding="utf-8",
        errors="replace",
    ) as file:
        lines = list(
            islice(
                file,
                START_LINE - 1,
                START_LINE - 1 + REQUESTED_LINES,
            )
        )

    if not lines:
        raise ValueError("No source log lines found.")

    statuses = Counter()
    decisions = Counter()
    latencies_ms = []
    completed_windows = []
    failures = 0

    print("========== LIVE INGESTION BENCHMARK ==========")
    print(f"Source line: {START_LINE:,}")
    print(f"Requested records: {REQUESTED_LINES:,}")
    print(f"Records available: {len(lines):,}")
    print(f"API URL: {API_URL}")
    print("Timing includes local HTTP request overhead.")

    start = time.perf_counter()

    with httpx.Client(timeout=30.0) as client:
        for index, raw_line in enumerate(lines, start=1):
            request_start = time.perf_counter()

            try:
                response = client.post(
                    f"{API_URL}/api/v1/live/ingest",
                    json={"raw_line": raw_line.rstrip("\r\n")},
                )
                response.raise_for_status()
                result = response.json()

                latency = (
                    time.perf_counter() - request_start
                ) * 1000

                latencies_ms.append(latency)
                statuses[result["event"]["event_status"]] += 1

                window = result.get("window_decision")
                if window:
                    decisions[window["decision"]] += 1
                    completed_windows.append(window)

            except (httpx.HTTPError, ValueError, KeyError) as exc:
                failures += 1
                print(f"Request {index} failed: {exc}")

            if index % 100 == 0 or index == len(lines):
                print(f"Processed {index}/{len(lines)} records")

    elapsed = time.perf_counter() - start
    successful = len(latencies_ms)

    scores = [
        float(window["lstm_anomaly_score"])
        for window in completed_windows
        if window["lstm_anomaly_score"] is not None
    ]

    report = {
        "mode": "local_sequential_http_benchmark",
        "source_file": str(LOG_PATH),
        "source_start_line": START_LINE,
        "records_requested": REQUESTED_LINES,
        "records_available": len(lines),
        "successful_requests": successful,
        "failed_requests": failures,
        "elapsed_seconds": round(elapsed, 6),
        "records_per_second": (
            round(successful / elapsed, 3) if elapsed > 0 else None
        ),
        "latency_ms": {
            "mean": (
                round(statistics.mean(latencies_ms), 3)
                if latencies_ms else None
            ),
            "median_p50": (
                round(percentile(latencies_ms, 50), 3)
                if latencies_ms else None
            ),
            "p95": (
                round(percentile(latencies_ms, 95), 3)
                if latencies_ms else None
            ),
            "p99": (
                round(percentile(latencies_ms, 99), 3)
                if latencies_ms else None
            ),
            "max": (
                round(max(latencies_ms), 3)
                if latencies_ms else None
            ),
        },
        "event_match_status_counts": dict(statuses),
        "completed_windows": len(completed_windows),
        "window_decision_counts": dict(decisions),
        "scorable_windows": len(scores),
        "mean_lstm_score": (
            round(statistics.mean(scores), 6) if scores else None
        ),
        "limitations": [
            "Single-process local API benchmark.",
            "Sequential requests; no concurrency tested.",
            "Latency includes local HTTP overhead.",
            "Not a production or multi-client throughput guarantee.",
            "The live API state is modified by this benchmark.",
        ],
    }

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(
        json.dumps(report, indent=2),
        encoding="utf-8",
    )

    print("\n========== BENCHMARK RESULTS ==========")
    print(f"Successful requests: {successful:,}")
    print(f"Failed requests: {failures:,}")
    print(f"Elapsed seconds: {elapsed:.3f}")
    print(f"Throughput: {report['records_per_second']} records/sec")
    print(f"Mean latency: {report['latency_ms']['mean']} ms")
    print(f"Median latency (P50): {report['latency_ms']['median_p50']} ms")
    print(f"P95 latency: {report['latency_ms']['p95']} ms")
    print(f"P99 latency: {report['latency_ms']['p99']} ms")
    print(f"Matched/unknown statuses: {dict(statuses)}")
    print(f"Completed windows: {len(completed_windows)}")
    print(f"Window decisions: {dict(decisions)}")
    print(f"\nReport saved: {REPORT_PATH}")


if __name__ == "__main__":
    main()
