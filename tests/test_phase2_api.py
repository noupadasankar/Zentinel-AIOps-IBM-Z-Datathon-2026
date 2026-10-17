import hashlib
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

ADMIN_KEY = "admin-secret-test-key"
ADMIN_KEY_HASH = hashlib.sha256(ADMIN_KEY.encode("utf-8")).hexdigest()

INGEST_KEY = "ingest-secret-test-key"
INGEST_KEY_HASH = hashlib.sha256(INGEST_KEY.encode("utf-8")).hexdigest()

READ_KEY = "read-secret-test-key"
READ_KEY_HASH = hashlib.sha256(READ_KEY.encode("utf-8")).hexdigest()

SAMPLE_BGL_LINE = (
    "- 1117838570 2005.06.03 "
    "R02-M1-N0-C:J12-U11 "
    "2005-06-03-15.42.50.363779 "
    "R02-M1-N0-C:J12-U11 "
    "RAS KERNEL INFO instruction cache parity error corrected"
)


@pytest.fixture(autouse=True)
def setup_phase2_env(tmp_path):
    db_path = tmp_path / "test_phase2.sqlite3"
    store = LiveStateStore(db_path)

    with patch("zentinel.api.live_api.STORE", store), patch("zentinel.api.live_api.CONFIG.db_path", db_path):
        STATE.clear()
        STATE.update({"logs_received": 0, "windows_completed": 0, "unknown_events_received": 0})
        CURRENT_WINDOW.clear()
        RECENT_WINDOWS.clear()
        INCIDENT_POLICY.current_incident = None
        INCIDENT_POLICY.incident_counter = 0

        keys_cfg = f"admin:{ADMIN_KEY_HASH},ingest:{INGEST_KEY_HASH},read:{READ_KEY_HASH}"
        SECURITY.api_keys_config = keys_cfg
        SECURITY._load_keys()
        SECURITY.rate_limiter.reset()

        yield store

        store.close()


def test_alerts_crud_and_actions(setup_phase2_env):
    store = setup_phase2_env
    store.save_or_update_alert(
        {
            "incident_id": "INC-0001",
            "status": "OPEN",
            "severity": "HIGH",
            "score": 6.8,
            "threshold": 3.4791,
            "first_window_id": 1,
            "last_window_id": 3,
            "first_line_number": 1,
            "last_line_number": 300,
            "window_count": 3,
            "summary": "HIGH Severity Anomaly: 3 windows",
            "evidence": {"top_k_templates_by_loss": []},
        }
    )

    with TestClient(app) as client:
        r_list = client.get("/api/v1/alerts", headers={"X-API-Key": READ_KEY})
        assert r_list.status_code == 200
        data = r_list.json()
        assert data["count"] == 1
        alert = data["items"][0]
        alert_id = alert["alert_id"]
        assert alert["status"] == "OPEN"
        assert alert["severity"] == "HIGH"

        r_detail = client.get(f"/api/v1/alerts/{alert_id}", headers={"X-API-Key": READ_KEY})
        assert r_detail.status_code == 200
        assert r_detail.json()["incident_id"] == "INC-0001"

        r_ack = client.post(
            f"/api/v1/alerts/{alert_id}/ack",
            json={"actor": "sre-alice", "comment": "Investigating node J12"},
            headers={"X-API-Key": ADMIN_KEY},
        )
        assert r_ack.status_code == 200
        ack_data = r_ack.json()
        assert ack_data["status"] == "ACKNOWLEDGED"
        assert len(ack_data["actions"]) == 1
        assert ack_data["actions"][0]["action_type"] == "ACKNOWLEDGE"

        r_close = client.post(
            f"/api/v1/alerts/{alert_id}/close",
            json={"actor": "sre-alice", "comment": "Hardware repaired", "reason": "RESOLVED"},
            headers={"X-API-Key": ADMIN_KEY},
        )
        assert r_close.status_code == 200
        close_data = r_close.json()
        assert close_data["status"] == "CLOSED"
        assert close_data["closed_at"] is not None


def test_batch_ingest_endpoint():
    with TestClient(app) as client:
        lines = [SAMPLE_BGL_LINE] * 105
        response = client.post(
            "/api/v1/live/ingest/batch",
            json={"lines": lines},
            headers={"X-API-Key": INGEST_KEY},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["ingested_count"] == 105
        assert data["completed_windows"] == 1
        assert len(data["decisions"]) == 1


def test_review_queue_json_and_csv(setup_phase2_env):
    store = setup_phase2_env
    review_window = {
        "window_id": 1,
        "first_line_number": 1,
        "last_line_number": 100,
        "decision": "NOVELTY_DRIFT_REVIEW",
        "decision_reason": "UNKNOWN_RATE_ABOVE_VALIDATION_THRESHOLD",
        "unknown_event_rate": 0.15,
        "unknown_event_count": 15,
        "known_transitions_scored": 80,
        "lstm_anomaly_score": None,
    }
    store.save_event_and_state(
        event={
            "line_number": 1,
            "timestamp": 1000,
            "parse_status": "standard",
            "token": 1,
            "event_status": "unknown_template",
            "matched_event_id": None,
            "template": None,
        },
        state={"logs_received": 100, "windows_completed": 1, "unknown_events_received": 15},
        current_window=[],
        window_result=review_window,
    )

    with TestClient(app) as client:
        r_json = client.get("/api/v1/review?format=json", headers={"X-API-Key": READ_KEY})
        assert r_json.status_code == 200
        assert r_json.json()["count"] == 1
        assert r_json.json()["items"][0]["decision"] == "NOVELTY_DRIFT_REVIEW"

        r_csv = client.get("/api/v1/review?format=csv", headers={"X-API-Key": READ_KEY})
        assert r_csv.status_code == 200
        assert "text/csv" in r_csv.headers["content-type"]
        assert "window_id,first_line_number" in r_csv.text
        assert "NOVELTY_DRIFT_REVIEW" in r_csv.text


def test_prometheus_metrics_endpoint():
    with TestClient(app) as client:
        response = client.get("/api/v1/metrics")
        assert response.status_code == 200
        assert "text/plain" in response.headers["content-type"]
        text = response.text
        assert "zentinel_logs_ingested_total" in text
        assert "zentinel_windows_completed_total" in text
        assert "zentinel_open_incidents" in text
        assert "zentinel_lstm_threshold" in text


def test_models_and_drift_endpoints(setup_phase2_env):
    store = setup_phase2_env
    store.register_model(
        model_id="test_model",
        name="Test LSTM",
        version="1.0.0",
        model_type="LSTMNextEvent",
        artifact_path="artifacts/lstm_next_event.pt",
        threshold=3.4791,
    )

    with TestClient(app) as client:
        r_models = client.get("/api/v1/models", headers={"X-API-Key": READ_KEY})
        assert r_models.status_code == 200
        assert len(r_models.json()["active_models"]) >= 1

        r_drift = client.get("/api/v1/drift", headers={"X-API-Key": READ_KEY})
        assert r_drift.status_code == 200
        assert "drift_status" in r_drift.json()
        assert "validation_threshold" in r_drift.json()
