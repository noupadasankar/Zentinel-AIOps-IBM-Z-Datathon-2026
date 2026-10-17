"""Structured parser for LogHub BGL records."""

from dataclasses import asdict, dataclass

KNOWN_SEVERITIES = frozenset(
    {
        "INFO",
        "FATAL",
        "ERROR",
        "WARNING",
        "SEVERE",
        "FAILURE",
        "Kill",
    }
)


@dataclass(frozen=True)
class BGLRecord:
    """One parsed BGL record with its original label and timestamp."""

    line_number: int
    label: str
    timestamp: int
    date: str
    node: str
    event_time: str
    node_repeat: str | None
    event_type: str | None
    component: str | None
    severity: str | None
    message: str
    parse_status: str

    def to_dict(self) -> dict:
        """Convert the record to a JSON-compatible dictionary."""
        return asdict(self)


def parse_bgl_line(raw_line: str, line_number: int = 0) -> BGLRecord:
    """
    Parse one BGL line without discarding its original message.

    Standard layout:
        label timestamp date node time node_repeat type component
        severity message

    Nonstandard records retain the complete remaining text as their
    message and receive parse_status='fallback'.
    """
    line = raw_line.rstrip("\r\n")

    # Preserve the message tail instead of splitting the whole line
    # into individual whitespace-separated tokens.
    parts = line.split(maxsplit=5)

    if len(parts) < 5:
        raise ValueError(f"Line {line_number}: fewer than five fixed-prefix fields")

    label, timestamp_text, date, node, event_time = parts[:5]
    tail = parts[5] if len(parts) == 6 else ""

    try:
        timestamp = int(timestamp_text)
    except ValueError as exc:
        raise ValueError(f"Line {line_number}: invalid Unix timestamp {timestamp_text!r}") from exc

    tail_fields = tail.split(maxsplit=4)

    # Only parse metadata when the recognized severity occupies
    # the expected fourth position in the metadata tail.
    standard_layout = len(tail_fields) >= 4 and tail_fields[3] in KNOWN_SEVERITIES

    if standard_layout:
        node_repeat, event_type, component, severity = tail_fields[:4]

        # An empty message is valid. Do not invent message text.
        message = tail_fields[4] if len(tail_fields) == 5 else ""
        parse_status = "standard"
    else:
        # Preserve all text when the metadata structure is uncertain.
        node_repeat = None
        event_type = None
        component = None
        severity = None
        message = tail
        parse_status = "fallback"

    return BGLRecord(
        line_number=line_number,
        label=label,
        timestamp=timestamp,
        date=date,
        node=node,
        event_time=event_time,
        node_repeat=node_repeat,
        event_type=event_type,
        component=component,
        severity=severity,
        message=message,
        parse_status=parse_status,
    )
