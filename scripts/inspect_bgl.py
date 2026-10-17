
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
import json
import sys


ROOT = Path(__file__).resolve().parents[1]
LOG_PATH = ROOT / "data" / "raw" / "BGL.log"
REPORT_PATH = ROOT / "reports" / "bgl_inspection.json"

FIELD_NAMES = [
    "label",
    "timestamp",
    "date",
    "node",
    "time",
    "node_repeat",
    "type",
    "component",
    "level",
    "content",
]

EXPECTED_LEVELS = {
    "INFO",
    "FATAL",
    "ERROR",
    "WARNING",
    "SEVERE",
    "FAILURE",
    "Kill",
}


def main() -> None:
    if not LOG_PATH.is_file():
        sys.exit(f"ERROR: Dataset not found: {LOG_PATH}")

    label_counts = Counter()
    level_counts = Counter()
    type_counts = Counter()
    component_counts = Counter()
    unexpected_level_counts = Counter()

    total_lines = 0
    valid_lines = 0
    malformed_lines = 0
    empty_message_lines = 0

    min_timestamp = None
    max_timestamp = None

    sample_records = []
    malformed_samples = []
    unexpected_level_samples = []

    file_size = LOG_PATH.stat().st_size

    print(f"Inspecting: {LOG_PATH}")
    print(f"File size: {file_size:,} bytes")
    print("Processing sequentially. Please wait...\n")

    with LOG_PATH.open(
        "r",
        encoding="utf-8",
        errors="replace",
    ) as file:
        for line_number, raw_line in enumerate(file, start=1):
            total_lines += 1
            line = raw_line.rstrip("\r\n")

            # Split into the first nine fields and the remaining message.
            parts = line.split(maxsplit=9)

            # Some legitimate BGL records end at the severity field,
            # meaning the message field is empty.
            if len(parts) == 9:
                parts.append("")
                empty_message_lines += 1

            # Records with fewer than nine fields need investigation.
            if len(parts) != len(FIELD_NAMES):
                malformed_lines += 1

                if len(malformed_samples) < 10:
                    malformed_samples.append({
                        "line_number": line_number,
                        "reason": (
                            f"Expected 9 or 10 fields; "
                            f"found {len(parts)}"
                        ),
                        "sample": line[:250],
                    })
                continue

            (
                label,
                timestamp_text,
                date,
                node,
                precise_time,
                node_repeat,
                event_type,
                component,
                level,
                content,
            ) = parts

            # Reject records whose timestamp cannot be parsed.
            try:
                timestamp = int(timestamp_text)
            except ValueError:
                malformed_lines += 1

                if len(malformed_samples) < 10:
                    malformed_samples.append({
                        "line_number": line_number,
                        "reason": "Invalid Unix timestamp",
                        "sample": line[:250],
                    })
                continue

            valid_lines += 1

            # Preserve the original dataset labels.
            label_counts[label] += 1
            level_counts[level] += 1
            type_counts[event_type] += 1
            component_counts[component] += 1

            # Record unusual severity values without discarding them.
            if level not in EXPECTED_LEVELS:
                unexpected_level_counts[level] += 1

                if len(unexpected_level_samples) < 10:
                    unexpected_level_samples.append({
                        "line_number": line_number,
                        "level": level,
                        "event_type": event_type,
                        "component": component,
                        "sample": line[:250],
                    })

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

            if len(sample_records) < 5:
                sample_records.append({
                    "label": label,
                    "timestamp": timestamp,
                    "date": date,
                    "node": node,
                    "time": precise_time,
                    "node_repeat": node_repeat,
                    "type": event_type,
                    "component": component,
                    "level": level,
                    "content": content[:180],
                })

            if total_lines % 500_000 == 0:
                print(
                    f"Processed {total_lines:,} lines; "
                    f"valid={valid_lines:,}; "
                    f"malformed={malformed_lines:,}"
                )

    normal_lines = label_counts.get("-", 0)
    alert_lines = valid_lines - normal_lines

    timestamp_range = None

    if min_timestamp is not None and max_timestamp is not None:
        timestamp_range = {
            "min_unix": min_timestamp,
            "max_unix": max_timestamp,
            "min_utc": datetime.fromtimestamp(
                min_timestamp, timezone.utc
            ).isoformat(),
            "max_utc": datetime.fromtimestamp(
                max_timestamp, timezone.utc
            ).isoformat(),
        }

    report = {
        "dataset": "LogHub BGL",
        "source_file": str(LOG_PATH.relative_to(ROOT)),
        "file_size_bytes": file_size,
        "total_lines": total_lines,
        "valid_lines": valid_lines,
        "malformed_lines": malformed_lines,
        "empty_message_lines": empty_message_lines,
        "normal_label": "-",
        "normal_lines": normal_lines,
        "alert_label_lines": alert_lines,
        "alert_percentage": (
            round(alert_lines * 100 / valid_lines, 4)
            if valid_lines
            else 0.0
        ),
        "unique_labels": len(label_counts),
        "label_counts": dict(
            sorted(
                label_counts.items(),
                key=lambda item: (-item[1], item[0]),
            )
        ),
        "top_alert_labels": dict(
            sorted(
                (
                    (label, count)
                    for label, count in label_counts.items()
                    if label != "-"
                ),
                key=lambda item: (-item[1], item[0]),
            )[:15]
        ),
        "severity_counts": dict(level_counts.most_common()),
        "unexpected_severity_counts": dict(
            unexpected_level_counts.most_common()
        ),
        "unexpected_severity_samples": unexpected_level_samples,
        "top_event_types": dict(type_counts.most_common(10)),
        "top_components": dict(component_counts.most_common(10)),
        "timestamp_range": timestamp_range,
        "sample_records": sample_records,
        "malformed_samples": malformed_samples,
    }

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)

    with REPORT_PATH.open("w", encoding="utf-8") as report_file:
        json.dump(
            report,
            report_file,
            indent=2,
            ensure_ascii=False,
        )

    print("\n========== BGL DATA QUALITY REPORT ==========")
    print(f"Total lines:          {total_lines:,}")
    print(f"Valid lines:          {valid_lines:,}")
    print(f"Malformed lines:      {malformed_lines:,}")
    print(f"Empty-message lines:  {empty_message_lines:,}")
    print(f"Normal-labelled:      {normal_lines:,}")
    print(f"Alert-labelled:       {alert_lines:,}")
    print(f"Alert percentage:     {report['alert_percentage']:.4f}%")
    print(f"Unique labels:        {len(label_counts):,}")

    print("\nTop alert labels:")
    for label, count in report["top_alert_labels"].items():
        print(f"  {label}: {count:,}")

    print("\nSeverity counts:")
    for level, count in level_counts.most_common():
        print(f"  {level}: {count:,}")

    print("\nUnexpected severity values requiring review:")
    if unexpected_level_counts:
        for level, count in unexpected_level_counts.most_common():
            print(f"  {level!r}: {count:,}")
    else:
        print("  None")

    print("\nTimestamp range:")
    print(json.dumps(timestamp_range, indent=2))

    print(f"\nFull report saved to: {REPORT_PATH}")

    if malformed_lines:
        print(
            "\nWARNING: Inspect malformed_samples in the JSON report."
        )
    else:
        print(
            "\nSUCCESS: All records passed the basic structural "
            "and timestamp checks."
        )

    if unexpected_level_counts:
        print(
            "NOTE: Unusual severity values were retained for "
            "investigation; they were not automatically discarded."
        )


if __name__ == "__main__":
    main()
