"""Comprehensive ingestion benchmark on TEST-period BGL lines.

Measures sequential (concurrency=1) and 8-way concurrent throughput, latency percentiles
(p50, p95, p99), CPU utilization, and memory usage across 3 repeated trials of 60 seconds each.
Outputs verified results to reports/benchmark_report.json and reports/live_ingestion_benchmark.json.
"""

from __future__ import annotations

import argparse
import itertools
import json
import os
import platform
import sqlite3
import sys
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import numpy as np
import psutil

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR / "src"))

import fastapi
import onnx
import onnxruntime as ort
import torch
from fastapi.testclient import TestClient

from zentinel.api.live_api import app
from zentinel.config import RAW_LOG_PATH, REPORTS_DIR

TEST_START_LINE = 4035801  # Exact start of chronological test split
BUFFER_LINES = 150000


def load_test_lines(log_path: Path = RAW_LOG_PATH, start_line: int = TEST_START_LINE, count: int = BUFFER_LINES) -> list[str]:
    """Load test-period log lines into memory to isolate engine performance from disk bottlenecks."""
    if not log_path.is_file():
        raise FileNotFoundError(f"Source log file missing: {log_path}")

    lines: list[str] = []
    with log_path.open("r", encoding="utf-8", errors="replace") as f:
        # Fast-forward to start_line
        for _ in range(start_line - 1):
            f.readline()
        for _ in range(count):
            line = f.readline()
            if not line:
                break
            lines.append(line.rstrip("\r\n"))

    if not lines:
        raise ValueError(f"No log lines found starting at line {start_line}")
    return lines


class ResourceMonitor(threading.Thread):
    """Samples process CPU and memory utilization in the background."""

    def __init__(self, interval: float = 0.5):
        super().__init__(daemon=True)
        self.interval = interval
        self.running = True
        self.process = psutil.Process(os.getpid())
        self.cpu_samples: list[float] = []
        self.memory_samples: list[float] = []

    def run(self) -> None:
        self.process.cpu_percent(interval=None)  # prime
        while self.running:
            time.sleep(self.interval)
            try:
                cpu = self.process.cpu_percent(interval=None)
                mem = self.process.memory_info().rss / (1024 * 1024)  # MB
                self.cpu_samples.append(cpu)
                self.memory_samples.append(mem)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                break

    def stop(self) -> dict[str, Any]:
        self.running = False
        return {
            "cpu_percent_mean": round(float(np.mean(self.cpu_samples)), 2) if self.cpu_samples else 0.0,
            "cpu_percent_max": round(float(np.max(self.cpu_samples)), 2) if self.cpu_samples else 0.0,
            "memory_rss_mb_start": round(float(self.memory_samples[0]), 2) if self.memory_samples else 0.0,
            "memory_rss_mb_peak": round(float(np.max(self.memory_samples)), 2) if self.memory_samples else 0.0,
            "memory_rss_mb_end": round(float(self.memory_samples[-1]), 2) if self.memory_samples else 0.0,
        }


def run_benchmark_trial(
    client: TestClient,
    lines: list[str],
    concurrency: int,
    duration_seconds: float,
) -> dict[str, Any]:
    """Execute a single timed benchmark trial under specified concurrency."""
    line_cycle = itertools.cycle(lines)
    latencies_ms: list[float] = []
    status_counts: Counter[str] = Counter()
    decision_counts: Counter[str] = Counter()
    completed_windows = 0
    lock = threading.Lock()

    monitor = ResourceMonitor(interval=0.5)
    monitor.start()

    stop_event = threading.Event()
    start_time = time.perf_counter()

    def worker() -> None:
        while not stop_event.is_set():
            with lock:
                raw_line = next(line_cycle)

            t0 = time.perf_counter()
            resp = client.post("/api/v1/live/ingest", json={"raw_line": raw_line})
            lat = (time.perf_counter() - t0) * 1000.0

            if resp.status_code == 200:
                data = resp.json()
                event_status = data.get("event", {}).get("event_status", "unknown")
                win = data.get("window_decision")

                with lock:
                    latencies_ms.append(lat)
                    status_counts[event_status] += 1
                    if win:
                        decision_counts[win.get("decision", "UNKNOWN")] += 1
                        nonlocal completed_windows
                        completed_windows += 1

            if time.perf_counter() - start_time >= duration_seconds:
                stop_event.set()
                break

    if concurrency == 1:
        worker()
    else:
        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            futures = [executor.submit(worker) for _ in range(concurrency)]
            for f in futures:
                f.result()

    elapsed = time.perf_counter() - start_time
    resource_stats = monitor.stop()

    total_events = len(latencies_ms)
    throughput = total_events / elapsed if elapsed > 0 else 0.0

    return {
        "concurrency": concurrency,
        "duration_seconds": round(elapsed, 3),
        "total_events": total_events,
        "events_per_second": round(throughput, 2),
        "latency_ms": {
            "mean": round(float(np.mean(latencies_ms)), 3) if latencies_ms else 0.0,
            "p50": round(float(np.percentile(latencies_ms, 50)), 3) if latencies_ms else 0.0,
            "p95": round(float(np.percentile(latencies_ms, 95)), 3) if latencies_ms else 0.0,
            "p99": round(float(np.percentile(latencies_ms, 99)), 3) if latencies_ms else 0.0,
            "max": round(float(np.max(latencies_ms)), 3) if latencies_ms else 0.0,
        },
        "completed_windows": completed_windows,
        "window_decisions": dict(decision_counts),
        "status_counts": dict(status_counts),
        "resources": resource_stats,
    }


