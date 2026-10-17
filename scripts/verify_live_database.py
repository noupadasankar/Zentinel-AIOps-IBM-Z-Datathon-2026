import sqlite3
from pathlib import Path

db_path = Path("data/processed/zentinel_live.sqlite3")

print("========== ZENTINEL DATABASE VERIFICATION ==========")
print(f"Database exists: {db_path.exists()}")

if not db_path.exists():
    raise SystemExit("ERROR: SQLite database was not created.")

with sqlite3.connect(str(db_path)) as connection:
    connection.row_factory = sqlite3.Row

    integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]

    print(f"SQLite integrity: {integrity}")

    state = connection.execute("""
        SELECT logs_received,
               windows_completed,
               unknown_events_received,
               current_window_json,
               updated_at
        FROM live_service_state
        WHERE singleton_id = 1
    """).fetchone()

    if state is None:
        raise SystemExit("ERROR: Persistent service state not found.")

    event_count = connection.execute("SELECT COUNT(*) FROM ingested_events").fetchone()[0]

    window_count = connection.execute("SELECT COUNT(*) FROM live_windows").fetchone()[0]

    print(f"Stored log counter: {state['logs_received']}")
    print(f"Stored completed-window counter: {state['windows_completed']}")
    print(f"Stored unknown-event counter: {state['unknown_events_received']}")
    print(f"Event rows in SQLite: {event_count}")
    print(f"Completed-window rows in SQLite: {window_count}")

    import json

    pending = json.loads(state["current_window_json"])
    print(f"Persisted unfinished-window lines: {len(pending)}")
    print(f"Last state update: {state['updated_at']}")

    latest = connection.execute("""
        SELECT window_id, payload_json
        FROM live_windows
        ORDER BY window_id DESC
        LIMIT 1
    """).fetchone()

    if latest:
        window = json.loads(latest["payload_json"])
        print(f"Latest saved window ID: {latest['window_id']}")
        print(f"Latest saved decision: {window.get('decision')}")
        print(f"Latest saved unknown-event rate: {window.get('unknown_event_rate')}")

    if integrity != "ok":
        raise SystemExit("ERROR: SQLite integrity check failed.")

print("\nDatabase inspection completed.")
