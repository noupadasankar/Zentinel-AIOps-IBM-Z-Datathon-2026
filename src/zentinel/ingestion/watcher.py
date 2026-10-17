import hashlib
import json
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

from zentinel.config import DATA_DIR

DEFAULT_FILE = DATA_DIR / "ingest/incoming.log"
DEFAULT_CURSOR_PATH = DATA_DIR / "processed/log_tailer_state.json"
DEFAULT_DEAD_LETTER_PATH = DATA_DIR / "processed/rejected_live_lines.jsonl"


def compute_file_id(file_path: Path) -> str:
    """
    Compute a unique file identifier based on device, inode, and first line hash:
    file_id = f"{device}:{inode}:{sha256(first_line)}"
    """
    st = file_path.stat()
    device = getattr(st, "st_dev", 0)
    inode = getattr(st, "st_ino", 0)

    first_line = b""
    try:
        with file_path.open("rb") as f:
            first_line = f.readline()
    except OSError:
        pass

    first_line_hash = hashlib.sha256(first_line).hexdigest()
    return f"{device}:{inode}:{first_line_hash}"


def generate_idempotency_key(file_id: str, offset: int) -> str:
    """Generate SHA-256 idempotency key: sha256(file_id:offset)."""
    return hashlib.sha256(f"{file_id}:{offset}".encode()).hexdigest()


