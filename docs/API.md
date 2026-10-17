# Zentinel AIOps: REST API Reference

The Zentinel AIOps service exposes a high-performance RESTful API built on FastAPI. It handles real-time log ingestion, window decisioning, incident lifecycle management, retraining queue curation, and Prometheus metrics exposition.

- **Base URL**: `http://127.0.0.1:8000`
- **Interactive Documentation**: `http://127.0.0.1:8000/docs` (Swagger UI)
- **OpenAPI JSON**: `http://127.0.0.1:8000/openapi.json`

---

## 1. Authentication & Security

### 1.1 API Key Header
Protected endpoints require an `X-API-Key` HTTP header:
```http
X-API-Key: zentinel-operator-key-v1
```
- Keys are validated against `ZENTINEL_API_KEY_SHA256` environment variables.
- Verification uses constant-time comparison (`hmac.compare_digest`) to prevent timing side-channel attacks.
- Three granular role scopes are supported: `ingest`, `read`, and `admin`.

### 1.2 Rate Limiting
- Protected endpoints enforce an in-memory token bucket rate limiter per API key (default: 1,000 requests/sec with burst capacity).
- Exceeding the rate limit returns `HTTP 429 Too Many Requests`.

### 1.3 Uniform Error Envelope
Errors return a standardized JSON structure with a traceable `request_id`:
```json
{
  "status": "error",
  "request_id": "req-9b81f3d8a7",
  "code": "AUTHENTICATION_FAILED",
  "detail": "Invalid or missing X-API-Key."
}
```

---

## 2. Ingestion Endpoints

### 2.1 Single Log Event Ingestion
Ingests a single raw log line, matches templates, and returns the window decision on the 100th event.

- **Method**: `POST`
- **Path**: `/api/v1/live/ingest`
- **Headers**: `X-API-Key` (role: `ingest` or `admin`), `X-Idempotency-Key` (optional)
- **Request Body**:
```json
{
  "raw_line": "- 1117838570 2005.06.03 R02-M1-N0-C:J12-U11 2005-06-03-15.42.50.363779 R02-M1-N0-C:J12-U11 RAS KERNEL INFO 1445763 double-hummer alignment exceptions"
}
```
- **Response** (`HTTP 200 OK` on standard line):
```json
{
  "status": "ingested",
  "window_id": 42,
  "window_position": 87,
  "events_in_current_window": 87,
  "event": {
    "line_number": 4287,
    "timestamp": 1117838570,
    "token": 5,
    "event_status": "matched",
    "template": "RAS KERNEL INFO <*> double-hummer alignment exceptions"
  },
  "window_decision": null
}
```
- **Response on 100th Line** (completes window):
```json
{
  "status": "window_completed",
  "window_id": 42,
  "window_decision": {
    "window_id": 42,
    "decision": "NO_ANOMALY_DETECTED",
    "decision_reason": "BELOW_ANOMALY_AND_NOVELTY_THRESHOLDS",
    "lstm_anomaly_score": 0.4215,
    "unknown_event_rate": 0.01,
    "known_transitions_scored": 97
  }
}
```

### 2.2 High-Throughput Batch Ingestion
Processes up to 500 lines inside a single database transaction.

- **Method**: `POST`
- **Path**: `/api/v1/live/ingest/batch`
- **Request Body**:
```json
{
  "lines": [
    "- 1117838570 2005.06.03 R02-M1-N0-C:J12-U11 2005-06-03-15.42.50.363779 RAS KERNEL INFO 100 exceptions",
    "- 1117838571 2005.06.03 R02-M1-N0-C:J12-U11 2005-06-03-15.42.51.363779 RAS KERNEL INFO 101 exceptions"
  ]
}
```
- **Response**:
```json
{
  "status": "batch_completed",
  "events_ingested": 2,
  "windows_completed": 0,
  "latest_window_id": 42
}
```

---

## 3. Incident & Alert Management

### 3.1 List Alerts
- **Method**: `GET`
- **Path**: `/api/v1/alerts`
- **Query Parameters**:
  - `status`: `OPEN`, `ACKNOWLEDGED`, `CLOSED` (optional)
  - `severity`: `LOW`, `MEDIUM`, `HIGH` (optional)
  - `limit`: integer (default: 50)
- **Response**:
```json
[
  {
    "id": "INC-0001",
    "status": "OPEN",
    "severity": "HIGH",
    "peak_score": 5.482,
    "start_window_id": 104,
    "end_window_id": 106,
    "opened_at": "2026-10-17T14:20:00Z",
    "updated_at": "2026-10-17T14:23:00Z"
  }
]
```

### 3.2 Alert Drilldown with Evidence
- **Method**: `GET`
- **Path**: `/api/v1/alerts/{alert_id}`
- **Response**:
```json
{
  "id": "INC-0001",
  "status": "OPEN",
  "severity": "HIGH",
  "peak_score": 5.482,
  "evidence": {
    "top_surprising_templates": [
      {"template": "RAS KERNEL FATAL memory parity error", "loss": 8.92, "count": 4}
    ],
    "first_seen_templates": [],
    "unknown_event_rate": 0.00,
    "critical_event_counts": {"FATAL": 4, "SEVERE": 2},
    "referenced_log_lines": [10405, 10406, 10412]
  },
  "actions": [
    {"action": "OPEN", "actor": "system", "created_at": "2026-10-17T14:20:00Z"}
  ]
}
```

### 3.3 Acknowledge Alert
- **Method**: `POST`
- **Path**: `/api/v1/alerts/{alert_id}/ack`
- **Request Body**: `{"actor": "operator_jane"}`

### 3.4 Close Alert
- **Method**: `POST`
- **Path**: `/api/v1/alerts/{alert_id}/close`
- **Request Body**:
```json
{
  "actor": "operator_jane",
  "reason": "Hardware card reseated; error sequence resolved."
}
```

---

## 4. Retraining & Observability Endpoints

### 4.1 Retraining Review Queue
Returns unmatched log messages exceeding drift thresholds.
- **Method**: `GET`
- **Path**: `/api/v1/review`
- **Query Parameter**: `format=json` (default) or `format=csv` (triggers direct file download).

### 4.2 Prometheus Metrics
- **Method**: `GET`
- **Path**: `/api/v1/metrics`
- **Response**: Prometheus exposition text format tracking ingested events, completed windows, active incidents, and latency histograms.

### 4.3 Active Model Registry
- **Method**: `GET`
- **Path**: `/api/v1/models`
- **Response**: List of registered models, ONNX export versions, and cryptographic SHA-256 digests.

### 4.4 System Health Check (Open)
- **Method**: `GET`
- **Path**: `/health`
- **Response**:
```json
{
  "status": "ok",
  "mode": "live_ingestion",
  "frozen_drain_state_loaded": true,
  "lstm_loaded": true,
  "vocabulary_size": 616,
  "window_size": 100,
  "lstm_threshold": 3.4791,
  "novelty_threshold": 0.02
}
```
