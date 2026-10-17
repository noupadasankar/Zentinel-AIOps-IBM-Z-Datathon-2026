"""Plan chronological train, validation, and test splits for BGL."""

import json
import math
from pathlib import Path

import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]
INPUT_PATH = ROOT / "data" / "processed" / "bgl_parsed.parquet"
REPORT_PATH = ROOT / "reports" / "chronological_split_report.json"

WINDOW_SIZE = 100
TRAIN_RATIO = 0.70
VALIDATION_RATIO = 0.15


def main() -> None:
    if not INPUT_PATH.is_file():
        raise FileNotFoundError(f"Dataset not found: {INPUT_PATH}")

    parquet = pq.ParquetFile(INPUT_PATH)
    total_rows = parquet.metadata.num_rows

    total_windows = math.ceil(total_rows / WINDOW_SIZE)
    train_windows = int(total_windows * TRAIN_RATIO)
    validation_windows = int(total_windows * VALIDATION_RATIO)
    test_windows = total_windows - train_windows - validation_windows

    train_end_line = train_windows * WINDOW_SIZE
    validation_end_line = (train_windows + validation_windows) * WINDOW_SIZE

    boundaries = [
        ("train", 0, train_windows),
        (
            "validation",
            train_windows,
            train_windows + validation_windows,
        ),
        ("test", train_windows + validation_windows, total_windows),
    ]

    def split_for_window(window_id: int) -> str:
        if window_id < train_windows:
            return "train"
        if window_id < train_windows + validation_windows:
            return "validation"
        return "test"

    stats = {
        name: {
            "windows": 0,
            "log_lines": 0,
            "normal_lines": 0,
            "alert_lines": 0,
            "normal_windows": 0,
            "alert_windows": 0,
        }
        for name, _, _ in boundaries
    }

    row_index = 0
    current_window_id = None
    current_split = None
    current_window_has_alert = False

    def finish_window() -> None:
        if current_window_id is None:
            return

        section = stats[current_split]
        section["windows"] += 1

        if current_window_has_alert:
            section["alert_windows"] += 1
        else:
            section["normal_windows"] += 1

    print("Planning chronological BGL splits...")
    print(f"Input rows: {total_rows:,}")
    print(f"Window size: {WINDOW_SIZE} consecutive log records")
    print(f"Total windows: {total_windows:,}\n")

    # Read only labels in batches; this does not create model features.
    for batch in parquet.iter_batches(
        batch_size=100_000,
        columns=["label"],
    ):
        labels = batch.column(0).to_pylist()

        for label in labels:
            window_id = row_index // WINDOW_SIZE
            split = split_for_window(window_id)

            if window_id != current_window_id:
                finish_window()

                current_window_id = window_id
                current_split = split
                current_window_has_alert = False

            section = stats[split]
            section["log_lines"] += 1

            if label == "-":
                section["normal_lines"] += 1
            else:
                section["alert_lines"] += 1
                current_window_has_alert = True

            row_index += 1

    finish_window()

    if row_index != total_rows:
        raise RuntimeError(f"Read {row_index:,} rows but expected {total_rows:,}")

    for name, section in stats.items():
        section["alert_line_percentage"] = (
            round(
                section["alert_lines"] * 100 / section["log_lines"],
                4,
            )
            if section["log_lines"]
            else 0.0
        )

        section["alert_window_percentage"] = (
            round(
                section["alert_windows"] * 100 / section["windows"],
                4,
            )
            if section["windows"]
            else 0.0
        )

    split_report = {
        "dataset": "LogHub BGL",
        "split_method": "Chronological source order",
        "window_size_records": WINDOW_SIZE,
        "total_rows": total_rows,
        "total_windows": total_windows,
        "ratios": {
            "train": TRAIN_RATIO,
            "validation": VALIDATION_RATIO,
            "test": 1.0 - TRAIN_RATIO - VALIDATION_RATIO,
        },
        "line_boundaries": {
            "train": {
                "start": 1,
                "end": min(train_end_line, total_rows),
            },
            "validation": {
                "start": train_end_line + 1,
                "end": min(validation_end_line, total_rows),
            },
            "test": {
                "start": validation_end_line + 1,
                "end": total_rows,
            },
        },
        "window_ranges": {
            name: {
                "start_window_zero_based": start,
                "end_window_exclusive": end,
            }
            for name, start, end in boundaries
        },
        "statistics": stats,
        "label_definition": (
            "A window is anomalous if any record in that window has "
            "a label other than '-'. Labels are evaluation targets, "
            "not model input features."
        ),
    }

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)

    with REPORT_PATH.open("w", encoding="utf-8") as file:
        json.dump(split_report, file, indent=2)

    print("========== CHRONOLOGICAL SPLIT REPORT ==========")

    for name, section in stats.items():
        print(f"\n{name.upper()}")
        print(f"  Windows:              {section['windows']:,}")
        print(f"  Log lines:             {section['log_lines']:,}")
        print(f"  Normal-labelled logs:  {section['normal_lines']:,}")
        print(f"  Alert-labelled logs:   {section['alert_lines']:,}")
        print(f"  Alert-labelled lines:  {section['alert_line_percentage']:.4f}%")
        print(f"  Normal windows:        {section['normal_windows']:,}")
        print(f"  Alert windows:         {section['alert_windows']:,}")
        print(f"  Alert windows:         {section['alert_window_percentage']:.4f}%")

    print(f"\nDetailed report: {REPORT_PATH}")

    if sum(s["windows"] for s in stats.values()) != total_windows:
        raise RuntimeError("Split window totals do not match.")

    if sum(s["log_lines"] for s in stats.values()) != total_rows:
        raise RuntimeError("Split row totals do not match.")

    print("\nSUCCESS: Chronological split boundaries validated.")


if __name__ == "__main__":
    main()
