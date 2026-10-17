import csv
import hashlib
import io
import os
import threading
import uuid
from collections import deque
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from zentinel.alerts.explanations import generate_incident_evidence
from zentinel.alerts.policy import IncidentPolicy
from zentinel.api.live_store import LiveStateStore
from zentinel.api.security import SecurityManager
from zentinel.config import (
    MAX_RECENT_WINDOWS,
    UNKNOWN_TOKEN,
    WINDOW_SIZE,
    ZentinelConfig,
    verify_manifest,
)
from zentinel.models.inference import InferenceEngine
from zentinel.parsing.drain_parser import parse_bgl_line

# In-memory streaming state protected by mutex
STATE_LOCK = threading.Lock()
CURRENT_WINDOW: list[dict[str, Any]] = []
RECENT_WINDOWS: deque = deque(maxlen=MAX_RECENT_WINDOWS)
STATE = {
    "logs_received": 0,
    "windows_completed": 0,
    "unknown_events_received": 0,
}

CONFIG = ZentinelConfig()
ENGINE = InferenceEngine()
STORE: LiveStateStore | None = None
SECURITY = SecurityManager(
    api_keys_config=CONFIG.api_key_sha256,
    rate_limit_rps=CONFIG.rate_limit_rps,
    rate_limit_burst=CONFIG.rate_limit_burst,
)
INCIDENT_POLICY = IncidentPolicy(threshold=3.4791)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    FastAPI lifespan context manager:
    1. Verifies single worker process (WEB_CONCURRENCY <= 1).
    2. Verifies cryptographic SHA-256 integrity of all artifacts against manifest.json.
    3. Initializes inference engine, model weights, and SQLite state store.
    """
    global STORE

    web_concurrency = int(os.getenv("WEB_CONCURRENCY", "1"))
    if web_concurrency > 1:
        raise RuntimeError(
            f"WEB_CONCURRENCY is set to {web_concurrency}. Zentinel must run "
            "with a single worker (WEB_CONCURRENCY <= 1) to maintain window state consistency."
        )

    # Verify frozen artifact integrity
    verify_manifest()

    # Load frozen Drain3 matcher and LSTM next-event model
    ENGINE.load()
    INCIDENT_POLICY.threshold = ENGINE.lstm_threshold
    INCIDENT_POLICY.hysteresis_threshold = ENGINE.lstm_threshold * 0.70

    # Initialize SQLite store and restore previous state
    STORE = LiveStateStore(CONFIG.db_path)
    restored = STORE.load_state(recent_limit=MAX_RECENT_WINDOWS)

    with STATE_LOCK:
        STATE.clear()
        STATE.update(restored["state"])
        CURRENT_WINDOW.clear()
        CURRENT_WINDOW.extend(restored["current_window"])
        RECENT_WINDOWS.clear()
        RECENT_WINDOWS.extend(restored["recent_windows"])

    # Restore incident numbering so restarted processes never replay incident IDs
    INCIDENT_POLICY.incident_counter = max(
        INCIDENT_POLICY.incident_counter,
        STORE.get_last_incident_sequence(),
    )

    # Register default active model in model registry
    STORE.register_model(
        model_id="lstm_next_event_v1",
        name="LSTM Next-Event Predictor",
        version="1.0.0",
        model_type="LSTMNextEvent",
        artifact_path="artifacts/lstm_next_event.pt",
        threshold=ENGINE.lstm_threshold,
        metrics={
            "validation_pr_auc": 0.9279,
            "validation_f1": 0.8482,
            "validation_precision": 0.9574,
            "validation_recall": 0.7613,
            "exploratory_test_precision": 0.8173,
            "coverage": 0.3322,
        },
    )

    yield

    # Teardown
    if STORE is not None:
        STORE.close()


app = FastAPI(
    title="Zentinel Live Inference API",
    description=(
        "Enterprise log anomaly detection with frozen Drain3 template matching, "
        "known-run LSTM scoring, and novelty/drift review."
    ),
    version="0.4.0",
    lifespan=lifespan,
)

# CORS Configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=CONFIG.cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


@app.middleware("http")
async def add_request_id_middleware(request: Request, call_next):
    """Ensure every request has a request ID attached."""
    req_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
    request.state.request_id = req_id
    response = await call_next(request)
    response.headers["X-Request-ID"] = req_id
    return response


# --- Uniform Error Handlers (Suppresses stack traces, includes request_id) ---


@app.exception_handler(StarletteHTTPException)
async def starlette_http_exception_handler(request: Request, exc: StarletteHTTPException):
    req_id = getattr(request.state, "request_id", uuid.uuid4().hex)
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": {
                "code": exc.status_code,
                "message": exc.detail,
            },
            "detail": exc.detail,
            "request_id": req_id,
        },
        headers={"X-Request-ID": req_id},
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    req_id = getattr(request.state, "request_id", uuid.uuid4().hex)
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": {
                "code": exc.status_code,
                "message": exc.detail,
            },
            "detail": exc.detail,
            "request_id": req_id,
        },
        headers={"X-Request-ID": req_id},
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    req_id = getattr(request.state, "request_id", uuid.uuid4().hex)
    return JSONResponse(
        status_code=422,
        content={
            "error": {
                "code": 422,
                "message": "Validation Error: Invalid request payload",
                "details": exc.errors(),
            },
            "detail": f"Validation Error: {exc}",
            "request_id": req_id,
        },
        headers={"X-Request-ID": req_id},
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    req_id = getattr(request.state, "request_id", uuid.uuid4().hex)
    return JSONResponse(
        status_code=500,
        content={
            "error": {
                "code": 500,
                "message": "Internal server error",
            },
            "detail": "An unexpected error occurred. Reference request_id.",
            "request_id": req_id,
        },
        headers={"X-Request-ID": req_id},
    )


# --- Security and Role Dependencies ---


def get_current_role(
    required_role: str,
    x_api_key: str | None = Header(None, alias="X-API-Key"),
    request: Request = None,
) -> str:
    """Validate API key role and enforce rate limits."""
    client_ip = request.client.host if request and request.client else "unknown"
    rate_key = x_api_key or client_ip
    SECURITY.check_rate_limit(rate_key)

    return SECURITY.verify_api_key(x_api_key, required_role=required_role)


def require_ingest(
    x_api_key: str | None = Header(None, alias="X-API-Key"),
    request: Request = None,
) -> str:
    return get_current_role("ingest", x_api_key, request)


def require_read(
    x_api_key: str | None = Header(None, alias="X-API-Key"),
    request: Request = None,
) -> str:
    return get_current_role("read", x_api_key, request)


def require_admin(
    x_api_key: str | None = Header(None, alias="X-API-Key"),
    request: Request = None,
) -> str:
    return get_current_role("admin", x_api_key, request)


# --- Request & Response Models with OpenAPI Examples ---


class IngestRequest(BaseModel):
    raw_line: str = Field(
        min_length=1,
        max_length=20000,
        description="Single raw BGL-format log line",
        json_schema_extra={
            "example": "- 1117838570 2005.06.03 R02-M1-N0-C:J12-U11 2005-06-03-15.42.50.363779 R02-M1-N0-C:J12-U11 RAS KERNEL INFO instruction cache parity error corrected"
        },
    )
    idempotency_key: str | None = Field(
        default=None,
        min_length=1,
        max_length=512,
        description="Unique deduplication key: sha256(file_id:offset)",
        json_schema_extra={"example": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"},
    )


class BatchIngestRequest(BaseModel):
    lines: list[str] = Field(
        ...,
        min_length=1,
        max_length=500,
        description="Array of up to 500 BGL log lines to ingest sequentially",
    )
    source_id: str = Field(default="default", max_length=64, description="Identifier for log source or component")


class BatchIngestResponse(BaseModel):
    ingested_count: int = Field(description="Number of valid lines successfully processed")
    rejected_count: int = Field(description="Number of invalid lines rejected")
    completed_windows: int = Field(description="Number of 100-line windows completed")
    alerts_generated: int = Field(description="Number of new or updated incidents")
    decisions: list[dict[str, Any]] = Field(description="Window decision results completed in this batch")


class AlertActionRequest(BaseModel):
    actor: str = Field(default="operator", description="Operator name or service identifier")
    comment: str = Field(default="", description="Operator note explaining the action")


class AlertCloseRequest(BaseModel):
    actor: str = Field(default="operator", description="Operator name or service identifier")
    comment: str = Field(default="", description="Closure rationale")
    reason: str = Field(
        default="RESOLVED",
        description="Resolution reason (RESOLVED, FALSE_POSITIVE)",
        pattern="^(RESOLVED|FALSE_POSITIVE)$",
    )


# --- Endpoints ---


@app.get("/", tags=["System"])
def root():
    return {
        "service": "Zentinel Live Inference API",
        "mode": "live_ingestion",
        "health": "/health",
        "documentation": "/docs",
        "version": "0.4.0",
    }


@app.get("/health", tags=["System"])
def health():
    """Health check endpoint: open to public monitoring without auth."""
    vocab_size = int(ENGINE.vocab.get("vocabulary_size", 0)) if ENGINE._is_loaded and ENGINE.vocab else 616
    return {
        "status": "ok",
        "mode": "live_ingestion",
        "frozen_drain_state_loaded": ENGINE._is_loaded and ENGINE.miner is not None,
        "lstm_loaded": ENGINE._is_loaded and ENGINE.model is not None,
        "vocabulary_size": vocab_size,
        "window_size": WINDOW_SIZE,
        "lstm_threshold": ENGINE.lstm_threshold,
        "novelty_threshold": ENGINE.novelty_threshold,
    }


@app.post("/api/v1/live/ingest", tags=["Live Ingestion"])
def ingest_log(
    request: IngestRequest,
    role: str = Depends(require_ingest),
):
    """
    Ingest one raw BGL-format line.
    Enforces idempotency collision detection, transactional SQLite commit,
    and memory state consistency.
    """
    store = STORE or LiveStateStore(CONFIG.db_path)
    if not ENGINE._is_loaded:
        ENGINE.load()

    payload_sha256 = hashlib.sha256(request.raw_line.encode("utf-8")).hexdigest()

    with STATE_LOCK:
        # Check idempotency
        if request.idempotency_key:
            record = store.get_idempotency_record(request.idempotency_key)
            if record is not None:
                # Key exists: check for payload collision
                if record["payload_sha256"] != payload_sha256:
                    raise HTTPException(
                        status_code=409,
                        detail=("Idempotency key collision: key already used with different payload"),
                    )
                cached = dict(record["response"])
                cached["idempotency_replayed"] = True
                return cached

        # Prepare state mutation on candidate copies (prevents state divergence on DB failure)
        candidate_state = dict(STATE)
        candidate_window = list(CURRENT_WINDOW)

        next_line_number = candidate_state["logs_received"] + 1

        try:
            record = parse_bgl_line(
                request.raw_line,
                line_number=next_line_number,
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail=f"Invalid BGL log line: {exc}",
            ) from exc

        match = ENGINE.map_event_to_token(record)

        event = {
            "line_number": next_line_number,
            "timestamp": int(record.timestamp),
            "parse_status": record.parse_status,
            "token": int(match["token"]),
            "event_status": match["event_status"],
            "matched_event_id": match["matched_event_id"],
            "template": match["template"],
        }

        candidate_state["logs_received"] = next_line_number
        if event["token"] == UNKNOWN_TOKEN:
            candidate_state["unknown_events_received"] += 1

        candidate_window.append(event)

        response = {
            "mode": "live_ingestion",
            "event": event,
            "window_completed": False,
            "lines_in_current_window": len(candidate_window),
            "lines_remaining": WINDOW_SIZE - len(candidate_window),
            "window_decision": None,
        }

        window_result = None
        new_recent_window = None

        if len(candidate_window) == WINDOW_SIZE:
            completed_events = list(candidate_window)
            decision = ENGINE.decide_window(completed_events)

            candidate_state["windows_completed"] += 1

            window_result = {
                "window_id": candidate_state["windows_completed"],
                "first_line_number": completed_events[0]["line_number"],
                "last_line_number": completed_events[-1]["line_number"],
                "lines_processed": len(completed_events),
                **decision,
            }

            # Generate evidence & evaluate incident policy
            evidence = generate_incident_evidence(
                completed_events,
                window_result,
                model=ENGINE.model,
            )
            incident, action = INCIDENT_POLICY.process_window(window_result, evidence=evidence)
            if incident is not None:
                store.save_or_update_alert(incident.to_dict())
                window_result["incident_id"] = incident.incident_id
                window_result["incident_action"] = action

            new_recent_window = window_result
            candidate_window.clear()

            response["window_completed"] = True
            response["lines_in_current_window"] = 0
            response["lines_remaining"] = WINDOW_SIZE
            response["window_decision"] = window_result

        # Phase 1: Commit to SQLite atomically first
        store.save_event_and_state(
            event=event,
            state=candidate_state,
            current_window=candidate_window,
            window_result=window_result,
            idempotency_key=request.idempotency_key,
            payload_sha256=payload_sha256 if request.idempotency_key else None,
            response=response,
        )

        # Phase 2: ONLY upon successful DB commit, publish candidate state to memory
        STATE.clear()
        STATE.update(candidate_state)
        CURRENT_WINDOW.clear()
        CURRENT_WINDOW.extend(candidate_window)
        if new_recent_window is not None:
            RECENT_WINDOWS.append(new_recent_window)

        return response


@app.post(
    "/api/v1/live/ingest/batch",
    response_model=BatchIngestResponse,
    tags=["Live Ingestion"],
)
def ingest_batch(
    request: BatchIngestRequest,
    role: str = Depends(require_ingest),
):
    """
    Ingest a batch of up to 500 BGL log lines sequentially.
    Completed windows generate decisions and update incidents.
    """
    ingested_count = 0
    rejected_count = 0
    decisions = []
    alerts_count = 0

    for raw in request.lines:
        line_clean = raw.strip()
        if not line_clean:
            continue

        try:
            resp = ingest_log(
                IngestRequest(raw_line=line_clean),
                role=role,
            )
            ingested_count += 1
            if resp.get("window_completed") and resp.get("window_decision"):
                w = resp["window_decision"]
                decisions.append(w)
                if w.get("decision") == "ANOMALY_ALERT":
                    alerts_count += 1
        except HTTPException as exc:
            if exc.status_code == 422:
                rejected_count += 1
            else:
                raise

    return BatchIngestResponse(
        ingested_count=ingested_count,
        rejected_count=rejected_count,
        completed_windows=len(decisions),
        alerts_generated=alerts_count,
        decisions=decisions,
    )


@app.get("/api/v1/live/status", tags=["Live Ingestion"])
def live_status(role: str = Depends(require_read)):
    """Return live ingestion counters, window state, and persistence details."""
    store = STORE
    open_alerts = 0
    if store is not None:
        open_alerts = len(store.get_alerts(status="OPEN", severity=None, limit=200, offset=0))

    with STATE_LOCK:
        current_unknown = sum(event["token"] == UNKNOWN_TOKEN for event in CURRENT_WINDOW)

        return {
            "mode": "live_ingestion",
            "logs_received": int(STATE["logs_received"]),
            "windows_completed": int(STATE["windows_completed"]),
            "unknown_events_received": int(STATE["unknown_events_received"]),
            "current_window_size": len(CURRENT_WINDOW),
            "current_window_unknown_count": int(current_unknown),
            "current_window_unknown_rate": (float(current_unknown / len(CURRENT_WINDOW)) if CURRENT_WINDOW else 0.0),
            "window_size": WINDOW_SIZE,
            "recent_windows_retained": len(RECENT_WINDOWS),
            "open_alerts": int(open_alerts),
            "persistence": "sqlite",
            "database": str(CONFIG.db_path),
            "active_incident": (
                INCIDENT_POLICY.current_incident.to_dict() if INCIDENT_POLICY.current_incident else None
            ),
        }


@app.get("/api/v1/live/windows", tags=["Live Ingestion"])
def recent_windows(
    limit: int = Query(default=20, ge=1, le=500),
    decision: str | None = Query(
        default=None,
        description="Filter by decision (ANOMALY_ALERT, NO_ANOMALY_DETECTED, NOVELTY_DRIFT_REVIEW)",
    ),
    offset: int = Query(default=0, ge=0),
    role: str = Depends(require_read),
):
    """Retrieve persisted window decisions, newest first, with optional filtering."""
    store = STORE
    if store is None:
        # Fallback to in-memory ring before the store is wired (e.g. early startup)
        with STATE_LOCK:
            items = list(RECENT_WINDOWS)[-limit:]
            items.reverse()
    else:
        items = store.get_windows_page(decision=decision, limit=limit, offset=offset)

    return {
        "count": len(items),
        "limit": limit,
        "offset": offset,
        "decision": decision,
        "items": items,
    }


@app.get("/api/v1/dashboards/summary", tags=["Live Ingestion"])
def dashboard_summary(role: str = Depends(require_read)):
    """
    Single aggregated feed for the operations dashboard:
    counters, KPIs, decision distribution, incidents, drift, and thresholds.
    """
    store = STORE
    with STATE_LOCK:
        logs_total = int(STATE["logs_received"])
        windows_total = int(STATE["windows_completed"])
        unknown_total = int(STATE["unknown_events_received"])
        current_unknown = sum(e["token"] == UNKNOWN_TOKEN for e in CURRENT_WINDOW)
        current_window_size = len(CURRENT_WINDOW)
        current_unknown_rate = (current_unknown / current_window_size) if current_window_size else 0.0

    decision_counts_recent: dict[str, int] = {}
    decision_counts_all: dict[str, int] = {}
    review_count = 0
    open_alerts = 0
    recent_alerts: list[dict[str, Any]] = []

    if store is not None:
        decision_counts_recent = store.get_decision_distribution(recent_limit=100)
        decision_counts_all = store.get_decision_distribution()
        review_count = len(store.get_review_windows(limit=200))
        alerts_page = store.get_alerts(status="OPEN", severity=None, limit=200, offset=0)
        open_alerts = len(alerts_page)
        recent_alerts = store.get_alerts(status=None, severity=None, limit=10, offset=0)

    anomaly_count = decision_counts_recent.get("ANOMALY_ALERT", 0)
    recent_total = sum(decision_counts_recent.values())
    anomaly_rate_recent = (anomaly_count / recent_total) if recent_total else 0.0

    return {
        "counters": {
            "logs_received": logs_total,
            "windows_completed": windows_total,
            "unknown_events_received": unknown_total,
        },
        "window_state": {
            "window_size": WINDOW_SIZE,
            "current_window_size": current_window_size,
            "current_window_unknown_rate": current_unknown_rate,
        },
        "kpis": {
            "anomaly_rate_recent_100": anomaly_rate_recent,
            "anomaly_windows_recent_100": anomaly_count,
            "recent_windows_sampled": recent_total,
            "open_alerts": open_alerts,
            "review_windows_total": review_count,
        },
        "decisions": {
            "recent_100": decision_counts_recent,
            "all_time": decision_counts_all,
        },
        "active_incident": (INCIDENT_POLICY.current_incident.to_dict() if INCIDENT_POLICY.current_incident else None),
        "recent_alerts": recent_alerts,
        "thresholds": {
            "lstm_threshold": ENGINE.lstm_threshold,
            "hysteresis_threshold": ENGINE.lstm_threshold * 0.70,
            "novelty_threshold": ENGINE.novelty_threshold,
        },
    }


# --- Alert Management Endpoints ---


@app.get("/api/v1/alerts", tags=["Alerts"])
def list_alerts(
    status: str | None = Query(None, description="Filter by status (OPEN, ACKNOWLEDGED, CLOSED)"),
    severity: str | None = Query(None, description="Filter by severity (LOW, MEDIUM, HIGH)"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    role: str = Depends(require_read),
):
    """List operational alerts and incidents with optional filtering."""
    store = STORE or LiveStateStore(CONFIG.db_path)
    alerts = store.get_alerts(status=status, severity=severity, limit=limit, offset=offset)
    return {
        "count": len(alerts),
        "limit": limit,
        "offset": offset,
        "items": alerts,
    }


@app.get("/api/v1/alerts/{alert_id}", tags=["Alerts"])
def get_alert_detail(
    alert_id: str,
    role: str = Depends(require_read),
):
    """Get single alert details including evidence and action history."""
    store = STORE or LiveStateStore(CONFIG.db_path)
    alert = store.get_alert(alert_id)
    if not alert:
        raise HTTPException(status_code=404, detail=f"Alert {alert_id} not found")
    return alert


@app.post("/api/v1/alerts/{alert_id}/ack", tags=["Alerts"])
def acknowledge_alert(
    alert_id: int,
    action: AlertActionRequest,
    role: str = Depends(require_admin),
):
    """Acknowledge an open alert/incident."""
    store = STORE or LiveStateStore(CONFIG.db_path)
    alert = store.get_alert(alert_id)
    if not alert:
        raise HTTPException(status_code=404, detail=f"Alert {alert_id} not found")

    updated = store.add_alert_action(
        alert_id=alert_id,
        action_type="ACKNOWLEDGE",
        actor=action.actor,
        comment=action.comment,
    )
    return updated


@app.post("/api/v1/alerts/{alert_id}/close", tags=["Alerts"])
def close_alert(
    alert_id: int,
    action: AlertCloseRequest,
    role: str = Depends(require_admin),
):
    """Close an alert/incident (marked as RESOLVED or FALSE_POSITIVE)."""
    store = STORE or LiveStateStore(CONFIG.db_path)
    alert = store.get_alert(alert_id)
    if not alert:
        raise HTTPException(status_code=404, detail=f"Alert {alert_id} not found")

    updated = store.add_alert_action(
        alert_id=alert_id,
        action_type=action.reason,
        actor=action.actor,
        comment=action.comment,
    )

    # If policy has this as current incident, reset it
    if INCIDENT_POLICY.current_incident and INCIDENT_POLICY.current_incident.incident_id == alert.get("incident_id"):
        INCIDENT_POLICY.current_incident = None

    return updated


# --- Review Queue and Drift Endpoints ---


@app.get("/api/v1/review", tags=["Review Queue"])
def review_queue(
    format: str = Query(default="json", pattern="^(json|csv)$"),
    limit: int = Query(default=50, ge=1, le=200),
    role: str = Depends(require_read),
):
    """
    Retrieve windows routed to NOVELTY_DRIFT_REVIEW.
    Supports CSV export for retraining dataset curation.
    """
    store = STORE or LiveStateStore(CONFIG.db_path)
    windows = store.get_review_windows(limit=limit)

    if format == "csv":
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(
            [
                "window_id",
                "first_line_number",
                "last_line_number",
                "decision",
                "decision_reason",
                "unknown_event_rate",
                "unknown_event_count",
                "known_transitions_scored",
                "lstm_anomaly_score",
                "persisted_at",
            ]
        )
        for w in windows:
            writer.writerow(
                [
                    w.get("window_id"),
                    w.get("first_line_number"),
                    w.get("last_line_number"),
                    w.get("decision"),
                    w.get("decision_reason"),
                    w.get("unknown_event_rate"),
                    w.get("unknown_event_count"),
                    w.get("known_transitions_scored"),
                    w.get("lstm_anomaly_score"),
                    w.get("persisted_at"),
                ]
            )
        csv_content = output.getvalue()
        return Response(
            content=csv_content,
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=zentinel_review_queue.csv"},
        )

    return {
        "count": len(windows),
        "limit": limit,
        "items": windows,
    }


@app.get("/api/v1/drift", tags=["Drift & Diagnostics"])
def drift_diagnostics(role: str = Depends(require_read)):
    """Aggregate template drift diagnostics and unknown event distributions."""
    store = STORE or LiveStateStore(CONFIG.db_path)
    return store.get_drift_summary()


@app.get("/api/v1/models", tags=["Model Registry"])
def list_models(role: str = Depends(require_read)):
    """List registered models, thresholds, and performance metrics."""
    store = STORE or LiveStateStore(CONFIG.db_path)
    return {
        "active_models": store.get_models(),
    }


@app.get("/api/v1/metrics", tags=["Metrics"])
def prometheus_metrics():
    """Expose Prometheus exposition text format metrics."""
    with STATE_LOCK:
        logs_total = int(STATE["logs_received"])
        windows_total = int(STATE["windows_completed"])
        unknown_total = int(STATE["unknown_events_received"])
        curr_unknown = sum(e["token"] == UNKNOWN_TOKEN for e in CURRENT_WINDOW)
        unknown_rate = (curr_unknown / len(CURRENT_WINDOW)) if CURRENT_WINDOW else 0.0

        decision_counts = {"ANOMALY_ALERT": 0, "NO_ANOMALY_DETECTED": 0, "NOVELTY_DRIFT_REVIEW": 0}
        for w in RECENT_WINDOWS:
            dec = w.get("decision")
            if dec in decision_counts:
                decision_counts[dec] += 1

    active_incidents = 1 if INCIDENT_POLICY.current_incident else 0

    lines = [
        "# HELP zentinel_logs_ingested_total Total raw log lines ingested",
        "# TYPE zentinel_logs_ingested_total counter",
        f"zentinel_logs_ingested_total {logs_total}",
        "",
        "# HELP zentinel_windows_completed_total Total windows evaluated",
        "# TYPE zentinel_windows_completed_total counter",
        f"zentinel_windows_completed_total {windows_total}",
        "",
        "# HELP zentinel_windows_decision_total Windows completed per decision category",
        "# TYPE zentinel_windows_decision_total counter",
        f'zentinel_windows_decision_total{{decision="ANOMALY_ALERT"}} {decision_counts["ANOMALY_ALERT"]}',
        f'zentinel_windows_decision_total{{decision="NO_ANOMALY_DETECTED"}} {decision_counts["NO_ANOMALY_DETECTED"]}',
        f'zentinel_windows_decision_total{{decision="NOVELTY_DRIFT_REVIEW"}} {decision_counts["NOVELTY_DRIFT_REVIEW"]}',
        "",
        "# HELP zentinel_unknown_events_total Total unknown events received",
        "# TYPE zentinel_unknown_events_total counter",
        f"zentinel_unknown_events_total {unknown_total}",
        "",
        "# HELP zentinel_current_window_unknown_rate Current in-flight window unknown rate",
        "# TYPE zentinel_current_window_unknown_rate gauge",
        f"zentinel_current_window_unknown_rate {unknown_rate:.4f}",
        "",
        "# HELP zentinel_open_incidents Current number of open anomaly incidents",
        "# TYPE zentinel_open_incidents gauge",
        f"zentinel_open_incidents {active_incidents}",
        "",
        "# HELP zentinel_lstm_threshold Active LSTM anomaly score threshold",
        "# TYPE zentinel_lstm_threshold gauge",
        f"zentinel_lstm_threshold {ENGINE.lstm_threshold:.4f}",
    ]

    return PlainTextResponse(
        content="\n".join(lines) + "\n",
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )


# --- Admin Maintenance Endpoints ---


@app.post("/api/v1/admin/retention", tags=["Administration"])
def trigger_retention(
    idempotency_days: int = Query(default=7, ge=1),
    event_days: int = Query(default=30, ge=1),
    role: str = Depends(require_admin),
):
    """Execute data retention policy purging stale idempotency and event rows."""
    store = STORE or LiveStateStore(CONFIG.db_path)
    purged = store.purge_retention(
        idempotency_retention_days=idempotency_days,
        event_retention_days=event_days,
    )
    return {
        "status": "success",
        "purged": purged,
    }


@app.post("/api/v1/admin/checkpoint", tags=["Administration"])
def trigger_checkpoint(
    mode: str = Query(default="PASSIVE"),
    role: str = Depends(require_admin),
):
    """Trigger SQLite WAL checkpoint."""
    store = STORE or LiveStateStore(CONFIG.db_path)
    result = store.wal_checkpoint(mode=mode)
    return {
        "status": "success",
        "result": result,
    }


@app.post("/api/v1/admin/backup", tags=["Administration"])
def trigger_backup(
    target_path: str = Query(...),
    role: str = Depends(require_admin),
):
    """Online backup of the SQLite database."""
    store = STORE or LiveStateStore(CONFIG.db_path)
    store.backup_database(target_path)
    return {
        "status": "success",
        "backup_path": target_path,
    }
