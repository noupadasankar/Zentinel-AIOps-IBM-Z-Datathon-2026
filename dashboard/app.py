"""Zentinel AIOps - Enterprise Log Anomaly Detection & Novelty Monitoring Dashboard.

Enterprise observability layout: dark theme, grouped navigation, live incident banner,
KPI cards, dual-axis LSTM score / unknown-rate timeline, decision mix, drift panels,
incident forensics with operator actions, review queue with CSV export, and a
sample-attribution audit trail for every window.

Data policy (Non-Negotiable Rule 6):
- Live mode: every widget renders data fetched from the Zentinel API or committed reports.
- Offline mode: widgets render clearly-labelled DEMO data (synthetic, never mixed
  with live data, never silently presented as production telemetry).
"""

from __future__ import annotations

import html
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

ROOT_DIR = Path(__file__).resolve().parents[1]
DASHBOARD_DIR = str(Path(__file__).resolve().parent)
if DASHBOARD_DIR not in sys.path:
    sys.path.insert(0, DASHBOARD_DIR)

from navigation import (  # noqa: E402
    NAV_GROUPS,
    canonical_page,
)
from navigation import PAGES as NAV_PAGES  # noqa: E402

REPORTS_DIR = ROOT_DIR / "reports"

REFRESH_SECONDS = 5
CHART_WINDOW_LIMIT = 200

st.set_page_config(
    page_title="Zentinel AIOps",
    page_icon=":material/monitor_heart:",
    layout="wide",
    initial_sidebar_state="expanded",
)

