import hashlib
import json
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from zentinel.api.live_api import CURRENT_WINDOW, RECENT_WINDOWS, SECURITY, STATE, app
from zentinel.api.live_store import LiveStateStore

VALID_BGL_LINE = (
    "- 1117838570 2005.06.03 "
    "R02-M1-N0-C:J12-U11 "
    "2005-06-03-15.42.50.363779 "
    "R02-M1-N0-C:J12-U11 "
    "RAS KERNEL INFO instruction cache parity error corrected"
)

ADMIN_KEY = "admin-secret-test-key"
ADMIN_KEY_HASH = hashlib.sha256(ADMIN_KEY.encode("utf-8")).hexdigest()

INGEST_KEY = "ingest-secret-test-key"
INGEST_KEY_HASH = hashlib.sha256(INGEST_KEY.encode("utf-8")).hexdigest()

READ_KEY = "read-secret-test-key"
READ_KEY_HASH = hashlib.sha256(READ_KEY.encode("utf-8")).hexdigest()


@pytest.fixture(autouse=True)
def setup_test_environment(tmp_path):
    """Setup isolated database, state, and security manager for each test."""
    db_path = tmp_path / "test_api.sqlite3"
    store = LiveStateStore(db_path)

    with patch("zentinel.api.live_api.STORE", store), patch("zentinel.api.live_api.CONFIG.db_path", db_path):
        STATE.clear()
        STATE.update({"logs_received": 0, "windows_completed": 0, "unknown_events_received": 0})
        CURRENT_WINDOW.clear()
        RECENT_WINDOWS.clear()

        keys_cfg = f"admin:{ADMIN_KEY_HASH},ingest:{INGEST_KEY_HASH},read:{READ_KEY_HASH}"
        SECURITY.api_keys_config = keys_cfg
        SECURITY._load_keys()
        SECURITY.rate_limiter.reset()

        yield store

        store.close()


def test_health_endpoint_is_open_without_auth():
    with TestClient(app) as client:
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert "window_size" in data


def test_readiness_endpoint_no_sensitive_info():
    with TestClient(app) as client:
        response = client.get("/ready")
        assert response.status_code == 200
        data = response.json()
        assert data["ready"] is True
        assert data["engine"] is True
        assert data["persistence"] is True
        payload_text = json.dumps(data)
        assert "database" not in payload_text
        assert "ZENTINEL" not in payload_text


def test_missing_api_key_returns_401():
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/live/ingest",
            json={"raw_line": VALID_BGL_LINE},
        )
        assert response.status_code == 401
        data = response.json()
        assert "error" in data
        assert data["error"]["code"] == 401
        assert "request_id" in data


def test_invalid_api_key_returns_401():
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/live/ingest",
            json={"raw_line": VALID_BGL_LINE},
            headers={"X-API-Key": "wrong-key"},
        )
        assert response.status_code == 401
        data = response.json()
        assert data["error"]["code"] == 401


def test_insufficient_role_returns_403():
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/live/ingest",
            json={"raw_line": VALID_BGL_LINE},
            headers={"X-API-Key": READ_KEY},
        )
        assert response.status_code == 403
        data = response.json()
        assert "cannot perform 'ingest'" in data["detail"]


def test_invalid_bgl_line_returns_422():
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/live/ingest",
            json={"raw_line": "not a valid log line"},
            headers={"X-API-Key": INGEST_KEY},
        )
        assert response.status_code == 422
        data = response.json()
        assert "Invalid BGL log line" in data["detail"]
        assert "request_id" in data


def test_successful_ingest_updates_state():
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/live/ingest",
            json={"raw_line": VALID_BGL_LINE},
            headers={"X-API-Key": INGEST_KEY},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["window_completed"] is False
        assert data["lines_in_current_window"] == 1
        assert data["lines_remaining"] == 99
        assert data["event"]["line_number"] == 1


