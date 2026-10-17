import os
import math

import httpx
import pandas as pd
import plotly.express as px
import streamlit as st

st.set_page_config(
    page_title="Zentinel | AIOps",
    page_icon="🛡️",
    layout="wide",
    initial_sidebar_state="expanded",
)

DEFAULT_API_URL = os.getenv(
    "ZENTINEL_API_URL",
    "http://127.0.0.1:8000",
)

DECISION_LABELS = {
    "ANOMALY_ALERT": "Anomaly Alert",
    "NO_ANOMALY_DETECTED": "No Anomaly Detected",
    "NOVELTY_DRIFT_REVIEW": "Novelty / Drift Review",
}

DECISION_COLORS = {
    "ANOMALY_ALERT": "#DC5454",
    "NO_ANOMALY_DETECTED": "#16A34A",
    "NOVELTY_DRIFT_REVIEW": "#D97706",
}


def api_get(base_url, endpoint, params=None):
    """Request JSON from the Zentinel FastAPI service."""
    response = httpx.get(
        f"{base_url.rstrip('/')}{endpoint}",
        params=params,
        timeout=15.0,
    )
    response.raise_for_status()
    return response.json()


def format_percent(value):
    if value is None:
        return "N/A"
    return f"{float(value):.1%}"


def metric_value(value, digits=4):
    if value is None:
        return "N/A"
    return f"{float(value):.{digits}f}"


# ---------- Sidebar ----------
st.sidebar.title("🛡️ Zentinel")
st.sidebar.caption("AI-powered log anomaly analysis")

api_url = st.sidebar.text_input(
    "FastAPI base URL",
    value=DEFAULT_API_URL,
    help="Address of the running Zentinel replay API.",
)

st.sidebar.divider()
st.sidebar.subheader("Decision filters")

selected_decision = st.sidebar.selectbox(
    "Decision category",
    options=[
        "ALL",
        "ANOMALY_ALERT",
        "NO_ANOMALY_DETECTED",
        "NOVELTY_DRIFT_REVIEW",
    ],
    format_func=lambda value: (
        "All decisions"
        if value == "ALL"
        else DECISION_LABELS[value]
    ),
)

page_size = st.sidebar.selectbox(
    "Rows per page",
    options=[25, 50, 100, 250, 500],
    index=2,
)

if st.sidebar.button("Refresh dashboard", use_container_width=True):
    st.rerun()

st.sidebar.divider()
st.sidebar.caption("Current mode: Historical offline replay")
st.sidebar.caption(
    "Unknown events are sent for review when appropriate; "
    "they are not automatically confirmed incidents."
)

# ---------- API connection ----------
try:
    health = api_get(api_url, "/health")

    if health.get("status") != "ok":
        st.error("The API returned an unexpected health status.")
        st.stop()

    summary = api_get(api_url, "/api/v1/summary")
    evaluation = api_get(api_url, "/api/v1/evaluation")

except httpx.HTTPStatusError as exc:
    st.error(
        f"API returned HTTP {exc.response.status_code}. "
        "Check the FastAPI terminal and endpoint configuration."
    )
    st.stop()

except httpx.RequestError as exc:
    st.error(
        "Could not connect to the Zentinel API. Start FastAPI in the "
        "other terminal and verify the API base URL."
    )
    st.code(str(exc))
    st.stop()

except (ValueError, KeyError) as exc:
    st.error(f"Unexpected API response: {exc}")
    st.stop()

# ---------- Page heading ----------
header_left, header_right = st.columns([4, 1])

with header_left:
    st.title("Zentinel AIOps Dashboard")
    st.write(
        "Log anomaly detection, unknown-event monitoring, "
        "and operational decision review."
    )

with header_right:
    st.success("API Connected")
    st.caption(f"Mode: {summary.get('mode', 'unknown')}")

st.warning(
    "This dashboard currently replays previously generated BGL test "
    "decisions. It is not yet ingesting or scoring live log streams."
)

# ---------- Summary metrics ----------
counts = summary.get("decisions", {})