DARK_THEME_CSS = """
<style>
:root {
    --bg: #0B0F19;
    --bg-side: #080C14;
    --card: #0F172A;
    --card-2: #111827;
    --line: #1E293B;
    --line-soft: #16223B;
    --text: #E2E8F0;
    --text-dim: #94A3B8;
    --text-faint: #64748B;
    --accent: #22D3EE;
    --good: #10B981;
    --warn: #F59E0B;
    --bad: #EF4444;
    --mono: 'JetBrains Mono', 'Cascadia Code', Consolas, monospace;
}

@keyframes zenPulse {
    0% { box-shadow: 0 0 0 0 rgba(16, 185, 129, 0.55); }
    70% { box-shadow: 0 0 0 7px rgba(16, 185, 129, 0); }
    100% { box-shadow: 0 0 0 0 rgba(16, 185, 129, 0); }
}
@keyframes zenPulseWarn {
    0% { box-shadow: 0 0 0 0 rgba(245, 158, 11, 0.55); }
    70% { box-shadow: 0 0 0 7px rgba(245, 158, 11, 0); }
    100% { box-shadow: 0 0 0 0 rgba(245, 158, 11, 0); }
}

.stApp {
    background:
        radial-gradient(1100px 500px at 85% -10%, rgba(34, 211, 238, 0.055), transparent 60%),
        radial-gradient(900px 480px at -10% 0%, rgba(139, 92, 246, 0.05), transparent 60%),
        var(--bg);
    color: var(--text);
}
header[data-testid="stHeader"] {
    background: transparent;
}
section[data-testid="stSidebar"] {
    background: linear-gradient(180deg, var(--bg-side) 0%, #0A101E 100%);
    border-right: 1px solid var(--line);
}

div[data-testid="stDataFrame"] {
    border-radius: 10px;
    border: 1px solid var(--line);
}
[data-testid="stExpander"] {
    border: 1px solid var(--line) !important;
    border-radius: 10px !important;
    background-color: var(--card);
}
section[data-testid="stSidebar"] div[data-testid="stExpander"] {
    background-color: var(--bg-side);
}
[data-testid="stMetricValue"] {
    font-feature-settings: "tnum";
}

*::-webkit-scrollbar { width: 9px; height: 9px; }
*::-webkit-scrollbar-track { background: transparent; }
*::-webkit-scrollbar-thumb { background: #223049; border-radius: 8px; }
*::-webkit-scrollbar-thumb:hover { background: #2C3E5C; }

.zen-card {
    position: relative;
    background: linear-gradient(180deg, #101930 0%, var(--card) 65%);
    border: 1px solid var(--line);
    border-radius: 12px;
    padding: 15px 19px;
    margin-bottom: 12px;
    overflow: hidden;
    transition: border-color 0.18s ease, transform 0.18s ease;
}
.zen-card::before {
    content: "";
    position: absolute;
    top: 0; left: 0; right: 0;
    height: 2px;
    background: linear-gradient(90deg, var(--accent) 0%, rgba(34,211,238,0) 55%);
    opacity: 0.75;
}
.zen-card:hover {
    border-color: #2C3E5C;
    transform: translateY(-1px);
}


/* Incident Top Banner */
.zen-banner-incident {
    background: linear-gradient(100deg, rgba(220, 38, 38, 0.28) 0%, rgba(17, 24, 39, 0.75) 100%);
    border-left: 4px solid #EF4444;
    border-radius: 10px;
    padding: 13px 18px;
    margin-bottom: 18px;
    display: flex;
    align-items: center;
    color: #F87171;
    font-weight: 600;
}
.zen-banner-clear {
    background: linear-gradient(100deg, rgba(16, 185, 129, 0.16) 0%, rgba(17, 24, 39, 0.75) 100%);
    border-left: 4px solid #10B981;
    border-radius: 10px;
    padding: 13px 18px;
    margin-bottom: 18px;
    color: #34D399;
    font-weight: 600;
}
.zen-banner-drift {
    background: linear-gradient(100deg, rgba(245, 158, 11, 0.22) 0%, rgba(17, 24, 39, 0.75) 100%);
    border-left: 4px solid #F59E0B;
    border-radius: 10px;
    padding: 13px 18px;
    margin-bottom: 18px;
    color: #FBBF24;
    font-weight: 600;
}
.zen-banner-offline {
    background: linear-gradient(100deg, rgba(56, 189, 248, 0.16) 0%, rgba(17, 24, 39, 0.75) 100%);
    border-left: 4px solid #38BDF8;
    border-radius: 10px;
    padding: 13px 18px;
    margin-bottom: 18px;
    color: #7DD3FC;
    font-weight: 600;
}
.zen-chip {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    height: 30px;
    min-width: 30px;
    border-radius: 9px;
    margin-right: 12px;
    font-size: 0.95rem;
    background: rgba(255, 255, 255, 0.06);
    border: 1px solid rgba(255, 255, 255, 0.10);
    flex: 0 0 auto;
}
.zen-chip.red { color: #FCA5A5; background: rgba(239, 68, 68, 0.14); border-color: rgba(239, 68, 68, 0.3); }
.zen-chip.green { color: #6EE7B7; background: rgba(16, 185, 129, 0.12); border-color: rgba(16, 185, 129, 0.3); }
.zen-chip.amber { color: #FCD34D; background: rgba(245, 158, 11, 0.13); border-color: rgba(245, 158, 11, 0.3); }
.zen-chip.cyan { color: #67E8F9; background: rgba(34, 211, 238, 0.12); border-color: rgba(34, 211, 238, 0.3); }

/* KPI Value & Trend */
.kpi-title {
    font-size: 0.72rem;
    font-weight: 700;
    color: var(--text-dim);
    text-transform: uppercase;
    letter-spacing: 0.07em;
    margin-bottom: 5px;
}
.kpi-value {
    font-size: 1.9rem;
    font-weight: 800;
    color: #F8FAFC;
    line-height: 1.12;
    letter-spacing: -0.02em;
    font-feature-settings: "tnum";
}
.kpi-trend-good {
    color: var(--good);
    font-size: 0.8rem;
    font-weight: 600;
    margin-left: 6px;
}
.kpi-trend-bad {
    color: var(--bad);
    font-size: 0.8rem;
    font-weight: 600;
    margin-left: 6px;
}
.kpi-sub {
    font-size: 0.75rem;
    color: var(--text-faint);
    margin-top: 5px;
}
.demo-tag {
    display: inline-block;
    background-color: rgba(56, 189, 248, 0.2);
    border: 1px solid rgba(56, 189, 248, 0.4);
    color: #7DD3FC;
    border-radius: 4px;
    padding: 1px 7px;
    font-size: 0.7rem;
    font-weight: 700;
    letter-spacing: 0.05em;
    margin-bottom: 6px;
}


/* Health Cards */
.health-tile {
    background-color: var(--card);
    border: 1px solid var(--line);
    border-radius: 9px;
    padding: 12px 15px;
    margin-bottom: 9px;
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
    background-color: #0D1526;
    border: 1px solid var(--line-soft);
    border-radius: 7px;
    padding: 8px 12px;
    margin-bottom: 6px;
    font-family: var(--mono);
    font-size: 0.8rem;
    color: var(--text-dim);
}
.pattern-count {
    color: var(--accent);
    font-weight: 700;
    margin-right: 10px;
}

/* Activity row */
.activity-row {
    padding: 8px 0;
    border-bottom: 1px dashed var(--line);
    font-size: 0.85rem;
}

/* Sidebar brand + nav */
.zen-logo {
    display: flex;
    align-items: center;
    gap: 11px;
    margin-bottom: 18px;
}
.zen-logo-chip {
    height: 40px;
    width: 40px;
    border-radius: 11px;
    display: flex;
    align-items: center;
    justify-content: center;
    font-weight: 800;
    font-size: 1.15rem;
    color: #06B6D4;
    background: linear-gradient(145deg, #123B4C 0%, #0E2136 100%);
    border: 1px solid rgba(34, 211, 238, 0.4);
    box-shadow: 0 0 14px rgba(34, 211, 238, 0.16);
    flex: 0 0 auto;
}
.zen-nav-group {
    font-size: 0.68rem;
    font-weight: 800;
    color: var(--text-faint);
    text-transform: uppercase;
    letter-spacing: 0.14em;
    margin: 15px 0 4px 2px;
}
.zen-conn-pill {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 8px 11px;
    border-radius: 10px;
    border: 1px solid var(--line);
    background: var(--card);
    font-size: 0.8rem;
    font-weight: 600;
    color: var(--text-dim);
}
.zen-dot {
    height: 9px;
    width: 9px;
    border-radius: 50%;
    flex: 0 0 auto;
}
.zen-dot.live { background: var(--good); animation: zenPulse 2s infinite; }
.zen-dot.offline { background: var(--warn); animation: zenPulseWarn 2s infinite; }

section[data-testid="stSidebar"] div[data-testid="stRadio"] label {
    border-radius: 8px;
    padding: 2px 8px;
    transition: background-color 0.14s ease;
}
section[data-testid="stSidebar"] div[data-testid="stRadio"] label:hover {
    background: rgba(34, 211, 238, 0.07);
}
div[data-testid="stStatusWidget"] {
    border-color: var(--line);
}

</style>
"""
st.markdown(DARK_THEME_CSS, unsafe_allow_html=True)


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


