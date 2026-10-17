from collections import Counter, defaultdict
from typing import Any

import torch
import torch.nn.functional as F


def compute_per_event_losses(
    events: list[dict[str, Any]],
    model: Any | None,
) -> list[tuple[dict[str, Any], float]]:
    """
    Compute actual next-event cross-entropy loss for each valid transition in the window.
    Returns list of (event, loss) pairs.
    """
    if model is None:
        return []

    tokens = [int(event["token"]) for event in events]
    scored_pairs: list[tuple[dict[str, Any], float]] = []

    current_events: list[dict[str, Any]] = []
    current_tokens: list[int] = []

    for event, token in zip(events, tokens, strict=False):
        if token >= 2:
            current_events.append(event)
            current_tokens.append(token)
        else:
            if len(current_tokens) >= 2:
                inputs = torch.tensor(current_tokens[:-1], dtype=torch.long).unsqueeze(0)
                targets = torch.tensor(current_tokens[1:], dtype=torch.long)
                with torch.inference_mode():
                    logits = model(inputs).squeeze(0)
                    losses = F.cross_entropy(logits, targets, reduction="none")
                    for ev, loss_val in zip(current_events[1:], losses.tolist(), strict=False):
                        scored_pairs.append((ev, float(loss_val)))

            current_events = []
            current_tokens = []

    # Final run
    if len(current_tokens) >= 2:
        inputs = torch.tensor(current_tokens[:-1], dtype=torch.long).unsqueeze(0)
        targets = torch.tensor(current_tokens[1:], dtype=torch.long)
        with torch.inference_mode():
            logits = model(inputs).squeeze(0)
            losses = F.cross_entropy(logits, targets, reduction="none")
            for ev, loss_val in zip(current_events[1:], losses.tolist(), strict=False):
                scored_pairs.append((ev, float(loss_val)))

    return scored_pairs


def generate_incident_evidence(
    events: list[dict[str, Any]],
    window_decision: dict[str, Any],
    model: Any | None = None,
    top_k: int = 5,
) -> dict[str, Any]:
    """
    Extract forensic evidence grounded strictly in measured physical quantities:
    - top-k templates by per-event cross-entropy loss
    - first-seen / unknown templates
    - unknown rate compared to validation threshold (0.02)
    - repeated high-severity events (FATAL, SEVERE, ERROR, WARNING)
    - matching raw line references
    """
    evidence: dict[str, Any] = {}

    # 1. Top-k templates by actual per-event cross-entropy loss
    scored_pairs = compute_per_event_losses(events, model)
    template_losses: dict[str, list[float]] = defaultdict(list)
    template_sample_lines: dict[str, int] = {}

    for ev, loss in scored_pairs:
        tmpl = ev.get("template") or "[UNKNOWN_TEMPLATE]"
        template_losses[tmpl].append(loss)
        if tmpl not in template_sample_lines:
            template_sample_lines[tmpl] = ev.get("line_number", 0)

    top_templates = []
    for tmpl, losses in template_losses.items():
        top_templates.append({
            "template": tmpl,
            "event_count": len(losses),
            "mean_loss": float(sum(losses) / len(losses)),
            "max_loss": float(max(losses)),
            "sample_line_number": template_sample_lines.get(tmpl),
        })

    top_templates.sort(key=lambda x: x["mean_loss"], reverse=True)
    evidence["top_k_templates_by_loss"] = top_templates[:top_k]

    # 2. First-seen / unmatched templates
    unmatched = [
        ev for ev in events
        if ev.get("event_status") in ("unknown_template", "template_missing_from_vocabulary")
    ]
    unmatched_counts = Counter(
        ev.get("template") or "[NEW_UNMATCHED_MESSAGE]" for ev in unmatched
    )
    evidence["first_seen_templates"] = [
        {"template": tmpl, "count": count}
        for tmpl, count in unmatched_counts.most_common(5)
    ]

    # 3. Unknown event rate vs validation threshold
    observed_unknown_rate = float(window_decision.get("unknown_event_rate", 0.0))
    validation_novelty_threshold = 0.02
    evidence["unknown_rate_evaluation"] = {
        "observed_unknown_rate": observed_unknown_rate,
        "validation_novelty_threshold": validation_novelty_threshold,
        "unknown_event_count": int(window_decision.get("unknown_event_count", len(unmatched))),
        "exceeded_threshold": bool(observed_unknown_rate > validation_novelty_threshold),
    }

    # 4. Repeated high-severity events
    high_sev_records = []
    severity_counter = Counter()

    for ev in events:
        # Check raw line severity if available in template or parse
        template = ev.get("template") or ""
        for sev in ["FATAL", "SEVERE", "ERROR", "FAILURE", "Kill", "WARNING"]:
            if sev in template:
                severity_counter[sev] += 1
                high_sev_records.append({
                    "line_number": ev.get("line_number"),
                    "timestamp": ev.get("timestamp"),
                    "severity": sev,
                    "template": template,
                })
                break

    evidence["high_severity_breakdown"] = dict(severity_counter)
    evidence["high_severity_count"] = len(high_sev_records)

    # 5. Matching line references (top representative events)
    representative_lines = []
    # Include up to 5 events with highest loss
    scored_pairs_sorted = sorted(scored_pairs, key=lambda x: x[1], reverse=True)
    for ev, loss in scored_pairs_sorted[:5]:
        representative_lines.append({
            "line_number": ev.get("line_number"),
            "timestamp": ev.get("timestamp"),
            "loss": float(loss),
            "template": ev.get("template"),
            "reason": "HIGH_EVENT_LOSS",
        })

    # Include any FATAL lines
    for item in high_sev_records[:3]:
        representative_lines.append({
            "line_number": item["line_number"],
            "timestamp": item["timestamp"],
            "loss": None,
            "template": item["template"],
            "reason": f"CRITICAL_SEVERITY_{item['severity']}",
        })

    evidence["matching_line_references"] = representative_lines

    return evidence
