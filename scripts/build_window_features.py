
"""Create fixed-size numerical log-window features without label leakage."""

from collections import Counter, defaultdict
from pathlib import Path
import json
import os
import re

import pyarrow as pa
import pyarrow.parquet as pq


ROOT = Path(__file__).resolve().parents[1]

INPUT_PATH = ROOT / "data" / "processed" / "bgl_frozen_events.parquet"
OUTPUT_PATH = ROOT / "data" / "processed" / "bgl_window_features.parquet"
TEMP_PATH = ROOT / "data" / "processed" / "bgl_window_features.partial.parquet"

REPORT_PATH = ROOT / "reports" / "window_feature_summary.json"
FEATURE_COLUMNS_PATH = ROOT / "reports" / "window_feature_columns.json"

WINDOW_SIZE = 100
BATCH_SIZE = 100_000
OUTPUT_BATCH_WINDOWS = 1_000


CATEGORY_FIELDS = ["event_type", "component", "severity"]

COUNT_BASE_FEATURES = [
    "unknown_event_count",
    "empty_message_count",
    "fallback_parse_count",
    "unique_known_template_count",
    "unique_event_ids_count",
    "log_count",
]

FLOAT_FEATURES = {
    "unknown_event_rate",
    "window_duration_seconds",
    "logs_per_second",
}


def slug(value: str) -> str:
    """Make a readable, filesystem-safe feature-name fragment."""
    value = re.sub(r"[^A-Za-z0-9]+", "_", value.strip())
    return value.strip("_").lower() or "empty"


def collect_training_vocabulary(parquet):
    """Learn feature vocabularies using training records only."""
    template_ids = set()
    category_counts = {
        field: Counter()
        for field in CATEGORY_FIELDS
    }

    columns = [
        "split",
        "frozen_event_id",
        "template_match_status",
        *CATEGORY_FIELDS,
    ]

    print("Learning feature vocabularies from training data only...")

    rows_seen = 0

    for batch in parquet.iter_batches(
        batch_size=BATCH_SIZE,
        columns=columns,
    ):
        for row in batch.to_pylist():
            if row["split"] != "train":
                continue

            rows_seen += 1

            if (
                row["template_match_status"] == "matched"
                and row["frozen_event_id"] is not None
                and row["frozen_event_id"] >= 0
            ):
                template_ids.add(int(row["frozen_event_id"]))

            for field in CATEGORY_FIELDS:
                value = row[field]
                if value is not None:
                    category_counts[field][value] += 1

    if not template_ids:
        raise RuntimeError("No known training templates were found.")

    vocabularies = {
        field: sorted(category_counts[field].keys())
        for field in CATEGORY_FIELDS
    }

    print(f"Training records inspected: {rows_seen:,}")
    print(f"Known training templates: {len(template_ids):,}")

    return sorted(template_ids), vocabularies


def build_category_columns(vocabularies):
    """Build collision-safe column names from training vocabularies."""
    category_maps = {}
    category_feature_columns = []

    for field, values in vocabularies.items():
        field_map = {}
        used_names = set()

        for value in values:
            base = f"category_{field}_{slug(value)}_count"
            column = base
            suffix = 2

            while column in used_names:
                column = f"{base}_{suffix}"
                suffix += 1

            used_names.add(column)
            field_map[value] = column
            category_feature_columns.append(column)

        other_column = f"category_{field}_other_count"
        field_map["__OTHER__"] = other_column
        category_feature_columns.append(other_column)

        category_maps[field] = field_map

    return category_maps, category_feature_columns


