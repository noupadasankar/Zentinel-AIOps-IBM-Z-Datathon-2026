"""Parse a small BGL sample and mine event templates with Drain3."""

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from drain3 import TemplateMiner
from drain3.template_miner_config import TemplateMinerConfig

from zentinel.parsing.drain_parser import parse_bgl_line

INPUT_PATH = ROOT / "data" / "raw" / "BGL.log"
OUTPUT_PATH = ROOT / "data" / "processed" / "bgl_parsed_sample.jsonl"
REPORT_PATH = ROOT / "reports" / "drain_sample_summary.json"

SAMPLE_LIMIT = 20_000


def main() -> None:
    if not INPUT_PATH.is_file():
        raise FileNotFoundError(f"BGL dataset not found: {INPUT_PATH}")

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)

    config = TemplateMinerConfig()
    config.drain_sim_th = 0.4
    config.drain_depth = 4

    miner = TemplateMiner(config=config)

    label_counts = Counter()
    parse_status_counts = Counter()
    cluster_counts = Counter()
    change_type_counts = Counter()
    template_by_cluster = {}
    empty_message_count = 0
    processed = 0

    print(f"Input: {INPUT_PATH}")
    print(f"Limit: {SAMPLE_LIMIT:,} lines")
    print("Starting streaming parse and template mining...\n")

    with (
        INPUT_PATH.open(
            "r",
            encoding="utf-8",
            errors="replace",
        ) as source,
        OUTPUT_PATH.open(
            "w",
            encoding="utf-8",
        ) as output,
    ):
        for line_number, raw_line in enumerate(source, start=1):
            if processed >= SAMPLE_LIMIT:
                break

            raw_line = raw_line.rstrip("\r\n")

            try:
                record = parse_bgl_line(raw_line, line_number)
            except ValueError as exc:
                print(f"Parsing error at line {line_number}: {exc}")
                raise

            if not record.message:
                empty_message_count += 1

            model_input = " ".join(
                value
                for value in (
                    record.event_type,
                    record.component,
                    record.severity,
                    record.message,
                )
                if value
            )

            if not model_input:
                model_input = "[EMPTY_LOG_MESSAGE]"

            result = miner.add_log_message(model_input)

            cluster_id = result["cluster_id"]
            template = result["template_mined"]
            change_type = str(result.get("change_type", "unknown"))

            processed += 1
            label_counts[record.label] += 1
            parse_status_counts[record.parse_status] += 1
            cluster_counts[cluster_id] += 1
            template_by_cluster[cluster_id] = template
            change_type_counts[change_type] += 1

            row = record.to_dict()
            row.update(
                {
                    "source": "LogHub BGL",
                    "raw_line": raw_line,
                    "template_input": model_input,
                    "event_id": cluster_id,
                    "event_template": template,
                    "template_change_type": change_type,
                }
            )

            output.write(json.dumps(row, ensure_ascii=False) + "\n")

            if processed % 5_000 == 0:
                print(f"Processed {processed:,} lines | templates={len(cluster_counts):,}")

    top_templates = []

    for cluster_id, count in cluster_counts.most_common(20):
        top_templates.append(
            {
                "event_id": cluster_id,
                "count": count,
                "template": template_by_cluster[cluster_id],
            }
        )

    report = {
        "dataset": "LogHub BGL",
        "sample_type": "First contiguous records from original dataset",
        "sample_limit": SAMPLE_LIMIT,
        "processed_lines": processed,
        "unique_templates": len(cluster_counts),
        "empty_message_records": empty_message_count,
        "label_counts": dict(label_counts.most_common()),
        "parse_status_counts": dict(parse_status_counts),
        "template_change_counts": dict(change_type_counts),
        "top_templates": top_templates,
        "output_file": str(OUTPUT_PATH.relative_to(ROOT)),
    }

    with REPORT_PATH.open("w", encoding="utf-8") as report_file:
        json.dump(report, report_file, indent=2, ensure_ascii=False)

    print("\n========== DRAIN3 SAMPLE REPORT ==========")
    print(f"Records processed: {processed:,}")
    print(f"Unique event templates: {len(cluster_counts):,}")
    print(f"Empty messages: {empty_message_count:,}")

    print("\nLabels:")
    for label, count in label_counts.most_common():
        print(f"  {label}: {count:,}")

    print("\nParser statuses:")
    for status, count in parse_status_counts.items():
        print(f"  {status}: {count:,}")

    print("\nTop event templates:")
    for item in top_templates[:10]:
        print(f"  ID {item['event_id']}: {item['count']:,} records | {item['template']}")

    print(f"\nParsed records: {OUTPUT_PATH}")
    print(f"Summary report: {REPORT_PATH}")

    if processed == SAMPLE_LIMIT:
        print("\nSUCCESS: The complete sample was parsed.")
    else:
        print(f"\nNOTE: The source ended after {processed:,} records.")


if __name__ == "__main__":
    main()