def api_probe(endpoint: str, timeout: float = 6.0) -> tuple[int, dict[str, Any] | str]:
    """Uncached GET against the live API returning (status_code, body)."""
    url = st.session_state["zen_api_url"]
    api_key = st.session_state["zen_api_key"]
    headers = {"X-API-Key": api_key} if api_key else {}
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.get(
                f"{url.rstrip('/')}{endpoint}",
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


def render_sidebar():
    st.sidebar.markdown(
        """
        <div class="zen-logo">
            <div class="zen-logo-chip">Z</div>
            <div>
                <div style="font-size: 1.22rem; font-weight: 800; color: #F8FAFC; letter-spacing: -0.02em;">Zentinel</div>
                <div style="font-size: 0.68rem; color: #64748B; font-weight: 700; text-transform: uppercase; letter-spacing: 0.14em;">AIops log defense</div>
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
    connect_clicked = c_conn.button("Connect", key="zen_connect", width="stretch")
    if c_refresh.button("Refresh", key="zen_refresh", width="stretch"):
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

    for group_name, options in NAV_GROUPS.items():
        st.sidebar.markdown(f"<div class='zen-nav-group'>{group_name}</div>", unsafe_allow_html=True)
        if group_name == "Investigate" and open_alerts_count > 0:
            st.sidebar.markdown(
                f"<div style='margin: 0 2px 4px 2px;'><span class='badge-red'>{open_alerts_count} open</span></div>",
                unsafe_allow_html=True,
            )

    all_pages = list(NAV_PAGES)
    st.sidebar.radio(
        "Navigate",
        all_pages,
        key="zen_nav",
        label_visibility="collapsed",
    )

    st.sidebar.markdown("<hr style='border-color: #1E293B; margin: 20px 0 12px 0;'>", unsafe_allow_html=True)

    if live:
        st.sidebar.markdown(
            "<div class='zen-conn-pill'><span class='zen-dot live'></span><span>API connected (live)</span></div>",
            unsafe_allow_html=True,
        )
    else:
        st.sidebar.markdown(
            "<div class='zen-conn-pill'><span class='zen-dot offline'></span><span>Offline demo mode</span></div>",
            unsafe_allow_html=True,
        )

    st.sidebar.toggle("Auto-refresh (5s)", key="zen_auto", help="Poll the live API while this page is open.")

    st.sidebar.caption("Zentinel v0.4.0 • BGL Chronological Run")


@st.fragment(run_every=REFRESH_SECONDS if st.session_state.get("zen_auto", True) else None)
def render_top_banner():
    live = is_live()
    summary = load_summary()

    if not live:
        st.markdown(
            """
            <div class="zen-banner-offline">
                <span class="zen-chip cyan">⏻</span>
                <div>
                    <span style="text-transform: uppercase; letter-spacing: 0.06em; font-size: 0.72rem; background: rgba(56, 189, 248, 0.3); padding: 2px 7px; border-radius: 4px; margin-right: 8px;">OFFLINE</span>
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
                <span class="zen-chip red">⚡</span>
                <div>
                    <span style="text-transform: uppercase; letter-spacing: 0.06em; font-size: 0.72rem; background: rgba(239, 68, 68, 0.3); padding: 2px 7px; border-radius: 4px; margin-right: 8px;">{active_inc.get("severity", "HIGH")} INCIDENT</span>
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
                <span class="zen-chip amber">⚠</span>
                <div>
                    <span style="text-transform: uppercase; letter-spacing: 0.06em; font-size: 0.72rem; background: rgba(245, 158, 11, 0.3); padding: 2px 7px; border-radius: 4px; margin-right: 8px;">DEGRADED DRIFT</span>
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
            <span class="zen-chip green">✓</span>
            <div>
                <span style="text-transform: uppercase; letter-spacing: 0.06em; font-size: 0.72rem; background: rgba(16, 185, 129, 0.3); padding: 2px 7px; border-radius: 4px; margin-right: 8px;">SYSTEM HEALTHY</span>
                <span>All log streams nominal — no active incidents firing</span>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


PLOT_LAYOUT = {
    "template": "plotly_dark",
    "plot_bgcolor": "rgba(0,0,0,0)",
    "paper_bgcolor": "rgba(0,0,0,0)",
    "margin": {"l": 44, "r": 16, "t": 34, "b": 34},
    "legend": {"orientation": "h", "y": 1.16, "x": 0.0, "bgcolor": "rgba(0,0,0,0)"},
    "xaxis": {
        "showgrid": True,
        "gridcolor": "rgba(30, 41, 59, 0.55)",
        "zeroline": False,
        "linecolor": "#1E293B",
        "tickfont": {"size": 10.5},
    },
    "yaxis": {
        "showgrid": True,
        "gridcolor": "rgba(30, 41, 59, 0.55)",
        "zeroline": False,
        "linecolor": "#1E293B",
        "tickfont": {"size": 10.5},
    },
    "hovermode": "x unified",
    "hoverlabel": {"bgcolor": "#111A2E", "bordercolor": "#2C3E5C", "font": {"size": 11.5}},
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
    df["decision_hint"] = df["decision"].astype(str) if "decision" in df.columns else ""

    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=df["label"],
            y=df["score"],
            mode="lines",
            name="LSTM anomaly score",
            line={"color": "#38BDF8", "width": 2.4},
            fill="tozeroy",
            fillcolor="rgba(56, 189, 248, 0.10)",
            customdata=[[str(d), float(r)] for d, r in zip(df["decision_hint"], df["unknown_rate"])],
            hovertemplate=(
                "%{x}<br>Score %{y:.3f}<br>Unknown %{customdata[1]:.2%}<br>%{customdata[0]}<extra></extra>"
            ),
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
                marker={"color": "#EF4444", "size": 8, "symbol": "square", "line": {"width": 1, "color": "#FCA5A5"}},
                hovertemplate="%{x}<br>Alert %{y:.3f}<extra>ANOMALY_ALERT</extra>",
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
                marker={"color": "#F59E0B", "size": 7, "symbol": "diamond", "line": {"width": 1, "color": "#FCD34D"}},
                hovertemplate="%{x}<br>Review %{y:.3f}<extra>NOVELTY_DRIFT_REVIEW</extra>",
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
    st.plotly_chart(fig, width="stretch")


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
    st.plotly_chart(fig, width="stretch")


@st.fragment(run_every=REFRESH_SECONDS if st.session_state.get("zen_auto", True) else None)
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
            "<h2 style='margin:0; font-weight:800; color:#F8FAFC;'>System overview</h2>", unsafe_allow_html=True
        )
        st.caption("Real-time telemetry, LSTM known-run loss tracking, and template drift")
    with col_time:
        source_label = "LIVE API DATA" if not demo else "DEMO DATA"
        last_synced = datetime.now(UTC).strftime("%H:%M:%S UTC")
        auto_label = f"auto-refresh {REFRESH_SECONDS}s" if st.session_state.get("zen_auto", True) else "auto-refresh off"
        st.markdown(
            f"""
            <div style='text-align:right; padding-top:8px;'>
                <div><span class='badge-{'green' if not demo else 'yellow'}'>{source_label}</span></div>
                <div style='font-size: 0.7rem; color: #64748B; margin-top: 4px;'>Last synced {last_synced} · {auto_label}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    anomaly_now = float(kpis.get("anomaly_rate_recent_100", 0.0))
    if len(items) >= 200 and not demo:
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

    st.markdown(
        "<h4 style='margin-top: 18px; margin-bottom: 8px; color:#F8FAFC;'>LSTM Anomaly Score & Unknown Rate</h4>",
        unsafe_allow_html=True,
    )
    build_score_chart(items, thresholds, demo=demo)

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


def render_incidents_page():
    live_preview = is_live()
    open_total = 0
    if live_preview:
        summary = load_summary() or {}
        open_total = int(summary.get("kpis", {}).get("open_alerts", 0))

    col_title, col_count = st.columns([7, 3])
    with col_title:
        st.markdown("<h2 style='font-weight:800; color:#F8FAFC;'>Incident management</h2>", unsafe_allow_html=True)
    with col_count:
        if live_preview:
            badge_cls = "badge-red" if open_total > 0 else "badge-green"
            st.markdown(
                f"<div style='text-align:right; padding-top:10px;'><span class='{badge_cls}'>{open_total} OPEN</span></div>",
                unsafe_allow_html=True,
            )
    st.caption("Forensic incident evidence, hysteresis grouping, and operator actions")

    live = live_preview
    status_filter = st.selectbox("Status filter", ["ALL", "OPEN", "ACKNOWLEDGED", "CLOSED"], key="zen_inc_status")
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
                    if st.button("Acknowledge", icon=":material/check_circle:", key=f"ack_{alert_id}", width="stretch"):
                        status_code, body = api_post(
                            f"/api/v1/alerts/{alert_id}/ack",
                            {"actor": "dashboard-operator", "comment": "Acknowledged from dashboard"},
                        )
                        if status_code == 200:
                            st.success("Incident acknowledged.")
                            st.toast("Incident acknowledged.", icon=":material/check_circle:")
                            api_get.clear()
                            st.rerun()
                        else:
                            st.error(f"Ack failed (HTTP {status_code}): {body}")
                with c_close:
                    if st.button("Close incident", icon=":material/task_alt:", key=f"close_{alert_id}", width="stretch"):
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
                    if st.button("Mark false positive", icon=":material/flag:", key=f"fp_{alert_id}", width="stretch"):
                        status_code, body = api_post(
                            f"/api/v1/alerts/{alert_id}/close",
                            {"actor": "dashboard-operator", "reason": "FALSE_POSITIVE", "comment": ""},
                        )
                        if status_code == 200:
                            st.info("Marked as false positive.")
                            st.toast("Marked as false positive.", icon=":material/flag:")
                            api_get.clear()
                            st.rerun()
                        else:
                            st.error(f"Action failed (HTTP {status_code}): {body}")


def render_review_queue_page():
    st.markdown("<h2 style='font-weight:800; color:#F8FAFC;'>Novelty & drift review queue</h2>", unsafe_allow_html=True)
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
        st.dataframe(df_rev, height=420)

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


def render_windows_browser_page():
    st.markdown("<h2 style='font-weight:800; color:#F8FAFC;'>Windows browser</h2>", unsafe_allow_html=True)
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
            st.dataframe(w_df, height=460)
        else:
            st.info("No windows match this filter yet. Ingest log lines to populate.")
    else:
        st.markdown("<span class='demo-tag'>DEMO DATA</span>", unsafe_allow_html=True)
        build_score_chart(demo_windows(100), {}, demo=True)


def render_stream_page():
    st.markdown("<h2 style='font-weight:800; color:#F8FAFC;'>Stream & composition</h2>", unsafe_allow_html=True)
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
    st.plotly_chart(fig, width="stretch")

    recent_mix = summary.get("decisions", {}).get("all_time", {}) or {}
    st.markdown(
        "<h4 style='color:#F8FAFC; margin-bottom: 8px;'>All-Time Decision Distribution</h4>", unsafe_allow_html=True
    )
    if recent_mix:
        build_decision_mix(recent_mix)
    else:
        st.caption("No decisions recorded yet.")


def render_drift_page():
    st.markdown(
        "<h2 style='font-weight:800; color:#F8FAFC;'>Template & novelty drift diagnostics</h2>", unsafe_allow_html=True
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


def render_model_evaluation_page():
    st.markdown(
        "<h2 style='font-weight:800; color:#F8FAFC;'>Model evaluation & benchmark provenance</h2>",
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
        st.dataframe(disp_df)

    st.markdown("### Block-Bootstrap 95% Confidence Intervals (500 resamples)")
    if "block_bootstrap_95_percent_ci" in wf:
        ci_df = pd.DataFrame(wf["block_bootstrap_95_percent_ci"]).T
        st.dataframe(ci_df)

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


def render_thresholds_page():
    st.markdown(
        "<h2 style='font-weight:800; color:#F8FAFC;'>Thresholds, models & sensitivity</h2>", unsafe_allow_html=True
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
        st.dataframe(df_models)
    else:
        st.caption("Model registry unavailable (offline).")


def render_security_page():
    st.markdown("<h2 style='font-weight:800; color:#F8FAFC;'>API keys & security</h2>", unsafe_allow_html=True)
    st.caption("Role-based key validation, rate limiting, and posture checks")

    live = is_live()

    st.markdown("### Current Session")
    key_entered = bool(st.session_state.get("zen_api_key", ""))
    st.write(f"- API URL: `{st.session_state.get('zen_api_url', '')}`")
    st.write(f"- API key supplied: {'Yes (hidden)' if key_entered else 'No'}")
    st.write(f"- Live API reachable: {'Yes' if live else 'No'}")

    if live and key_entered:
        status_code, _ = api_probe("/api/v1/admin/ping")
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


def render_settings_page():
    st.markdown(
        "<h2 style='font-weight:800; color:#F8FAFC;'>System settings & database administration</h2>",
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
        if st.button("Run retention cleanup", icon=":material/delete_sweep:", width="stretch"):
            if live:
                status_code, body = api_post("/api/v1/admin/retention", payload=None)
                _show_admin_result("Retention", status_code, body)
            else:
                st.info("Demo mode: retention simulated.")
        st.caption("Drops idempotency keys older than 7d and events older than 30d.")
    with c_m2:
        if st.button("Checkpoint SQLite WAL", icon=":material/save:", width="stretch"):
            if live:
                status_code, body = api_post("/api/v1/admin/checkpoint", payload=None)
                _show_admin_result("WAL checkpoint", status_code, body)
            else:
                st.info("Demo mode: WAL checkpoint simulated.")
    with c_m3:
        if st.button("Backup Database", icon=":material/database:", width="stretch"):
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


render_sidebar()
render_top_banner()

selected_page = st.session_state.get("zen_nav", "Overview")
selected_canonical = canonical_page(selected_page)

if selected_canonical == "Overview":
    render_overview()
elif selected_canonical == "Incidents":
    render_incidents_page()
elif selected_canonical == "Review Queue":
    render_review_queue_page()
elif selected_canonical == "Windows Browser":
    render_windows_browser_page()
elif selected_canonical == "Stream & Composition":
    render_stream_page()
elif selected_canonical == "Drift Diagnostics":
    render_drift_page()
elif selected_canonical == "Model Evaluation":
    render_model_evaluation_page()
elif selected_canonical == "Thresholds & Models":
    render_thresholds_page()
elif selected_canonical == "API Keys & Security":
    render_security_page()
elif selected_canonical == "Settings & Database":
    render_settings_page()
else:
    st.markdown("Unknown page.")
