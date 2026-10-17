
"""Investigate unknown Drain3 matches by time, label, and log type."""

import json
from collections import Counter, defaultdict, deque
from pathlib import Path

import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]
INPUT_PATH = ROOT / "data" / "processed" / "bgl_frozen_events.parquet"
REPORT_PATH = ROOT / "reports" / "unknown_event_diagnostics.json"

BATCH_SIZE = 100_000
CHUNK_SIZE = 100_000


def new_stats():
    return {
        "records": 0,
        "unknown": 0,
        "matched": 0,
        "alert_labelled": 0,
        "normal_labelled": 0,
        "unknown_alert_labelled": 0,
        "unknown_normal_labelled": 0,
    }


def add_record(stats, row):
    is_unknown = row["template_match_status"] == "unknown"
    is_alert = row["label"] != "-"

    stats["records"] += 1

    if is_unknown:
        stats["unknown"] += 1
    else:
        stats["matched"] += 1

    if is_alert:
        stats["alert_labelled"] += 1
    else:
        stats["normal_labelled"] += 1

    if is_unknown and is_alert:
        stats["unknown_alert_labelled"] += 1

    if is_unknown and not is_alert:
        stats["unknown_normal_labelled"] += 1


def finalize_stats(stats):
    records = stats["records"]
    unknown = stats["unknown"]

    result = dict(stats)
    result["unknown_percentage"] = round(
        100 * unknown / records, 4
    ) if records else 0.0

    result["unknown_alert_label_percentage"] = round(
        100 * stats["unknown_alert_labelled"] /
        stats["alert_labelled"], 4
    ) if stats["alert_labelled"] else 0.0

    result["unknown_normal_label_percentage"] = round(
        100 * stats["unknown_normal_labelled"] /
        stats["normal_labelled"], 4
    ) if stats["normal_labelled"] else 0.0

    return result


