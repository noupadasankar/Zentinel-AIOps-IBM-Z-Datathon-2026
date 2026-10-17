
"""Build integer event sequences for LSTM anomaly detection."""

from pathlib import Path
import json
import os

import numpy as np
import pyarrow.parquet as pq


ROOT = Path(__file__).resolve().parents[1]

INPUT_PATH = ROOT / "data" / "processed" / "bgl_frozen_events.parquet"
SEQUENCE_PATH = ROOT / "data" / "processed" / "bgl_event_sequences.npz"
TEMP_SEQUENCE_PATH = (
    ROOT / "data" / "processed" / "bgl_event_sequences.partial.npz"
)

VOCAB_PATH = ROOT / "artifacts" / "event_token_vocabulary.json"
REPORT_PATH = ROOT / "reports" / "event_sequence_summary.json"

WINDOW_SIZE = 100
BATCH_SIZE = 100_000

SPLIT_CODES = {
    "train": 0,
    "validation": 1,
    "test": 2,
}


def main() -> None:
    if not INPUT_PATH.is_file():
        raise FileNotFoundError(INPUT_PATH)

    existing = [
        path
        for path in (
            SEQUENCE_PATH,
            TEMP_SEQUENCE_PATH,
            VOCAB_PATH,
            REPORT_PATH,
        )
        if path.exists()
    ]

    if existing:
        raise SystemExit(
            "Sequence output files already exist. Inspect them before "
            "rerunning to avoid overwriting previous work:\n"
            + "\n".join(map(str, existing))
        )

    parquet = pq.ParquetFile(INPUT_PATH)
    total_rows = parquet.metadata.num_rows
    total_windows = (total_rows + WINDOW_SIZE - 1) // WINDOW_SIZE

    # First pass: derive the vocabulary using training events only.
    training_event_ids = set()

    print("========== TRAINING EVENT VOCABULARY ==========")

    for batch in parquet.iter_batches(
        batch_size=BATCH_SIZE,
        columns=["split", "frozen_event_id"],
    ):
        for split, event_id in zip(
            batch.column("split").to_pylist(),
            batch.column("frozen_event_id").to_pylist(),
        ):
            if (
                split == "train"
                and event_id is not None
                and int(event_id) >= 0
            ):
                training_event_ids.add(int(event_id))

    if not training_event_ids:
        raise RuntimeError("No known training event IDs were found.")

    # Token 0 is reserved for unknown events. Known training IDs receive
    # stable, consecutive token IDs starting at 1.
    event_to_token = {
        event_id: token
        for token, event_id in enumerate(
            sorted(training_event_ids),
            start=1,
        )
    }

    vocabulary_size = len(event_to_token)

    print(f"Known training templates: {vocabulary_size:,}")
    print(f"Total log records: {total_rows:,}")
    print(f"Expected sequences: {total_windows:,}")

    sequences = np.zeros(
        (total_windows, WINDOW_SIZE),
        dtype=np.int16,
    )
    lengths = np.zeros(total_windows, dtype=np.int16)
    targets = np.zeros(total_windows, dtype=np.uint8)

    split_codes = np.full(
        total_windows,
        255,
        dtype=np.uint8,
    )

    window_ids = np.arange(total_windows, dtype=np.int32)
    start_lines = np.zeros(total_windows, dtype=np.int64)
    end_lines = np.zeros(total_windows, dtype=np.int64)
    start_timestamps = np.zeros(total_windows, dtype=np.int64)
    end_timestamps = np.zeros(total_windows, dtype=np.int64)

    unknown_counts = {
        name: 0 for name in SPLIT_CODES
    }
    record_counts = {
        name: 0 for name in SPLIT_CODES
    }
    unknown_windows = {
        name: set() for name in SPLIT_CODES
    }
    target_windows = {
        name: 0 for name in SPLIT_CODES
    }

    processed = 0
    previous_line = 0

    print("\nBuilding event sequences...")

    columns = [
        "line_number",
        "window_id",
        "split",
        "timestamp",
        "label",
        "frozen_event_id",
        "template_match_status",
    ]

    for batch in parquet.iter_batches(
        batch_size=BATCH_SIZE,
        columns=columns,
    ):
        for row in batch.to_pylist():
            processed += 1

            line_number = int(row["line_number"])
            window_id = int(row["window_id"])
            split = row["split"]

            if line_number != previous_line + 1:
                raise RuntimeError(
                    f"Source order gap at line {line_number:,}"
                )
            previous_line = line_number

            expected_window = (line_number - 1) // WINDOW_SIZE

            if window_id != expected_window:
                raise RuntimeError(
                    f"Window mismatch at line {line_number:,}: "
                    f"stored={window_id}, expected={expected_window}"
                )

            if split not in SPLIT_CODES:
                raise ValueError(f"Unknown split: {split!r}")

            if not 0 <= window_id < total_windows:
                raise ValueError(f"Invalid window ID: {window_id}")

            split_code = SPLIT_CODES[split]
            existing_split = int(split_codes[window_id])

            if existing_split == 255:
                split_codes[window_id] = split_code
                start_lines[window_id] = line_number
                start_timestamps[window_id] = int(row["timestamp"])

            elif existing_split != split_code:
                raise RuntimeError(
                    f"Window {window_id} crosses split boundaries."
                )

            position = int(lengths[window_id])

            if position >= WINDOW_SIZE:
                raise RuntimeError(
                    f"Window {window_id} exceeds {WINDOW_SIZE} records."
                )

            original_id = row["frozen_event_id"]

            if (
                row["template_match_status"] == "matched"
                and original_id is not None
                and int(original_id) in event_to_token
            ):
                token = event_to_token[int(original_id)]
            else:
                token = 0
                unknown_counts[split] += 1
                unknown_windows[split].add(window_id)

            sequences[window_id, position] = token
            lengths[window_id] += 1

            record_counts[split] += 1
            end_lines[window_id] = line_number
            end_timestamps[window_id] = int(row["timestamp"])

            # Labels are used solely for the evaluation target.
            if row["label"] != "-":
                targets[window_id] = 1

            if processed % 500_000 == 0:
                print(
                    f"Processed {processed:,} records | "
                    f"completed sequences so far: "
                    f"{int(np.count_nonzero(lengths)):,}"
                )

    if processed != total_rows:
        raise RuntimeError(
            f"Expected {total_rows:,} records; processed {processed:,}"
        )

    if np.any(lengths == 0):
        raise RuntimeError("Some output windows are empty.")

    if np.any(split_codes == 255):
        raise RuntimeError("Some windows have no split assignment.")

    for name, code in SPLIT_CODES.items():
        indices = np.flatnonzero(split_codes == code)

        target_windows[name] = int(targets[indices].sum())

    # Save the sequence matrix atomically.
    SEQUENCE_PATH.parent.mkdir(parents=True, exist_ok=True)
    VOCAB_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)

    with TEMP_SEQUENCE_PATH.open("wb") as file:
        np.savez_compressed(
            file,
            sequences=sequences,
            lengths=lengths,
            targets=targets,
            split_codes=split_codes,
            window_ids=window_ids,
            start_lines=start_lines,
            end_lines=end_lines,
            start_timestamps=start_timestamps,
            end_timestamps=end_timestamps,
        )

    os.replace(TEMP_SEQUENCE_PATH, SEQUENCE_PATH)

    vocabulary = {
        "unknown_token": 0,
        "known_template_count": vocabulary_size,
        "token_to_original_event_id": {
            str(token): original_id
            for original_id, token in event_to_token.items()
        },
        "original_event_id_to_token": {
            str(original_id): token
            for original_id, token in event_to_token.items()
        },
        "source": "Frozen Drain3 training-only vocabulary",
    }

    with VOCAB_PATH.open("w", encoding="utf-8") as file:
        json.dump(vocabulary, file, indent=2)

    split_summary = {}

    for name, code in SPLIT_CODES.items():
        indices = np.flatnonzero(split_codes == code)
        records = record_counts[name]

        split_summary[name] = {
            "windows": int(len(indices)),
            "records": int(records),
            "anomalous_windows": int(targets[indices].sum()),
            "normal_windows": int(len(indices) - targets[indices].sum()),
            "unknown_records": int(unknown_counts[name]),
            "unknown_record_percentage": round(
                unknown_counts[name] * 100 / records,
                4,
            ) if records else 0.0,
            "windows_with_unknown_events": int(
                len(unknown_windows[name])
            ),
        }

    report = {
        "dataset": "LogHub BGL",
        "window_size": WINDOW_SIZE,
        "total_records": total_rows,
        "total_windows": total_windows,
        "processed_records": processed,
        "known_training_templates": vocabulary_size,
        "unknown_token": 0,
        "padding_token": 0,
        "padding_distinguished_by_length": True,
        "labels_used_as_sequence_inputs": False,
        "labels_used_only_as_window_targets": True,
        "final_window_length": int(lengths[-1]),
        "split_statistics": split_summary,
        "sequence_file": str(SEQUENCE_PATH.relative_to(ROOT)),
        "vocabulary_file": str(VOCAB_PATH.relative_to(ROOT)),
    }

    with REPORT_PATH.open("w", encoding="utf-8") as file:
        json.dump(report, file, indent=2)

    print("\n========== EVENT SEQUENCE SUMMARY ==========")
    print(f"Records processed: {processed:,}")
    print(f"Windows created: {total_windows:,}")
    print(f"Known training templates: {vocabulary_size:,}")
    print(f"Final window length: {lengths[-1]}")

    for name, stats in split_summary.items():
        print(f"\n{name.upper()}")
        print(f"  Windows: {stats['windows']:,}")
        print(f"  Anomalous windows: {stats['anomalous_windows']:,}")
        print(f"  Unknown records: {stats['unknown_records']:,}")
        print(
            "  Unknown record percentage: "
            f"{stats['unknown_record_percentage']:.4f}%"
        )

    print(f"\nSequences: {SEQUENCE_PATH}")
    print(f"Vocabulary: {VOCAB_PATH}")
    print(f"Report: {REPORT_PATH}")
    print("\nSUCCESS: Event sequence generation completed.")


if __name__ == "__main__":
    main()

