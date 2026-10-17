import os

import httpx
import pandas as pd
import plotly.express as px
import streamlit as st

st.set_page_config(
    page_title="Zentinel | Live Ingestion",
    page_icon="🛡️",
    layout="wide",
)

DEFAULT_URL = os.getenv(
    "ZENTINEL_LIVE_API_URL",
    "http://127.0.0.1:8001",
)

DECISION_COLORS = {
    "ANOMALY_ALERT": "#DC5454",
    "NO_ANOMALY_DETECTED": "#16A34A",
    "NOVELTY_DRIFT_REVIEW": "#D97706",
}


def api_get(base_url, endpoint, params=None):
    response = httpx.get(
        f"{base_url.rstrip('/')}{endpoint}",
        params=params,
        timeout=15,
    )
    response.raise_for_status()
    return response.json()


def submit_log(base_url, raw_line):
    response = httpx.post(
        f"{base_url.rstrip('/')}/api/v1/live/ingest",
        json={"raw_line": raw_line},
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


st.sidebar.title("🛡️ Zentinel")
st.sidebar.caption("Live BGL log ingestion")

api_url = st.sidebar.text_input(
    "Live API URL",
    value=DEFAULT_URL,
)

st.sidebar.caption("API port: 8001")
st.sidebar.caption("Window size: 100 log lines")

st.title("Zentinel Live Inference")
st.write(
    "Submit BGL-format logs to the frozen Drain3 matcher and LSTM "
    "scorer. Completed windows appear in the decision feed."
)

st.warning(
    "Research prototype: event metadata, counters, unfinished windows, "
    "and completed decisions are persisted in local SQLite. Raw log lines "
    "are not stored. Input currently uses BGL-format logs through manual "
    "submission or the file watcher; automatic operating-system log "
    "discovery is not implemented. Exactly-once delivery across crashes "
    "is not yet guaranteed."
)

# The form sends every non-empty line as one API request.
with st.form("log_ingestion_form", clear_on_submit=False):
    raw_text = st.text_area(
        "Paste BGL log lines",
        height=220,
        placeholder=(
            "Paste one complete BGL-format log record per line.\n"
            "A decision is produced after each group of 100 valid lines."
        ),
        help="Maximum 500 non-empty lines per submission.",
    )

    submitted = st.form_submit_button(
        "Submit log lines",
        type="primary",
        use_container_width=True,
    )

if submitted:
    log_lines = [
        line.strip()
        for line in raw_text.splitlines()
        if line.strip()
    ]

    if not log_lines:
        st.warning("Paste at least one non-empty log line.")
    elif len(log_lines) > 500:
        st.error(
            f"You submitted {len(log_lines)} lines. "
            "Submit at most 500 lines at a time."
        )
    else:
        results = []
        failures = []

        progress = st.progress(0.0)
        status_text = st.empty()

        with httpx.Client(timeout=30) as client:
            for index, raw_line in enumerate(log_lines, start=1):
                try:
                    response = client.post(
                        f"{api_url.rstrip('/')}/api/v1/live/ingest",
                        json={"raw_line": raw_line},
                    )
                    response.raise_for_status()
                    payload = response.json()
                    results.append(payload)

                except (httpx.HTTPError, ValueError) as exc:
                    failures.append({
                        "line_in_submission": index,
                        "error": str(exc),
                    })

                progress.progress(index / len(log_lines))
                status_text.text(
                    f"Processed {index} of {len(log_lines)} submitted lines"
                )

        st.session_state["last_live_results"] = results
        st.session_state["last_live_failures"] = failures

        completed = [
            result["window_decision"]
            for result in results
            if result.get("window_completed")
            and result.get("window_decision")
        ]

        st.success(
            f"Requests succeeded: {len(results):,} | "
            f"Windows completed: {len(completed):,} | "
            f"Request failures: {len(failures):,}"
        )

        if failures:
            st.error("Some lines could not be submitted.")
            st.dataframe(
                pd.DataFrame(failures),
                use_container_width=True,
                hide_index=True,
            )

# Show results from the latest submission.
latest_results = st.session_state.get("last_live_results", [])

if latest_results:
    completed = [
        result["window_decision"]
        for result in latest_results
        if result.get("window_completed")
        and result.get("window_decision")
    ]

    if completed:
        st.subheader("Completed windows from last submission")
        completed_df = pd.DataFrame(completed)

        st.dataframe(
            completed_df,
            use_container_width=True,
            hide_index=True,
        )

    last_event = latest_results[-1].get("event", {})
    with st.expander("Last ingested event"):
        st.json(last_event)

# Load latest server state after any submitted lines have been processed.
st.divider()
st.subheader("Live service status")

try:
    health = api_get(api_url, "/health")
    status = api_get(api_url, "/api/v1/live/status")
    recent = api_get(
        api_url,
        "/api/v1/live/windows",
        params={"limit": 100},
    )

except httpx.HTTPError as exc:
    st.error(
        "Cannot connect to the live API. Check that the server on "
        f"port 8001 is running.\n\n{exc}"
    )
    st.stop()

if health.get("status") != "ok":
    st.error("Live API health check failed.")
    st.stop()

st.success("Live inference API connected")

m1, m2, m3, m4 = st.columns(4)
m1.metric("Logs received", f"{status['logs_received']:,}")
m2.metric("Windows completed", f"{status['windows_completed']:,}")
m3.metric("Unknown events received", f"{status['unknown_events_received']:,}")
m4.metric(
    "Lines in current window",
    f"{status['current_window_size']}/{status['window_size']}",
)

unknown_rate = status["current_window_unknown_rate"]

st.write("Current-window unknown-event rate")
st.progress(min(max(float(unknown_rate), 0.0), 1.0))
st.caption(f"{unknown_rate:.1%} unknown events in the unfinished window")

st.caption(
    f"LSTM threshold: {health['lstm_threshold']:.6f} · "
    f"Unknown-event review threshold: "
    f"{health['novelty_threshold']:.1%}"
)

windows = recent.get("items", [])

st.divider()
st.subheader("Recent window decisions")

if not windows:
    st.info(
        "No completed windows are available yet. Submit at least "
        "100 valid BGL-format lines."
    )
else:
    window_df = pd.DataFrame(windows)

    distribution = (
        window_df["decision"]
        .value_counts()
        .rename_axis("Decision")
        .reset_index(name="Windows")
    )

    left, right = st.columns(2)

    with left:
        fig = px.bar(
            distribution,
            x="Decision",
            y="Windows",
            color="Decision",
            color_discrete_map=DECISION_COLORS,
            title="Recent decision distribution",
            text="Windows",
        )
        fig.update_layout(showlegend=False)
        st.plotly_chart(fig, use_container_width=True)

    with right:
        score_df = window_df.dropna(
            subset=["lstm_anomaly_score"]
        ).copy()

        if not score_df.empty:
            score_df["window_id"] = score_df["window_id"].astype(str)
            fig = px.scatter(
                score_df,
                x="window_id",
                y="lstm_anomaly_score",
                color="decision",
                color_discrete_map=DECISION_COLORS,
                hover_data=[
                    "unknown_event_rate",
                    "known_transitions_scored",
                    "decision_reason",
                ],
                title="Recent LSTM window scores",
            )
            fig.add_hline(
                y=health["lstm_threshold"],
                line_dash="dash",
                annotation_text="Saved LSTM threshold",
            )
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("No recent windows have a valid LSTM score.")

    display_columns = [
        "window_id",
        "first_line_number",
        "last_line_number",
        "unknown_event_count",
        "unknown_event_rate",
        "known_transitions_scored",
        "lstm_anomaly_score",
        "decision",
        "decision_reason",
    ]
    display_columns = [
        column for column in display_columns if column in window_df.columns
    ]

    st.dataframe(
        window_df[display_columns],
        use_container_width=True,
        hide_index=True,
    )

    st.download_button(
        "Download recent decisions as CSV",
        data=window_df.to_csv(index=False).encode("utf-8"),
        file_name="zentinel_live_window_decisions.csv",
        mime="text/csv",
    )

with st.expander("Decision policy"):
    st.markdown(
        """
        - **ANOMALY_ALERT:** The LSTM score exceeds the saved threshold and
          novelty review does not take precedence.
        - **NO_ANOMALY_DETECTED:** The score is below the threshold and
          novelty review was not triggered.
        - **NOVELTY_DRIFT_REVIEW:** Too few known-to-known transitions are
          available, or the unknown-event rate exceeds the configured
          threshold.

        An anomaly score is not proof of an incident. Synthetic test logs
        only verify the software pipeline, not production accuracy.
        """
    )

st.caption(
    "Zentinel AIOps | Live ingestion prototype | "
    "Frozen Drain3 templates + LSTM known-run scoring"
)
