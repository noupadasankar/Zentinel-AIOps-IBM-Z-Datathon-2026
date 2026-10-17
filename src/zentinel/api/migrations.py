import json
import sqlite3

MIGRATIONS: list[tuple[int, str, list[str]]] = [
    (
        1,
        "001_initial_schema",
        [
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            """,
            """
            CREATE TABLE IF NOT EXISTS live_service_state (
                singleton_id INTEGER PRIMARY KEY CHECK (singleton_id = 1),
                logs_received INTEGER NOT NULL DEFAULT 0,
                windows_completed INTEGER NOT NULL DEFAULT 0,
                unknown_events_received INTEGER NOT NULL DEFAULT 0,
                current_window_json TEXT NOT NULL DEFAULT '[]',
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            """,
            """
            CREATE TABLE IF NOT EXISTS ingested_events (
                line_number INTEGER PRIMARY KEY,
                timestamp INTEGER NOT NULL,
                parse_status TEXT NOT NULL,
                token INTEGER NOT NULL,
                event_status TEXT NOT NULL,
                matched_event_id INTEGER,
                template TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            """,
            """
            CREATE TABLE IF NOT EXISTS live_windows (
                window_id INTEGER PRIMARY KEY,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            """,
            """
            CREATE TABLE IF NOT EXISTS ingestion_idempotency (
                idempotency_key TEXT PRIMARY KEY,
                payload_sha256 TEXT NOT NULL DEFAULT '',
                response_json TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_events_status ON ingested_events(event_status);
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_events_created_at ON ingested_events(created_at);
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_idempotency_created_at ON ingestion_idempotency(created_at);
            """,
            """
            INSERT OR IGNORE INTO live_service_state(singleton_id) VALUES (1);
            """,
        ],
    ),
    (
        2,
        "002_add_metadata_columns",
        [
            """
            ALTER TABLE ingestion_idempotency ADD COLUMN payload_sha256 TEXT NOT NULL DEFAULT '';
            """,
            """
            ALTER TABLE ingested_events ADD COLUMN source_id TEXT NOT NULL DEFAULT 'default';
            """,
            """
            ALTER TABLE ingested_events ADD COLUMN model_id TEXT NOT NULL DEFAULT 'lstm_v1';
            """,
            """
            ALTER TABLE ingested_events ADD COLUMN latency_ms REAL NOT NULL DEFAULT 0.0;
            """,
            """
            ALTER TABLE live_windows ADD COLUMN source_id TEXT NOT NULL DEFAULT 'default';
            """,
            """
            ALTER TABLE live_windows ADD COLUMN model_id TEXT NOT NULL DEFAULT 'lstm_v1';
            """,
            """
            ALTER TABLE live_windows ADD COLUMN latency_ms REAL NOT NULL DEFAULT 0.0;
            """,
        ],
    ),
    (
        3,
        "003_alerts_and_incident_audit",
        [
            """
            CREATE TABLE IF NOT EXISTS alerts (
                alert_id INTEGER PRIMARY KEY AUTOINCREMENT,
                incident_id TEXT NOT NULL UNIQUE,
                status TEXT NOT NULL DEFAULT 'OPEN',
                severity TEXT NOT NULL DEFAULT 'MEDIUM',
                score REAL NOT NULL,
                threshold REAL NOT NULL,
                first_window_id INTEGER NOT NULL,
                last_window_id INTEGER NOT NULL,
                first_line_number INTEGER NOT NULL,
                last_line_number INTEGER NOT NULL,
                window_count INTEGER NOT NULL DEFAULT 1,
                summary TEXT NOT NULL,
                evidence_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                closed_at TEXT
            );
            """,
            """
            CREATE TABLE IF NOT EXISTS alert_actions (
                action_id INTEGER PRIMARY KEY AUTOINCREMENT,
                alert_id INTEGER NOT NULL REFERENCES alerts(alert_id) ON DELETE CASCADE,
                action_type TEXT NOT NULL,
                actor TEXT NOT NULL DEFAULT 'operator',
                comment TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            """,
            """
            CREATE TABLE IF NOT EXISTS model_registry (
                model_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                version TEXT NOT NULL,
                model_type TEXT NOT NULL,
                artifact_path TEXT NOT NULL,
                threshold REAL NOT NULL,
                metrics_json TEXT NOT NULL DEFAULT '{}',
                is_active INTEGER NOT NULL DEFAULT 1,
                registered_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            """,
            """
            CREATE TABLE IF NOT EXISTS api_keys (
                key_id TEXT PRIMARY KEY,
                key_prefix TEXT NOT NULL,
                key_hash TEXT NOT NULL UNIQUE,
                role TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_alerts_status ON alerts(status);
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_alerts_severity ON alerts(severity);
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_alert_actions_alert_id ON alert_actions(alert_id);
            """,
        ],
    ),
    (
        4,
        "004_live_window_decision_column",
        [
            """
            ALTER TABLE live_windows ADD COLUMN decision TEXT NOT NULL DEFAULT '';
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_live_windows_decision ON live_windows(decision);
            """,
        ],
    ),
]


def apply_migrations(conn: sqlite3.Connection) -> list[int]:
    """
    Run pending numbered migrations inside transactions.
    Returns list of applied migration versions.
    """
    applied = []

    with conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
        """)

    cursor = conn.execute("SELECT version FROM schema_migrations")
    existing_versions = {row[0] for row in cursor.fetchall()}

    for version, name, statements in MIGRATIONS:
        if version in existing_versions:
            continue

        with conn:
            for statement in statements:
                try:
                    conn.execute(statement)
                except sqlite3.OperationalError as exc:
                    if "duplicate column name" in str(exc).lower():
                        continue
                    raise

            if version == 4:
                _backfill_live_window_decisions(conn)

            conn.execute(
                "INSERT INTO schema_migrations (version, name) VALUES (?, ?)",
                (version, name),
            )
            applied.append(version)

    return applied


def _backfill_live_window_decisions(conn: sqlite3.Connection) -> None:
    """Populate the decision column from existing payload_json blobs."""
    rows = conn.execute("SELECT window_id, payload_json FROM live_windows WHERE decision = ''").fetchall()
    for window_id, payload_json in rows:
        try:
            decision = json.loads(payload_json).get("decision", "")
        except (json.JSONDecodeError, TypeError, AttributeError):
            decision = ""
        conn.execute(
            "UPDATE live_windows SET decision = ? WHERE window_id = ?",
            (decision, window_id),
        )
