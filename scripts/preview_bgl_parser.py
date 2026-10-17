import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOG_PATH = ROOT / "data" / "raw" / "BGL.log"

KNOWN_LEVELS = {
    "INFO",
    "FATAL",
    "ERROR",
    "WARNING",
    "SEVERE",
    "FAILURE",
    "Kill",
}

DATE_PATTERN = re.compile(r"^\d{4}\.\d{2}\.\d{2}$")
TIME_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}-\d{2}\.\d{2}\.\d{2}\.\d{6}$")


def main() -> None:
    if not LOG_PATH.is_file():
        raise FileNotFoundError(f"Dataset not found: {LOG_PATH}")

    total = 0
    valid_prefix = 0
    invalid_prefix = 0
    standard_layout = 0
    nonstandard_layout = 0
    empty_messages = 0

    label_counts = Counter()
    severity_counts = Counter()
    nonstandard_examples = []
    invalid_examples = []

    print(f"Inspecting {LOG_PATH}")
    print("Checking the fixed prefix and remaining message structure...\n")

    with LOG_PATH.open(
        "r",
        encoding="utf-8",
        errors="replace",
    ) as file:
        for line_number, raw_line in enumerate(file, start=1):
            total += 1
            line = raw_line.rstrip("\r\n")

            # Preserve everything after the first five fields as a tail.
            parts = line.split(maxsplit=5)

            if len(parts) < 5:
                invalid_prefix += 1

                if len(invalid_examples) < 10:
                    invalid_examples.append(
                        {
                            "line": line_number,
                            "reason": "Fewer than five prefix fields",
                            "raw": line[:250],
                        }
                    )
                continue

            label, timestamp_text, date, node, event_time = parts[:5]
            tail = parts[5] if len(parts) == 6 else ""

            try:
                int(timestamp_text)
                prefix_ok = DATE_PATTERN.fullmatch(date) is not None and TIME_PATTERN.fullmatch(event_time) is not None
            except ValueError:
                prefix_ok = False

            if not prefix_ok:
                invalid_prefix += 1

                if len(invalid_examples) < 10:
                    invalid_examples.append(
                        {
                            "line": line_number,
                            "reason": "Invalid timestamp or date/time format",
                            "raw": line[:250],
                        }
                    )
                continue

            valid_prefix += 1
            label_counts[label] += 1

            # Only interpret metadata when its expected structure is present.
            tail_fields = tail.split(maxsplit=4)

            has_standard_layout = len(tail_fields) >= 4 and tail_fields[3] in KNOWN_LEVELS

            if has_standard_layout:
                standard_layout += 1
                severity_counts[tail_fields[3]] += 1

                # Four metadata tokens with no trailing content means
                # that the message is empty.
                if len(tail_fields) == 4:
                    empty_messages += 1
            else:
                nonstandard_layout += 1

                if len(nonstandard_examples) < 10:
                    nonstandard_examples.append(
                        {
                            "line": line_number,
                            "label": label,
                            "timestamp": timestamp_text,
                            "node": node,
                            "tail": tail[:350],
                        }
                    )

            if total % 500_000 == 0:
                print(
                    f"Processed {total:,} lines | "
                    f"valid prefixes={valid_prefix:,} | "
                    f"nonstandard layouts={nonstandard_layout:,}"
                )

    print("\n========== PARSER PREVIEW REPORT ==========")
    print(f"Total lines:             {total:,}")
    print(f"Valid fixed prefixes:     {valid_prefix:,}")
    print(f"Invalid fixed prefixes:   {invalid_prefix:,}")
    print(f"Standard metadata layout: {standard_layout:,}")
    print(f"Nonstandard layout:       {nonstandard_layout:,}")
    print(f"Empty messages:           {empty_messages:,}")

    print("\nTop labels:")
    for label, count in label_counts.most_common(15):
        print(f"  {label}: {count:,}")

    print("\nRecognized severity levels:")
    for level, count in severity_counts.most_common():
        print(f"  {level}: {count:,}")

    print("\nFirst nonstandard records:")
    for record in nonstandard_examples:
        print(f"\nLine {record['line']}")
        print(f"Label: {record['label']}")
        print(f"Timestamp: {record['timestamp']}")
        print(f"Node: {record['node']}")
        print(f"Remaining text: {record['tail']}")

    if invalid_examples:
        print("\nInvalid prefix examples:")
        for record in invalid_examples:
            print(record)

    print("\nPreview complete. No dataset files were modified.")


if __name__ == "__main__":
    main()