alerts = int(counts.get("ANOMALY_ALERT", 0))
normal = int(counts.get("NO_ANOMALY_DETECTED", 0))
review = int(counts.get("NOVELTY_DRIFT_REVIEW", 0))
total = int(summary.get("total_windows", 0))

st.subheader("Decision overview")

m1, m2, m3, m4 = st.columns(4)

m1.metric("Test windows", f"{total:,}")
m2.metric("Anomaly alerts", f"{alerts:,}")
m3.metric("No anomaly detected", f"{normal:,}")
m4.metric(
    "Novelty / drift review",
    f"{review:,}",
    delta=format_percent(summary.get("review_rate")),
    delta_description="of test windows",
    delta_color="off",
)

m5, m6, m7 = st.columns(3)

m5.metric(
    "Automatic decision coverage",
    format_percent(summary.get("automatic_decision_coverage")),
)
m6.metric(
    "LSTM score threshold",
    metric_value(summary.get("lstm_threshold"), 6),
)
m7.metric(
    "Unknown-event threshold",
    format_percent(summary.get("novelty_threshold")),
)

# ---------- Decision charts ----------
st.divider()
st.subheader("Decision distribution")

distribution_rows = [
    {
        "Decision": DECISION_LABELS[decision],
        "Code": decision,
        "Windows": int(counts.get(decision, 0)),
    }
    for decision in DECISION_LABELS
]

distribution = pd.DataFrame(distribution_rows)

chart_left, chart_right = st.columns(2)

with chart_left:
    fig_bar = px.bar(
        distribution,
        x="Decision",
        y="Windows",
        color="Code",
        color_discrete_map=DECISION_COLORS,
        text="Windows",
        title="Windows by decision category",
    )
    fig_bar.update_layout(
        showlegend=False,
        xaxis_title=None,
        yaxis_title="Number of windows",
    )
    st.plotly_chart(fig_bar, use_container_width=True)

with chart_right:
    fig_pie = px.pie(
        distribution,
        names="Decision",
        values="Windows",
        color="Code",
        color_discrete_map=DECISION_COLORS,
        hole=0.58,
        title="Share of all test-window decisions",
    )
    fig_pie.update_traces(
        textposition="inside",
        textinfo="percent+label",
    )
    st.plotly_chart(fig_pie, use_container_width=True)

# ---------- Historical evaluation ----------
st.divider()
st.subheader("Exploratory model evaluation")

metrics = evaluation.get("metrics", {})

e1, e2, e3, e4 = st.columns(4)

e1.metric(
    "Alert precision",
    format_percent(metrics.get("alert_precision")),
)
e2.metric(
    "Recall over all labelled anomalies",
    format_percent(
        metrics.get("alert_recall_over_all_test_labelled_anomalies")
    ),
)
e3.metric(
    "Alert true positives",
    str(metrics.get("alert_true_positives", "N/A")),
)
e4.metric(
    "Alert false positives",
    str(metrics.get("alert_false_positives", "N/A")),
)

st.caption(
    "These metrics use retrospective dataset labels. A dataset label is "
    "not equivalent to an independently verified operational incident. "
    "The test period was previously inspected for drift diagnostics."
)

# ---------- Filtered decision explorer ----------
st.divider()
st.subheader("Decision explorer")
st.write(
    "Browse individual windows, inspect their LSTM scores, "
    "and identify unfamiliar event patterns."
)

query_params = {"offset": 0, "limit": 1}

if selected_decision != "ALL":
    query_params["decision"] = selected_decision

try:
    first_page = api_get(
        api_url,
        "/api/v1/decisions",
        params=query_params,
    )
    matching_total = int(first_page.get("total_matching", 0))

    page_count = max(1, math.ceil(matching_total / page_size))
    current_page = st.number_input(
        "Page",
        min_value=1,
        max_value=page_count,
        value=1,
        step=1,
    )

    params = {
        "offset": (int(current_page) - 1) * page_size,
        "limit": page_size,
    }

    if selected_decision != "ALL":
        params["decision"] = selected_decision

    page_data = api_get(
        api_url,
        "/api/v1/decisions",
        params=params,
    )

