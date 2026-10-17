
"""Stream the complete BGL dataset through Drain3 and write Parquet."""

from collections import Counter
from pathlib import Path
import json
import os
import sys

import pyarrow as pa
import pyarrow.parquet as pq

from drain3 import TemplateMiner
from drain3.file_persistence import FilePersistence
from drain3.template_miner_config import TemplateMinerConfig

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from zentinel.parsing.drain_parser import parse_bgl_line


INPUT_PATH = ROOT / "data" / "raw" / "BGL.log"
OUTPUT_PATH = ROOT / "data" / "processed" / "bgl_parsed.parquet"
TEMP_PATH = ROOT / "data" / "processed" / "bgl_parsed.partial.parquet"
STATE_PATH = ROOT / "artifacts" / "drain3_bgl_state.bin"
REPORT_PATH = ROOT / "reports" / "bgl_parsing_summary.json"

BATCH_SIZE = 10_000
PROGRESS_INTERVAL = 100_000


SCHEMA = pa.schema([
    pa.field("line_number", pa.int64(), nullable=False),
    pa.field("label", pa.string(), nullable=False),
    pa.field("timestamp", pa.int64(), nullable=False),
    pa.field("date", pa.string(), nullable=False),
    pa.field("node", pa.string(), nullable=False),
    pa.field("event_time", pa.string(), nullable=False),
    pa.field("node_repeat", pa.string()),
    pa.field("event_type", pa.string()),
    pa.field("component", pa.string()),
    pa.field("severity", pa.string()),
    pa.field("message", pa.string(), nullable=False),
    pa.field("parse_status", pa.string(), nullable=False),
    pa.field("raw_line", pa.string(), nullable=False),
    pa.field("event_id", pa.int64(), nullable=False),
    pa.field("event_template", pa.string(), nullable=False),
    pa.field("template_change_type", pa.string(), nullable=False),
])


