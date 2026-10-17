from zentinel.api.live_store import LiveStateStore


def make_event(line_number=1, token=2):
    return {
        "line_number": line_number,
        "timestamp": 1117838570,
        "parse_status": "standard",
        "token": token,
        "event_status": "matched" if token >= 2 else "unknown_template",
        "matched_event_id": 1 if token >= 2 else None,
        "template": "RAS KERNEL INFO instruction cache parity error corrected" if token >= 2 else None,
    }


def make_state(
    logs_received=1,
    windows_completed=0,
    unknown_events_received=0,
):
    return {
        "logs_received": logs_received,
        "windows_completed": windows_completed,
        "unknown_events_received": unknown_events_received,
    }


def test_new_database_starts_with_empty_state(tmp_path):
    database_path = tmp_path / "empty.sqlite3"
    store = LiveStateStore(database_path)

    try:
        loaded = store.load_state()

        assert loaded["state"]["logs_received"] == 0
        assert loaded["state"]["windows_completed"] == 0
        assert loaded["state"]["unknown_events_received"] == 0
        assert loaded["current_window"] == []
        assert loaded["recent_windows"] == []
    finally:
        store.close()


def test_unfinished_window_survives_store_reload(tmp_path):
    database_path = tmp_path / "pending.sqlite3"
    store = LiveStateStore(database_path)

    event = make_event()
    pending_window = [event]
    state = make_state(logs_received=1)

    try:
        store.save_event_and_state(
            event=event,
            state=state,
            current_window=pending_window,
        )

        loaded = store.load_state()

        assert loaded["state"]["logs_received"] == 1
        assert loaded["state"]["windows_completed"] == 0
        assert len(loaded["current_window"]) == 1
        assert loaded["current_window"][0]["line_number"] == 1
        assert loaded["recent_windows"] == []
    finally:
        store.close()


def test_completed_window_and_decision_are_persisted(tmp_path):
    database_path = tmp_path / "completed.sqlite3"
    store = LiveStateStore(database_path)

    event = make_event()
    decision = {
        "window_id": 1,
        "first_line_number": 1,
        "last_line_number": 100,
        "lines_processed": 100,
        "unknown_event_count": 0,
        "known_event_count": 100,
        "unknown_event_rate": 0.0,
        "known_transitions_scored": 99,
        "model_scorable": True,
        "lstm_anomaly_score": 0.0213,
        "lstm_would_alert": False,
        "high_unknown_rate": False,
        "decision": "NO_ANOMALY_DETECTED",
        "decision_reason": "BELOW_ANOMALY_AND_NOVELTY_THRESHOLDS",
    }

    try:
        store.save_event_and_state(
            event=event,
            state=make_state(
                logs_received=100,
                windows_completed=1,
            ),
            current_window=[],
            window_result=decision,
        )

        loaded = store.load_state()

        assert loaded["state"]["logs_received"] == 100
        assert loaded["state"]["windows_completed"] == 1
        assert loaded["current_window"] == []
        assert len(loaded["recent_windows"]) == 1
        assert loaded["recent_windows"][0] == decision
    finally:
        store.close()


def test_idempotency_response_survives_store_reload(tmp_path):
    database_path = tmp_path / "idempotency.sqlite3"
    store = LiveStateStore(database_path)

    key = "test-file-line-001"
    event = make_event()
    original_response = {
        "mode": "live_ingestion",
        "event": event,
        "window_completed": False,
        "lines_in_current_window": 1,
        "lines_remaining": 99,
        "window_decision": None,
    }

    try:
        store.save_event_and_state(
            event=event,
            state=make_state(logs_received=1),
            current_window=[event],
            idempotency_key=key,
            payload_sha256="abc123hash",
            response=original_response,
        )

        assert store.get_idempotent_response(key) == original_response
        assert store.get_idempotent_response("unknown-key") is None

        record = store.get_idempotency_record(key)
        assert record is not None
        assert record["payload_sha256"] == "abc123hash"
        assert record["response"] == original_response
    finally:
        store.close()

    restored_store = LiveStateStore(database_path)
    try:
        assert restored_store.get_idempotent_response(key) == original_response
        rec = restored_store.get_idempotency_record(key)
        assert rec["payload_sha256"] == "abc123hash"
    finally:
        restored_store.close()


def test_retention_purge(tmp_path):
    database_path = tmp_path / "retention.sqlite3"
    store = LiveStateStore(database_path)

    try:
        with store._connection:
            store._connection.execute("""
                INSERT INTO ingestion_idempotency (idempotency_key, payload_sha256, response_json, created_at)
                VALUES ('old-key', 'old-sha', '{}', datetime('now', '-10 days')),
                       ('recent-key', 'recent-sha', '{}', datetime('now', '-1 days'))
            """)
            store._connection.execute("""
                INSERT INTO ingested_events (line_number, timestamp, parse_status, token, event_status, created_at)
                VALUES (1, 1000, 'standard', 2, 'matched', datetime('now', '-35 days')),
                       (2, 2000, 'standard', 2, 'matched', datetime('now', '-5 days'))
            """)

        result = store.purge_retention(idempotency_retention_days=7, event_retention_days=30)
        assert result["purged_idempotency_rows"] == 1
        assert result["purged_event_rows"] == 1

        with store._connection:
            remaining_keys = [
                row[0]
                for row in store._connection.execute("SELECT idempotency_key FROM ingestion_idempotency").fetchall()
            ]
            assert remaining_keys == ["recent-key"]

            remaining_lines = [
                row[0] for row in store._connection.execute("SELECT line_number FROM ingested_events").fetchall()
            ]
            assert remaining_lines == [2]
    finally:
        store.close()


def test_wal_checkpoint_and_backup(tmp_path):
    database_path = tmp_path / "source.sqlite3"
    backup_path = tmp_path / "backup.sqlite3"
    store = LiveStateStore(database_path)

    try:
        event = make_event(line_number=1)
        store.save_event_and_state(
            event=event,
            state=make_state(logs_received=1),
            current_window=[event],
        )

        checkpoint_result = store.wal_checkpoint(mode="PASSIVE")
        assert checkpoint_result["mode"] == "PASSIVE"

        store.backup_database(backup_path)
        assert backup_path.exists()

        backup_store = LiveStateStore(backup_path)
        try:
            loaded = backup_store.load_state()
            assert loaded["state"]["logs_received"] == 1
            assert len(loaded["current_window"]) == 1
        finally:
            backup_store.close()
    finally:
        store.close()