def main():
    if not INPUT_PATH.is_file():
        raise FileNotFoundError(INPUT_PATH)

    parquet = pq.ParquetFile(INPUT_PATH)

    required_columns = [
        "line_number",
        "split",
        "date",
        "label",
        "template_match_status",
        "event_type",
        "component",
        "severity",
        "message",
        "raw_line",
        "frozen_event_id",
        "parse_status",
    ]

    split_stats = defaultdict(new_stats)
    date_stats = defaultdict(new_stats)
    chunk_stats = defaultdict(new_stats)

    unknown_labels = Counter()
    unknown_categories = Counter()
    unknown_ids = Counter()
    unknown_parse_status = Counter()

    first_examples = []
    last_examples = deque(maxlen=10)

    processed = 0

    print(f"Dataset: {INPUT_PATH}")
    print("Diagnosing unknown events in batches...\n")

    for batch in parquet.iter_batches(
        batch_size=BATCH_SIZE,
        columns=required_columns,
    ):
        for row in batch.to_pylist():
            processed += 1

            split = row["split"]
            date = row["date"] or "<missing-date>"
            is_unknown = row["template_match_status"] == "unknown"

            add_record(split_stats[split], row)
            add_record(date_stats[(split, date)], row)

            chunk_id = (row["line_number"] - 1) // CHUNK_SIZE
            add_record(chunk_stats[chunk_id], row)

            if is_unknown:
                unknown_labels[row["label"]] += 1

                category = (
                    row["event_type"] or "<missing>",
                    row["component"] or "<missing>",
                    row["severity"] or "<missing>",
                )
                unknown_categories[category] += 1

                unknown_ids[row["frozen_event_id"]] += 1
                unknown_parse_status[row["parse_status"]] += 1

                example = {
                    "line_number": row["line_number"],
                    "split": split,
                    "date": date,
                    "label": row["label"],
                    "event_type": row["event_type"],
                    "component": row["component"],
                    "severity": row["severity"],
                    "message": (row["message"] or "")[:220],
                    "raw_line": (row["raw_line"] or "")[:300],
                }

                if len(first_examples) < 10:
                    first_examples.append(example)

                last_examples.append(example)

        if processed % 500_000 == 0:
            print(f"Inspected {processed:,} records")

    total_unknown = sum(s["unknown"] for s in split_stats.values())
    total_records = sum(s["records"] for s in split_stats.values())

    split_report = {
        name: finalize_stats(stats)
        for name, stats in split_stats.items()
    }

    # Print date-level diagnostics specifically for validation and test.
    date_report = []

    for (split, date), stats in sorted(date_stats.items()):
        if split in {"validation", "test"}:
            item = finalize_stats(stats)
            item["split"] = split
            item["date"] = date
            date_report.append(item)

    # Inspect contiguous source blocks to identify when unknowns rise.
    chunk_report = []

    for chunk_id, stats in sorted(chunk_stats.items()):
        item = finalize_stats(stats)
        item["chunk_id"] = chunk_id
        item["start_line"] = chunk_id * CHUNK_SIZE + 1
        item["end_line"] = min(
            (chunk_id + 1) * CHUNK_SIZE,
            total_records,
        )
        chunk_report.append(item)

    top_categories = [
        {
            "event_type": category[0],
            "component": category[1],
            "severity": category[2],
            "unknown_count": count,
        }
        for category, count in unknown_categories.most_common(20)
    ]

    top_unknown_labels = [
        {"label": label, "unknown_count": count}
        for label, count in unknown_labels.most_common(20)
    ]

    report = {
        "records_inspected": processed,
        "total_unknown": total_unknown,
        "unknown_percentage": round(
            100 * total_unknown / total_records, 4
        ) if total_records else 0.0,
        "split_statistics": split_report,
        "validation_and_test_by_date": date_report,
        "contiguous_source_chunks": chunk_report,
        "unknown_counts_by_label": top_unknown_labels,
        "top_unknown_metadata_categories": top_categories,
        "unknown_event_ids": dict(unknown_ids.most_common(20)),
        "unknown_parse_status_counts": dict(unknown_parse_status),
        "first_unknown_examples": first_examples,
        "last_unknown_examples": list(last_examples),
    }

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)

    with REPORT_PATH.open("w", encoding="utf-8") as file:
        json.dump(report, file, indent=2, ensure_ascii=False)

    print("\n========== UNKNOWN EVENT DIAGNOSTICS ==========")

    for split, stats in split_report.items():
        print(f"\n{split.upper()}")
        print(f"  Records: {stats['records']:,}")
        print(f"  Unknown: {stats['unknown']:,}")
        print(f"  Unknown rate: {stats['unknown_percentage']:.4f}%")
        print(
            "  Unknown among alert-labelled records: "
            f"{stats['unknown_alert_label_percentage']:.4f}%"
        )
        print(
            "  Unknown among normal-labelled records: "
            f"{stats['unknown_normal_label_percentage']:.4f}%"
        )

    print("\nContiguous chunks with unknown rate >= 5%:")

    high_unknown_chunks = [
        item for item in chunk_report
        if item["unknown_percentage"] >= 5.0
    ]

    for item in high_unknown_chunks[:30]:
        print(
            f"  Lines {item['start_line']:,}-"
            f"{item['end_line']:,} | "
            f"{item['split'] if 'split' in item else ''} | "
            f"{item['unknown_percentage']:.2f}% unknown | "
            f"{item['unknown']:,}/{item['records']:,}"
        )

    print("\nTop unknown labels:")
    for item in top_unknown_labels[:10]:
        print(f"  {item['label']}: {item['unknown_count']:,}")

    print("\nTop unknown metadata categories:")
    for item in top_categories[:10]:
        print(
            f"  {item['event_type']} / {item['component']} / "
            f"{item['severity']}: {item['unknown_count']:,}"
        )

    print("\nFirst unknown raw log examples:")

    for item in first_examples[:5]:
        print(
            f"\n  Line {item['line_number']:,} | "
            f"{item['date']} | label={item['label']}"
        )
        print(f"  {item['raw_line']}")

    print(f"\nDetailed report saved to: {REPORT_PATH}")
    print("\nSUCCESS: Unknown-event diagnosis completed.")


if __name__ == "__main__":
    main()
