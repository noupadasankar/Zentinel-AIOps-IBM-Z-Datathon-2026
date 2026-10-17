import json
import sqlite3
import threading
from pathlib import Path
from typing import Any


class LiveStateStore:
    """Persistent SQLite store for Zentinel's live-ingestion state."""

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

            self._connection.execute("""
                CREATE TABLE IF NOT EXISTS live_service_state (
                    singleton_id INTEGER PRIMARY KEY CHECK (singleton_id = 1),
                    logs_received INTEGER NOT NULL DEFAULT 0,
                    windows_completed INTEGER NOT NULL DEFAULT 0,
                    unknown_events_received INTEGER NOT NULL DEFAULT 0,
                    current_window_json TEXT NOT NULL DEFAULT '[]',
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)

            self._connection.execute("""
                CREATE TABLE IF NOT EXISTS ingested_events (
                    line_number INTEGER PRIMARY KEY,
                    timestamp INTEGER NOT NULL,
                    parse_status TEXT NOT NULL,
                    token INTEGER NOT NULL,
                    event_status TEXT NOT NULL,
                    matched_event_id INTEGER,
                    template TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)

            self._connection.execute("""
                CREATE TABLE IF NOT EXISTS live_windows (
                    window_id INTEGER PRIMARY KEY,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)

            self._connection.execute("""
                CREATE TABLE IF NOT EXISTS ingestion_idempotency (
                    idempotency_key TEXT PRIMARY KEY,
                    payload_sha256 TEXT NOT NULL DEFAULT '',
                    response_json TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)

            self._connection.execute("""
                CREATE INDEX IF NOT EXISTS idx_events_status
                ON ingested_events(event_status)
            """)

            self._connection.execute("""
                CREATE INDEX IF NOT EXISTS idx_events_created_at
                ON ingested_events(created_at)
            """)

            self._connection.execute("""
                CREATE INDEX IF NOT EXISTS idx_idempotency_created_at
                ON ingestion_idempotency(created_at)
            """)

            self._connection.execute("""
                INSERT OR IGNORE INTO live_service_state(singleton_id)
                VALUES (1)
            """)

        self._ensure_columns()

    def _ensure_columns(self):
        """Ensure backward-compatible schema evolutions."""
        with self._lock:
            cursor = self._connection.execute("PRAGMA table_info(ingestion_idempotency)")
            columns = {row["name"] for row in cursor.fetchall()}
            if "payload_sha256" not in columns:
                self._connection.execute(
                    "ALTER TABLE ingestion_idempotency ADD COLUMN payload_sha256 TEXT NOT NULL DEFAULT ''"
                )

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

            recent_rows = self._connection.execute("""
                SELECT payload_json
                FROM live_windows
                ORDER BY window_id DESC
                LIMIT ?
            """, (recent_limit,)).fetchall()

        current_window = json.loads(state_row["current_window_json"])

        recent_windows = [
            json.loads(row["payload_json"])
            for row in reversed(recent_rows)
        ]

        return {
            "state": {
                "logs_received": int(state_row["logs_received"]),
                "windows_completed": int(state_row["windows_completed"]),
                "unknown_events_received": int(
                    state_row["unknown_events_received"]
                ),
            },
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
            self._connection.execute("""
                    INSERT OR REPLACE INTO ingested_events (
                        line_number,
                        timestamp,
                        parse_status,
                        token,
                        event_status,
                        matched_event_id,
                        template
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (
                int(event["line_number"]),
                int(event["timestamp"]),
                str(event["parse_status"]),
                int(event["token"]),
                str(event["event_status"]),
                event["matched_event_id"],
                event["template"],
            ))

            self._connection.execute("""
                    UPDATE live_service_state
                    SET logs_received = ?,
                        windows_completed = ?,
                        unknown_events_received = ?,
                        current_window_json = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE singleton_id = 1
                """, (
                int(state["logs_received"]),
                int(state["windows_completed"]),
                int(state["unknown_events_received"]),
                current_json,
            ))

            if idempotency_key is not None and response is not None:
                sha_val = payload_sha256 if payload_sha256 is not None else ""
                self._connection.execute("""
                        INSERT INTO ingestion_idempotency (
                            idempotency_key,
                            payload_sha256,
                            response_json
                        ) VALUES (?, ?, ?)
                    """, (
                    idempotency_key,
                    sha_val,
                    json.dumps(response, ensure_ascii=False),
                ))

            if window_result is not None:
                self._connection.execute("""
                        INSERT OR REPLACE INTO live_windows (
                            window_id,
                            payload_json
                        ) VALUES (?, ?)
                    """, (
                    int(window_result["window_id"]),
                    json.dumps(
                        window_result,
                        ensure_ascii=False,
                    ),
                ))

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
            row = self._connection.execute(
                f"PRAGMA wal_checkpoint({mode_upper})"
            ).fetchone()

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