except (httpx.RequestError, httpx.HTTPStatusError, ValueError) as exc:
    st.error(f"Could not retrieve decisions: {exc}")
    st.stop()

items = page_data.get("items", [])

st.caption(
    f"Showing {len(items):,} of {matching_total:,} matching windows"
)

if not items:
    st.info("No windows match the selected filter.")
else:
    table = pd.DataFrame(items)

    chart_left, chart_right = st.columns(2)

    with chart_left:
        histogram_data = table.copy()
        histogram_data["unknown_event_rate"] = pd.to_numeric(
            histogram_data["unknown_event_rate"],
            errors="coerce",
        )

        fig_unknown = px.histogram(
            histogram_data,
            x="unknown_event_rate",
            nbins=25,
            title="Unknown-event rate on this page",
        )
        fig_unknown.update_layout(
            xaxis_title="Unknown-event rate",
            yaxis_title="Number of windows",
        )
        fig_unknown.update_xaxes(tickformat=".0%")
        st.plotly_chart(fig_unknown, use_container_width=True)

    with chart_right:
        scored = table.dropna(subset=["lstm_anomaly_score"]).copy()

        if not scored.empty:
            scored["Decision label"] = scored["decision"].map(
                DECISION_LABELS
            )

            fig_scores = px.scatter(
                scored,
                x="window_index",
                y="lstm_anomaly_score",
                color="decision",
                color_discrete_map=DECISION_COLORS,
                hover_data=[
                    "unknown_event_rate",
                    "decision_reason",
                ],
                title="LSTM anomaly scores on this page",
            )
            fig_scores.add_hline(
                y=summary.get("lstm_threshold"),
                line_dash="dash",
                annotation_text="Validation threshold",
            )
            fig_scores.update_layout(
                xaxis_title="Window index",
                yaxis_title="Mean next-event loss",
            )
            st.plotly_chart(fig_scores, use_container_width=True)
        else:
            st.info("No LSTM-scored windows appear on this page.")

    display_columns = [
        "window_index",
        "decision",
        "decision_reason",
        "lstm_anomaly_score",
        "lstm_would_alert",
        "unknown_event_rate",
        "unknown_event_count",
        "known_event_count",
        "model_scorable",
    ]

    display = table[display_columns].copy()
    display["decision"] = display["decision"].map(DECISION_LABELS)
    display["unknown_event_rate"] = (
        pd.to_numeric(display["unknown_event_rate"], errors="coerce") * 100
    ).round(2)
    display["lstm_anomaly_score"] = pd.to_numeric(
        display["lstm_anomaly_score"],
        errors="coerce",
    ).round(4)
    display = display.rename(columns={
        "window_index": "Window",
        "decision": "Decision",
        "decision_reason": "Reason",
        "lstm_anomaly_score": "LSTM score",
        "lstm_would_alert": "LSTM alert",
        "unknown_event_rate": "Unknown events (%)",
        "unknown_event_count": "Unknown count",
        "known_event_count": "Known count",
        "model_scorable": "Scorable",
    })

    st.dataframe(
        display,
        use_container_width=True,
        hide_index=True,
    )

    csv_data = display.to_csv(index=False).encode("utf-8")
    st.download_button(
        "Download displayed results as CSV",
        data=csv_data,
        file_name="zentinel_decision_page.csv",
        mime="text/csv",
    )

# ---------- Review guidance ----------
with st.expander("How to interpret these decisions"):
    st.markdown(
        """
        **Anomaly Alert:** The LSTM score exceeded the fixed threshold
        and the window did not trigger novelty review.

        **No Anomaly Detected:** The known-event score was below the
        threshold and the unknown-event rate did not trigger review.
        This does not guarantee system health.

        **Novelty / Drift Review:** The window contained insufficient
        known-event transitions or had an unknown-event rate above the
        validation-derived threshold. Review status does not confirm
        an incident.

        **Important limitation:** This is a historical dataset replay.
        Live log ingestion, streaming inference, incident integrations,
        and production monitoring have not yet been implemented.
        """
    )

st.divider()
st.caption(
    "Zentinel AIOps • Research prototype • "
    "BGL / LogHub historical log evaluation"
)
