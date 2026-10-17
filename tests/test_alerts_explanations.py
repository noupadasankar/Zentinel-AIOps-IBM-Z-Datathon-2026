import torch
from torch import nn

from zentinel.alerts.explanations import generate_incident_evidence
from zentinel.config import UNKNOWN_TOKEN


class MockModel(nn.Module):
    def __init__(self):
        super().__init__()

    def forward(self, tokens):
        # Return deterministic logits giving higher loss for token 5 than token 2
        batch_size, seq_len = tokens.shape
        logits = torch.zeros((batch_size, seq_len, 10), dtype=torch.float32)
        # bias toward token 2
        logits[:, :, 2] = 2.0
        return logits


def test_generate_incident_evidence():
    events = [
        {
            "line_number": 1,
            "timestamp": 1000,
            "token": 2,
            "template": "RAS KERNEL INFO normal",
            "event_status": "matched",
        },
        {
            "line_number": 2,
            "timestamp": 1001,
            "token": 5,
            "template": "RAS KERNEL FATAL memory error",
            "event_status": "matched",
        },
        {
            "line_number": 3,
            "timestamp": 1002,
            "token": UNKNOWN_TOKEN,
            "template": "CUSTOM UNMATCHED LOG",
            "event_status": "unknown_template",
        },
    ]

    window_decision = {
        "unknown_event_rate": 0.333,
        "unknown_event_count": 1,
        "known_event_count": 2,
    }

    evidence = generate_incident_evidence(
        events=events,
        window_decision=window_decision,
        model=MockModel(),
        top_k=3,
    )

    # 1. Top-k templates by loss
    assert "top_k_templates_by_loss" in evidence
    assert len(evidence["top_k_templates_by_loss"]) > 0

    # 2. First seen / unmatched templates
    assert "first_seen_templates" in evidence
    unmatched_tmpl = evidence["first_seen_templates"][0]
    assert unmatched_tmpl["template"] == "CUSTOM UNMATCHED LOG"
    assert unmatched_tmpl["count"] == 1

    # 3. Unknown rate vs validation threshold
    assert evidence["unknown_rate_evaluation"]["observed_unknown_rate"] == 0.333
    assert evidence["unknown_rate_evaluation"]["exceeded_threshold"] is True

    # 4. Repeated high severity events
    assert evidence["high_severity_breakdown"]["FATAL"] == 1

    # 5. Matching line references
    assert len(evidence["matching_line_references"]) > 0
    ref_lines = [ref["line_number"] for ref in evidence["matching_line_references"]]
    assert 2 in ref_lines  # Fatal line included
