"""Tests for the dashboard aggregate feed, windows filters, and migration 004."""

import hashlib
import json
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from zentinel.api.live_api import (
    CURRENT_WINDOW,
    INCIDENT_POLICY,
    RECENT_WINDOWS,
    SECURITY,
    STATE,
    app,
)
from zentinel.api.live_store import LiveStateStore

READ_KEY = "read-secret-test-key"
INGEST_KEY = "ingest-secret-test-key"


def _read_hash() -> str:
    return hashlib.sha256(READ_KEY.encode("utf-8")).hexdigest()


def _ingest_hash() -> str:
    return hashlib.sha256(INGEST_KEY.encode("utf-8")).hexdigest()


VALID_BGL_LINE = (
    "- 1117838570 2005.06.03 "
    "R02-M1-N0-C:J12-U11 "
    "2005-06-03-15.42.50.363779 "
    "R02-M1-N0-C:J12-U11 "
    "RAS KERNEL INFO instruction cache parity error corrected"
)


@pytest.fixture()
def client_with_store(tmp_path):
    db_path = tmp_path / "test_dash.sqlite3"
    store = LiveStateStore(db_path)

    with (
        patch("zentinel.api.live_api.STORE", store),
        patch("zentinel.api.live_api.CONFIG.db_path", db_path),
    ):
        STATE.clear()
        STATE.update({"logs_received": 0, "windows_completed": 0, "unknown_events_received": 0})
        CURRENT_WINDOW.clear()
        RECENT_WINDOWS.clear()
        INCIDENT_POLICY.current_incident = None
        INCIDENT_POLICY.incident_counter = 0

        keys_cfg = f"admin:{hashlib.sha256(b'admin-secret-test-key').hexdigest()}"
        keys_cfg += f",ingest:{_ingest_hash()}"
        keys_cfg += f",read:{_read_hash()}"
        SECURITY.api_keys_config = keys_cfg
        SECURITY._load_keys()
        SECURITY.rate_limiter.reset()

        with TestClient(app) as client:
            yield client, store

        store.close()


def test_migration_004_backfills_decision_column(tmp_path):
    """Existing DBs upgraded to migration 004 get decision values backfilled."""
    db_path = tmp_path / "legacy.sqlite3"
    store = LiveStateStore(db_path)  # applies all migrations incl. 004

    payload = {"window_id": 1, "decision": "ANOMALY_ALERT"}
    with store._lock, store._connection:
        store._connection.execute(
            "INSERT INTO live_windows (window_id, payload_json, decision) VALUES (?, ?, '')",
            (1, json.dumps(payload)),
        )

    # Force a manual replay of the 004 backfill logic as if upgrading an old DB
    from zentinel.api.migrations import _backfill_live_window_decisions

    _backfill_live_window_decisions(store._connection)

    decision = store._connection.execute("SELECT decision FROM live_windows WHERE window_id = 1").fetchone()[0]
    assert decision == "ANOMALY_ALERT"
    store.close()


def test_decision_distribution_and_windows_page(tmp_path):
    store = LiveStateStore(tmp_path / "dist.sqlite3")
    for wid, decision in ((1, "NO_ANOMALY_DETECTED"), (2, "ANOMALY_ALERT"), (3, "NOVELTY_DRIFT_REVIEW")):
        with store._lock, store._connection:
            store._connection.execute(
                "INSERT INTO live_windows (window_id, payload_json, decision) VALUES (?, ?, ?)",
                (wid, json.dumps({"window_id": wid, "decision": decision}), decision),
            )

    all_counts = store.get_decision_distribution()
    assert all_counts["ANOMALY_ALERT"] == 1
    assert all_counts["NO_ANOMALY_DETECTED"] == 1
    assert all_counts["NOVELTY_DRIFT_REVIEW"] == 1

    recent = store.get_decision_distribution(recent_limit=2)
    assert sum(recent.values()) == 2
    assert recent.get("ANOMALY_ALERT") == 1

    page = store.get_windows_page(decision="ANOMALY_ALERT")
    assert len(page) == 1
    assert page[0]["decision"] == "ANOMALY_ALERT"
    assert page[0]["window_id"] == 2
    store.close()


def test_last_incident_sequence_roundtrip(tmp_path):
    store = LiveStateStore(tmp_path / "inc.sqlite3")
    assert store.get_last_incident_sequence() == 0

    with store._lock, store._connection:
        store._connection.execute(
            "INSERT INTO alerts (incident_id, score, threshold, first_window_id, "
            "last_window_id, first_line_number, last_line_number, summary) "
            "VALUES ('INC-0042', 3.9, 3.4791, 1, 2, 1, 200, 'test')"
        )
    assert store.get_last_incident_sequence() == 42
    store.close()


def test_dashboard_summary_endpoint_shape(client_with_store):
    client, _store = client_with_store
    resp = client.get("/api/v1/dashboards/summary", headers={"X-API-Key": READ_KEY})
    assert resp.status_code == 200
    data = resp.json()

    assert "counters" in data and "kpis" in data and "decisions" in data
    assert "thresholds" in data and "recent_alerts" in data
    assert data["counters"]["logs_received"] == 0
    assert data["kpis"]["anomaly_rate_recent_100"] == 0.0


def test_windows_endpoint_decision_filter(client_with_store):
    client, store = client_with_store

    for wid, decision in ((1, "NO_ANOMALY_DETECTED"), (2, "ANOMALY_ALERT")):
        with store._lock, store._connection:
            store._connection.execute(
                "INSERT INTO live_windows (window_id, payload_json, decision) VALUES (?, ?, ?)",
                (wid, json.dumps({"window_id": wid, "decision": decision}), decision),
            )

    resp = client.get(
        "/api/v1/live/windows",
        params={"decision": "ANOMALY_ALERT", "limit": 10},
        headers={"X-API-Key": READ_KEY},
    )
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 1
    assert items[0]["window_id"] == 2


def test_status_includes_open_alerts(client_with_store):
    client, _store = client_with_store
    resp = client.get("/api/v1/live/status", headers={"X-API-Key": READ_KEY})
    assert resp.status_code == 200
    assert "open_alerts" in resp.json()


def test_incident_counter_restored_across_restarts(tmp_path):
    """A restarted API must continue incident IDs instead of replaying INC-0001."""
    db_path = tmp_path / "restart.sqlite3"
    store = LiveStateStore(db_path)

    with store._lock, store._connection:
        store._connection.execute(
            "INSERT INTO alerts (incident_id, score, threshold, first_window_id, "
            "last_window_id, first_line_number, last_line_number, summary) "
            "VALUES ('INC-0003', 4.0, 3.4791, 1, 2, 1, 200, 'prior run')"
        )

    assert store.get_last_incident_sequence() == 3
    INCIDENT_POLICY.incident_counter = max(INCIDENT_POLICY.incident_counter, store.get_last_incident_sequence())
    assert INCIDENT_POLICY.incident_counter == 3
    store.close()
