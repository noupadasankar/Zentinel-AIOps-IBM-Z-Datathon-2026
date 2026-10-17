from pathlib import Path

root = Path(__file__).resolve().parents[1]
api_path = root / "src/zentinel/api/live_api.py"
backup_path = api_path.with_suffix(".py.pre_persistence.bak")

text = api_path.read_text(encoding="utf-8-sig")

if "LiveStateStore" in text:
    raise SystemExit(
        "Persistence integration already appears to exist. "
        "Inspect live_api.py before applying another patch."
    )

def replace_once(source, old, new, description):
    count = source.count(old)
    if count != 1:
        raise RuntimeError(
            f"Expected one matching location for {description}, found {count}. "
            "No changes have been written."
        )
    return source.replace(old, new, 1)

patched = text

patched = replace_once(
    patched,
    "from zentinel.parsing.drain_parser import parse_bgl_line",
    (
        "from zentinel.parsing.drain_parser import parse_bgl_line\n"
        "from zentinel.api.live_store import LiveStateStore"
    ),
    "the parser import",
)

state_block = '''STATE = {
    "logs_received": 0,
    "windows_completed": 0,
    "unknown_events_received": 0,
}'''

state_replacement = '''STATE = {
    "logs_received": 0,
    "windows_completed": 0,
    "unknown_events_received": 0,
}

STORE = LiveStateStore(ROOT / "data/processed/zentinel_live.sqlite3")
RESTORED_STATE = STORE.load_state()

STATE.update(RESTORED_STATE["state"])
CURRENT_WINDOW.extend(RESTORED_STATE["current_window"])
RECENT_WINDOWS.extend(RESTORED_STATE["recent_windows"])'''

patched = replace_once(
    patched,
    state_block,
    state_replacement,
    "the in-memory state initialization",
)

patched = replace_once(
    patched,
    "        if len(CURRENT_WINDOW) == WINDOW_SIZE:",
    "        window_result = None\n\n        if len(CURRENT_WINDOW) == WINDOW_SIZE:",
    "the completed-window condition",
)

response_end = '''            response["window_decision"] = window_result

        return response'''

response_replacement = '''            response["window_decision"] = window_result

        STORE.save_event_and_state(
            event=event,
            state=STATE,
            current_window=CURRENT_WINDOW,
            window_result=window_result,
        )

        return response'''

patched = replace_once(
    patched,
    response_end,
    response_replacement,
    "the ingestion response ending",
)

backup_path.write_text(text, encoding="utf-8")
api_path.write_text(patched, encoding="utf-8")

print("SUCCESS: SQLite persistence was integrated.")
print(f"Updated API: {api_path}")
print(f"Original backup: {backup_path}")
print("Database path: data/processed/zentinel_live.sqlite3")
