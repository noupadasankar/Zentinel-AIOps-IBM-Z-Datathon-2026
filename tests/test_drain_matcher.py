from unittest.mock import MagicMock

from zentinel.config import UNKNOWN_TOKEN
from zentinel.models.inference import InferenceEngine
from zentinel.parsing.drain_parser import BGLRecord


def test_make_template_input():
    engine = InferenceEngine()
    record = BGLRecord(
        line_number=1,
        label="-",
        timestamp=1117838570,
        date="2005.06.03",
        node="R02-M1-N0-C:J12-U11",
        event_time="2005-06-03-15.42.50.363779",
        node_repeat="R02-M1-N0-C:J12-U11",
        event_type="RAS",
        component="KERNEL",
        severity="INFO",
        message="instruction cache parity error corrected",
        parse_status="standard",
    )
    result = engine.make_template_input(record)
    assert result == "RAS KERNEL INFO instruction cache parity error corrected"


def test_map_event_to_token_matched():
    engine = InferenceEngine()
    engine._is_loaded = True
    engine.original_id_to_token = {"42": 15}

    mock_cluster = MagicMock()
    mock_cluster.cluster_id = 42
    mock_cluster.get_template.return_value = "RAS KERNEL INFO <*>"

    mock_miner = MagicMock()
    mock_miner.match.return_value = mock_cluster
    engine.miner = mock_miner

    record = BGLRecord(
        line_number=1,
        label="-",
        timestamp=1117838570,
        date="2005.06.03",
        node="node1",
        event_time="2005-06-03",
        node_repeat="node1",
        event_type="RAS",
        component="KERNEL",
        severity="INFO",
        message="test",
        parse_status="standard",
    )

    out = engine.map_event_to_token(record)
    assert out["token"] == 15
    assert out["event_status"] == "matched"
    assert out["matched_event_id"] == 42
    assert out["template"] == "RAS KERNEL INFO <*>"


def test_map_event_to_token_unknown_template():
    engine = InferenceEngine()
    engine._is_loaded = True
    mock_miner = MagicMock()
    mock_miner.match.return_value = None
    engine.miner = mock_miner

    record = BGLRecord(
        line_number=1,
        label="-",
        timestamp=1117838570,
        date="2005.06.03",
        node="node1",
        event_time="2005-06-03",
        node_repeat="node1",
        event_type="RAS",
        component="KERNEL",
        severity="INFO",
        message="completely novel message",
        parse_status="standard",
    )

    out = engine.map_event_to_token(record)
    assert out["token"] == UNKNOWN_TOKEN
    assert out["event_status"] == "unknown_template"
    assert out["matched_event_id"] is None
    assert out["template"] is None


def test_map_event_to_token_missing_from_vocabulary():
    engine = InferenceEngine()
    engine._is_loaded = True
    engine.original_id_to_token = {"10": 2}

    mock_cluster = MagicMock()
    mock_cluster.cluster_id = 999
    mock_cluster.get_template.return_value = "NEW APP MSG"

    mock_miner = MagicMock()
    mock_miner.match.return_value = mock_cluster
    engine.miner = mock_miner

    record = BGLRecord(
        line_number=1,
        label="-",
        timestamp=1117838570,
        date="2005.06.03",
        node="node1",
        event_time="2005-06-03",
        node_repeat="node1",
        event_type="APP",
        component="USER",
        severity="INFO",
        message="test",
        parse_status="standard",
    )

    out = engine.map_event_to_token(record)
    assert out["token"] == UNKNOWN_TOKEN
    assert out["event_status"] == "template_missing_from_vocabulary"
    assert out["matched_event_id"] == 999
