import hashlib
import os
import threading
import uuid
from collections import deque
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

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
    version="0.3.0",
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


from starlette.exceptions import HTTPException as StarletteHTTPException

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
    # Enforce rate limit per key (or client IP if no key)
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


# --- Request & Response Models ---


class IngestRequest(BaseModel):
    raw_line: str = Field(min_length=1, max_length=20000)
    idempotency_key: str | None = Field(
        default=None,
        min_length=1,
        max_length=512,
    )


# --- Endpoints ---


@app.get("/", tags=["System"])
def root():
    return {
        "service": "Zentinel Live Inference API",
        "mode": "live_ingestion",
        "health": "/health",
        "documentation": "/docs",
    }


@app.get("/health", tags=["System"])
def health():
    """Health check endpoint: open to public monitoring without auth."""
    vocab_size = (
        int(ENGINE.vocab.get("vocabulary_size", 0))
        if ENGINE._is_loaded and ENGINE.vocab
        else 616
    )
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
                        detail=(
                            "Idempotency key collision: key already used with different payload"
                        ),
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


@app.get("/api/v1/live/status", tags=["Live Ingestion"])
def live_status(role: str = Depends(require_read)):
    """Return live ingestion counters, window state, and persistence details."""
    with STATE_LOCK:
        current_unknown = sum(
            event["token"] == UNKNOWN_TOKEN
            for event in CURRENT_WINDOW
        )

        return {
            "mode": "live_ingestion",
            "logs_received": int(STATE["logs_received"]),
            "windows_completed": int(STATE["windows_completed"]),
            "unknown_events_received": int(STATE["unknown_events_received"]),
            "current_window_size": len(CURRENT_WINDOW),
            "current_window_unknown_count": int(current_unknown),
            "current_window_unknown_rate": (
                float(current_unknown / len(CURRENT_WINDOW))
                if CURRENT_WINDOW else 0.0
            ),
            "window_size": WINDOW_SIZE,
            "recent_windows_retained": len(RECENT_WINDOWS),
            "persistence": "sqlite",
            "database": str(CONFIG.db_path),
            "persistence_note": (
                "SQLite persists event metadata, counters, unfinished "
                "windows, and completed-window decisions. Raw log lines "
                "are not stored."
            ),
        }


@app.get("/api/v1/live/windows", tags=["Live Ingestion"])
def recent_windows(
    limit: int = Query(default=20, ge=1, le=100),
    role: str = Depends(require_read),
):
    """Retrieve recently completed window decisions."""
    with STATE_LOCK:
        items = list(RECENT_WINDOWS)[-limit:]
        items.reverse()

        return {
            "count": len(items),
            "items": items,
        }


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