def aggregate_repeats(trials: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate measurements across repeated trials."""
    events_sec = [t["events_per_second"] for t in trials]
    p50s = [t["latency_ms"]["p50"] for t in trials]
    p95s = [t["latency_ms"]["p95"] for t in trials]
    p99s = [t["latency_ms"]["p99"] for t in trials]
    means = [t["latency_ms"]["mean"] for t in trials]
    cpus = [t["resources"]["cpu_percent_mean"] for t in trials]
    mems = [t["resources"]["memory_rss_mb_peak"] for t in trials]

    return {
        "trials_count": len(trials),
        "events_per_second_mean": round(float(np.mean(events_sec)), 2),
        "events_per_second_std": round(float(np.std(events_sec)), 2),
        "latency_ms_p50_mean": round(float(np.mean(p50s)), 3),
        "latency_ms_p95_mean": round(float(np.mean(p95s)), 3),
        "latency_ms_p99_mean": round(float(np.mean(p99s)), 3),
        "latency_ms_mean": round(float(np.mean(means)), 3),
        "cpu_percent_mean": round(float(np.mean(cpus)), 2),
        "memory_rss_mb_peak_mean": round(float(np.mean(mems)), 2),
        "individual_trials": trials,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Live ingestion benchmark on TEST-period lines.")
    parser.add_argument("--duration", type=float, default=60.0, help="Duration in seconds per trial (default: 60.0)")
    parser.add_argument("--repeats", type=int, default=3, help="Number of repeats per configuration (default: 3)")
    parser.add_argument("--quick", action="store_true", help="Quick mode for testing: 5s duration, 1 repeat")
    parser.add_argument(
        "--output",
        type=Path,
        default=REPORTS_DIR / "benchmark_report.json",
        help="Target output report path",
    )
    args = parser.parse_args()

    duration = 5.0 if args.quick else args.duration
    repeats = 1 if args.quick else args.repeats

    print("\n================== ZENTINEL INGESTION BENCHMARK ==================")
    print(f"Data Source:       TEST period (line {TEST_START_LINE:,} onwards)")
    print(f"Duration / Trial:  {duration} seconds")
    print(f"Repeats / Config:  {repeats}")
    print("Configurations:    Sequential (concurrency=1) & 8-way Concurrent (concurrency=8)")
    print("------------------------------------------------------------------")

    print(f"--> Loading {BUFFER_LINES:,} test-period lines from {RAW_LOG_PATH.name}...")
    lines = load_test_lines(RAW_LOG_PATH, start_line=TEST_START_LINE, count=BUFFER_LINES)
    print(f"--> Loaded {len(lines):,} test lines successfully.")

    benchmark_results: dict[str, Any] = {}

    with TestClient(app) as client:
        # 1. Sequential Trials
        seq_trials: list[dict[str, Any]] = []
        for r in range(1, repeats + 1):
            print(f"\n[Trial {r}/{repeats}] Running SEQUENTIAL benchmark (60s)...")
            trial_res = run_benchmark_trial(client, lines, concurrency=1, duration_seconds=duration)
            seq_trials.append(trial_res)
            print(f"   -> Throughput: {trial_res['events_per_second']:.2f} events/s | "
                  f"Latency: p50={trial_res['latency_ms']['p50']:.2f}ms, "
                  f"p95={trial_res['latency_ms']['p95']:.2f}ms, "
                  f"p99={trial_res['latency_ms']['p99']:.2f}ms | "
                  f"CPU: {trial_res['resources']['cpu_percent_mean']:.1f}%")

        benchmark_results["sequential"] = aggregate_repeats(seq_trials)

        # 2. 8-way Concurrent Trials
        concur_trials: list[dict[str, Any]] = []
        for r in range(1, repeats + 1):
            print(f"\n[Trial {r}/{repeats}] Running 8-WAY CONCURRENT benchmark (60s)...")
            trial_res = run_benchmark_trial(client, lines, concurrency=8, duration_seconds=duration)
            concur_trials.append(trial_res)
            print(f"   -> Throughput: {trial_res['events_per_second']:.2f} events/s | "
                  f"Latency: p50={trial_res['latency_ms']['p50']:.2f}ms, "
                  f"p95={trial_res['latency_ms']['p95']:.2f}ms, "
                  f"p99={trial_res['latency_ms']['p99']:.2f}ms | "
                  f"CPU: {trial_res['resources']['cpu_percent_mean']:.1f}%")

        benchmark_results["concurrent_8"] = aggregate_repeats(concur_trials)

    # Hardware & System Info
    cpu_freq = psutil.cpu_freq()
    system_metadata = {
        "os": platform.platform(),
        "processor": platform.processor(),
        "cpu_count_logical": psutil.cpu_count(logical=True),
        "cpu_count_physical": psutil.cpu_count(logical=False),
        "cpu_freq_mhz_max": round(cpu_freq.max, 1) if cpu_freq else None,
        "total_memory_gb": round(psutil.virtual_memory().total / (1024**3), 2),
        "versions": {
            "python": platform.python_version(),
            "fastapi": fastapi.__version__,
            "sqlite": sqlite3.sqlite_version,
            "torch": torch.__version__,
            "onnx": onnx.__version__,
            "onnxruntime": ort.__version__,
        },
    }

    final_report = {
        "benchmark_type": "bgl_test_period_live_ingestion",
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "test_dataset_provenance": {
            "source_file": "data/raw/BGL.log",
            "start_line": TEST_START_LINE,
            "lines_buffered": len(lines),
            "period": "exploratory temporal test period",
        },
        "system": system_metadata,
        "results": benchmark_results,
    }

    # Save to reports/benchmark_report.json and reports/live_ingestion_benchmark.json
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as f:
        json.dump(final_report, f, indent=2)

    live_bench_path = REPORTS_DIR / "live_ingestion_benchmark.json"
    with live_bench_path.open("w", encoding="utf-8") as f:
        json.dump(final_report, f, indent=2)

    print("\n================== BENCHMARK SUMMARY ==================")
    seq_res = benchmark_results["sequential"]
    con_res = benchmark_results["concurrent_8"]

    print(f"Sequential (1 worker):     {seq_res['events_per_second_mean']} +/- {seq_res['events_per_second_std']} events/s")
    print(f"  Latency: p50={seq_res['latency_ms_p50_mean']}ms | p95={seq_res['latency_ms_p95_mean']}ms | p99={seq_res['latency_ms_p99_mean']}ms")
    print(f"  Resource: CPU={seq_res['cpu_percent_mean']}% | Peak RAM={seq_res['memory_rss_mb_peak_mean']} MB")
    print("-------------------------------------------------------")
    print(f"Concurrent (8 workers):    {con_res['events_per_second_mean']} +/- {con_res['events_per_second_std']} events/s")
    print(f"  Latency: p50={con_res['latency_ms_p50_mean']}ms | p95={con_res['latency_ms_p95_mean']}ms | p99={con_res['latency_ms_p99_mean']}ms")
    print(f"  Resource: CPU={con_res['cpu_percent_mean']}% | Peak RAM={con_res['memory_rss_mb_peak_mean']} MB")
    print("-------------------------------------------------------")
    print(f"Report saved to: {args.output}")
    print(f"Live benchmark saved to: {live_bench_path}")
    print("=======================================================\n")


if __name__ == "__main__":
    main()
