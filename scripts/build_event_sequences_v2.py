
"""Build event sequences with separate PAD and UNK tokens."""

import json
import os
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]

INPUT_PATH = ROOT / "data" / "processed" / "bgl_frozen_events.parquet"

SEQUENCE_PATH = ROOT / "data" / "processed" / "bgl_event_sequences_v2.npz"
TEMP_PATH = ROOT / "data" / "processed" / "bgl_event_sequences_v2.partial.npz"

VOCAB_PATH = ROOT / "artifacts" / "event_token_vocabulary_v2.json"
REPORT_PATH = ROOT / "reports" / "event_sequence_summary_v2.json"

WINDOW_SIZE = 100
BATCH_SIZE = 100_000

PAD_TOKEN = 0
UNKNOWN_TOKEN = 1

SPLIT_CODES = {
    "train": 0,
    "validation": 1,
    "test": 2,
}


def main():
    if not INPUT_PATH.is_file():
        raise FileNotFoundError(INPUT_PATH)

    outputs = [SEQUENCE_PATH, TEMP_PATH, VOCAB_PATH, REPORT_PATH]
    existing = [str(path) for path in outputs if path.exists()]

    if existing:
        raise SystemExit(
            "Version-2 output already exists. Inspect it before rerunning:\n"
            + "\n".join(existing)
        )

    parquet = pq.ParquetFile(INPUT_PATH)
    total_rows = parquet.metadata.num_rows
    total_windows = (total_rows + WINDOW_SIZE - 1) // WINDOW_SIZE

    # Learn the token vocabulary from training records only.
    training_event_ids = set()

    print("Building vocabulary from training records only...")

    for batch in parquet.iter_batches(
        batch_size=BATCH_SIZE,
        columns=["split", "frozen_event_id", "template_match_status"],
    ):
        splits = batch.column("split").to_pylist()
        event_ids = batch.column("frozen_event_id").to_pylist()
        statuses = batch.column("template_match_status").to_pylist()

        for split, event_id, status in zip(splits, event_ids, statuses):
            if (
                split == "train"
                and status == "matched"
                and event_id is not None
                and int(event_id) >= 0
            ):
                training_event_ids.add(int(event_id))

    if not training_event_ids:
        raise RuntimeError("No known training event IDs were found.")

    # Token 0 is PAD; token 1 is UNK; known event tokens start at 2.
    event_to_token = {
        event_id: token
        for token, event_id in enumerate(
            sorted(training_event_ids),
            start=2,
        )
    }

    vocabulary_size = len(event_to_token) + 2

    sequences = np.zeros(
        (total_windows, WINDOW_SIZE),
        dtype=np.int16,
    )
    lengths = np.zeros(total_windows, dtype=np.int16)
    targets = np.zeros(total_windows, dtype=np.uint8)
    split_codes = np.full(total_windows, 255, dtype=np.uint8)

    window_ids = np.arange(total_windows, dtype=np.int32)
    start_lines = np.zeros(total_windows, dtype=np.int64)
    end_lines = np.zeros(total_windows, dtype=np.int64)
    start_timestamps = np.zeros(total_windows, dtype=np.int64)
    end_timestamps = np.zeros(total_windows, dtype=np.int64)

    record_counts = {name: 0 for name in SPLIT_CODES}
    unknown_counts = {name: 0 for name in SPLIT_CODES}
    unknown_window_ids = {name: set() for name in SPLIT_CODES}

    input_rows_seen = 0
    previous_line = 0

    print(f"Known templates: {len(event_to_token):,}")
    print(f"Vocabulary size, including PAD and UNK: {vocabulary_size:,}")
    print(f"Expected records: {total_rows:,}")
    print(f"Expected windows: {total_windows:,}\n")

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
            input_rows_seen += 1

            line_number = int(row["line_number"])
            window_id = int(row["window_id"])
            split = row["split"]

            if line_number != previous_line + 1:
                raise RuntimeError(
                    f"Non-contiguous source order at line {line_number:,}"
                )
            previous_line = line_number

            expected_window = (line_number - 1) // WINDOW_SIZE
            if window_id != expected_window:
                raise RuntimeError(
                    f"Unexpected window ID at line {line_number:,}"
                )

            if split not in SPLIT_CODES:
                raise ValueError(f"Invalid split: {split!r}")

            position = int(lengths[window_id])

            if position >= WINDOW_SIZE:
                raise RuntimeError(f"Window {window_id} is already full.")

            code = SPLIT_CODES[split]

            if split_codes[window_id] == 255:
                split_codes[window_id] = code
                start_lines[window_id] = line_number
                start_timestamps[window_id] = int(row["timestamp"])
            elif int(split_codes[window_id]) != code:
                raise RuntimeError(
                    f"Window {window_id} crosses split boundaries."
                )

            original_id = row["frozen_event_id"]

            if (
                row["template_match_status"] == "matched"
                and original_id is not None
                and int(original_id) in event_to_token
            ):
                token = event_to_token[int(original_id)]
            else:
                token = UNKNOWN_TOKEN
                unknown_counts[split] += 1
                unknown_window_ids[split].add(window_id)

            sequences[window_id, position] = token
            lengths[window_id] += 1

            end_lines[window_id] = line_number
            end_timestamps[window_id] = int(row["timestamp"])
            record_counts[split] += 1

            # Labels are targets only; they never enter the sequence.
            if row["label"] != "-":
                targets[window_id] = 1

            if input_rows_seen % 500_000 == 0:
                print(
                    f"Processed {input_rows_seen:,} records | "
                    f"unknown records={sum(unknown_counts.values()):,}"
                )

    if input_rows_seen != total_rows:
        raise RuntimeError(
            f"Expected {total_rows:,} records but read {input_rows_seen:,}."
        )

    if np.any(lengths == 0):
        raise RuntimeError("Empty sequences were found.")

    if np.any(split_codes == 255):
        raise RuntimeError("Some windows have no split assignment.")

    # Persist the sequence arrays to a new artifact.
    SEQUENCE_PATH.parent.mkdir(parents=True, exist_ok=True)
    VOCAB_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)

    with TEMP_PATH.open("wb") as file:
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

    os.replace(TEMP_PATH, SEQUENCE_PATH)

    vocabulary = {
        "padding_token": PAD_TOKEN,
        "unknown_token": UNKNOWN_TOKEN,
        "known_template_count": len(event_to_token),
        "vocabulary_size": vocabulary_size,
        "token_to_original_event_id": {
            str(token): event_id
            for event_id, token in event_to_token.items()
        },
        "original_event_id_to_token": {
            str(event_id): token
            for event_id, token in event_to_token.items()
        },
        "source": "Frozen Drain3 training-only vocabulary",
    }

    with VOCAB_PATH.open("w", encoding="utf-8") as file:
        json.dump(vocabulary, file, indent=2)

    split_summary = {}

    for split, code in SPLIT_CODES.items():
        indices = np.flatnonzero(split_codes == code)
        split_records = record_counts[split]
        anomalous_windows = int(targets[indices].sum())

        split_summary[split] = {
            "windows": len(indices),
            "records": split_records,
            "normal_windows": int(len(indices) - anomalous_windows),
            "anomalous_windows": anomalous_windows,
            "unknown_records": unknown_counts[split],
            "unknown_record_percentage": round(
                unknown_counts[split] * 100 / split_records,
                4,
            ) if split_records else 0.0,
            "windows_with_unknown_events": len(
                unknown_window_ids[split]
            ),
        }

    report = {
        "dataset": "LogHub BGL",
        "records": total_rows,
        "windows": total_windows,
        "window_size": WINDOW_SIZE,
        "known_training_templates": len(event_to_token),
        "vocabulary_size": vocabulary_size,
        "padding_token": PAD_TOKEN,
        "unknown_token": UNKNOWN_TOKEN,
        "labels_used_as_sequence_inputs": False,
        "labels_used_only_as_window_targets": True,
        "final_sequence_length": int(lengths[-1]),
        "split_statistics": split_summary,
        "sequence_file": str(SEQUENCE_PATH.relative_to(ROOT)),
        "vocabulary_file": str(VOCAB_PATH.relative_to(ROOT)),
    }

    with REPORT_PATH.open("w", encoding="utf-8") as file:
        json.dump(report, file, indent=2)

    print("\n========== EVENT SEQUENCE V2 SUMMARY ==========")
    print(f"Records processed: {input_rows_seen:,}")
    print(f"Sequences: {total_windows:,}")
    print(f"Vocabulary size: {vocabulary_size:,}")
    print(f"Padding token: {PAD_TOKEN}")
    print(f"Unknown token: {UNKNOWN_TOKEN}")
    print(f"Final sequence length: {lengths[-1]}")

    for split, stats in split_summary.items():
        print(f"\n{split.upper()}")
        print(f"  Windows: {stats['windows']:,}")
        print(f"  Anomalous windows: {stats['anomalous_windows']:,}")
        print(f"  Unknown records: {stats['unknown_records']:,}")
        print(
            "  Unknown percentage: "
            f"{stats['unknown_record_percentage']:.4f}%"
        )

    print(f"\nSequences: {SEQUENCE_PATH}")
    print(f"Vocabulary: {VOCAB_PATH}")
    print(f"Report: {REPORT_PATH}")
    print("\nSUCCESS: Corrected event sequences created.")


if __name__ == "__main__":
    main()
