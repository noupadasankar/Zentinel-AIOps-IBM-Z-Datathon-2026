from pathlib import Path

path = Path("dashboard/live_app.py")
text = path.read_text(encoding="utf-8-sig")

old = '''st.warning(
    "Research prototype: the ingestion state is held in server memory. "
    "Restarting the live API clears its counters, incomplete window, "
    "and recent-window history. This page does not ingest operating-system "
    "logs automatically."
)'''

new = '''st.warning(
    "Research prototype: event metadata, counters, unfinished windows, "
    "and completed decisions are persisted in local SQLite. Raw log lines "
    "are not stored. Input currently uses BGL-format logs through manual "
    "submission or the file watcher; automatic operating-system log "
    "discovery is not implemented. Exactly-once delivery across crashes "
    "is not yet guaranteed."
)'''

if old not in text:
    raise SystemExit(
        "Expected warning text not found. No changes were made."
    )

backup = path.with_suffix(".py.banner.bak")
backup.write_text(text, encoding="utf-8")
path.write_text(text.replace(old, new, 1), encoding="utf-8")

print("SUCCESS: Live dashboard warning updated.")
print(f"Backup saved: {backup}")
