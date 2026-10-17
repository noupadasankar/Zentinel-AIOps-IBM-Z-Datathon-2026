"""Compare training matches with unknown messages in the later test period."""

from pathlib import Path

import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]
INPUT_PATH = ROOT / "data" / "processed" / "bgl_frozen_events.parquet"

LATE_START_LINE = 4_200_000
SAMPLE_COUNT = 8

COLUMNS = [
    "line_number",
    "split",
    "date",
    "label",
    "event_type",
    "component",
    "severity",
    "message",
    "frozen_event_id",
    "frozen_template",
    "template_match_status",
]


def show_record(record):
    print(
        f"\nLine: {record['line_number']:,} | "
        f"Date: {record['date']} | "
        f"Split: {record['split']} | "
        f"Label: {record['label']}"
    )
    print(f"Metadata: {record['event_type']} / {record['component']} / {record['severity']}")
    print(f"Match status: {record['template_match_status']}")
    print(f"Event ID: {record['frozen_event_id']}")
    print(f"Message: {(record['message'] or '')[:240]}")
    print(f"Template: {record['frozen_template']}")


def main():
    if not INPUT_PATH.is_file():
        raise FileNotFoundError(INPUT_PATH)

    parquet = pq.ParquetFile(INPUT_PATH)

    matched_training = []
    unknown_late_ras_kernel_info = []
    unknown_late_any = []
    matched_late_examples = []

    counts = {
        "late_records": 0,
        "late_unknown": 0,
        "late_ras_kernel_info_unknown": 0,
    }

    print(f"Inspecting: {INPUT_PATH}")
    print("Reading selected columns in batches...\n")

    for batch in parquet.iter_batches(
        batch_size=100_000,
        columns=COLUMNS,
    ):
        for record in batch.to_pylist():
            line_number = record["line_number"]
            status = record["template_match_status"]

            is_ras_kernel_info = (
                record["event_type"] == "RAS" and record["component"] == "KERNEL" and record["severity"] == "INFO"
            )

            if (
                record["split"] == "train"
                and status == "matched"
                and is_ras_kernel_info
                and len(matched_training) < SAMPLE_COUNT
            ):
                matched_training.append(record)

            if line_number >= LATE_START_LINE:
                counts["late_records"] += 1

                if status == "unknown":
                    counts["late_unknown"] += 1

                    if len(unknown_late_any) < SAMPLE_COUNT:
                        unknown_late_any.append(record)

                    if is_ras_kernel_info:
                        counts["late_ras_kernel_info_unknown"] += 1

                        if len(unknown_late_ras_kernel_info) < SAMPLE_COUNT:
                            unknown_late_ras_kernel_info.append(record)

                elif len(matched_late_examples) < SAMPLE_COUNT:
                    matched_late_examples.append(record)

    print("========== EARLIER MATCHED RAS/KERNEL/INFO ==========")
    for record in matched_training:
        show_record(record)

    print("\n========== LATE UNKNOWN RAS/KERNEL/INFO ==========")
    for record in unknown_late_ras_kernel_info:
        show_record(record)

    print("\n========== LATE UNKNOWN RECORDS, ALL TYPES ==========")
    for record in unknown_late_any:
        show_record(record)

    print("\n========== LATE MATCHED RECORDS ==========")
    for record in matched_late_examples:
        show_record(record)

    print("\n========== SUMMARY ==========")
    for name, value in counts.items():
        print(f"{name}: {value:,}")

    print("\nInspection complete. No data or model files were modified.")


if __name__ == "__main__":
    main()
