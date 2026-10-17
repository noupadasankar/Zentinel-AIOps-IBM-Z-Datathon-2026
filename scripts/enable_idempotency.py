from pathlib import Path

ROOT = Path.cwd()
store_path = ROOT / "src/zentinel/api/live_store.py"
api_path = ROOT / "src/zentinel/api/live_api.py"


def replace_once(text, old, new, description):
    count = text.count(old)
    if count != 1:
        raise RuntimeError(
            f"{description}: expected exactly one match, found {count}. "
            "No files have been written."
        )
    return text.replace(old, new, 1)


store = store_path.read_text(encoding="utf-8-sig")
api = api_path.read_text(encoding="utf-8-sig")

# Add the durable idempotency table.
store = replace_once(
    store,
    '''            self._connection.execute("""
                CREATE INDEX IF NOT EXISTS idx_events_status''',
    '''            self._connection.execute("""
                CREATE TABLE IF NOT EXISTS ingestion_idempotency (
                    idempotency_key TEXT PRIMARY KEY,
                    response_json TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)

            self._connection.execute("""
                CREATE INDEX IF NOT EXISTS idx_events_status''',
    "SQLite idempotency table",
)

# Add a lookup method before saving a new event.
store = replace_once(
    store,
    "    def save_event_and_state(\n",
    '''    def get_idempotent_response(self, idempotency_key):
        """Return the original response for a previously accepted key."""
        with self._lock:
            row = self._connection.execute(
                """
                SELECT response_json
                FROM ingestion_idempotency
                WHERE idempotency_key = ?
                """,
                (idempotency_key,),
            ).fetchone()

        if row is None:
            return None

        return json.loads(row["response_json"])

    def save_event_and_state(
''',
    "SQLite idempotency lookup method",
)

# Extend the existing atomic save method.
store = replace_once(
    store,
    '''        current_window,
        window_result=None,
    ):
        """Save an event, its current state, and an optional completed window."""''',
    '''        current_window,
        window_result=None,
        idempotency_key=None,
        response=None,
    ):
        """Save an event, state, completed window, and idempotency response."""''',
    "SQLite save-method signature",
)

# Persist the original response in the same transaction as event/state updates.
store = replace_once(
    store,
    '''                if window_result is not None:
                    self._connection.execute("""''',
    '''                if idempotency_key is not None and response is not None:
                    self._connection.execute("""
                        INSERT INTO ingestion_idempotency (
                            idempotency_key,
                            response_json
                        ) VALUES (?, ?)
                    """, (
                        idempotency_key,
                        json.dumps(response, ensure_ascii=False),
                    ))

                if window_result is not None:
                    self._connection.execute("""''',
    "atomic idempotency response persistence",
)

# Allow an optional key on each request.
api = replace_once(
    api,
    '''class IngestRequest(BaseModel):
    raw_line: str = Field(min_length=1, max_length=20000)''',
    '''class IngestRequest(BaseModel):
    raw_line: str = Field(min_length=1, max_length=20000)
    idempotency_key: Optional[str] = Field(
        default=None,
        min_length=1,
        max_length=512,
    )''',
    "ingestion request model",
)

# Return the original result before making any state changes.
api = replace_once(
    api,
    '''    with STATE_LOCK:
        next_line_number = STATE["logs_received"] + 1''',
    '''    with STATE_LOCK:
        if request.idempotency_key:
            cached_response = STORE.get_idempotent_response(
                request.idempotency_key
            )
            if cached_response is not None:
                cached_response["idempotency_replayed"] = True
                return cached_response

        next_line_number = STATE["logs_received"] + 1''',
    "duplicate request guard",
)

# Atomically save the response together with the event/state update.
api = replace_once(
    api,
    '''        STORE.save_event_and_state(
            event=event,
            state=STATE,
            current_window=CURRENT_WINDOW,
            window_result=window_result,
        )''',
    '''        STORE.save_event_and_state(
            event=event,
            state=STATE,
            current_window=CURRENT_WINDOW,
            window_result=window_result,
            idempotency_key=request.idempotency_key,
            response=response,
        )''',
    "SQLite idempotency save call",
)

# Keep originals and write patched versions only after all checks pass.
store_backup = store_path.with_suffix(".py.idempotency.bak")
api_backup = api_path.with_suffix(".py.idempotency.bak")

store_backup.write_text(
    store_path.read_text(encoding="utf-8-sig"),
    encoding="utf-8",
)
api_backup.write_text(
    api_path.read_text(encoding="utf-8-sig"),
    encoding="utf-8",
)

store_path.write_text(store, encoding="utf-8")
api_path.write_text(api, encoding="utf-8")

print("SUCCESS: Idempotency protection added.")
print(f"Store backup: {store_backup}")
print(f"API backup:   {api_backup}")
print("Existing model files and thresholds were not modified.")
