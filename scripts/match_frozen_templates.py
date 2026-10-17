
"""Match all BGL logs against frozen training-only Drain3 templates."""

import json
import os
from collections import Counter
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
from drain3 import TemplateMiner
from drain3.file_persistence import FilePersistence
from drain3.template_miner_config import TemplateMinerConfig

ROOT = Path(__file__).resolve().parents[1]

INPUT_PATH = ROOT / "data" / "processed" / "bgl_parsed.parquet"
STATE_PATH = ROOT / "artifacts" / "drain3_train_state_0_7.bin"
TRAIN_REPORT_PATH = ROOT / "reports" / "drain3_train_summary_0_7.json"
SPLIT_PATH = ROOT / "reports" / "chronological_split_report.json"

OUTPUT_PATH = ROOT / "data" / "processed" / "bgl_frozen_events.parquet"
TEMP_PATH = ROOT / "data" / "processed" / "bgl_frozen_events.partial.parquet"
REPORT_PATH = ROOT / "reports" / "frozen_template_match_report.json"

BATCH_SIZE = 10_000
PROGRESS_INTERVAL = 250_000

UNKNOWN_EVENT_ID = -1
UNKNOWN_TEMPLATE = "[UNKNOWN_EVENT]"

INPUT_COLUMNS = [
    "line_number",
    "label",
    "timestamp",
    "date",
    "node",
    "event_time",
    "node_repeat",
    "event_type",
    "component",
    "severity",
    "message",
    "parse_status",
    "raw_line",
]

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
    pa.field("window_id", pa.int64(), nullable=False),
    pa.field("split", pa.string(), nullable=False),
    pa.field("frozen_event_id", pa.int64(), nullable=False),
    pa.field("frozen_template", pa.string(), nullable=False),
    pa.field("template_match_status", pa.string(), nullable=False),
])


def build_template_input(row: dict) -> str:
    """Construct the same label-free input used during Drain3 fitting."""
    values = [
        row["event_type"],
        row["component"],
        row["severity"],
        row["message"],
    ]
    return " ".join(value for value in values if value) or (
        "[EMPTY_LOG_MESSAGE]"
    )


def get_split(line_number: int, train_end: int, validation_end: int) -> str:
    if line_number <= train_end:
        return "train"
    if line_number <= validation_end:
        return "validation"
    return "test"


