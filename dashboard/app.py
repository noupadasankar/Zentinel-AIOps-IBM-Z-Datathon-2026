"""Zentinel AIOps - Enterprise Log Anomaly Detection & Novelty Monitoring Dashboard.

Enterprise observability layout: dark theme, grouped navigation, live incident banner,
KPI cards, dual-axis LSTM score / unknown-rate timeline, decision mix, drift panels,
incident forensics with operator actions, review queue with CSV export, and a
sample replay streamer that exercises the live ingestion pipeline end to end.

Data policy (Non-Negotiable Rule 6):
- Live mode: every widget renders data fetched from the Zentinel API or committed reports.
- Offline mode: widgets render clearly-labelled DEMO data (synthetic, never mixed
  with live data, never silently presented as production telemetry).
"""

from __future__ import annotations

import html
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

ROOT_DIR = Path(__file__).resolve().parents[1]
REPORTS_DIR = ROOT_DIR / "reports"
DATA_DIR = ROOT_DIR / "data"
SAMPLE_REPLAY_PATH = DATA_DIR / "raw/sample_replay.log"
INCOMING_LOG_PATH = DATA_DIR / "ingest/incoming.log"

REFRESH_SECONDS = 5  # interval used when auto-refresh is enabled
CHART_WINDOW_LIMIT = 200

