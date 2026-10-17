from unittest.mock import MagicMock

from zentinel.config import UNKNOWN_TOKEN
from zentinel.models.inference import InferenceEngine


def make_events(known_count=98, unknown_count=2):
    events = []
    for _ in range(known_count):
        events.append({"token": 5})
    for _ in range(unknown_count):
        events.append({"token": UNKNOWN_TOKEN})
    return events


def test_decide_window_below_thresholds_is_normal():
    engine = InferenceEngine()
    engine.lstm_threshold = 1.50
    engine.novelty_threshold = 0.05
    engine.score_known_runs = MagicMock(return_value=(1.20, 95))

    events = make_events(known_count=98, unknown_count=2)
    decision = engine.decide_window(events)

    assert decision["decision"] == "NO_ANOMALY_DETECTED"
    assert decision["decision_reason"] == "BELOW_ANOMALY_AND_NOVELTY_THRESHOLDS"
    assert decision["model_scorable"] is True
    assert decision["lstm_would_alert"] is False
    assert decision["high_unknown_rate"] is False


def test_decide_window_above_lstm_threshold_alerts():
    engine = InferenceEngine()
    engine.lstm_threshold = 1.50
    engine.novelty_threshold = 0.05
    engine.score_known_runs = MagicMock(return_value=(1.55, 95))

    events = make_events(known_count=98, unknown_count=2)
    decision = engine.decide_window(events)

    assert decision["decision"] == "ANOMALY_ALERT"
    assert decision["decision_reason"] == "LSTM_SCORE_ABOVE_VALIDATION_THRESHOLD"
    assert decision["lstm_would_alert"] is True


def test_decide_window_exactly_at_lstm_threshold_alerts():
    engine = InferenceEngine()
    engine.lstm_threshold = 1.50
    engine.novelty_threshold = 0.05
    engine.score_known_runs = MagicMock(return_value=(1.50, 95))

    events = make_events(known_count=98, unknown_count=2)
    decision = engine.decide_window(events)

    assert decision["decision"] == "ANOMALY_ALERT"
    assert decision["lstm_would_alert"] is True


def test_decide_window_high_unknown_rate_takes_precedence_over_alert():
    engine = InferenceEngine()
    engine.lstm_threshold = 1.50
    engine.novelty_threshold = 0.02
    engine.score_known_runs = MagicMock(return_value=(3.50, 80))

    events = make_events(known_count=95, unknown_count=5)
    decision = engine.decide_window(events)

    assert decision["decision"] == "NOVELTY_DRIFT_REVIEW"
    assert decision["decision_reason"] == "UNKNOWN_RATE_ABOVE_VALIDATION_THRESHOLD"
    assert decision["high_unknown_rate"] is True
    assert decision["lstm_would_alert"] is True


def test_decide_window_unscorable_triggers_novelty_review():
    engine = InferenceEngine()
    engine.lstm_threshold = 1.50
    engine.novelty_threshold = 0.05
    engine.score_known_runs = MagicMock(return_value=(None, 0))

    events = make_events(known_count=0, unknown_count=100)
    decision = engine.decide_window(events)

    assert decision["decision"] == "NOVELTY_DRIFT_REVIEW"
    assert decision["decision_reason"] == "INSUFFICIENT_KNOWN_TRANSITIONS"
    assert decision["model_scorable"] is False
    assert decision["lstm_anomaly_score"] is None
    assert decision["lstm_would_alert"] is None


def test_decide_window_unknown_rate_boundary():
    engine = InferenceEngine()
    engine.lstm_threshold = 1.50
    engine.novelty_threshold = 0.02
    engine.score_known_runs = MagicMock(return_value=(1.00, 98))

    events_at = make_events(known_count=98, unknown_count=2)
    decision_at = engine.decide_window(events_at)
    assert decision_at["high_unknown_rate"] is False
    assert decision_at["decision"] == "NO_ANOMALY_DETECTED"

    events_above = make_events(known_count=97, unknown_count=3)
    decision_above = engine.decide_window(events_above)
    assert decision_above["high_unknown_rate"] is True
    assert decision_above["decision"] == "NOVELTY_DRIFT_REVIEW"