def main() -> None:
    required_files = [
        INPUT_PATH,
        STATE_PATH,
        TRAIN_REPORT_PATH,
        SPLIT_PATH,
    ]

    for path in required_files:
        if not path.is_file():
            raise FileNotFoundError(f"Required file missing: {path}")

    if OUTPUT_PATH.exists() or TEMP_PATH.exists():
        raise SystemExit(
            "An output or partial output already exists.\n"
            "Inspect the existing files before starting another run."
        )

    with TRAIN_REPORT_PATH.open("r", encoding="utf-8") as file:
        training_report = json.load(file)

    with SPLIT_PATH.open("r", encoding="utf-8") as file:
        split_report = json.load(file)

    train_end = split_report["line_boundaries"]["train"]["end"]
    validation_end = split_report["line_boundaries"]["validation"]["end"]

    config = TemplateMinerConfig()
    config.drain_sim_th = 0.70
    config.drain_depth = 4

    persistence = FilePersistence(str(STATE_PATH))
    miner = TemplateMiner(
        persistence_handler=persistence,
        config=config,
    )

    loaded_template_count = len(miner.drain.clusters)
    expected_template_count = training_report["unique_training_templates"]

    if loaded_template_count != expected_template_count:
        raise RuntimeError(
            "Loaded Drain3 state does not match the training report: "
            f"loaded={loaded_template_count}, "
            f"expected={expected_template_count}"
        )

    print("========== FROZEN TEMPLATE MATCHING ==========")
    print(f"Loaded training templates: {loaded_template_count:,}")
    print(f"Training boundary: {train_end:,}")
    print(f"Validation boundary: {validation_end:,}")
    print("Template updates are disabled: using match() only.\n")

    per_split = {
        name: {
            "records": 0,
            "matched": 0,
            "unknown": 0,
            "normal_label_records": 0,
            "alert_label_records": 0,
            "event_ids": set(),
        }
        for name in ("train", "validation", "test")
    }

    labels_by_split = {
        name: Counter()
        for name in ("train", "validation", "test")
    }

    matched_event_counts = Counter()
    matched_event_templates = {}
    processed = 0
    writer = None

    try:
        writer = pq.ParquetWriter(
            str(TEMP_PATH),
            SCHEMA,
            compression="zstd",
        )

        source_parquet = pq.ParquetFile(INPUT_PATH)

        for batch in source_parquet.iter_batches(
            batch_size=BATCH_SIZE,
            columns=INPUT_COLUMNS,
        ):
            output_rows = []

            for row in batch.to_pylist():
                processed += 1

                line_number = row["line_number"]
                split = get_split(
                    line_number,
                    train_end,
                    validation_end,
                )

                # Do not pass labels into the matcher.
                model_input = build_template_input(row)
                cluster = miner.match(model_input)

                if cluster is None:
                    event_id = UNKNOWN_EVENT_ID
                    template = UNKNOWN_TEMPLATE
                    status = "unknown"
                else:
                    event_id = int(cluster.cluster_id)
                    template = cluster.get_template()
                    status = "matched"

                    matched_event_counts[event_id] += 1
                    matched_event_templates[event_id] = template

                section = per_split[split]
                section["records"] += 1
                section[status] += 1
                section["event_ids"].add(event_id)

                label = row["label"]
                labels_by_split[split][label] += 1

                if label == "-":
                    section["normal_label_records"] += 1
                else:
                    section["alert_label_records"] += 1

                output_rows.append({
                    "line_number": line_number,
                    "label": label,
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
                    "raw_line": row["raw_line"],
                    "window_id": (line_number - 1) // 100,
                    "split": split,
                    "frozen_event_id": event_id,
                    "frozen_template": template,
                    "template_match_status": status,
                })

            if output_rows:
                table = pa.Table.from_pylist(
                    output_rows,
                    schema=SCHEMA,
                )
                writer.write_table(table)

            if processed % PROGRESS_INTERVAL == 0:
                print(
                    f"Processed {processed:,} rows | "
                    f"unknown={sum(s['unknown'] for s in per_split.values()):,}"
                )

        writer.close()
        writer = None

        # Publish the dataset only after all input records are processed.
        os.replace(TEMP_PATH, OUTPUT_PATH)

    except Exception:
        if writer is not None:
            writer.close()

        print(
            "\nERROR: Matching did not complete. A partial file may remain. "
            "Inspect it before retrying."
        )
        raise

    total_matched = sum(section["matched"] for section in per_split.values())
    total_unknown = sum(section["unknown"] for section in per_split.values())

    split_summary = {}

    for name, section in per_split.items():
        records = section["records"]

        split_summary[name] = {
            "records": records,
            "matched": section["matched"],
            "unknown": section["unknown"],
            "match_percentage": round(
                section["matched"] * 100 / records, 4
            ) if records else 0.0,
            "unknown_percentage": round(
                section["unknown"] * 100 / records, 4
            ) if records else 0.0,
            "normal_label_records": section["normal_label_records"],
            "alert_label_records": section["alert_label_records"],
            "unique_event_ids_including_unknown": len(section["event_ids"]),
            "label_counts": dict(labels_by_split[name].most_common()),
        }

    top_templates = [
        {
            "event_id": event_id,
            "matched_records": count,
            "template": matched_event_templates[event_id],
        }
        for event_id, count in matched_event_counts.most_common(20)
    ]

    report = {
        "dataset": "LogHub BGL",
        "matching_method": "Frozen training-only Drain3 match()",
        "loaded_training_templates": loaded_template_count,
        "similarity_threshold": 0.70,
        "template_updates_during_matching": False,
        "labels_used_as_matcher_input": False,
        "total_processed": processed,
        "total_matched": total_matched,
        "total_unknown": total_unknown,
        "total_match_percentage": round(
            total_matched * 100 / processed, 4
        ) if processed else 0.0,
        "total_unknown_percentage": round(
            total_unknown * 100 / processed, 4
        ) if processed else 0.0,
        "split_summary": split_summary,
        "top_matched_templates": top_templates,
        "output_file": str(OUTPUT_PATH.relative_to(ROOT)),
    }

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)

    with REPORT_PATH.open("w", encoding="utf-8") as file:
        json.dump(report, file, indent=2, ensure_ascii=False)

    print("\n========== MATCHING SUMMARY ==========")
    print(f"Processed records: {processed:,}")
    print(f"Matched records: {total_matched:,}")
    print(f"Unknown records: {total_unknown:,}")

    for name, section in split_summary.items():
        print(f"\n{name.upper()}")
        print(f"  Records: {section['records']:,}")
        print(f"  Matched: {section['matched']:,}")
        print(f"  Unknown: {section['unknown']:,}")
        print(f"  Unknown percentage: {section['unknown_percentage']:.4f}%")
        print(f"  Alert-labelled records: {section['alert_label_records']:,}")

    print(f"\nOutput dataset: {OUTPUT_PATH}")
    print(f"Report: {REPORT_PATH}")
    print("\nSUCCESS: Frozen template matching completed.")


if __name__ == "__main__":
    main()