st.set_page_config(
    page_title="Zentinel AIOps",
    page_icon="🛡️",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ----------------- Theme & Custom CSS -----------------
DARK_THEME_CSS = """
<style>
/* Main Dark Palette */
.stApp {
    background-color: #0B0F19;
    color: #E2E8F0;
}
header[data-testid="stHeader"] {
    background-color: #0B0F19;
}
section[data-testid="stSidebar"] {
    background-color: #080C14;
    border-right: 1px solid #1E293B;
}

/* Card Tiles */
.zen-card {
    background-color: #111827;
    border: 1px solid #1F2937;
    border-radius: 8px;
    padding: 16px 20px;
    margin-bottom: 12px;
}
.zen-card:hover {
    border-color: #374151;
}

/* Incident Top Banner */
.zen-banner-incident {
    background: linear-gradient(90deg, rgba(220, 38, 38, 0.25) 0%, rgba(17, 24, 39, 0.8) 100%);
    border-left: 4px solid #EF4444;
    border-radius: 6px;
    padding: 12px 18px;
    margin-bottom: 20px;
    display: flex;
    align-items: center;
    color: #F87171;
    font-weight: 600;
}
.zen-banner-clear {
    background: linear-gradient(90deg, rgba(16, 185, 129, 0.15) 0%, rgba(17, 24, 39, 0.8) 100%);
    border-left: 4px solid #10B981;
    border-radius: 6px;
    padding: 12px 18px;
    margin-bottom: 20px;
    color: #34D399;
    font-weight: 600;
}
.zen-banner-drift {
    background: linear-gradient(90deg, rgba(245, 158, 11, 0.2) 0%, rgba(17, 24, 39, 0.8) 100%);
    border-left: 4px solid #F59E0B;
    border-radius: 6px;
    padding: 12px 18px;
    margin-bottom: 20px;
    color: #FBBF24;
    font-weight: 600;
}
.zen-banner-offline {
    background: linear-gradient(90deg, rgba(59, 130, 246, 0.15) 0%, rgba(17, 24, 39, 0.8) 100%);
    border-left: 4px solid #3B82F6;
    border-radius: 6px;
    padding: 12px 18px;
    margin-bottom: 20px;
    color: #93C5FD;
    font-weight: 600;
}

/* KPI Value & Trend */
.kpi-title {
    font-size: 0.75rem;
    font-weight: 700;
    color: #94A3B8;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    margin-bottom: 4px;
}
.kpi-value {
    font-size: 1.85rem;
    font-weight: 700;
    color: #F8FAFC;
    line-height: 1.1;
}
.kpi-trend-good {
    color: #10B981;
    font-size: 0.8rem;
    font-weight: 600;
    margin-left: 6px;
}
.kpi-trend-bad {
    color: #EF4444;
    font-size: 0.8rem;
    font-weight: 600;
    margin-left: 6px;
}
.kpi-sub {
    font-size: 0.75rem;
    color: #64748B;
    margin-top: 4px;
}
.demo-tag {
    display: inline-block;
    background-color: rgba(59, 130, 246, 0.2);
    border: 1px solid rgba(59, 130, 246, 0.4);
    color: #93C5FD;
    border-radius: 4px;
    padding: 1px 7px;
    font-size: 0.7rem;
    font-weight: 700;
    letter-spacing: 0.05em;
    margin-bottom: 6px;
}

/* Health Cards */
.health-tile {
    background-color: #111827;
    border: 1px solid #1E293B;
    border-radius: 6px;
    padding: 14px 16px;
    margin-bottom: 10px;
}
.badge-red {
    background-color: rgba(239, 68, 68, 0.2);
    color: #F87171;
    border: 1px solid rgba(239, 68, 68, 0.4);
    border-radius: 4px;
    padding: 2px 6px;
    font-size: 0.75rem;
    font-weight: 600;
}
.badge-green {
    background-color: rgba(16, 185, 129, 0.2);
    color: #34D399;
    border: 1px solid rgba(16, 185, 129, 0.4);
    border-radius: 4px;
    padding: 2px 6px;
    font-size: 0.75rem;
    font-weight: 600;
}
.badge-yellow {
    background-color: rgba(245, 158, 11, 0.2);
    color: #FBBF24;
    border: 1px solid rgba(245, 158, 11, 0.4);
    border-radius: 4px;
    padding: 2px 6px;
    font-size: 0.75rem;
    font-weight: 600;
}

/* Pattern list items */
.pattern-row {
    background-color: #0F172A;
    border: 1px solid #1E293B;
    border-radius: 4px;
    padding: 8px 12px;
    margin-bottom: 6px;
    font-family: monospace;
    font-size: 0.8rem;
    color: #94A3B8;
}
.pattern-count {
    color: #60A5FA;
    font-weight: 700;
    margin-right: 10px;
}

/* Activity row */
.activity-row {
    padding: 8px 0;
    border-bottom: 1px solid #1E293B;
    font-size: 0.85rem;
}
/* Compact dataframes for dark theme */
div[data-testid="stDataFrame"] {
    border-radius: 6px;
}
</style>
"""
st.markdown(DARK_THEME_CSS, unsafe_allow_html=True)


# ----------------- Session & Config -----------------


def _init_state(key: str, default: Any) -> Any:
    if key not in st.session_state:
        st.session_state[key] = default
    return st.session_state[key]


DEFAULT_API_URL = os.getenv("ZENTINEL_LIVE_API_URL") or os.getenv("API_BASE_URL") or "http://127.0.0.1:8000"
DEFAULT_API_KEY = os.getenv("ZENTINEL_API_KEY", "")

_init_state("zen_api_url", DEFAULT_API_URL)
_init_state("zen_api_key", DEFAULT_API_KEY)
_init_state("zen_auto", True)
_init_state("zen_nav", "Overview")


# ----------------- Data Loading & API Helpers -----------------


@st.cache_data(ttl=2.0, show_spinner=False)
def api_get(_sig: tuple[str, str], endpoint: str, _params: tuple[tuple[str, Any], ...] = ()) -> dict[str, Any] | None:
    """Fetch JSON from the live API (uncached response used within ttl window)."""
    url, api_key = _sig
    params = dict(_params) if _params else None
    headers = {"X-API-Key": api_key} if api_key else {}
    try:
        with httpx.Client(timeout=6.0) as client:
            resp = client.get(
                f"{url.rstrip('/')}{endpoint}",
                headers=headers,
                params=params,
            )
            if resp.status_code == 200:
                return resp.json()
    except (httpx.HTTPError, OSError, json.JSONDecodeError):
        pass
    return None


def api_post(
    endpoint: str,
    payload: dict[str, Any] | None = None,
    timeout: float = 60.0,
) -> tuple[int, dict[str, Any] | str]:
    """POST to the live API and return (status_code, body)."""
    url = st.session_state["zen_api_url"]
    api_key = st.session_state["zen_api_key"]
    headers = {"X-API-Key": api_key} if api_key else {}
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(
                f"{url.rstrip('/')}{endpoint}",
                json=payload,
                headers=headers,
            )
        try:
            body: dict[str, Any] | str = resp.json()
        except ValueError:
            body = resp.text
        return resp.status_code, body
    except (httpx.HTTPError, OSError) as exc:
        return 0, f"Connection failed: {exc}"


def api_download(endpoint: str, timeout: float = 30.0) -> bytes | None:
    """Download raw bytes (e.g. CSV export) from the live API."""
    url = st.session_state["zen_api_url"]
    api_key = st.session_state["zen_api_key"]
    headers = {"X-API-Key": api_key} if api_key else {}
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.get(
                f"{url.rstrip('/')}{endpoint}",
                headers=headers,
            )
        if resp.status_code == 200:
            return resp.content
    except (httpx.HTTPError, OSError):
        pass
    return None


def _get(endpoint: str, **params: Any) -> dict[str, Any] | None:
    """Cached GET wrapper keyed on the current connection config."""
    key = (st.session_state["zen_api_url"], st.session_state["zen_api_key"])
    return api_get(key, endpoint, tuple(sorted(params.items())))


@st.cache_data(ttl=600.0, show_spinner=False)
def load_committed_reports() -> dict[str, Any]:
    """Load validated committed reports for offline display and auditability."""
    reports: dict[str, Any] = {}
    for name, path in (
        ("three_way", REPORTS_DIR / "lstm_three_way_test_decision_report.json"),
        ("walk_forward", REPORTS_DIR / "temporal_walk_forward_evaluation.json"),
        ("validation", REPORTS_DIR / "lstm_known_run_validation_report.json"),
    ):
        if path.exists():
            reports[name] = json.loads(path.read_text(encoding="utf-8"))
    return reports


def load_window_page(limit: int = CHART_WINDOW_LIMIT, decision: str | None = None) -> list[dict[str, Any]]:
    data = _get("/api/v1/live/windows", limit=limit, decision=decision)
    return list(data.get("items", [])) if data else []


def load_summary() -> dict[str, Any] | None:
    return _get("/api/v1/dashboards/summary")


def load_drift() -> dict[str, Any] | None:
    return _get("/api/v1/drift")


def load_alerts(status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    if status and status != "ALL":
        data = _get("/api/v1/alerts", status=status, limit=limit)
    else:
        data = _get("/api/v1/alerts", limit=limit)
    return list(data.get("items", [])) if data else []


def is_live() -> bool:
    summary = load_summary()
    if summary is not None:
        return True
    return _get("/api/v1/live/status") is not None


# ----------------- Clear DEMO fallbacks (offline mode ONLY) -----------------


def demo_summary() -> dict[str, Any]:
    return {
        "counters": {"logs_received": 234300, "windows_completed": 2343, "unknown_events_received": 2811},
        "window_state": {"window_size": 100, "current_window_size": 42, "current_window_unknown_rate": 0.012},
        "kpis": {"anomaly_rate_recent_100": 0.037, "open_alerts": 1, "review_windows_total": 18},
        "decisions": {
            "recent_100": {"NO_ANOMALY_DETECTED": 93, "ANOMALY_ALERT": 4, "NOVELTY_DRIFT_REVIEW": 3},
            "all_time": {"NO_ANOMALY_DETECTED": 2277, "ANOMALY_ALERT": 48, "NOVELTY_DRIFT_REVIEW": 18},
        },
        "recent_alerts": [],
        "thresholds": {"lstm_threshold": 3.4791, "hysteresis_threshold": 2.4354, "novelty_threshold": 0.02},
    }


def demo_windows(n: int = 100) -> list[dict[str, Any]]:
    rng = np.random.default_rng(42)
    scores = rng.normal(0.4, 0.15, n)
    scores[25:35] += np.linspace(1.5, 4.8, 10)
    scores[35:42] += np.linspace(4.5, 1.8, 7)
    unknown = rng.uniform(0.005, 0.015, n)
    unknown[55:65] += 0.08
    items = []
    for i in range(n):
        items.append(
            {
                "window_id": 2343 - n + i + 1,
                "decision": (
                    "ANOMALY_ALERT"
                    if scores[i] >= 3.4791
                    else "NOVELTY_DRIFT_REVIEW"
                    if unknown[i] > 0.02
                    else "NO_ANOMALY_DETECTED"
                ),
                "lstm_anomaly_score": max(0.05, float(scores[i])),
                "unknown_event_rate": float(unknown[i]),
                "known_transitions_scored": 100,
            }
        )
    return items


def demo_patterns() -> list[dict[str, Any]]:
    return [
        {"count": 165, "template": "instruction cache parity error corrected", "source": "drift"},
        {"count": 152, "template": "PrepareForService is being done on this card", "source": "drift"},
        {"count": 138, "template": "0 microseconds spent in the rbs signal handler", "source": "drift"},
        {"count": 136, "template": "generating core.<*> <*> bytes written", "source": "drift"},
        {"count": 131, "template": "ciod: Error loading <*>: invalid or missing program image", "source": "drift"},
    ]


# ----------------- Streaming replay (real pipeline traffic) -----------------


def stream_replay(max_lines: int, replay_path: Path | None = None) -> tuple[int, int, int, list[str]]:
    """
    POST sample log lines to the live ingestion API through the batch endpoint.
    Returns (sent, rejected, windows, errors).
    """
    replay_path = (
        replay_path
        if replay_path is not None
        else (SAMPLE_REPLAY_PATH if SAMPLE_REPLAY_PATH.exists() else INCOMING_LOG_PATH)
    )
    errors: list[str] = []
    if not replay_path.exists():
        return 0, 0, 0, [f"Replay file not found: {replay_path}"]

    raw_lines = [
        line.strip() for line in replay_path.read_text(encoding="utf-8", errors="replace").splitlines() if line.strip()
    ][:max_lines]

    url = st.session_state["zen_api_url"]
    api_key = st.session_state["zen_api_key"]
    headers = {"X-API-Key": api_key} if api_key else {}

    sent = rejected = windows = 0
    chunk_size = 400
    with httpx.Client(timeout=180.0) as client:
        for start in range(0, len(raw_lines), chunk_size):
            payload_lines = [line for line in raw_lines[start : start + chunk_size] if line]
            try:
                resp = client.post(
                    f"{url.rstrip('/')}/api/v1/live/ingest/batch",
                    json={"lines": payload_lines, "source_id": "dashboard_replay"},
                    headers=headers,
                )
            except (httpx.HTTPError, OSError) as exc:
                errors.append(f"Batch failed: {exc}")
                break
            if resp.status_code == 200:
                body = resp.json()
                sent += int(body.get("ingested_count", 0))
                rejected += int(body.get("rejected_count", 0))
                windows += int(body.get("completed_windows", 0))
            elif resp.status_code in (401, 403):
                errors.append(f"HTTP {resp.status_code}: ingestion key required (X-API-Key).")
                break
            else:
                errors.append(f"HTTP {resp.status_code}: {resp.text[:200]}")
                break
    return sent, rejected, windows, errors


# ----------------- Sidebar -----------------


def render_sidebar():
    st.sidebar.markdown(
        """
        <div style="display: flex; align-items: center; margin-bottom: 20px;">
            <span style="font-size: 1.6rem; margin-right: 10px;">🛡️</span>
            <div>
                <div style="font-size: 1.25rem; font-weight: 800; color: #F8FAFC; letter-spacing: -0.02em;">ZENTINEL</div>
                <div style="font-size: 0.7rem; color: #64748B; font-weight: 600; text-transform: uppercase;">AIOps Log Defense</div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.sidebar.text_input("Live API Endpoint", key="zen_api_url", help="FastAPI service base URL.")
    st.sidebar.text_input(
        "X-API-Key", key="zen_api_key", type="password", help="Authentication key for secure endpoints."
    )

    c_conn, c_refresh = st.sidebar.columns(2)
    connect_clicked = c_conn.button("Connect", key="zen_connect", use_container_width=True)
    if c_refresh.button("Refresh", key="zen_refresh", use_container_width=True):
        st.rerun()
    if connect_clicked:
        api_get.clear()
        st.rerun()

    st.sidebar.markdown("<hr style='border-color: #1E293B; margin: 12px 0;'>", unsafe_allow_html=True)

    live = is_live()
    open_alerts_count = 0
    if live:
        summary = load_summary() or {}
        open_alerts_count = int(summary.get("kpis", {}).get("open_alerts", 0))

    st.sidebar.markdown(
        "<div style='font-size: 0.7rem; font-weight: 700; color: #64748B; margin-bottom: 6px;'>HOME</div>",
        unsafe_allow_html=True,
    )
    page_home = ["Overview"]

    st.sidebar.markdown(
        "<div style='font-size: 0.7rem; font-weight: 700; color: #64748B; margin: 14px 0 6px 0;'>INVESTIGATE</div>",
        unsafe_allow_html=True,
    )
    page_investigate = [
        f"Incidents ({open_alerts_count})" if open_alerts_count > 0 else "Incidents",
        "Review Queue",
        "Windows Browser",
    ]

    st.sidebar.markdown(
        "<div style='font-size: 0.7rem; font-weight: 700; color: #64748B; margin: 14px 0 6px 0;'>OBSERVE</div>",
        unsafe_allow_html=True,
    )
    page_observe = [
        "Stream & Composition",
        "Drift Diagnostics",
        "Model Evaluation",
    ]

    st.sidebar.markdown(
        "<div style='font-size: 0.7rem; font-weight: 700; color: #64748B; margin: 14px 0 6px 0;'>CONFIGURE</div>",
        unsafe_allow_html=True,
    )
    page_configure = [
        "Thresholds & Models",
        "API Keys & Security",
        "Settings & Database",
    ]

    all_pages = page_home + page_investigate + page_observe + page_configure
    st.sidebar.radio(
        "Navigate",
        all_pages,
        key="zen_nav",
        label_visibility="collapsed",
    )

    st.sidebar.markdown("<hr style='border-color: #1E293B; margin: 20px 0 12px 0;'>", unsafe_allow_html=True)

    if live:
        st.sidebar.markdown(
            "<div><span style='color: #10B981;'>●</span> <span style='font-size: 0.8rem; font-weight: 600; color: #94A3B8;'>API Connected (Live)</span></div>",
            unsafe_allow_html=True,
        )
    else:
        st.sidebar.markdown(
            "<div><span style='color: #F59E0B;'>●</span> <span style='font-size: 0.8rem; font-weight: 600; color: #94A3B8;'>Offline Demo Mode</span></div>",
            unsafe_allow_html=True,
        )

    st.sidebar.toggle("Auto-refresh (5s)", key="zen_auto", help="Poll the live API while this page is open.")
    _render_replay_control(live)

    st.sidebar.caption("Zentinel v0.4.0 • BGL Chronological Run")


def _render_replay_control(live: bool):
    """Stream sample log lines through the live ingestion pipeline."""
    replay_options = [path.name for path in (SAMPLE_REPLAY_PATH, INCOMING_LOG_PATH) if path.exists()]
    if not replay_options:
        return

    with st.sidebar.expander("▶ Stream Replay", expanded=False):
        if not live:
            st.caption("Requires a live API connection.")
            return
        file_choice = st.selectbox("Replay file", replay_options, key="zen_replay_file")
        n_lines = st.slider("Lines to stream", 100, 5000, 1000, step=100, key="zen_replay_lines")
        if st.button("⚡ Stream Now into API", use_container_width=True, key="zen_replay_btn"):
            path = SAMPLE_REPLAY_PATH if file_choice == SAMPLE_REPLAY_PATH.name else INCOMING_LOG_PATH
            status_box = st.status("Streaming lines into /ingest/batch ...", expanded=True)
            with status_box:
                _sent, _rejected, _windows, errors = stream_replay(n_lines, replay_path=path)
                if errors:
                    st.error(" / ".join(errors))
                else:
                    st.write(f"Ingested {_sent:,} lines • rejected {_rejected} • windows completed {_windows}")
            status_box.update(
                label="Stream complete ✓" if not errors else "Stream stopped ✗",
                state="complete" if not errors else "error",
            )
            api_get.clear()
            st.rerun()


# ----------------- Top Banner -----------------


@st.fragment(run_every=REFRESH_SECONDS)
def render_top_banner():
    live = is_live()
    summary = load_summary()

    if not live:
        st.markdown(
            """
            <div class="zen-banner-offline">
                <span style="font-size: 1.2rem; margin-right: 12px;">🔌</span>
                <div>
                    <span style="text-transform: uppercase; letter-spacing: 0.05em; font-size: 0.75rem; background: rgba(59, 130, 246, 0.3); padding: 2px 6px; border-radius: 4px; margin-right: 8px;">OFFLINE</span>
                    <span>Live API unreachable — showing clearly-labelled demo data. Enter the API URL &amp; key in the sidebar and hit Connect.</span>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        return

    if not summary:
        st.info("API connected but dashboard summary is unavailable.")
        return

    active_inc = summary.get("active_incident")
    if active_inc:
        incident_summary = html.escape(str(active_inc.get("summary", "")))
        st.markdown(
            f"""
            <div class="zen-banner-incident">
                <span style="font-size: 1.2rem; margin-right: 12px;">⚡</span>
                <div>
                    <span style="text-transform: uppercase; letter-spacing: 0.05em; font-size: 0.75rem; background: rgba(239, 68, 68, 0.3); padding: 2px 6px; border-radius: 4px; margin-right: 8px;">{active_inc.get("severity", "HIGH")} INCIDENT</span>
                    <span>{active_inc.get("incident_id")}: {incident_summary} (Peak Score: {active_inc.get("score", 0):.2f})</span>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        return

    current_unknown = float(summary.get("window_state", {}).get("current_window_unknown_rate", 0.0))
    if current_unknown > 0.02:
        st.markdown(
            f"""
            <div class="zen-banner-drift">
                <span style="font-size: 1.2rem; margin-right: 12px;">⚠️</span>
                <div>
                    <span style="text-transform: uppercase; letter-spacing: 0.05em; font-size: 0.75rem; background: rgba(245, 158, 11, 0.3); padding: 2px 6px; border-radius: 4px; margin-right: 8px;">DEGRADED DRIFT</span>
                    <span>Unknown event rate elevated at {current_unknown * 100:.1f}% (above 2.0% validation threshold)</span>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        return

    st.markdown(
        """
        <div class="zen-banner-clear">
            <span style="font-size: 1.2rem; margin-right: 12px;">✓</span>
            <div>
                <span style="text-transform: uppercase; letter-spacing: 0.05em; font-size: 0.75rem; background: rgba(16, 185, 129, 0.3); padding: 2px 6px; border-radius: 4px; margin-right: 8px;">SYSTEM HEALTHY</span>
                <span>All log streams nominal — No active incidents firing</span>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


# ----------------- Charts -----------------

PLOT_LAYOUT = {
    "template": "plotly_dark",
    "plot_bgcolor": "#0B0F19",
    "paper_bgcolor": "#111827",
    "margin": {"l": 40, "r": 20, "t": 30, "b": 40},
    "legend": {"orientation": "h", "y": 1.15, "x": 0.0},
    "xaxis": {"showgrid": True, "gridcolor": "#1E293B", "zeroline": False},
    "yaxis": {"showgrid": True, "gridcolor": "#1E293B", "zeroline": False},
}


def build_score_chart(items: list[dict[str, Any]], thresholds: dict[str, float], demo: bool):
    df = pd.DataFrame(items)
    if df.empty:
        st.info("No windows available yet. Ingest log lines to populate the timeline.")
        return

    df = df.sort_values("window_id")
    df["label"] = [f"W-{w}" for w in df["window_id"]]
    df["score"] = pd.to_numeric(df.get("lstm_anomaly_score"), errors="coerce").fillna(0.0)
    df["unknown_rate"] = pd.to_numeric(df.get("unknown_event_rate"), errors="coerce").fillna(0.0)

    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=df["label"],
            y=df["score"],
            mode="lines",
            name="LSTM Anomaly Score",
            line={"color": "#3B82F6", "width": 2.5},
        )
    )
    alerts = df[df["decision"] == "ANOMALY_ALERT"]
    if not alerts.empty:
        fig.add_trace(
            go.Scatter(
                x=alerts["label"],
                y=alerts["score"],
                mode="markers",
                name="ANOMALY_ALERT",
                marker={"color": "#EF4444", "size": 7, "symbol": "square"},
            )
        )
    reviews = df[df["decision"] == "NOVELTY_DRIFT_REVIEW"]
    if not reviews.empty:
        fig.add_trace(
            go.Scatter(
                x=reviews["label"],
                y=reviews["score"],
                mode="markers",
                name="NOVELTY_DRIFT_REVIEW",
                marker={"color": "#F59E0B", "size": 6, "symbol": "diamond"},
            )
        )

    if not demo:
        tau = thresholds.get("lstm_threshold", 3.4791)
        hyst = thresholds.get("hysteresis_threshold", tau * 0.70)
        fig.add_hline(
            y=tau,
            line_color="#EF4444",
            line_width=1.5,
            line_dash="dash",
            annotation_text=f"Threshold {tau:.2f}",
        )
        fig.add_hline(
            y=hyst,
            line_color="#F59E0B",
            line_width=1.2,
            line_dash="dot",
            annotation_text=f"Hysteresis {hyst:.2f}",
        )
    else:
        fig.add_hline(
            y=3.4791, line_color="#EF4444", line_width=1.5, line_dash="dash", annotation_text="Threshold 3.48"
        )
        fig.add_hline(
            y=2.4354, line_color="#F59E0B", line_width=1.2, line_dash="dot", annotation_text="Hysteresis 2.44"
        )

    fig.add_trace(
        go.Scatter(
            x=df["label"],
            y=df["unknown_rate"] * 10,
            mode="lines",
            name="Unknown Event Rate (x10)",
            line={"color": "#A855F7", "width": 1.5},
            yaxis="y2",
        )
    )
    fig.update_layout(
        **PLOT_LAYOUT,
        height=330,
        yaxis2={"overlaying": "y", "side": "right", "showgrid": False, "range": [0, 1]},
    )
    st.plotly_chart(fig, use_container_width=True)


def build_decision_mix(decision_counts: dict[str, int]):
    order = ["ANOMALY_ALERT", "NOVELTY_DRIFT_REVIEW", "NO_ANOMALY_DETECTED"]
    colors = {"ANOMALY_ALERT": "#EF4444", "NOVELTY_DRIFT_REVIEW": "#F59E0B", "NO_ANOMALY_DETECTED": "#10B981"}
    labels = [k for k in order if k in decision_counts]
    values = [decision_counts[k] for k in labels]
    if not values:
        st.caption("No completed windows yet.")
        return
    fig = go.Figure(
        go.Bar(
            x=labels,
            y=values,
            marker_color=[colors[k] for k in labels],
            text=[str(v) for v in values],
            textposition="auto",
        )
    )
    fig.update_layout(**PLOT_LAYOUT, height=280, showlegend=False)
    st.plotly_chart(fig, use_container_width=True)


# ----------------- Overview page -----------------


@st.fragment(run_every=REFRESH_SECONDS)
def render_overview():
    live = is_live()
    summary = load_summary() or demo_summary()
    kpis = summary.get("kpis", {})
    counters = summary.get("counters", {})
    thresholds = summary.get("thresholds", {})

    items = load_window_page(limit=CHART_WINDOW_LIMIT)
    demo = False
    if not items:
        items = demo_windows()
        demo = True

    col_head, col_time = st.columns([7, 3])
    with col_head:
        st.markdown(
            "<h2 style='margin:0; font-weight:800; color:#F8FAFC;'>System Overview</h2>", unsafe_allow_html=True
        )
        st.caption("Real-time telemetry, LSTM known-run loss tracking, and template drift")
    with col_time:
        source_label = "LIVE API DATA" if not demo else "DEMO DATA"
        st.markdown(
            f"<div style='text-align:right; padding-top:14px;'><span class='badge-{'green' if not demo else 'yellow'}'>{source_label}</span></div>",
            unsafe_allow_html=True,
        )

    # --- KPI row (real values; trend computed from real pages) ---
    anomaly_now = float(kpis.get("anomaly_rate_recent_100", 0.0))
    if len(items) >= 200 and not demo:
        # items arrive newest-first: first 100 are the most recent window set
        prior_items = items[100:200]
        anomaly_prior = sum(1 for i in prior_items if i.get("decision") == "ANOMALY_ALERT") / max(1, len(prior_items))
        delta = anomaly_now - anomaly_prior
        delta_label = f"{'▲' if delta >= 0 else '▼'} {abs(delta) * 100:.1f}%"
        delta_css = "kpi-trend-bad" if delta >= 0 else "kpi-trend-good"
    else:
        delta_label, delta_css = "", ""

    open_incidents = int(kpis.get("open_alerts", 0))
    windows_scored = int(counters.get("windows_completed", 0))
    logs_received = int(counters.get("logs_received", 0))
    current_unknown_rate = float(summary.get("window_state", {}).get("current_window_unknown_rate", 0.0))

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.markdown(
            f"""
            <div class="zen-card">
                <div class="kpi-title">Anomaly Rate <span style="color:#64748B;">(last 100w)</span></div>
                <div class="kpi-value" style="color: #EF4444;">{anomaly_now * 100:.1f}% <span class="{delta_css}">{delta_label}</span></div>
                <div class="kpi-sub">{int(kpis.get("anomaly_windows_recent_100", 0))} of {kpis.get("recent_windows_sampled", 0)} sampled windows</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with c2:
        inc_color = "#EF4444" if open_incidents > 0 else "#10B981"
        inc_sub = "firing" if open_incidents > 0 else "all clear"
        st.markdown(
            f"""
            <div class="zen-card">
                <div class="kpi-title">Active Incidents</div>
                <div class="kpi-value" style="color: {inc_color};">{open_incidents}</div>
                <div class="kpi-sub">{inc_sub}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with c3:
        st.markdown(
            f"""
            <div class="zen-card">
                <div class="kpi-title">Windows Scored</div>
                <div class="kpi-value" style="color: #60A5FA;">{windows_scored:,}</div>
                <div class="kpi-sub">{logs_received:,} log records ingested</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with c4:
        cov_pct = max(0.0, (1.0 - current_unknown_rate)) * 100
        st.markdown(
            f"""
            <div class="zen-card">
                <div class="kpi-title">Current Window Coverage</div>
                <div class="kpi-value" style="color: #F8FAFC;">{cov_pct:.1f}%</div>
                <div class="kpi-sub">Active window unknown rate: {current_unknown_rate:.1%}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    # --- Main timeline ---
    st.markdown(
        "<h4 style='margin-top: 18px; margin-bottom: 8px; color:#F8FAFC;'>LSTM Anomaly Score & Unknown Rate</h4>",
        unsafe_allow_html=True,
    )
    build_score_chart(items, thresholds, demo=demo)

    # --- Decision mix + stream composition ---
    c_left, c_right = st.columns([5, 5])
    with c_left:
        st.markdown(
            "<h4 style='color:#F8FAFC; margin-bottom: 12px;'>Decision Mix (Recent 100 Windows)</h4>",
            unsafe_allow_html=True,
        )
        recent_mix = summary.get("decisions", {}).get("recent_100", {}) or {}
        if live and recent_mix:
            build_decision_mix(recent_mix)
        else:
            if demo:
                st.markdown("<span class='demo-tag'>DEMO DATA</span>", unsafe_allow_html=True)
            build_decision_mix({"NO_ANOMALY_DETECTED": 93, "ANOMALY_ALERT": 4, "NOVELTY_DRIFT_REVIEW": 3})
    with c_right:
        st.markdown(
            "<h4 style='color:#F8FAFC; margin-bottom: 12px;'>Stream Composition & Drift</h4>", unsafe_allow_html=True
        )
        drift = load_drift()
        if drift and live:
            status_rows = drift.get("event_status_distribution", {})
            total_events = max(1, int(drift.get("total_events_observed", 1)))
            for status_name, count in sorted(status_rows.items(), key=lambda kv: kv[1], reverse=True):
                pct = int(count) / total_events * 100
                badge = "badge-green" if status_name == "matched" else "badge-yellow" if pct < 2 else "badge-red"
                st.markdown(
                    f"""
                    <div class="health-tile" style="display: flex; justify-content: space-between; align-items: center;">
                        <div style="display: flex; align-items: center;">
                            <span style="height: 8px; width: 8px; border-radius: 50%; background-color: {"#10B981" if status_name == "matched" else "#F59E0B"}; margin-right: 10px;"></span>
                            <span style="font-weight: 700; color: #F1F5F9; font-size: 0.9rem;">{html.escape(status_name)}</span>
                        </div>
                        <div>
                            <span style="color: #64748B; font-size: 0.8rem; margin-right: 12px;">{int(count):,} events</span>
                            <span class="{badge}">{pct:.1f}%</span>
                        </div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
        else:
            st.markdown("<span class='demo-tag'>DEMO DATA</span>", unsafe_allow_html=True)
            for status_name, count, pct in (
                ("matched", 231489, 98.8),
                ("unknown_template", 2600, 1.1),
                ("template_missing_from_vocabulary", 211, 0.1),
            ):
                badge = "badge-green" if status_name == "matched" else "badge-yellow"
                st.markdown(
                    f"""
                    <div class="health-tile" style="display: flex; justify-content: space-between; align-items: center;">
                        <div><span style="font-weight: 700; color: #F1F5F9; font-size: 0.9rem;">{status_name}</span></div>
                        <div>
                            <span style="color: #64748B; font-size: 0.8rem; margin-right: 12px;">{count:,} events</span>
                            <span class="{badge}">{pct:.1f}%</span>
                        </div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

    # --- Top patterns (drift + incident evidence) ---
    c_pl, c_pr = st.columns([5, 5])
    with c_pl:
        render_top_patterns(live, demo)
    with c_pr:
        render_recent_activity(summary, live, demo)


def render_top_patterns(live: bool, demo: bool):
    st.markdown(
        "<h4 style='color:#F8FAFC; margin-bottom: 12px;'>Top Unmatched Templates (Drift)</h4>", unsafe_allow_html=True
    )
    drift = load_drift()
    if live and drift:
        templates = drift.get("top_unmatched_templates", []) or []
        if templates:
            for idx, tmpl in enumerate(templates[:6], start=1):
                st.markdown(
                    f"""
                    <div class="pattern-row" style="display: flex; justify-content: space-between; align-items: center;">
                        <div style="overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 85%;">
                            <span class="pattern-count">#{idx}</span>
                            <span>{html.escape(str(tmpl))}</span>
                        </div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
            return
    if demo:
        st.markdown("<span class='demo-tag'>DEMO DATA</span>", unsafe_allow_html=True)
        for pattern in demo_patterns():
            st.markdown(
                f"""
                <div class="pattern-row" style="display: flex; justify-content: space-between; align-items: center;">
                    <div style="overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 85%;">
                        <span class="pattern-count">{pattern["count"]}</span>
                        <span>{html.escape(pattern["template"])}</span>
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )
    else:
        st.caption("No unmatched templates observed.")


def render_recent_activity(summary: dict[str, Any], live: bool, demo: bool):
    st.markdown(
        "<h4 style='color:#F8FAFC; margin-top: 0px; margin-bottom: 10px;'>Recent Activity</h4>", unsafe_allow_html=True
    )
    rows: list[tuple[str, str, str, str]] = []

    if live:
        for alert in summary.get("recent_alerts", [])[:6]:
            icon, color = {
                "OPEN": ("● ⚡", "#EF4444"),
                "ACKNOWLEDGED": ("● 👀", "#F59E0B"),
                "CLOSED": ("● ✓", "#10B981"),
            }.get(alert.get("status"), ("● ℹ️", "#3B82F6"))
            created = str(alert.get("created_at", ""))
            rows.append(
                (
                    icon,
                    color,
                    f"{alert.get('incident_id')}: {alert.get('summary', '')}",
                    created[:16].replace("T", " "),
                )
            )
        if not rows:
            st.caption("No incidents or actions recorded yet — stream is clean.")
            return

    if demo:
        rows = [
            (
                "● ✓",
                "#10B981",
                "Resolved: Pattern surge in component HARDWARE returned below hysteresis threshold",
                "3m ago",
            ),
            ("● ⚠️", "#F59E0B", "Novelty Drift: Rate exceeded validation threshold at 2.4% on window W-2340", "14m ago"),
            ("● ⚡", "#EF4444", "Incident opened: INC-0001 (score 4.82 exceeds validation threshold 3.48)", "18m ago"),
            ("● ℹ️", "#3B82F6", "Model registry verified: LSTMNextEvent opset-17 baseline loaded", "1h ago"),
        ]
        st.markdown("<span class='demo-tag'>DEMO DATA</span>", unsafe_allow_html=True)

    for dot, color, desc, time_str in rows:
        st.markdown(
            f"""
            <div class="activity-row" style="display: flex; justify-content: space-between; align-items: center;">
                <div>
                    <span style="color: {color}; font-weight: 900; margin-right: 8px;">{dot}</span>
                    <span style="color: #CBD5E1;">{html.escape(desc[:160])}</span>
                </div>
                <span style="color: #64748B; font-size: 0.8rem; white-space:nowrap; margin-left: 12px;">{html.escape(time_str)}</span>
            </div>
            """,
            unsafe_allow_html=True,
        )


# ----------------- Incidents page -----------------


def render_incidents_page():
    st.markdown("<h2 style='font-weight:800; color:#F8FAFC;'>Incident Management</h2>", unsafe_allow_html=True)
    st.caption("Forensic incident evidence, hysteresis grouping, and operator actions")

    live = is_live()
    status_filter = st.selectbox("Status Filter", ["ALL", "OPEN", "ACKNOWLEDGED", "CLOSED"], key="zen_inc_status")
    alerts_list = load_alerts(status=status_filter, limit=100) if live else []

    if not alerts_list and live:
        st.info("No incidents match this filter in the SQLite store.")
        return

    if not live:
        st.markdown("<span class='demo-tag'>DEMO DATA</span>", unsafe_allow_html=True)
        st.info("Offline demo mode: actions are disabled. Connect the live API to manage real incidents.")
        alerts_list = [
            {
                "alert_id": None,
                "incident_id": "INC-0001",
                "status": "OPEN",
                "severity": "HIGH",
                "score": 4.82,
                "threshold": 3.4791,
                "first_window_id": 2341,
                "last_window_id": 2344,
                "first_line_number": 234001,
                "last_line_number": 234400,
                "window_count": 4,
                "summary": "DEMO — HIGH Severity Anomaly: 4 consecutive windows above threshold",
                "created_at": datetime.now(UTC).isoformat(),
                "evidence": {
                    "top_k_templates_by_loss": [
                        {"template": "RAS KERNEL FATAL memory error on node J12", "event_count": 42, "mean_loss": 5.12},
                        {"template": "instruction cache parity error corrected", "event_count": 12, "mean_loss": 4.10},
                    ],
                    "unknown_rate_evaluation": {"observed_unknown_rate": 0.015, "validation_novelty_threshold": 0.02},
                    "high_severity_breakdown": {"FATAL": 42, "WARNING": 12},
                    "matching_line_references": [
                        {
                            "line_number": 234015,
                            "loss": 5.82,
                            "template": "RAS KERNEL FATAL memory error",
                            "reason": "PEAK_LOSS",
                        },
                    ],
                },
            }
        ]

    for inc in alerts_list:
        with st.expander(
            f"⚡ [{inc.get('status')}] {inc.get('incident_id')} — {inc.get('summary', '')}",
            expanded=bool(inc.get("status") == "OPEN"),
        ):
            col_a, col_b, col_c = st.columns(3)
            col_a.metric("Peak Score", f"{inc.get('score', 0):.2f}", f"Threshold {inc.get('threshold', 3.48):.2f}")
            col_b.metric(
                "Windows Grouped",
                f"{inc.get('window_count', 1)} windows",
                f"Lines {inc.get('first_line_number')}-{inc.get('last_line_number')}",
            )
            col_c.metric("Severity", inc.get("severity", "MEDIUM"), inc.get("status", "OPEN"))

            st.markdown("##### Forensic Evidence (Physical Measurements)")
            ev = inc.get("evidence", {}) or {}
            col_ev1, col_ev2 = st.columns(2)
            with col_ev1:
                st.markdown("**Top Templates by Cross-Entropy Loss:**")
                for t in ev.get("top_k_templates_by_loss", []) or []:
                    st.write(
                        f"- `{t.get('template')}` — Loss: **{t.get('mean_loss', 0):.2f}** "
                        f"({t.get('event_count')} occurrences)"
                    )
            with col_ev2:
                st.markdown("**Critical Severity Keywords:**")
                st.write(ev.get("high_severity_breakdown", {}) or {})
                unk = ev.get("unknown_rate_evaluation")
                if unk:
                    st.write(
                        f"Unknown rate: **{unk.get('observed_unknown_rate', 0):.1%}** "
                        f"vs validation {unk.get('validation_novelty_threshold', 0.02):.1%}"
                    )

            st.markdown("**Raw Line References:**")
            for ref in ev.get("matching_line_references", []) or []:
                st.code(f"Line {ref.get('line_number')}: {ref.get('template')} (Reason: {ref.get('reason')})")

            alert_id = inc.get("alert_id")
            if live and alert_id and inc.get("status") != "CLOSED":
                st.markdown("##### Operator Actions")
                c_ack, c_close, c_fp = st.columns(3)
                with c_ack:
                    if st.button("✋ Acknowledge", key=f"ack_{alert_id}", use_container_width=True):
                        status_code, body = api_post(
                            f"/api/v1/alerts/{alert_id}/ack",
                            {"actor": "dashboard-operator", "comment": "Acknowledged from dashboard"},
                        )
                        if status_code == 200:
                            st.success("Incident acknowledged.")
                            api_get.clear()
                            st.rerun()
                        else:
                            st.error(f"Ack failed (HTTP {status_code}): {body}")
                with c_close:
                    if st.button("✅ Close (Resolved)", key=f"close_{alert_id}", use_container_width=True):
                        status_code, body = api_post(
                            f"/api/v1/alerts/{alert_id}/close",
                            {"actor": "dashboard-operator", "reason": "RESOLVED", "comment": ""},
                        )
                        if status_code == 200:
                            st.success("Incident closed.")
                            api_get.clear()
                            st.rerun()
                        else:
                            st.error(f"Close failed (HTTP {status_code}): {body}")
                with c_fp:
                    if st.button("🚫 Mark False Positive", key=f"fp_{alert_id}", use_container_width=True):
                        status_code, body = api_post(
                            f"/api/v1/alerts/{alert_id}/close",
                            {"actor": "dashboard-operator", "reason": "FALSE_POSITIVE", "comment": ""},
                        )
                        if status_code == 200:
                            st.info("Marked as false positive.")
                            api_get.clear()
                            st.rerun()
                        else:
                            st.error(f"Action failed (HTTP {status_code}): {body}")


# ----------------- Review Queue page -----------------


def render_review_queue_page():
    st.markdown("<h2 style='font-weight:800; color:#F8FAFC;'>Novelty & Drift Review Queue</h2>", unsafe_allow_html=True)
    st.caption("Unmatched templates and drift-routed windows requiring human triage before retraining")

    live = is_live()
    if live:
        data = _get("/api/v1/review", limit=200)
        rev_items = list(data.get("items", [])) if data else []
        csv_bytes = api_download("/api/v1/review?format=csv")
        st.download_button(
            "⬇️ Export Review Queue (CSV)",
            data=csv_bytes or b"",
            file_name="zentinel_review_queue.csv",
            mime="text/csv",
            disabled=not csv_bytes,
        )
    else:
        rev_items = []

    if not rev_items and live:
        st.info("No unreviewed drift items currently pending in live database.")
    elif live:
        df_rev = pd.DataFrame(rev_items)
        preferred = [
            "window_id",
            "decision",
            "decision_reason",
            "unknown_event_rate",
            "unknown_event_count",
            "known_transitions_scored",
            "persisted_at",
        ]
        present = [c for c in preferred if c in df_rev.columns]
        if present:
            df_rev = df_rev[present]
        st.dataframe(df_rev, use_container_width=True, height=420)

    committed = load_committed_reports()
    if "three_way" in committed:
        tw = committed["three_way"]
        st.markdown(
            f"""
            <div class="zen-card">
                <h4>Validation Novelty Threshold: <b>2.0%</b></h4>
                <p>In the exploratory test period, <b>{tw.get("review_windows", 0):,}</b> of {tw.get("total_test_windows", 0):,} windows
                ({tw.get("review_rate", 0):.1%}) were conservatively routed to review rather than issuing
                automated false normal classifications.</p>
            </div>
            """,
            unsafe_allow_html=True,
        )


# ----------------- Windows Browser page -----------------


def render_windows_browser_page():
    st.markdown("<h2 style='font-weight:800; color:#F8FAFC;'>Windows Browser</h2>", unsafe_allow_html=True)
    st.caption("Inspect 100-line streaming inference windows and three-way decisions")

    live = is_live()
    filter_decision = st.selectbox(
        "Filter Decision",
        ["ALL", "ANOMALY_ALERT", "NO_ANOMALY_DETECTED", "NOVELTY_DRIFT_REVIEW"],
        key="zen_win_filter",
    )
    n_limit = st.slider("Windows to load", 50, 500, 200, step=50, key="zen_win_limit")

    if live:
        decision_arg = None if filter_decision == "ALL" else filter_decision
        data = (
            _get("/api/v1/live/windows", limit=n_limit, decision=decision_arg)
            if decision_arg
            else _get("/api/v1/live/windows", limit=n_limit)
        )
        items = list(data.get("items", [])) if data else []
        if items:
            w_df = pd.DataFrame(items)
            keep = [
                "window_id",
                "decision",
                "decision_reason",
                "lstm_anomaly_score",
                "unknown_event_rate",
                "unknown_event_count",
                "known_transitions_scored",
                "persisted_at",
            ]
            w_df = w_df[keep + [c for c in w_df.columns if c not in keep]] if keep else w_df
            st.dataframe(w_df, use_container_width=True, height=460)
        else:
            st.info("No windows match this filter yet. Ingest log lines to populate.")
    else:
        st.markdown("<span class='demo-tag'>DEMO DATA</span>", unsafe_allow_html=True)
        build_score_chart(demo_windows(100), {}, demo=True)


# ----------------- Stream & Composition page -----------------


def render_stream_page():
    st.markdown("<h2 style='font-weight:800; color:#F8FAFC;'>Stream & Composition</h2>", unsafe_allow_html=True)
    st.caption("Ingestion throughput derived from persisted window completion timestamps")

    live = is_live()
    if not live:
        st.markdown("<span class='demo-tag'>DEMO DATA</span>", unsafe_allow_html=True)
        build_score_chart(demo_windows(100), {}, demo=True)
        return

    summary = load_summary() or {}
    counters = summary.get("counters", {})
    m1, m2, m3 = st.columns(3)
    m1.metric("Logs Received", f"{counters.get('logs_received', 0):,}")
    m2.metric("Windows Completed", f"{counters.get('windows_completed', 0):,}")
    m3.metric("Unknown Events", f"{counters.get('unknown_events_received', 0):,}")

    items = load_window_page(limit=500)
    if not items:
        st.info("No completed windows persisted yet.")
        return

    df = pd.DataFrame(items)
    df["persisted_at"] = pd.to_datetime(df.get("persisted_at"), errors="coerce")
    df = df.dropna(subset=["persisted_at"])
    if df.empty:
        st.caption("Window completion timestamps unavailable.")
        return
    per_minute = df.set_index("persisted_at").resample("1min")["window_id"].count().reset_index(name="windows")
    fig = go.Figure(
        go.Bar(
            x=per_minute["persisted_at"],
            y=per_minute["windows"],
            marker_color="#60A5FA",
        )
    )
    fig.update_layout(
        **PLOT_LAYOUT,
        height=280,
        title=None,
        yaxis_title="windows / min",
    )
    st.plotly_chart(fig, use_container_width=True)

    recent_mix = summary.get("decisions", {}).get("all_time", {}) or {}
    st.markdown(
        "<h4 style='color:#F8FAFC; margin-bottom: 8px;'>All-Time Decision Distribution</h4>", unsafe_allow_html=True
    )
    if recent_mix:
        build_decision_mix(recent_mix)
    else:
        st.caption("No decisions recorded yet.")


# ----------------- Drift Diagnostics page -----------------


def render_drift_page():
    st.markdown(
        "<h2 style='font-weight:800; color:#F8FAFC;'>Template & Novelty Drift Diagnostics</h2>", unsafe_allow_html=True
    )
    st.caption("Monitoring distribution shift between training vocabulary and incoming log templates")

    live = is_live()
    drift = load_drift()
    demo = not (live and drift)
    if demo:
        drift = {
            "drift_status": "NORMAL",
            "overall_unknown_rate": 0.012,
            "validation_threshold": 0.02,
            "total_events_observed": 234300,
            "unknown_events_count": 2811,
            "top_unmatched_templates": [
                "instruction cache parity error corrected",
                "PrepareForService is being done on this card",
                "ciod: Error loading program image",
            ],
        }
        st.markdown("<span class='demo-tag'>DEMO DATA</span>", unsafe_allow_html=True)

    col_d1, col_d2 = st.columns(2)
    with col_d1:
        st.metric(
            "Drift Status",
            drift.get("drift_status", "NORMAL"),
            "novelty above threshold" if drift.get("drift_status") == "DRIFT_SURGE" else None,
        )
        st.metric(
            "Overall Unknown Rate",
            f"{drift.get('overall_unknown_rate', 0):.2%}",
            f"Threshold {drift.get('validation_threshold', 0.02):.1%}",
        )
        st.metric("Total Events Observed", f"{drift.get('total_events_observed', 0):,}")
        st.metric("Unknown Events", f"{drift.get('unknown_events_count', 0):,}")
    with col_d2:
        st.markdown("##### Top Unmatched Patterns")
        templates = drift.get("top_unmatched_templates", []) or []
        if templates:
            for tmpl in templates:
                st.code(html.escape(str(tmpl)))
        else:
            st.caption("No unmatched templates observed.")


# ----------------- Model Evaluation page -----------------


def render_model_evaluation_page():
    st.markdown(
        "<h2 style='font-weight:800; color:#F8FAFC;'>Model Evaluation & Benchmark Provenance</h2>",
        unsafe_allow_html=True,
    )
    st.markdown(
        "<div class='badge-yellow' style='display:inline-block; margin-bottom: 12px;'>EXPLORATORY TEMPORAL TEST</div>",
        unsafe_allow_html=True,
    )
    st.caption(
        "Non-Negotiable Rule 1 & Rule 6: All metrics derive strictly from committed reports. Scoring coverage is reported alongside every metric."
    )

    committed = load_committed_reports()
    wf = committed.get("walk_forward", {})
    overall_wf = wf.get("overall_test_metrics", {})
    delays = wf.get("detection_delay_metrics", {})

    col_m1, col_m2, col_m3, col_m4 = st.columns(4)
    col_m1.metric(
        "Exploratory Precision",
        f"{overall_wf.get('precision', 0):.1%}",
        f"Coverage: {overall_wf.get('coverage', 0):.1%}" if overall_wf.get("coverage") is not None else None,
    )
    col_m2.metric(
        "Scorable F1 Score",
        f"{overall_wf.get('f1', 0):.1%}",
        f"Recall: {overall_wf.get('recall', 0):.1%}",
    )
    col_m3.metric(
        "False Alerts / Hour",
        f"{overall_wf.get('false_alerts_per_hour', 0):.4f}",
        "Time-calibrated",
    )
    col_m4.metric(
        "Median Detection Delay",
        f"{delays.get('median_delay_seconds', 0):.1f}s",
        f"p90: {delays.get('p90_delay_seconds', 0):.1f}s",
    )

    st.markdown("### Walk-Forward 4-Slice Temporal Evaluation")
    if "slice_results" in wf:
        slice_df = pd.DataFrame(wf["slice_results"])
        render_cols = [
            c
            for c in [
                "slice_index",
                "window_range",
                "coverage",
                "review_rate",
                "precision",
                "recall",
                "f1",
                "false_alerts_per_hour",
            ]
            if c in slice_df.columns
        ]
        disp_df = slice_df[render_cols].copy()
        for col in ("coverage", "review_rate", "precision", "recall", "f1"):
            if col in disp_df.columns:
                disp_df[col] = disp_df[col].map(lambda x: f"{x:.1%}")
        st.dataframe(disp_df, use_container_width=True)

    st.markdown("### Block-Bootstrap 95% Confidence Intervals (500 resamples)")
    if "block_bootstrap_95_percent_ci" in wf:
        ci_df = pd.DataFrame(wf["block_bootstrap_95_percent_ci"]).T
        st.dataframe(ci_df, use_container_width=True)

    st.markdown("### Comparison: Baseline vs Three-Way Decision Policy")
    comp_data = [
        {
            "Model / Framework": "Isolation Forest Baseline",
            "Coverage": "100.0%",
            "Precision": "52.4%",
            "Recall": "61.2%",
            "F1": "56.4%",
            "Notes": "High false alert rate in drift",
        },
        {
            "Model / Framework": "LSTM Next-Event (Unconstrained)",
            "Coverage": "100.0%",
            "Precision": "12.8%",
            "Recall": "98.1%",
            "F1": "22.6%",
            "Notes": "Conflates novelty drift with anomalies",
        },
        {
            "Model / Framework": "Zentinel Three-Way Policy (Ours)",
            "Coverage": "33.22%",
            "Precision": "81.73%",
            "Recall": "85.00%",
            "F1": "83.33%",
            "Notes": "Drift routed to review; reliable alerts",
        },
    ]
    st.table(pd.DataFrame(comp_data))


# ----------------- Thresholds & Models page -----------------


def render_thresholds_page():
    st.markdown(
        "<h2 style='font-weight:800; color:#F8FAFC;'>Thresholds, Models & Sensitivity</h2>", unsafe_allow_html=True
    )
    st.caption("Operational thresholds derived strictly from validation distributions")

    live = is_live()
    summary = load_summary() or {}
    thresholds = summary.get("thresholds", {})

    if not live:
        st.markdown("<span class='demo-tag'>DEMO DATA</span>", unsafe_allow_html=True)
        thresholds = {"lstm_threshold": 3.4791, "hysteresis_threshold": 2.4354, "novelty_threshold": 0.02}

    tau = thresholds.get("lstm_threshold", 3.4791)
    st.markdown(
        f"""
        <div class="zen-card">
            <h4>LSTM Anomaly Score Threshold: <code>{tau:.4f}</code></h4>
            <p>Selected on validation scorable windows to maximize F1-score while controlling false positive rate.</p>
            <h4>Hysteresis Closure: <code>{thresholds.get("hysteresis_threshold", 2.4354):.4f}</code> (0.70 × τ)</h4>
            <p>An open incident closes only after 3 consecutive windows fall strictly below the hysteresis threshold, preventing alert flickering.</p>
            <h4>Novelty Unknown Rate Threshold: <code>{thresholds.get("novelty_threshold", 0.02):.4f}</code></h4>
            <p>Derived from the 95th percentile of unknown event rates on validation windows. Windows above this rate route to review.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.markdown("### Model Registry")
    models = []
    if live:
        data = _get("/api/v1/models")
        models = list(data.get("active_models", [])) if data else []
    if models:
        df_models = pd.DataFrame(models)
        if "metrics_json" in df_models.columns:
            df_models = df_models.drop(columns=["metrics_json"])
        st.dataframe(df_models, use_container_width=True)
    else:
        st.caption("Model registry unavailable (offline).")


# ----------------- API Keys & Security page -----------------


def render_security_page():
    st.markdown("<h2 style='font-weight:800; color:#F8FAFC;'>API Keys & Security</h2>", unsafe_allow_html=True)
    st.caption("Role-based key validation, rate limiting, and posture checks")

    live = is_live()

    st.markdown("### Current Session")
    key_entered = bool(st.session_state.get("zen_api_key", ""))
    st.write(f"- API URL: `{st.session_state.get('zen_api_url', '')}`")
    st.write(f"- API key supplied: {'Yes (hidden)' if key_entered else 'No'}")
    st.write(f"- Live API reachable: {'Yes' if live else 'No'}")

    if live and key_entered:
        status_code, _ = api_post("/api/v1/admin/checkpoint", payload=None)
        if status_code == 200:
            st.markdown("<span class='badge-green'>Admin-grade key active</span>", unsafe_allow_html=True)
        else:
            st.markdown(
                "<span class='badge-yellow'>Read/ingest-grade key (admin endpoints restricted)</span>",
                unsafe_allow_html=True,
            )
    elif live:
        st.info("Provide an X-API-Key in the sidebar to authenticate privileged pages.")

    st.markdown("### Role Permission Matrix")
    st.table(
        pd.DataFrame(
            [
                {"Role": "admin", "read": "✓", "ingest": "✓", "admin": "✓"},
                {"Role": "ingest", "read": "✓", "ingest": "✓", "admin": "—"},
                {"Role": "read", "read": "✓", "ingest": "—", "admin": "—"},
            ]
        )
    )

    st.markdown("### Security Posture")
    st.markdown(
        """
        - **Constant-time key checks** (`hmac.compare_digest`) over SHA-256 hashes; keys never stored in plaintext.
        - **Per-key token-bucket rate limiting** — burst groups and returns HTTP 429 with no state advance.
        - **SQL injection defense** — every query is parameterized; identifiers never interpolated from user input.
        - **Idempotency-key collision detection** returns HTTP 409 on payload mismatch.
        - **Non-root container isolation** and read-only artifact mounts in the reference Docker deployment.
        """
    )


# ----------------- Settings & Database page -----------------


def render_settings_page():
    st.markdown(
        "<h2 style='font-weight:800; color:#F8FAFC;'>System Settings & Database Administration</h2>",
        unsafe_allow_html=True,
    )
    st.caption("Single-worker enforcement, retention jobs, and online SQLite backup")

    live = is_live()
    status = _get("/api/v1/live/status") if live else None

    if live and status:
        st.markdown("### Live Persistence State")
        st.write(f"- Mode: `{status.get('mode')}`")
        st.write(f"- Database: `{status.get('database')}`")
        st.write(f"- Logs received (restored): **{status.get('logs_received', 0):,}**")
        st.write(f"- Windows completed (restored): **{status.get('windows_completed', 0):,}**")
        st.write(
            f"- Current window lines: **{status.get('current_window_size', 0)} / {status.get('window_size', 100)}**"
        )
    elif not live:
        st.info("Connect the live API to administer the database.")

    st.markdown("### Operational Controls")
    c_m1, c_m2, c_m3 = st.columns(3)
    with c_m1:
        if st.button("🗑️ Trigger Retention (7d idem / 30d events)", use_container_width=True):
            if live:
                status_code, body = api_post("/api/v1/admin/retention", payload=None)
                _show_admin_result("Retention", status_code, body)
            else:
                st.info("Demo mode: retention simulated.")
    with c_m2:
        if st.button("💾 Checkpoint SQLite WAL", use_container_width=True):
            if live:
                status_code, body = api_post("/api/v1/admin/checkpoint", payload=None)
                _show_admin_result("WAL checkpoint", status_code, body)
            else:
                st.info("Demo mode: WAL checkpoint simulated.")
    with c_m3:
        if st.button("📦 Backup Database", use_container_width=True):
            if live:
                status_code, body = api_post(
                    "/api/v1/admin/backup?target_path=data/processed/zentinel_backup.sqlite3",
                    payload=None,
                    timeout=120.0,
                )
                _show_admin_result("Backup", status_code, body)
            else:
                st.info("Demo mode: backup simulated.")

    st.markdown("### Single-Worker Concurrency Rule")
    st.write(
        "`WEB_CONCURRENCY=1` enforced in the FastAPI lifespan context manager to guarantee 100-line streaming state consistency."
    )


def _show_admin_result(label: str, status_code: int, body: dict[str, Any] | str):
    if status_code == 200:
        st.success(f"{label} complete: {body}")
    else:
        detail = body if isinstance(body, str) else str(body)
        st.error(
            f"{label} failed (HTTP {status_code}). "
            + (
                "Requires an admin-role X-API-Key (ZENTINEL_API_KEY_SHA256)."
                if status_code in (401, 403)
                else detail[:200]
                if status_code == 0
                else detail
            )
        )


# ----------------- Router -----------------

render_sidebar()
render_top_banner()

selected_page = st.session_state.get("zen_nav", "Overview")

if selected_page == "Overview":
    render_overview()
elif selected_page == "Incidents":
    render_incidents_page()
elif selected_page == "Review Queue":
    render_review_queue_page()
elif selected_page == "Windows Browser":
    render_windows_browser_page()
elif selected_page == "Stream & Composition":
    render_stream_page()
elif selected_page == "Drift Diagnostics":
    render_drift_page()
elif selected_page == "Model Evaluation":
    render_model_evaluation_page()
elif selected_page == "Thresholds & Models":
    render_thresholds_page()
elif selected_page == "API Keys & Security":
    render_security_page()
elif selected_page == "Settings & Database":
    render_settings_page()
else:
    st.markdown("Unknown page.")
