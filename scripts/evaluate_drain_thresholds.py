"""Compare Drain3 thresholds using training data only."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from drain3 import TemplateMiner
from drain3.template_miner_config import TemplateMinerConfig

from zentinel.parsing.drain_parser import parse_bgl_line

INPUT_PATH = ROOT / "data" / "raw" / "BGL.log"
REPORT_PATH = ROOT / "reports" / "drain_threshold_experiment.json"

SAMPLE_SIZE = 100_000
THRESHOLDS = [0.4, 0.5, 0.6, 0.7, 0.8]


def build_template_input(record) -> str:
    parts = [
        record.event_type,
        record.component,
        record.severity,
        record.message,
    ]
    return " ".join(part for part in parts if part) or "[EMPTY_LOG_MESSAGE]"


def main() -> None:
    if not INPUT_PATH.is_file():
        raise FileNotFoundError(INPUT_PATH)

    results = []

    for threshold in THRESHOLDS:
        config = TemplateMinerConfig()
        config.drain_sim_th = threshold
        config.drain_depth = 4

        miner = TemplateMiner(
            persistence_handler=None,
            config=config,
        )

        print(f"\nTesting threshold {threshold:.2f}...")

        processed = 0

        with INPUT_PATH.open(
            "r",
            encoding="utf-8",
            errors="replace",
        ) as source:
            for line_number, raw_line in enumerate(source, start=1):
                record = parse_bgl_line(
                    raw_line.rstrip("\r\n"),
                    line_number,
                )
                miner.add_log_message(build_template_input(record))
                processed += 1

                if processed >= SAMPLE_SIZE:
                    break

        clusters = sorted(
            miner.drain.clusters,
            key=lambda cluster: (-cluster.size, cluster.cluster_id),
        )

        largest_count = clusters[0].size if clusters else 0

        total_wildcards = sum(cluster.size * cluster.get_template().count("<*>") for cluster in clusters)

        weighted_average_wildcards = total_wildcards / processed if processed else 0.0

        top_templates = [
            {
                "event_id": cluster.cluster_id,
                "count": cluster.size,
                "template": cluster.get_template(),
                "wildcard_count": cluster.get_template().count("<*>"),
            }
            for cluster in clusters[:10]
        ]

        result = {
            "threshold": threshold,
            "records_processed": processed,
            "template_count": len(clusters),
            "largest_template_count": largest_count,
            "largest_template_percentage": round(largest_count * 100 / processed, 2) if processed else 0.0,
            "weighted_average_wildcards_per_record": round(weighted_average_wildcards, 3),
            "top_templates": top_templates,
        }

        results.append(result)

        print(f"Records: {processed:,}")
        print(f"Templates: {len(clusters):,}")
        print(f"Largest template share: {result['largest_template_percentage']:.2f}%")
        print(f"Weighted average wildcards: {weighted_average_wildcards:.3f}")

        print("Top 3 templates:")
        for item in top_templates[:3]:
            print(f"  {item['count']:,} records | {item['template']}")

    report = {
        "dataset": "LogHub BGL",
        "sample": "First 100,000 training-period records",
        "labels_used_for_tuning": False,
        "threshold_results": results,
    }

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)

    with REPORT_PATH.open("w", encoding="utf-8") as file:
        json.dump(report, file, indent=2, ensure_ascii=False)

    print(f"\nDetailed report saved to: {REPORT_PATH}")
    print("\nSUCCESS: Threshold experiment completed.")


if __name__ == "__main__":
    main()