class LogFileWatcher:
    """Watches an appended log file and streams new lines to the Zentinel API."""

    def __init__(
        self,
        file_path: Path = DEFAULT_FILE,
        api_url: str = "http://127.0.0.1:8001",
        api_key: str | None = None,
        cursor_path: Path = DEFAULT_CURSOR_PATH,
        dead_letter_path: Path = DEFAULT_DEAD_LETTER_PATH,
        poll_seconds: float = 0.25,
        from_start: bool = False,
        initial_backoff: float = 0.5,
        max_backoff: float = 16.0,
    ):
        self.file_path = Path(file_path).resolve()
        self.api_url = api_url.rstrip("/")
        self.api_key = api_key or os.getenv("ZENTINEL_API_KEY")
        self.cursor_path = Path(cursor_path)
        self.dead_letter_path = Path(dead_letter_path)
        self.poll_seconds = max(0.01, float(poll_seconds))
        self.from_start = from_start
        self.initial_backoff = initial_backoff
        self.max_backoff = max_backoff

        self.current_backoff = initial_backoff
        self.file_id = ""
        self.offset = 0

    def save_cursor(self, offset: int, file_id: str) -> None:
        """Atomically persist cursor state."""
        self.cursor_path.parent.mkdir(parents=True, exist_ok=True)
        state = {
            "file_path": str(self.file_path),
            "file_id": file_id,
            "byte_offset": int(offset),
            "updated_at_unix": time.time(),
        }
        temp_path = self.cursor_path.with_suffix(".json.tmp")
        temp_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
        os.replace(temp_path, self.cursor_path)

    def load_cursor(self) -> tuple[int, str]:
        """Load cursor offset and file_id, detecting file mismatch or resets."""
        current_file_id = compute_file_id(self.file_path)

        if self.from_start:
            self.save_cursor(0, current_file_id)
            return 0, current_file_id

        if self.cursor_path.exists():
            try:
                state = json.loads(self.cursor_path.read_text(encoding="utf-8"))
                stored_file = state.get("file_path")
                if stored_file == str(self.file_path):
                    stored_file_id = state.get("file_id", "")
                    stored_offset = int(state.get("byte_offset", 0))

                    # Detect file replacement by inode or first-line change
                    if stored_file_id and stored_file_id != current_file_id:
                        self.save_cursor(0, current_file_id)
                        return 0, current_file_id

                    # Detect file truncation
                    file_size = self.file_path.stat().st_size
                    if file_size < stored_offset:
                        self.save_cursor(0, current_file_id)
                        return 0, current_file_id

                    return stored_offset, current_file_id
            except (json.JSONDecodeError, OSError, KeyError, ValueError):
                pass

        # Default to EOF if no cursor exists
        file_size = self.file_path.stat().st_size
        self.save_cursor(file_size, current_file_id)
        return file_size, current_file_id

    def save_dead_letter(self, line: str, offset: int, error: str) -> None:
        """Write rejected line to dead-letter queue with correct source_file."""
        self.dead_letter_path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "source_file": str(self.file_path),
            "byte_offset": offset,
            "raw_line": line,
            "error": error,
            "recorded_at_unix": time.time(),
        }
        with self.dead_letter_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def run_once(
        self,
        client: httpx.Client,
        on_decision: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any] | None:
        """
        Process the next available complete log line if present.
        Returns response dict if processed, or None if no full line available or retrying.
        """
        if not self.file_path.exists():
            time.sleep(self.poll_seconds)
            return None

        # Check for file rotation, replacement, or truncation
        current_file_id = compute_file_id(self.file_path)
        file_size = self.file_path.stat().st_size

        if (self.file_id and current_file_id != self.file_id) or file_size < self.offset:
            self.offset = 0
            self.file_id = current_file_id
            self.save_cursor(self.offset, self.file_id)

        with self.file_path.open("rb") as source:
            source.seek(self.offset)
            raw_bytes = source.readline()

            if not raw_bytes or not raw_bytes.endswith(b"\n"):
                time.sleep(self.poll_seconds)
                return None

            next_offset = source.tell()

        line = raw_bytes.decode("utf-8", errors="replace").rstrip("\r\n")

        # Skip empty lines
        if not line.strip():
            self.offset = next_offset
            self.save_cursor(self.offset, self.file_id)
            return None

        idempotency_key = generate_idempotency_key(self.file_id, self.offset)
        headers = {}
        if self.api_key:
            headers["X-API-Key"] = self.api_key

        try:
            response = client.post(
                f"{self.api_url}/api/v1/live/ingest",
                json={
                    "raw_line": line,
                    "idempotency_key": idempotency_key,
                },
                headers=headers,
                timeout=30.0,
            )
        except httpx.RequestError:
            # Network failure: back off and retry
            time.sleep(self.current_backoff)
            self.current_backoff = min(self.current_backoff * 2, self.max_backoff)
            return None

        # Dead-letter only 400 and 422 (and 409 key collision)
        if response.status_code in (400, 422, 409):
            self.save_dead_letter(
                line,
                self.offset,
                f"HTTP {response.status_code}: {response.text}",
            )
            self.offset = next_offset
            self.save_cursor(self.offset, self.file_id)
            self.current_backoff = self.initial_backoff
            return {"status": "rejected", "code": response.status_code}

        # Retry 408, 429, and 5xx with backoff
        if response.status_code in (408, 429) or response.status_code >= 500:
            time.sleep(self.current_backoff)
            self.current_backoff = min(self.current_backoff * 2, self.max_backoff)
            return None

        if response.status_code != 200:
            time.sleep(self.current_backoff)
            self.current_backoff = min(self.current_backoff * 2, self.max_backoff)
            return None

        # Successfully ingested
        self.current_backoff = self.initial_backoff
        self.offset = next_offset
        self.save_cursor(self.offset, self.file_id)

        try:
            result = response.json()
        except ValueError:
            return None

        if on_decision and result.get("window_completed") and result.get("window_decision"):
            on_decision(result["window_decision"])

        return result

    def start(self) -> None:
        """Start the continuous watching loop."""
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        self.file_path.touch(exist_ok=True)

        self.offset, self.file_id = self.load_cursor()

        print("\n========== ZENTINEL FILE WATCHER ==========")
        print(f"Input file: {self.file_path}")
        print(f"API URL: {self.api_url}")
        print(f"Initial Offset: {self.offset:,}")
        print("Watching for appended log lines... Press Ctrl+C to stop.")

        with httpx.Client(timeout=30.0) as client:
            try:
                while True:
                    self.run_once(
                        client,
                        on_decision=lambda w: print(
                            f"Window {w.get('window_id')}: decision={w.get('decision')} "
                            f"(score={w.get('lstm_anomaly_score')})"
                        ),
                    )
            except KeyboardInterrupt:
                print("\nWatcher stopped.")
