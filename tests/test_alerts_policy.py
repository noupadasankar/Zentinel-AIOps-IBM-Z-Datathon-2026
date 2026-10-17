from zentinel.alerts.policy import IncidentPolicy


def test_incident_opens_on_alert():
    policy = IncidentPolicy(threshold=3.0, hysteresis_factor=0.7, required_normal_windows=3)

    window = {
        "window_id": 1,
        "first_line_number": 1,
        "last_line_number": 100,
        "lstm_anomaly_score": 3.5,
        "decision": "ANOMALY_ALERT",
    }

    inc, action = policy.process_window(window)
    assert action == "OPENED"
    assert inc is not None
    assert inc.incident_id == "INC-0001"
    assert inc.status == "OPEN"
    assert inc.score == 3.5
    assert inc.window_count == 1
    assert inc.severity == "LOW"


def test_severity_escalation():
    policy = IncidentPolicy(threshold=3.0)

    # Low severity: score in [3.0, 4.05)
    assert policy.calculate_severity(3.5) == "LOW"
    # Medium severity: score in [4.05, 6.6)
    assert policy.calculate_severity(5.0) == "MEDIUM"
    # High severity: score >= 6.6
    assert policy.calculate_severity(7.5) == "HIGH"


def test_consecutive_anomalies_extend_incident():
    policy = IncidentPolicy(threshold=3.0)

    w1 = {"window_id": 1, "first_line_number": 1, "last_line_number": 100, "lstm_anomaly_score": 3.2, "decision": "ANOMALY_ALERT"}
    w2 = {"window_id": 2, "first_line_number": 101, "last_line_number": 200, "lstm_anomaly_score": 5.5, "decision": "ANOMALY_ALERT"}

    inc1, action1 = policy.process_window(w1)
    assert action1 == "OPENED"
    assert inc1.score == 3.2
    assert inc1.severity == "LOW"

    inc2, action2 = policy.process_window(w2)
    assert action2 == "UPDATED"
    assert inc2.window_count == 2
    assert inc2.score == 5.5  # peak score updated
    assert inc2.severity == "MEDIUM"  # escalated


def test_hysteresis_requires_three_consecutive_normal_windows_below_point_seven_threshold():
    # threshold = 3.0, hysteresis_threshold = 2.1
    policy = IncidentPolicy(threshold=3.0, hysteresis_factor=0.7, required_normal_windows=3)

    # Open incident
    w1 = {"window_id": 1, "first_line_number": 1, "last_line_number": 100, "lstm_anomaly_score": 3.5, "decision": "ANOMALY_ALERT"}
    policy.process_window(w1)

    # Normal window 1: score = 1.5 < 2.1 -> count = 1
    w_norm1 = {"window_id": 2, "first_line_number": 101, "last_line_number": 200, "lstm_anomaly_score": 1.5, "decision": "NO_ANOMALY_DETECTED"}
    inc, act = policy.process_window(w_norm1)
    assert act == "ACTIVE"
    assert inc.status == "OPEN"
    assert inc.consecutive_normal_count == 1

    # Ambiguous window: score = 2.5 (between 2.1 and 3.0) -> resets normal count to 0!
    w_amb = {"window_id": 3, "first_line_number": 201, "last_line_number": 300, "lstm_anomaly_score": 2.5, "decision": "NO_ANOMALY_DETECTED"}
    inc, act = policy.process_window(w_amb)
    assert act == "ACTIVE"
    assert inc.consecutive_normal_count == 0

    # Three consecutive windows strictly below 2.1
    for i in range(4, 7):
        w = {"window_id": i, "first_line_number": i * 100, "last_line_number": (i + 1) * 100, "lstm_anomaly_score": 1.0, "decision": "NO_ANOMALY_DETECTED"}
        inc, act = policy.process_window(w)

    assert act == "CLOSED"
    assert inc.status == "CLOSED"
    assert inc.closed_at is not None
    assert policy.current_incident is None
