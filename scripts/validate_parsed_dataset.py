
"""Validate full BGL Parquet output against the original log file."""

import json
from collections import Counter
from pathlib import Path

import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]
SOURCE_PATH = ROOT / "data" / "raw" / "BGL.log"
PARQUET_PATH = ROOT / "data" / "processed" / "bgl_parsed.parquet"
REPORT_PATH = ROOT / "reports" / "parsed_integrity_report.json"

EXPECTED_ROWS = 4_747_963
BATCH_SIZE = 10_000


def main() -> None:
    if not SOURCE_PATH.is_file():
        raise FileNotFoundError(SOURCE_PATH)

    if not PARQUET_PATH.is_file():
        raise FileNotFoundError(PARQUET_PATH)

    parquet = pq.ParquetFile(PARQUET_PATH)
    parquet_rows = parquet.metadata.num_rows

    columns = [
        "line_number",
        "label",
        "timestamp",
        "raw_line",
        "event_id",
        "event_template",
        "parse_status",
        "message",
    ]

    total = 0
    raw_mismatches = 0
    line_number_mismatches = 0
    label_mismatches = 0
    timestamp_mismatches = 0
    missing_required_values = 0
    source_ended_early = 0
    report_samples = []

    label_counts = Counter()
    parse_status_counts = Counter()
    template_counts = Counter()
    empty_messages = 0
    min_timestamp = None
    max_timestamp = None

    print("========== PARQUET INTEGRITY VALIDATION ==========")
    print(f"Original file: {SOURCE_PATH}")
    print(f"Parquet file:  {PARQUET_PATH}")
    print(f"Parquet rows:  {parquet_rows:,}")
    print("\nComparing original and processed records...\n")

    with SOURCE_PATH.open(
        "r",
        encoding="utf-8",
        errors="replace",
    ) as source:
        for batch in parquet.iter_batches(
            batch_size=BATCH_SIZE,
            columns=columns,
        ):
            for row in batch.to_pylist():
                total += 1
                source_text = source.readline()

                if source_text == "":
                    source_ended_early += 1
                    continue

                original = source_text.rstrip("\r\n")
                saved = row["raw_line"]

                if row["line_number"] != total:
                    line_number_mismatches += 1

                if saved != original:
                    raw_mismatches += 1

                    if len(report_samples) < 10:
                        report_samples.append({
                            "row": total,
                            "issue": "Raw line mismatch",
                            "original": original[:200],
                            "saved": (saved or "")[:200],
                        })

                tokens = original.split(maxsplit=2)

                if len(tokens) < 2:
                    label_mismatches += 1
                    timestamp_mismatches += 1
                else:
                    if row["label"] != tokens[0]:
                        label_mismatches += 1

                    try:
                        original_timestamp = int(tokens[1])
                    except ValueError:
                        timestamp_mismatches += 1
                    else:
                        if row["timestamp"] != original_timestamp:
                            timestamp_mismatches += 1

                required = (
                    row["line_number"] is not None
                    and row["label"] is not None
                    and row["timestamp"] is not None
                    and row["raw_line"] is not None
                    and row["event_id"] is not None
                    and row["event_template"] is not None
                    and row["parse_status"] in {"standard", "fallback"}
                )

                if not required:
                    missing_required_values += 1

                label_counts[row["label"]] += 1
                parse_status_counts[row["parse_status"]] += 1
                template_counts[row["event_id"]] += 1

                if row["message"] == "":
                    empty_messages += 1

                timestamp = row["timestamp"]

                if timestamp is not None:
                    min_timestamp = (
                        timestamp
                        if min_timestamp is None
                        else min(min_timestamp, timestamp)
                    )
                    max_timestamp = (
                        timestamp
                        if max_timestamp is None
                        else max(max_timestamp, timestamp)
                    )

                if total % 500_000 == 0:
                    print(
                        f"Checked {total:,} records | "
                        f"raw mismatches={raw_mismatches:,}"
                    )

        # Check that the original file has no unprocessed trailing lines.
        extra_source_line = source.readline() != ""

    normal_count = label_counts.get("-", 0)
    alert_count = total - normal_count

    report = {
        "expected_rows": EXPECTED_ROWS,
        "parquet_rows": parquet_rows,
        "validated_rows": total,
        "row_count_matches_expected": (
            parquet_rows == EXPECTED_ROWS == total
        ),
        "source_has_extra_lines": extra_source_line,
        "source_ended_early": source_ended_early,
        "raw_line_mismatches": raw_mismatches,
        "line_number_mismatches": line_number_mismatches,
        "label_mismatches": label_mismatches,
        "timestamp_mismatches": timestamp_mismatches,
        "missing_required_values": missing_required_values,
        "normal_label_lines": normal_count,
        "alert_label_lines": alert_count,
        "empty_message_lines": empty_messages,
        "parse_status_counts": dict(parse_status_counts),
        "unique_event_ids": len(template_counts),
        "timestamp_range": {
            "min": min_timestamp,
            "max": max_timestamp,
        },
        "mismatch_samples": report_samples,
    }

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)

    with REPORT_PATH.open("w", encoding="utf-8") as file:
        json.dump(report, file, indent=2, ensure_ascii=False)

    print("\n========== VALIDATION RESULTS ==========")
    print(f"Validated rows:          {total:,}")
    print(f"Raw-line mismatches:     {raw_mismatches:,}")
    print(f"Line-number mismatches:  {line_number_mismatches:,}")
    print(f"Label mismatches:        {label_mismatches:,}")
    print(f"Timestamp mismatches:    {timestamp_mismatches:,}")
    print(f"Missing required values: {missing_required_values:,}")
    print(f"Normal-labelled:         {normal_count:,}")
    print(f"Alert-labelled:          {alert_count:,}")
    print(f"Empty messages:          {empty_messages:,}")
    print(f"Unique event IDs:        {len(template_counts):,}")
    print(f"Parser statuses:         {dict(parse_status_counts)}")
    print(f"Extra source lines:      {extra_source_line}")

    print(f"\nDetailed report: {REPORT_PATH}")

    passed = (
        report["row_count_matches_expected"]
        and not extra_source_line
        and source_ended_early == 0
        and raw_mismatches == 0
        and line_number_mismatches == 0
        and label_mismatches == 0
        and timestamp_mismatches == 0
        and missing_required_values == 0
    )

    if passed:
        print("\nSUCCESS: Full dataset integrity checks passed.")
    else:
        print("\nWARNING: Review the validation report before model training.")


if __name__ == "__main__":
    main()
