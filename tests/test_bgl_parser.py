import pytest

from zentinel.parsing.drain_parser import parse_bgl_line


def test_standard_bgl_record():
    raw = (
        "- 1117838570 2005.06.03 "
        "R02-M1-N0-C:J12-U11 "
        "2005-06-03-15.42.50.363779 "
        "R02-M1-N0-C:J12-U11 "
        "RAS KERNEL INFO instruction cache parity error corrected"
    )

    record = parse_bgl_line(raw, line_number=1)

    assert record.label == "-"
    assert record.timestamp == 1117838570
    assert record.severity == "INFO"
    assert record.event_type == "RAS"
    assert record.component == "KERNEL"
    assert record.message == "instruction cache parity error corrected"
    assert record.parse_status == "standard"


def test_empty_message_is_preserved():
    raw = (
        "- 1120866514 2005.07.08 "
        "R02-M1-N8-C:J13-U01 "
        "2005-07-08-16.48.34.004234 "
        "R02-M1-N8-C:J13-U01 "
        "RAS KERNEL FATAL"
    )

    record = parse_bgl_line(raw, line_number=2)

    assert record.severity == "FATAL"
    assert record.message == ""
    assert record.parse_status == "standard"


def test_null_event_type_is_supported():
    raw = (
        "- 1118241709 2005.07.08 R33-M1-ND "
        "2005-07-08-16.48.29.000000 "
        "R33-M1-ND NULL HARDWARE WARNING "
        "PrepareForService is being done on this card"
    )

    record = parse_bgl_line(raw, line_number=3)

    assert record.event_type == "NULL"
    assert record.component == "HARDWARE"
    assert record.severity == "WARNING"
    assert record.parse_status == "standard"


def test_nonstandard_record_preserves_its_message():
    raw = (
        "- 1133447861 2005.12.01 - "
        "2005-12-01-06.37.41.417709 "
        "time for a single instance of a correctable ddr. "
        "RAS KERNEL INFO 0 microseconds spent in the rbs signal handler"
    )

    record = parse_bgl_line(raw, line_number=4)

    assert record.label == "-"
    assert record.timestamp == 1133447861
    assert record.parse_status == "fallback"
    assert record.severity is None
    assert "time for a single instance" in record.message
    assert "RAS KERNEL INFO" in record.message


def test_invalid_timestamp_is_rejected():
    raw = (
        "- invalid 2005.06.03 "
        "R02-M1-N0-C:J12-U11 "
        "2005-06-03-15.42.50.363779 "
        "R02-M1-N0-C:J12-U11 RAS KERNEL INFO message"
    )

    with pytest.raises(ValueError, match="invalid Unix timestamp"):
        parse_bgl_line(raw, line_number=5)


def test_too_few_fields_rejected():
    raw = "- 1117838570 2005.06.03"
    with pytest.raises(ValueError, match="fewer than five fixed-prefix fields"):
        parse_bgl_line(raw, line_number=6)


def test_empty_string_rejected():
    with pytest.raises(ValueError):
        parse_bgl_line("", line_number=7)


def test_whitespace_only_string_rejected():
    with pytest.raises(ValueError):
        parse_bgl_line("   \t  \n", line_number=8)