def test_idempotency_replay_and_collision_409():
    with TestClient(app) as client:
        key = "test-idemp-key-1"
        resp1 = client.post(
            "/api/v1/live/ingest",
            json={"raw_line": VALID_BGL_LINE, "idempotency_key": key},
            headers={"X-API-Key": INGEST_KEY},
        )
        assert resp1.status_code == 200
        data1 = resp1.json()
        assert data1.get("idempotency_replayed") is None

        resp2 = client.post(
            "/api/v1/live/ingest",
            json={"raw_line": VALID_BGL_LINE, "idempotency_key": key},
            headers={"X-API-Key": INGEST_KEY},
        )
        assert resp2.status_code == 200
        data2 = resp2.json()
        assert data2.get("idempotency_replayed") is True
        assert data2["event"]["line_number"] == data1["event"]["line_number"]

        different_line = VALID_BGL_LINE.replace("instruction cache", "data cache")
        resp3 = client.post(
            "/api/v1/live/ingest",
            json={"raw_line": different_line, "idempotency_key": key},
            headers={"X-API-Key": INGEST_KEY},
        )
        assert resp3.status_code == 409
        data3 = resp3.json()
        assert "collision" in data3["detail"].lower()


def test_db_failure_rolls_back_and_prevents_state_divergence(setup_test_environment):
    store = setup_test_environment
    with TestClient(app, raise_server_exceptions=False) as client:
        assert STATE["logs_received"] == 0
        assert len(CURRENT_WINDOW) == 0

        with (
            patch.object(
                store,
                "save_event_and_state",
                side_effect=RuntimeError("Simulated DB Disk Full"),
            ),
            patch("zentinel.api.live_api.STORE", store),
        ):
            resp = client.post(
                "/api/v1/live/ingest",
                json={"raw_line": VALID_BGL_LINE},
                headers={"X-API-Key": INGEST_KEY},
            )
            assert resp.status_code == 500

        assert STATE["logs_received"] == 0
        assert len(CURRENT_WINDOW) == 0

        with patch("zentinel.api.live_api.STORE", store):
            retry_resp = client.post(
                "/api/v1/live/ingest",
                json={"raw_line": VALID_BGL_LINE},
                headers={"X-API-Key": INGEST_KEY},
            )
            assert retry_resp.status_code == 200
            retry_data = retry_resp.json()
            assert retry_data["event"]["line_number"] == 1
            assert STATE["logs_received"] == 1
            assert len(CURRENT_WINDOW) == 1


def test_hundredth_line_completes_window():
    with TestClient(app) as client:
        for i in range(1, 100):
            r = client.post(
                "/api/v1/live/ingest",
                json={"raw_line": VALID_BGL_LINE},
                headers={"X-API-Key": INGEST_KEY},
            )
            assert r.status_code == 200
            assert r.json()["window_completed"] is False

        r100 = client.post(
            "/api/v1/live/ingest",
            json={"raw_line": VALID_BGL_LINE},
            headers={"X-API-Key": INGEST_KEY},
        )
        assert r100.status_code == 200
        d100 = r100.json()
        assert d100["window_completed"] is True
        assert d100["lines_in_current_window"] == 0
        assert d100["window_decision"] is not None
        assert d100["window_decision"]["window_id"] == 1
        assert d100["window_decision"]["lines_processed"] == 100


def test_rate_limiting_enforced():
    with TestClient(app) as client:
        SECURITY.rate_limiter.rps = 1.0
        SECURITY.rate_limiter.burst = 2
        SECURITY.rate_limiter.reset()

        r1 = client.get("/api/v1/live/status", headers={"X-API-Key": READ_KEY})
        assert r1.status_code == 200
        r2 = client.get("/api/v1/live/status", headers={"X-API-Key": READ_KEY})
        assert r2.status_code == 200

        r3 = client.get("/api/v1/live/status", headers={"X-API-Key": READ_KEY})
        assert r3.status_code == 429
        data = r3.json()
        assert "Rate limit exceeded" in data["detail"]


def test_uniform_error_json_structure():
    with TestClient(app) as client:
        response = client.get("/nonexistent-endpoint", headers={"X-API-Key": READ_KEY})
        assert response.status_code == 404
        data = response.json()
        assert "error" in data
        assert "code" in data["error"]
        assert "message" in data["error"]
        assert "request_id" in data
        assert "traceback" not in str(data).lower()