def main() -> None:
    if not INPUT_PATH.is_file():
        raise FileNotFoundError(f"BGL input not found: {INPUT_PATH}")

    # Never silently reuse a previous run's model state or partial output.
    existing = [
        path for path in (OUTPUT_PATH, TEMP_PATH, STATE_PATH)
        if path.exists()
    ]
    if existing:
        paths = "\n".join(str(path) for path in existing)
        raise SystemExit(
            "Existing output/model state found. Do not rerun from the "
            "beginning using an old Drain3 state.\n"
            f"{paths}\n"
            "Inspect these generated files before deciding whether to "
            "resume or start a clean run."
        )

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)

    config = TemplateMinerConfig()
    config.drain_sim_th = 0.4
    config.drain_depth = 4

    persistence = FilePersistence(str(STATE_PATH))
    miner = TemplateMiner(persistence, config)

    labels = Counter()
    parse_statuses = Counter()
    severities = Counter()
    template_counts = Counter()
    template_text = {}
    change_types = Counter()

    processed = 0
    empty_messages = 0
    writer = None

    file_size = INPUT_PATH.stat().st_size

    print("========== ZENTINEL AIOPS: FULL BGL PARSING ==========")
    print(f"Input: {INPUT_PATH}")
    print(f"Input size: {file_size:,} bytes")
    print(f"Parquet output: {OUTPUT_PATH}")
    print(f"Drain3 state: {STATE_PATH}")
    print("Processing all records sequentially...\n")

    try:
        writer = pq.ParquetWriter(
            str(TEMP_PATH),
            SCHEMA,
            compression="zstd",
        )

        batch = []

        with INPUT_PATH.open(
            "r",
            encoding="utf-8",
            errors="replace",
        ) as source:
            for line_number, raw in enumerate(source, start=1):
                raw_line = raw.rstrip("\r\n")

                record = parse_bgl_line(raw_line, line_number)

                # Labels are preserved for evaluation but excluded from
                # the input sent to Drain3 to avoid label leakage.
                template_input = " ".join(
                    value
                    for value in (
                        record.event_type,
                        record.component,
                        record.severity,
                        record.message,
                    )
                    if value
                )

                if not template_input:
                    template_input = "[EMPTY_LOG_MESSAGE]"

                result = miner.add_log_message(template_input)

                event_id = int(result["cluster_id"])
                event_template = str(result["template_mined"])
                change_type = str(result.get("change_type") or "none")

                processed += 1
                labels[record.label] += 1
                parse_statuses[record.parse_status] += 1
                change_types[change_type] += 1
                template_counts[event_id] += 1
                template_text[event_id] = event_template

                if record.severity:
                    severities[record.severity] += 1

                if not record.message:
                    empty_messages += 1

                row = record.to_dict()

                batch.append({
                    "line_number": row["line_number"],
                    "label": row["label"],
                    "timestamp": row["timestamp"],
                    "date": row["date"],
                    "node": row["node"],
                    "event_time": row["event_time"],
                    "node_repeat": row["node_repeat"],
                    "event_type": row["event_type"],
                    "component": row["component"],
                    "severity": row["severity"],
                    "message": row["message"],
                    "parse_status": row["parse_status"],
                    "raw_line": raw_line,
                    "event_id": event_id,
                    "event_template": event_template,
                    "template_change_type": change_type,
                })

                if len(batch) >= BATCH_SIZE:
                    table = pa.Table.from_pylist(batch, schema=SCHEMA)
                    writer.write_table(table)
                    batch.clear()

                if processed % PROGRESS_INTERVAL == 0:
                    print(
                        f"Processed {processed:,} lines | "
                        f"templates={len(template_counts):,} | "
                        f"fallbacks={parse_statuses.get('fallback', 0):,}"
                    )

        if batch:
            table = pa.Table.from_pylist(batch, schema=SCHEMA)
            writer.write_table(table)

        writer.close()
        writer = None

        # Publish the completed Parquet file only after the input finishes.
        os.replace(TEMP_PATH, OUTPUT_PATH)

    except Exception:
        if writer is not None:
            writer.close()
        print(
            "\nERROR: Full parsing did not complete. A partial output "
            "or saved Drain3 state may remain. Do not restart blindly."
        )
        raise

    normal_count = labels.get("-", 0)
    alert_count = processed - normal_count

    top_templates = [
        {
            "event_id": event_id,
            "count": count,
            "template": template_text[event_id],
        }
        for event_id, count in template_counts.most_common(30)
    ]

    report = {
        "dataset": "LogHub BGL",
        "input_file": str(INPUT_PATH.relative_to(ROOT)),
        "input_size_bytes": file_size,
        "processed_lines": processed,
        "normal_label": "-",
        "normal_label_lines": normal_count,
        "alert_label_lines": alert_count,
        "alert_label_percentage": (
            round(alert_count * 100 / processed, 4)
            if processed else 0.0
        ),
        "empty_message_lines": empty_messages,
        "parse_status_counts": dict(parse_statuses),
        "severity_counts": dict(severities.most_common()),
        "label_counts": dict(labels.most_common()),
        "unique_event_templates": len(template_counts),
        "template_change_counts": dict(change_types),
        "top_templates": top_templates,
        "output_file": str(OUTPUT_PATH.relative_to(ROOT)),
        "model_state_file": str(STATE_PATH.relative_to(ROOT)),
        "output_rows_written": processed,
    }

    with REPORT_PATH.open("w", encoding="utf-8") as report_file:
        json.dump(
            report,
            report_file,
            indent=2,
            ensure_ascii=False,
        )

    print("\n========== FULL BGL PARSING REPORT ==========")
    print(f"Processed records: {processed:,}")
    print(f"Unique templates: {len(template_counts):,}")
    print(f"Normal-labelled: {normal_count:,}")
    print(f"Alert-labelled: {alert_count:,}")
    print(f"Alert percentage: {report['alert_label_percentage']:.4f}%")
    print(f"Empty messages: {empty_messages:,}")
    print(f"Parser status counts: {dict(parse_statuses)}")

    print("\nTop 10 templates:")
    for item in top_templates[:10]:
        print(
            f"  ID {item['event_id']}: {item['count']:,} records | "
            f"{item['template']}"
        )

    print(f"\nParquet output: {OUTPUT_PATH}")
    print(f"Drain3 state: {STATE_PATH}")
    print(f"Summary report: {REPORT_PATH}")
    print("\nSUCCESS: Full BGL parsing completed.")


if __name__ == "__main__":
    main()
