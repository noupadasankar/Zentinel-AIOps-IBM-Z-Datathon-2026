import argparse
import sys
from pathlib import Path

# Add src to sys.path
SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from zentinel.models.evaluation import run_full_walk_forward_evaluation


def main():
    parser = argparse.ArgumentParser(
        description="Run walk-forward evaluation across 4 test slices."
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Target report JSON path.",
    )
    args = parser.parse_args()

    print("Running walk-forward temporal evaluation...")
    report = run_full_walk_forward_evaluation(output_report_path=args.output)

    print("\n========== WALK-FORWARD EVALUATION RESULTS ==========")
    print(f"Total Test Windows: {report['total_test_windows']}")
    overall = report["overall_test_metrics"]
    print(f"Overall Coverage: {overall['coverage']:.2%}")
    print(f"Precision: {overall['precision']:.2%} | Recall: {overall['recall']:.2%} | F1: {overall['f1']:.2%}")
    if overall.get("false_alerts_per_hour") is not None:
        print(f"False Alerts / Hour: {overall['false_alerts_per_hour']:.4f}")

    delays = report["detection_delay_metrics"]
    if delays.get("median_delay_seconds") is not None:
        print(f"Detection Delay: Median={delays['median_delay_seconds']:.1f}s, p90={delays['p90_delay_seconds']:.1f}s")

    print("\nSlice Breakdown:")
    for s in report["slice_results"]:
        print(
            f"  Slice {s['slice_index']}: Windows {s['window_range'][0]}-{s['window_range'][1]} | "
            f"Coverage={s['coverage']:.2%} | Precision={s['precision']:.2%} | "
            f"Recall={s['recall']:.2%} | F1={s['f1']:.2%}"
        )

    print("\nReport saved to reports/temporal_walk_forward_evaluation.json")


if __name__ == "__main__":
    main()
