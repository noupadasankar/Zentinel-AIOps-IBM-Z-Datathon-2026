import json
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from zentinel.api.migrations import apply_migrations


class LiveStateStore:
    """Persistent SQLite store for Zentinel's live-ingestion state, alerts, and audit trail."""

    def __init__(self, database_path: Path | str):
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        self._lock = threading.Lock()
        self._connection = sqlite3.connect(
            str(self.database_path),
            timeout=30.0,
            check_same_thread=False,
        )
        self._connection.row_factory = sqlite3.Row

        with self._connection:
            self._connection.execute("PRAGMA journal_mode=WAL")
            self._connection.execute("PRAGMA synchronous=NORMAL")
            self._connection.execute("PRAGMA foreign_keys=ON")
            self._connection.execute("PRAGMA busy_timeout=5000")

        with self._lock:
            apply_migrations(self._connection)

    def load_state(self, recent_limit=500):
        """Restore counters, an unfinished window, and recent decisions."""
        with self._lock:
            state_row = self._connection.execute("""
                SELECT logs_received,
                       windows_completed,
                       unknown_events_received,
                       current_window_json
                FROM live_service_state
                WHERE singleton_id = 1
            """).fetchone()

            recent_rows = self._connection.execute(
                """
                SELECT payload_json
                FROM live_windows
                ORDER BY window_id DESC
                LIMIT ?
            """,
                (recent_limit,),
            ).fetchall()

        current_window = json.loads(state_row["current_window_json"]) if state_row else []

        recent_windows = [json.loads(row["payload_json"]) for row in reversed(recent_rows)]

        state = {
            "logs_received": int(state_row["logs_received"]) if state_row else 0,
            "windows_completed": int(state_row["windows_completed"]) if state_row else 0,
            "unknown_events_received": int(state_row["unknown_events_received"]) if state_row else 0,
        }

        return {
            "state": state,
            "current_window": current_window,
            "recent_windows": recent_windows,
        }

    def get_idempotency_record(self, idempotency_key: str) -> dict[str, Any] | None:
        """Return the idempotency record including payload_sha256 and response."""
        with self._lock:
            row = self._connection.execute(
                """
                SELECT idempotency_key, payload_sha256, response_json, created_at
                FROM ingestion_idempotency
                WHERE idempotency_key = ?
                """,
                (idempotency_key,),
            ).fetchone()

        if row is None:
            return None

        return {
            "idempotency_key": row["idempotency_key"],
            "payload_sha256": row["payload_sha256"],
            "response": json.loads(row["response_json"]),
            "created_at": row["created_at"],
        }

    def get_idempotent_response(self, idempotency_key: str) -> dict[str, Any] | None:
        """Return the original response for a previously accepted key."""
        record = self.get_idempotency_record(idempotency_key)
        return record["response"] if record is not None else None

    def save_event_and_state(
        self,
        event: dict[str, Any],
        state: dict[str, Any],
        current_window: list[dict[str, Any]],
        window_result: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
        payload_sha256: str | None = None,
        response: dict[str, Any] | None = None,
        source_id: str = "default",
        model_id: str = "lstm_v1",
        latency_ms: float = 0.0,
    ) -> None:
        """
        Save an event, state, completed window, and idempotency response atomically.
        If any operation fails, the transaction rolls back cleanly.
        """
        current_json = json.dumps(
            list(current_window),
            ensure_ascii=False,
        )

        with self._lock, self._connection:
            self._connection.execute(
                """
                    INSERT OR REPLACE INTO ingested_events (
                        line_number,
                        timestamp,
                        parse_status,
                        token,
                        event_status,
                        matched_event_id,
                        template,
                        source_id,
                        model_id,
                        latency_ms
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(event["line_number"]),
                    int(event["timestamp"]),
                    str(event["parse_status"]),
                    int(event["token"]),
                    str(event["event_status"]),
                    event["matched_event_id"],
                    event["template"],
                    source_id,
                    model_id,
                    float(latency_ms),
                ),
            )

            self._connection.execute(
                """
                    UPDATE live_service_state
                    SET logs_received = ?,
                        windows_completed = ?,
                        unknown_events_received = ?,
                        current_window_json = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE singleton_id = 1
                """,
                (
                    int(state["logs_received"]),
                    int(state["windows_completed"]),
                    int(state["unknown_events_received"]),
                    current_json,
                ),
            )

            if idempotency_key is not None and response is not None:
                sha_val = payload_sha256 if payload_sha256 is not None else ""
                self._connection.execute(
                    """
                        INSERT INTO ingestion_idempotency (
                            idempotency_key,
                            payload_sha256,
                            response_json
                        ) VALUES (?, ?, ?)
                    """,
                    (
                        idempotency_key,
                        sha_val,
                        json.dumps(response, ensure_ascii=False),
                    ),
                )

            if window_result is not None:
                self._connection.execute(
                    """
                        INSERT OR REPLACE INTO live_windows (
                            window_id,
                            payload_json,
                            decision,
                            source_id,
                            model_id,
                            latency_ms
                        ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        int(window_result["window_id"]),
                        json.dumps(
                            window_result,
                            ensure_ascii=False,
                        ),
                        str(window_result.get("decision", "")),
                        source_id,
                        model_id,
                        float(latency_ms),
                    ),
                )

    def save_or_update_alert(self, incident_dict: dict[str, Any]) -> dict[str, Any]:
        """Insert or update an alert/incident in the database."""
        evidence_str = json.dumps(incident_dict.get("evidence", {}), ensure_ascii=False)

        with self._lock, self._connection:
            existing = self._connection.execute(
                "SELECT alert_id, status, closed_at FROM alerts WHERE incident_id = ?",
                (incident_dict["incident_id"],),
            ).fetchone()

            if existing:
                alert_id = int(existing["alert_id"])
                # Operator-managed states (ACKNOWLEDGED/CLOSED) are never
                # silently downgraded back to OPEN by automatic incident updates.
                incoming_status = str(incident_dict["status"]).upper()
                current_status = str(existing["status"]).upper()
                status = incoming_status
                closed_at = incident_dict.get("closed_at")
                if incoming_status == "OPEN" and current_status in ("ACKNOWLEDGED", "CLOSED"):
                    status = current_status
                    closed_at = existing["closed_at"]
                self._connection.execute(
                    """
                        UPDATE alerts
                        SET status = ?,
                            severity = ?,
                            score = ?,
                            threshold = ?,
                            last_window_id = ?,
                            last_line_number = ?,
                            window_count = ?,
                            summary = ?,
                            evidence_json = ?,
                            updated_at = ?,
                            closed_at = ?
                        WHERE alert_id = ?
                    """,
                    (
                        status,
                        incident_dict["severity"],
                        float(incident_dict["score"]),
                        float(incident_dict["threshold"]),
                        int(incident_dict["last_window_id"]),
                        int(incident_dict["last_line_number"]),
                        int(incident_dict["window_count"]),
                        incident_dict["summary"],
                        evidence_str,
                        incident_dict.get("updated_at") or datetime.now(UTC).isoformat(),
                        closed_at,
                        alert_id,
                    ),
                )
            else:
                cur = self._connection.execute(
                    """
                        INSERT INTO alerts (
                            incident_id, status, severity, score, threshold,
                            first_window_id, last_window_id, first_line_number,
                            last_line_number, window_count, summary, evidence_json,
                            created_at, updated_at, closed_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        incident_dict["incident_id"],
                        incident_dict["status"],
                        incident_dict["severity"],
                        float(incident_dict["score"]),
                        float(incident_dict["threshold"]),
                        int(incident_dict["first_window_id"]),
                        int(incident_dict["last_window_id"]),
                        int(incident_dict["first_line_number"]),
                        int(incident_dict["last_line_number"]),
                        int(incident_dict["window_count"]),
                        incident_dict["summary"],
                        evidence_str,
                        incident_dict.get("created_at") or datetime.now(UTC).isoformat(),
                        incident_dict.get("updated_at") or datetime.now(UTC).isoformat(),
                        incident_dict.get("closed_at"),
                    ),
                )
                alert_id = cur.lastrowid

        return self.get_alert(alert_id) or {}

    def get_alerts(
        self,
        status: str | None = None,
        severity: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Query alerts with filtering and pagination."""
        query = "SELECT * FROM alerts WHERE 1=1"
        params: list[Any] = []

        if status:
            query += " AND status = ?"
            params.append(status.upper())
        if severity:
            query += " AND severity = ?"
            params.append(severity.upper())

        query += " ORDER BY alert_id DESC LIMIT ? OFFSET ?"
        params.extend([int(limit), int(offset)])

        with self._lock:
            rows = self._connection.execute(query, params).fetchall()

        results = []
        for row in rows:
            item = dict(row)
            try:
                item["evidence"] = json.loads(item.get("evidence_json", "{}"))
            except (json.JSONDecodeError, TypeError):
                item["evidence"] = {}
            results.append(item)
        return results

    def get_alert(self, alert_id_or_incident_id: int | str) -> dict[str, Any] | None:
        """Retrieve single alert with action history."""
        with self._lock:
            if isinstance(alert_id_or_incident_id, int) or str(alert_id_or_incident_id).isdigit():
                row = self._connection.execute(
                    "SELECT * FROM alerts WHERE alert_id = ?",
                    (int(alert_id_or_incident_id),),
                ).fetchone()
            else:
                row = self._connection.execute(
                    "SELECT * FROM alerts WHERE incident_id = ?",
                    (str(alert_id_or_incident_id),),
                ).fetchone()

            if row is None:
                return None

            alert = dict(row)
            try:
                alert["evidence"] = json.loads(alert.get("evidence_json", "{}"))
            except (json.JSONDecodeError, TypeError):
                alert["evidence"] = {}

            actions_rows = self._connection.execute(
                """
                SELECT action_id, alert_id, action_type, actor, comment, created_at
                FROM alert_actions
                WHERE alert_id = ?
                ORDER BY action_id ASC
                """,
                (alert["alert_id"],),
            ).fetchall()
            alert["actions"] = [dict(a) for a in actions_rows]

        return alert

    def add_alert_action(
        self,
        alert_id: int,
        action_type: str,
        actor: str = "operator",
        comment: str = "",
    ) -> dict[str, Any]:
        """Record an operator action on an alert."""
        now_iso = datetime.now(UTC).isoformat()
        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT INTO alert_actions (alert_id, action_type, actor, comment, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (alert_id, action_type.upper(), actor, comment, now_iso),
            )
            if action_type.upper() == "ACKNOWLEDGE":
                self._connection.execute(
                    "UPDATE alerts SET status = 'ACKNOWLEDGED', updated_at = ? WHERE alert_id = ?",
                    (now_iso, alert_id),
                )
            elif action_type.upper() in ("CLOSE", "FALSE_POSITIVE", "RESOLVED"):
                self._connection.execute(
                    "UPDATE alerts SET status = 'CLOSED', closed_at = ?, updated_at = ? WHERE alert_id = ?",
                    (now_iso, now_iso, alert_id),
                )

        return self.get_alert(alert_id) or {}

    def get_review_windows(self, limit: int = 50) -> list[dict[str, Any]]:
        """Retrieve windows routed to NOVELTY_DRIFT_REVIEW using the indexed decision column."""
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT window_id, payload_json, decision, created_at
                FROM live_windows
                WHERE decision = 'NOVELTY_DRIFT_REVIEW'
                ORDER BY window_id DESC
                LIMIT ?
                """,
                (int(limit),),
            ).fetchall()

        reviews = []
        for row in rows:
            try:
                payload = json.loads(row["payload_json"])
                payload["persisted_at"] = row["created_at"]
                reviews.append(payload)
            except (json.JSONDecodeError, TypeError):
                pass
        return reviews

    def get_decision_distribution(self, recent_limit: int | None = None) -> dict[str, int]:
        """
        Count completed windows per decision. When recent_limit is provided the
        counts cover only the most recent N windows (by window_id).
        """
        base = "SELECT decision FROM live_windows"
        query = f"""
            SELECT decision, COUNT(*)
            FROM ({base + " ORDER BY window_id DESC LIMIT ?" if recent_limit else base})
            GROUP BY decision
        """
        with self._lock:
            rows = self._connection.execute(
                query,
                (int(recent_limit),) if recent_limit else (),
            ).fetchall()

        return {row[0]: int(row[1]) for row in rows if row[0]}

    def get_windows_page(
        self,
        decision: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Query persisted window decisions with optional decision filter."""
        query = "SELECT payload_json, created_at FROM live_windows"
        params: list[Any] = []

        if decision and decision != "ALL":
            query += " WHERE decision = ?"
            params.append(decision.upper())

        query += " ORDER BY window_id DESC LIMIT ? OFFSET ?"
        params.extend([int(limit), int(offset)])

        with self._lock:
            rows = self._connection.execute(query, params).fetchall()

        items = []
        for row in rows:
            try:
                payload = json.loads(row["payload_json"])
                payload["persisted_at"] = row["created_at"]
                items.append(payload)
            except (json.JSONDecodeError, TypeError):
                pass
        return items

    def get_last_incident_sequence(self) -> int:
        """
        Highest INC-#### sequence persisted, so IncidentPolicy counters survive
        process restarts without replaying duplicate incident IDs.
        """
        with self._lock:
            row = self._connection.execute("SELECT incident_id FROM alerts ORDER BY alert_id DESC LIMIT 1").fetchone()

        if row is None:
            return 0
        incident_id = str(row["incident_id"])
        if incident_id.startswith("INC-"):
            suffix = incident_id[4:]
            if suffix.isdigit():
                return int(suffix)
        return 0

    def get_drift_summary(self) -> dict[str, Any]:
        """Aggregate template drift and unknown token metrics."""
        with self._lock:
            total_events = self._connection.execute("SELECT COUNT(*) FROM ingested_events").fetchone()[0]

            unknown_events = self._connection.execute(
                "SELECT COUNT(*) FROM ingested_events WHERE token = 1"
            ).fetchone()[0]

            status_counts = dict(
                self._connection.execute(
                    "SELECT event_status, COUNT(*) FROM ingested_events GROUP BY event_status"
                ).fetchall()
            )

            unmatched_samples = [
                row[0]
                for row in self._connection.execute(
                    """
                    SELECT template FROM ingested_events
                    WHERE token = 1 AND template IS NOT NULL
                    GROUP BY template ORDER BY COUNT(*) DESC LIMIT 10
                    """
                ).fetchall()
            ]

        unknown_rate = (unknown_events / total_events) if total_events > 0 else 0.0

        return {
            "total_events_observed": total_events,
            "unknown_events_count": unknown_events,
            "overall_unknown_rate": unknown_rate,
            "validation_threshold": 0.02,
            "drift_status": "DRIFT_SURGE" if unknown_rate > 0.02 else "NORMAL",
            "event_status_distribution": status_counts,
            "top_unmatched_templates": unmatched_samples,
        }

    def register_model(
        self,
        model_id: str,
        name: str,
        version: str,
        model_type: str,
        artifact_path: str,
        threshold: float,
        metrics: dict[str, Any] | None = None,
    ) -> None:
        """Register model metadata and operational thresholds in the registry."""
        metrics_json = json.dumps(metrics or {}, ensure_ascii=False)
        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT OR REPLACE INTO model_registry (
                    model_id, name, version, model_type, artifact_path,
                    threshold, metrics_json, is_active
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 1)
                """,
                (model_id, name, version, model_type, artifact_path, float(threshold), metrics_json),
            )

    def get_models(self) -> list[dict[str, Any]]:
        """List registered models and active thresholds."""
        with self._lock:
            rows = self._connection.execute("SELECT * FROM model_registry ORDER BY registered_at DESC").fetchall()
        models = []
        for row in rows:
            item = dict(row)
            try:
                item["metrics"] = json.loads(item.get("metrics_json", "{}"))
            except (json.JSONDecodeError, TypeError):
                item["metrics"] = {}
            models.append(item)
        return models

    def purge_retention(
        self,
        idempotency_retention_days: int = 7,
        event_retention_days: int = 30,
    ) -> dict[str, int]:
        """
        Retention job: purge idempotency rows older than idempotency_retention_days
        and ingested_events older than event_retention_days.
        """
        with self._lock, self._connection:
            cur1 = self._connection.execute(
                """
                    DELETE FROM ingestion_idempotency
                    WHERE datetime(created_at) < datetime('now', ? || ' days')
                    """,
                (f"-{int(idempotency_retention_days)}",),
            )
            idempotency_deleted = cur1.rowcount

            cur2 = self._connection.execute(
                """
                    DELETE FROM ingested_events
                    WHERE datetime(created_at) < datetime('now', ? || ' days')
                    """,
                (f"-{int(event_retention_days)}",),
            )
            events_deleted = cur2.rowcount

        return {
            "purged_idempotency_rows": max(0, idempotency_deleted),
            "purged_event_rows": max(0, events_deleted),
        }

    def wal_checkpoint(self, mode: str = "PASSIVE") -> dict[str, Any]:
        """Execute a SQLite WAL checkpoint (PASSIVE, FULL, RESTART, TRUNCATE)."""
        valid_modes = {"PASSIVE", "FULL", "RESTART", "TRUNCATE"}
        mode_upper = mode.upper()
        if mode_upper not in valid_modes:
            raise ValueError(f"Invalid WAL checkpoint mode: {mode}. Must be one of {valid_modes}")

        with self._lock:
            row = self._connection.execute(f"PRAGMA wal_checkpoint({mode_upper})").fetchone()

        return {
            "mode": mode_upper,
            "busy": int(row[0]) if row else 0,
            "log": int(row[1]) if row else 0,
            "checkpointed": int(row[2]) if row else 0,
        }

    def backup_database(self, target_path: Path | str) -> None:
        """Online backup of the SQLite database using SQLite's backup API."""
        dest_path = Path(target_path)
        dest_path.parent.mkdir(parents=True, exist_ok=True)

        with self._lock, sqlite3.connect(str(dest_path)) as dest_conn:
            self._connection.backup(dest_conn)

    def close(self):
        """Close the database connection cleanly."""
        with self._lock:
            try:
                self._connection.close()
            except sqlite3.Error:
                pass
