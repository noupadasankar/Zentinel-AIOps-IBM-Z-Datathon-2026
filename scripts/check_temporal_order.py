"""Verify source-order timestamps before chronological model evaluation."""

import json
import re
from pathlib import Path

import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]
INPUT_PATH = ROOT / "data" / "processed" / "bgl_parsed.parquet"
REPORT_PATH = ROOT / "reports" / "temporal_order_report.json"

EXPECTED_ROWS = 4_747_963
BATCH_SIZE = 100_000

EVENT_TIME_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}-\d{2}\.\d{2}\.\d{2}\.\d{6}$")


def main() -> None:
    if not INPUT_PATH.is_file():
        raise FileNotFoundError(INPUT_PATH)

    parquet = pq.ParquetFile(INPUT_PATH)

    previous_time = None
    previous_line = None

    total = 0
    decreasing_pairs = 0
    invalid_timestamps = 0
    examples = []

    minimum_time = None
    maximum_time = None

    print(f"Checking event-time order in: {INPUT_PATH}")
    print(f"Expected records: {EXPECTED_ROWS:,}\n")

    for batch in parquet.iter_batches(
        batch_size=BATCH_SIZE,
        columns=["line_number", "event_time"],
    ):
        line_numbers = batch.column("line_number").to_pylist()
        event_times = batch.column("event_time").to_pylist()

        for line_number, event_time in zip(line_numbers, event_times):
            total += 1

            if not isinstance(event_time, str) or not EVENT_TIME_PATTERN.fullmatch(event_time):
                invalid_timestamps += 1

                if len(examples) < 10:
                    examples.append(
                        {
                            "line_number": line_number,
                            "issue": "Invalid event_time format",
                            "event_time": event_time,
                        }
                    )

                continue

            if minimum_time is None or event_time < minimum_time:
                minimum_time = event_time

            if maximum_time is None or event_time > maximum_time:
                maximum_time = event_time

            if previous_time is not None and event_time < previous_time:
                decreasing_pairs += 1

                if len(examples) < 10:
                    examples.append(
                        {
                            "line_number": line_number,
                            "issue": "Event time moved backwards",
                            "previous_line_number": previous_line,
                            "previous_event_time": previous_time,
                            "current_event_time": event_time,
                        }
                    )

            previous_time = event_time
            previous_line = line_number

        if total % 500_000 == 0:
            print(f"Checked {total:,} rows | backwards transitions={decreasing_pairs:,}")

    report = {
        "expected_rows": EXPECTED_ROWS,
        "checked_rows": total,
        "row_count_matches": total == EXPECTED_ROWS,
        "decreasing_timestamp_pairs": decreasing_pairs,
        "invalid_timestamps": invalid_timestamps,
        "minimum_event_time": minimum_time,
        "maximum_event_time": maximum_time,
        "examples": examples,
        "source_order_is_nondecreasing": (total == EXPECTED_ROWS and decreasing_pairs == 0 and invalid_timestamps == 0),
    }

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)

    with REPORT_PATH.open("w", encoding="utf-8") as file:
        json.dump(report, file, indent=2)

    print("\n========== TEMPORAL ORDER REPORT ==========")
    print(f"Checked rows:              {total:,}")
    print(f"Backwards transitions:     {decreasing_pairs:,}")
    print(f"Invalid timestamps:        {invalid_timestamps:,}")
    print(f"Earliest event time:       {minimum_time}")
    print(f"Latest event time:         {maximum_time}")
    print(
        "Source order nondecreasing:",
        report["source_order_is_nondecreasing"],
    )
    print(f"\nDetailed report: {REPORT_PATH}")

    if report["source_order_is_nondecreasing"]:
        print("\nSUCCESS: Source order is temporally nondecreasing.")
    else:
        print("\nWARNING: Review the report before using source order for chronological splits.")


if __name__ == "__main__":
    main()
