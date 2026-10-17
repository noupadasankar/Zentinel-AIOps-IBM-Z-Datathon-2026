"""Script to export LSTM model to ONNX opset 17 and run 1,000-run parity verification."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR / "src"))

from zentinel.config import REPORTS_DIR
from zentinel.models.onnx_export import (
    DEFAULT_ONNX_PATH,
    evaluate_onnx_parity,
    export_lstm_to_onnx,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Export PyTorch LSTM to ONNX and verify parity.")
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_ONNX_PATH,
        help="Target path for ONNX artifact",
    )
    parser.add_argument(
        "--opset",
        type=int,
        default=17,
        help="ONNX operator set version (default: 17)",
    )
    parser.add_argument(
        "--runs",
        type=int,
        default=1000,
        help="Number of real runs for parity check (default: 1000)",
    )
    parser.add_argument(
        "--windows",
        type=int,
        default=1000,
        help="Number of test windows for decision check (default: 1000)",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=REPORTS_DIR / "onnx_parity_report.json",
        help="Output JSON path for parity verification report",
    )
    args = parser.parse_args()

    print(f"--> Exporting LSTM to ONNX (opset {args.opset}): {args.output}")
    export_lstm_to_onnx(output_path=args.output, opset_version=args.opset)
    print("--> Export complete. Verifying ONNX graph and running parity evaluation...")

    report = evaluate_onnx_parity(
        onnx_path=args.output,
        num_runs=args.runs,
        num_windows=args.windows,
    )

    args.report.parent.mkdir(parents=True, exist_ok=True)
    with args.report.open("w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    print("\n================== ONNX PARITY & LATENCY REPORT ==================")
    print(f"Status:                      {report['status']}")
    print(f"Runs Evaluated:              {report['runs_parity']['runs_evaluated']}")
    print(f"Max Logit Difference:        {report['runs_parity']['max_logit_difference']:.2e}")
    print(f"Mean Logit Difference:       {report['runs_parity']['mean_logit_difference']:.2e}")
    print(f"Windows Evaluated:           {report['window_decisions_parity']['windows_evaluated']}")
    print(f"Identical Decisions %:       {report['window_decisions_parity']['identical_decisions_percentage']:.2f}%")
    print("------------------------------------------------------------------")
    print(f"PyTorch CPU Latency (mean):  {report['cpu_latency_benchmark']['pytorch_cpu_ms']['mean']:.3f} ms")
    print(f"ONNX Runtime Latency (mean): {report['cpu_latency_benchmark']['onnxruntime_cpu_ms']['mean']:.3f} ms")
    print(f"Speedup Factor:              {report['cpu_latency_benchmark']['speedup_factor']}x")
    print(f"Report saved to:             {args.report}")
    print("==================================================================\n")


if __name__ == "__main__":
    main()
