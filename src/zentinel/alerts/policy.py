from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


@dataclass
class Incident:
    """An incident grouping consecutive anomalous windows with hysteresis."""

    incident_id: str
    status: str  # OPEN, ACKNOWLEDGED, CLOSED
    severity: str  # LOW, MEDIUM, HIGH
    score: float
    threshold: float
    first_window_id: int
    last_window_id: int
    first_line_number: int
    last_line_number: int
    window_count: int = 1
    consecutive_normal_count: int = 0
    created_at: str = field(
        default_factory=lambda: datetime.now(UTC).isoformat()
    )
    updated_at: str = field(
        default_factory=lambda: datetime.now(UTC).isoformat()
    )
    closed_at: str | None = None
    summary: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "incident_id": self.incident_id,
            "status": self.status,
            "severity": self.severity,
            "score": float(self.score),
            "threshold": float(self.threshold),
            "first_window_id": self.first_window_id,
            "last_window_id": self.last_window_id,
            "first_line_number": self.first_line_number,
            "last_line_number": self.last_line_number,
            "window_count": self.window_count,
            "consecutive_normal_count": self.consecutive_normal_count,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "closed_at": self.closed_at,
            "summary": self.summary,
            "evidence": self.evidence,
        }


class IncidentPolicy:
    """
    Manages grouping consecutive anomalous windows into incidents.

    Rules:
    - Incident opens when a window produces an ANOMALY_ALERT (score >= threshold).
    - Hysteresis: Incident closes only after 3 consecutive windows fall below 0.7 * threshold.
    - Severity: Derived from score percentile versus validation distribution:
        - LOW: threshold <= score < 1.35 * threshold
        - MEDIUM: 1.35 * threshold <= score < 2.2 * threshold
        - HIGH: score >= 2.2 * threshold
    - Cooldown: Cooldown windows prevent immediate duplicate incident creation.
    """

    def __init__(
        self,
        threshold: float = 3.4791,
        hysteresis_factor: float = 0.70,
        required_normal_windows: int = 3,
        cooldown_windows: int = 2,
    ):
        self.threshold = float(threshold)
        self.hysteresis_threshold = float(threshold * hysteresis_factor)
        self.required_normal_windows = max(1, int(required_normal_windows))
        self.cooldown_windows = max(0, int(cooldown_windows))

        self.current_incident: Incident | None = None
        self.incident_counter = 0
        self.windows_since_last_close = 9999

    def calculate_severity(self, score: float) -> str:
        """Assign severity using validation distribution score bands."""
        if score >= 2.2 * self.threshold:
            return "HIGH"
        if score >= 1.35 * self.threshold:
            return "MEDIUM"
        return "LOW"

    def process_window(
        self,
        window: dict[str, Any],
        evidence: dict[str, Any] | None = None,
    ) -> tuple[Incident | None, str]:
        """
        Evaluate a completed window.
        Returns (incident, action) where action is one of:
        - "OPENED": A new incident was triggered
        - "UPDATED": An existing open incident was extended
        - "CLOSED": The open incident met hysteresis closure criteria
        - "ACTIVE": Incident remains open
        - "NONE": No active incident
        """
        score = window.get("lstm_anomaly_score")
        decision = window.get("decision", "NO_ANOMALY_DETECTED")
        window_id = int(window.get("window_id", 0))
        first_line = int(window.get("first_line_number", 0))
        last_line = int(window.get("last_line_number", 0))
        now_iso = datetime.now(UTC).isoformat()

        is_anomalous = (
            decision == "ANOMALY_ALERT"
            or (score is not None and score >= self.threshold)
        )

        if is_anomalous:
            score_val = float(score if score is not None else self.threshold)
            severity = self.calculate_severity(score_val)

            if self.current_incident is not None and self.current_incident.status != "CLOSED":
                # Extend existing open incident
                inc = self.current_incident
                inc.last_window_id = window_id
                inc.last_line_number = last_line
                inc.window_count += 1
                inc.consecutive_normal_count = 0
                inc.updated_at = now_iso

                # Peak score and severity escalate if score increases
                if score_val > inc.score:
                    inc.score = score_val
                    inc.severity = self.calculate_severity(score_val)

                if evidence:
                    inc.evidence.update(evidence)

                inc.summary = (
                    f"{inc.severity} Severity Anomaly: {inc.window_count} windows "
                    f"(lines {inc.first_line_number}-{inc.last_line_number}, peak score {inc.score:.2f})"
                )
                return inc, "UPDATED"

            # Check cooldown before opening a brand-new incident
            if self.windows_since_last_close < self.cooldown_windows and self.incident_counter > 0:
                # Still within cooldown; suppressed as part of cooldown smoothing
                return None, "COOLDOWN"

            # Open a new incident
            self.incident_counter += 1
            incident_id = f"INC-{self.incident_counter:04d}"

            inc = Incident(
                incident_id=incident_id,
                status="OPEN",
                severity=severity,
                score=score_val,
                threshold=self.threshold,
                first_window_id=window_id,
                last_window_id=window_id,
                first_line_number=first_line,
                last_line_number=last_line,
                window_count=1,
                consecutive_normal_count=0,
                created_at=now_iso,
                updated_at=now_iso,
                summary=(
                    f"{severity} Severity Anomaly: Window {window_id} "
                    f"(lines {first_line}-{last_line}, score {score_val:.2f})"
                ),
                evidence=evidence or {},
            )
            self.current_incident = inc
            return inc, "OPENED"

        # Non-anomalous window (Normal or Novelty Drift)
        self.windows_since_last_close += 1

        if self.current_incident is not None and self.current_incident.status != "CLOSED":
            # Check hysteresis threshold: score strictly below 0.7 * threshold
            if score is not None and score < self.hysteresis_threshold or decision == "NO_ANOMALY_DETECTED" and score is None:
                self.current_incident.consecutive_normal_count += 1
            else:
                # Score still in indeterminate band between 0.7*threshold and threshold,
                # or high unknown rate: do not count toward closure
                self.current_incident.consecutive_normal_count = 0

            if self.current_incident.consecutive_normal_count >= self.required_normal_windows:
                # Close the incident via hysteresis
                inc = self.current_incident
                inc.status = "CLOSED"
                inc.closed_at = now_iso
                inc.updated_at = now_iso
                self.windows_since_last_close = 0
                closed_inc = inc
                self.current_incident = None
                return closed_inc, "CLOSED"

            return self.current_incident, "ACTIVE"

        return None, "NONE"