def main():
    if not INPUT_PATH.is_file():
        raise FileNotFoundError(INPUT_PATH)

    if OUTPUT_PATH.exists() or TEMP_PATH.exists():
        raise SystemExit(
            "Feature output already exists. Inspect it before rerunning "
            "to avoid overwriting previous results."
        )

    parquet = pq.ParquetFile(INPUT_PATH)
    total_input_rows = parquet.metadata.num_rows

    template_ids, vocabularies = collect_training_vocabulary(parquet)

    template_columns = [
        f"template_{event_id}_count"
        for event_id in template_ids
    ]

    category_maps, category_columns = build_category_columns(vocabularies)

    count_features = (
        template_columns
        + COUNT_BASE_FEATURES
        + category_columns
    )

    feature_columns = count_features + [
        "unknown_event_rate",
        "window_duration_seconds",
        "logs_per_second",
    ]

    metadata_schema = [
        pa.field("window_id", pa.int64(), nullable=False),
        pa.field("split", pa.string(), nullable=False),
        pa.field("start_line", pa.int64(), nullable=False),
        pa.field("end_line", pa.int64(), nullable=False),
        pa.field("start_timestamp", pa.int64(), nullable=False),
        pa.field("end_timestamp", pa.int64(), nullable=False),
        pa.field("target_anomaly", pa.int8(), nullable=False),
    ]

    feature_schema = [
        pa.field(
            name,
            pa.float32() if name in FLOAT_FEATURES else pa.int32(),
            nullable=False,
        )
        for name in feature_columns
    ]

    schema = pa.schema(metadata_schema + feature_schema)

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)

    window_summary = defaultdict(Counter)
    output_window_count = 0
    writer = None
    pending_windows = []

    current = None
    previous_window_id = -1
    input_rows_seen = 0

    def start_window(row):
        counts = {name: 0 for name in count_features}

        return {
            "window_id": int(row["window_id"]),
            "split": row["split"],
            "start_line": int(row["line_number"]),
            "end_line": int(row["line_number"]),
            "start_timestamp": int(row["timestamp"]),
            "end_timestamp": int(row["timestamp"]),
            "counts": counts,
            "seen_ids": set(),
            "seen_known_ids": set(),
            "unknown_count": 0,
            "target_anomaly": 0,
        }

    def finish_window(window):
        nonlocal output_window_count

        counts = window["counts"]
        log_count = counts["log_count"]
        unknown_count = counts["unknown_event_count"]

        duration = max(
            0,
            window["end_timestamp"] - window["start_timestamp"],
        )

        # This records temporal throughput, not incident severity.
        counts["unique_known_template_count"] = len(
            window["seen_known_ids"]
        )
        counts["unique_event_ids_count"] = len(window["seen_ids"])

        row = {
            "window_id": window["window_id"],
            "split": window["split"],
            "start_line": window["start_line"],
            "end_line": window["end_line"],
            "start_timestamp": window["start_timestamp"],
            "end_timestamp": window["end_timestamp"],
            "target_anomaly": window["target_anomaly"],
            **counts,
            "unknown_event_rate": (
                unknown_count / log_count if log_count else 0.0
            ),
            "window_duration_seconds": float(duration),
            "logs_per_second": (
                log_count / max(duration, 1)
                if log_count else 0.0
            ),
        }

        # Guard against accidental omission of any numerical feature.
        if set(feature_columns) - set(row):
            missing = sorted(set(feature_columns) - set(row))
            raise RuntimeError(f"Missing feature values: {missing}")

        pending_windows.append(row)
        output_window_count += 1

        stats = window_summary[window["split"]]
        stats["windows"] += 1
        stats["records"] += log_count
        stats["anomalous_windows"] += window["target_anomaly"]
        stats["unknown_windows"] += int(unknown_count > 0)
        stats["windows_with_empty_messages"] += int(
            counts["empty_message_count"] > 0
        )

    def write_pending_windows():
        if not pending_windows:
            return

        table = pa.Table.from_pylist(
            pending_windows,
            schema=schema,
        )
        writer.write_table(table)
        pending_windows.clear()

    print("\nBuilding chronological 100-record windows...")
    print(f"Input records: {total_input_rows:,}")
    print(f"Model feature columns: {len(feature_columns):,}\n")

    try:
        writer = pq.ParquetWriter(
            str(TEMP_PATH),
            schema,
            compression="zstd",
        )

        columns = [
            "line_number",
            "window_id",
            "split",
            "timestamp",
            "label",
            "frozen_event_id",
            "template_match_status",
            "parse_status",
            "message",
            *CATEGORY_FIELDS,
        ]

        for batch in parquet.iter_batches(
            batch_size=BATCH_SIZE,
            columns=columns,
        ):
            for row in batch.to_pylist():
                input_rows_seen += 1

                window_id = int(row["window_id"])

                if window_id < previous_window_id:
                    raise RuntimeError(
                        "Window IDs are not in chronological order."
                    )

                if current is None:
                    current = start_window(row)

                elif window_id != current["window_id"]:
                    finish_window(current)

                    if len(pending_windows) >= OUTPUT_BATCH_WINDOWS:
                        write_pending_windows()

                    current = start_window(row)

                previous_window_id = window_id

                if row["split"] != current["split"]:
                    raise RuntimeError(
                        f"Window {window_id} crosses split boundaries."
                    )

                current["end_line"] = int(row["line_number"])
                current["end_timestamp"] = int(row["timestamp"])

                counts = current["counts"]
                counts["log_count"] += 1

                # Labels are used only to produce the target, never a feature.
                if row["label"] != "-":
                    current["target_anomaly"] = 1

                is_unknown = (
                    row["template_match_status"] == "unknown"
                    or row["frozen_event_id"] is None
                    or row["frozen_event_id"] < 0
                    or int(row["frozen_event_id"]) not in set(template_ids)
                )

                event_id = (
                    int(row["frozen_event_id"])
                    if not is_unknown
                    else -1
                )

                if is_unknown:
                    counts["unknown_event_count"] += 1
                    current["seen_ids"].add(-1)
                else:
                    counts[f"template_{event_id}_count"] += 1
                    current["seen_ids"].add(event_id)
                    current["seen_known_ids"].add(event_id)

                if row["message"] == "":
                    counts["empty_message_count"] += 1

                if row["parse_status"] == "fallback":
                    counts["fallback_parse_count"] += 1

                for field in CATEGORY_FIELDS:
                    value = row[field]
                    field_map = category_maps[field]

                    feature_name = field_map.get(
                        value,
                        field_map["__OTHER__"],
                    )
                    counts[feature_name] += 1

                if input_rows_seen % 500_000 == 0:
                    print(
                        f"Processed {input_rows_seen:,} rows | "
                        f"windows written or buffered="
                        f"{output_window_count + len(pending_windows):,}"
                    )

        if current is not None:
            finish_window(current)

        write_pending_windows()

        writer.close()
        writer = None

        os.replace(TEMP_PATH, OUTPUT_PATH)

    except Exception:
        if writer is not None:
            writer.close()

        print(
            "\nERROR: Feature generation failed. A partial output may "
            "remain; inspect it before rerunning."
        )
        raise

    if input_rows_seen != total_input_rows:
        raise RuntimeError(
            f"Read {input_rows_seen:,} input rows; "
            f"expected {total_input_rows:,}."
        )

    feature_manifest = {
        "feature_columns": feature_columns,
        "target_column": "target_anomaly",
        "metadata_columns": [
            "window_id",
            "split",
            "start_line",
            "end_line",
            "start_timestamp",
            "end_timestamp",
        ],
        "window_size": WINDOW_SIZE,
        "template_ids_from_training_only": template_ids,
        "categorical_vocabularies_from_training_only": vocabularies,
        "labels_used_as_feature_inputs": False,
    }

    with FEATURE_COLUMNS_PATH.open("w", encoding="utf-8") as file:
        json.dump(feature_manifest, file, indent=2, ensure_ascii=False)

    stats_report = {
        "source_rows": total_input_rows,
        "processed_rows": input_rows_seen,
        "window_size": WINDOW_SIZE,
        "windows_created": output_window_count,
        "model_feature_count": len(feature_columns),
        "training_template_count": len(template_ids),
        "target_definition": (
            "1 if any log in the window has a BGL label other than '-'; "
            "0 otherwise"
        ),
        "labels_used_as_model_features": False,
        "statistics_by_split": {
            split: dict(stats)
            for split, stats in window_summary.items()
        },
        "output_file": str(OUTPUT_PATH.relative_to(ROOT)),
        "feature_manifest": str(FEATURE_COLUMNS_PATH.relative_to(ROOT)),
    }

    with REPORT_PATH.open("w", encoding="utf-8") as file:
        json.dump(stats_report, file, indent=2, ensure_ascii=False)

    print("\n========== WINDOW FEATURE SUMMARY ==========")
    print(f"Input rows processed: {input_rows_seen:,}")
    print(f"Windows created: {output_window_count:,}")
    print(f"Numerical model features: {len(feature_columns):,}")

    for split, stats in stats_report["statistics_by_split"].items():
        print(f"\n{split.upper()}")
        print(f"  Windows: {stats['windows']:,}")
        print(f"  Anomalous windows: {stats['anomalous_windows']:,}")
        print(f"  Windows containing unknown events: {stats['unknown_windows']:,}")

    print(f"\nFeatures: {OUTPUT_PATH}")
    print(f"Feature manifest: {FEATURE_COLUMNS_PATH}")
    print(f"Summary report: {REPORT_PATH}")
    print("\nSUCCESS: Window feature generation completed.")


if __name__ == "__main__":
    main()
