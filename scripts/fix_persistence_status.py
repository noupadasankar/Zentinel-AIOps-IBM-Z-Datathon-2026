from pathlib import Path

path = Path("src/zentinel/api/live_api.py")
text = path.read_text(encoding="utf-8-sig")

old = '''            "persistence_warning": (
                "Ingestion counters and unfinished windows are in memory "
                "only and reset when this process restarts."
            ),'''

new = '''            "persistence": "sqlite",
            "database": "data/processed/zentinel_live.sqlite3",
            "persistence_note": (
                "SQLite persists event metadata, counters, unfinished "
                "windows, and completed-window decisions. Raw log lines "
                "are not stored."
            ),'''

if old not in text:
    raise SystemExit(
        "Expected warning text not found. No changes were made; "
        "inspect the live_status() function before editing."
    )

backup = path.with_suffix(".py.persistence_status.bak")
backup.write_text(text, encoding="utf-8")
path.write_text(text.replace(old, new, 1), encoding="utf-8")

print("SUCCESS: Persistence status message updated.")
print(f"Backup saved to: {backup}")
