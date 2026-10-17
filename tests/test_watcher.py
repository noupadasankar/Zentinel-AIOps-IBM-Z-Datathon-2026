import hashlib
import json
from unittest.mock import MagicMock, patch

from zentinel.ingestion.watcher import (
    LogFileWatcher,
    compute_file_id,
    generate_idempotency_key,
)


def test_file_id_and_key_generation(tmp_path):
    log_file = tmp_path / "test.log"
    first_line = b"first line of the log file\n"
    log_file.write_bytes(first_line + b"second line\n")

    file_id = compute_file_id(log_file)
    expected_first_line_hash = hashlib.sha256(first_line).hexdigest()
    assert expected_first_line_hash in file_id

    key0 = generate_idempotency_key(file_id, 0)
    key100 = generate_idempotency_key(file_id, 100)
    assert key0 != key100
    assert len(key0) == 64


def test_file_replacement_detected_by_first_line_change(tmp_path):
    log_file = tmp_path / "stream.log"
    log_file.write_text("initial first line\nsecond line\n", encoding="utf-8")
    cursor_file = tmp_path / "cursor.json"

    watcher = LogFileWatcher(
        file_path=log_file,
        cursor_path=cursor_file,
        from_start=False,
    )
    offset, file_id = watcher.load_cursor()
    assert offset == log_file.stat().st_size

    # Simulate log rotation/replacement where first line changes
    log_file.write_text("completely new first line after rotation\n", encoding="utf-8")
    new_offset, new_file_id = watcher.load_cursor()

    # Watcher detects replacement and resets offset to 0!
    assert new_offset == 0
    assert new_file_id != file_id


def test_dead_letter_records_400_and_422_with_correct_source_file(tmp_path):
    log_file = tmp_path / "watched.log"
    log_file.write_text("invalid log line payload\n", encoding="utf-8")
    cursor_file = tmp_path / "cursor.json"
    dead_letter_file = tmp_path / "dead_letter.jsonl"

    watcher = LogFileWatcher(
        file_path=log_file,
        cursor_path=cursor_file,
        dead_letter_path=dead_letter_file,
        from_start=True,
    )
    watcher.offset, watcher.file_id = watcher.load_cursor()

    # Mock API returning 422 Unprocessable Entity
    mock_resp = MagicMock()
    mock_resp.status_code = 422
    mock_resp.text = "Unprocessable log record"

    mock_client = MagicMock()
    mock_client.post.return_value = mock_resp

    result = watcher.run_once(mock_client)
    assert result == {"status": "rejected", "code": 422}

    # Offset must have advanced past the bad line
    assert watcher.offset > 0

    # Dead letter must record the actual watched file path
    assert dead_letter_file.exists()
    records = [json.loads(line) for line in dead_letter_file.read_text(encoding="utf-8").splitlines()]
    assert len(records) == 1
    assert records[0]["source_file"] == str(log_file.resolve())
    assert records[0]["raw_line"] == "invalid log line payload"
    assert "422" in records[0]["error"]


def test_transient_error_retries_with_backoff_without_advancing(tmp_path):
    log_file = tmp_path / "stream.log"
    log_file.write_text("valid log line\n", encoding="utf-8")
    cursor_file = tmp_path / "cursor.json"
    dead_letter_file = tmp_path / "dead_letter.jsonl"

    watcher = LogFileWatcher(
        file_path=log_file,
        cursor_path=cursor_file,
        dead_letter_path=dead_letter_file,
        from_start=True,
        initial_backoff=0.1,
    )
    watcher.offset, watcher.file_id = watcher.load_cursor()
    initial_offset = watcher.offset

    # Mock transient 503 Service Unavailable
    mock_resp = MagicMock()
    mock_resp.status_code = 503

    mock_client = MagicMock()
    mock_client.post.return_value = mock_resp

    with patch("time.sleep"):
        result = watcher.run_once(mock_client)

    assert result is None
    # Cursor offset must NOT advance on server error!
    assert watcher.offset == initial_offset
    # Backoff increased
    assert watcher.current_backoff == 0.2
    # Not sent to dead letter
    assert not dead_letter_file.exists()


def test_rate_limit_429_retries_without_advancing(tmp_path):
    log_file = tmp_path / "stream.log"
    log_file.write_text("valid line\n", encoding="utf-8")
    cursor_file = tmp_path / "cursor.json"

    watcher = LogFileWatcher(
        file_path=log_file,
        cursor_path=cursor_file,
        from_start=True,
        initial_backoff=0.1,
    )
    watcher.offset, watcher.file_id = watcher.load_cursor()
    initial_offset = watcher.offset

    mock_resp = MagicMock()
    mock_resp.status_code = 429
    mock_client = MagicMock()
    mock_client.post.return_value = mock_resp

    with patch("time.sleep"):
        result = watcher.run_once(mock_client)

    assert result is None
    assert watcher.offset == initial_offset
    assert watcher.current_backoff > 0.1


def test_successful_ingest_advances_cursor_and_resets_backoff(tmp_path):
    log_file = tmp_path / "stream.log"
    log_file.write_text("valid line\n", encoding="utf-8")
    cursor_file = tmp_path / "cursor.json"

    watcher = LogFileWatcher(
        file_path=log_file,
        cursor_path=cursor_file,
        from_start=True,
        initial_backoff=0.1,
    )
    watcher.offset, watcher.file_id = watcher.load_cursor()
    watcher.current_backoff = 0.8  # elevated backoff

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "mode": "live_ingestion",
        "window_completed": True,
        "window_decision": {"window_id": 1, "decision": "NO_ANOMALY_DETECTED"},
    }
    mock_client = MagicMock()
    mock_client.post.return_value = mock_resp

    decisions = []
    result = watcher.run_once(mock_client, on_decision=lambda d: decisions.append(d))

    assert result is not None
    assert watcher.offset == log_file.stat().st_size
    assert watcher.current_backoff == 0.1  # reset to initial
    assert len(decisions) == 1
