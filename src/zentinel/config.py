import hashlib
import json
import os
from pathlib import Path

# Base directories
ROOT_DIR = Path(__file__).resolve().parents[2]
ARTIFACTS_DIR = ROOT_DIR / "artifacts"
REPORTS_DIR = ROOT_DIR / "reports"
DATA_DIR = ROOT_DIR / "data"
RAW_LOG_PATH = DATA_DIR / "raw" / "BGL.log"

# Standard frozen model / inference assets
DRAIN_STATE_PATH = ARTIFACTS_DIR / "drain3_train_state_0_7.bin"
VOCAB_PATH = ARTIFACTS_DIR / "event_token_vocabulary_v2.json"
MODEL_PATH = ARTIFACTS_DIR / "lstm_next_event.pt"
MODEL_THRESHOLD_PATH = ARTIFACTS_DIR / "lstm_known_run_threshold.json"
DECISION_REPORT_PATH = REPORTS_DIR / "lstm_three_way_test_decision_report.json"
MANIFEST_PATH = ARTIFACTS_DIR / "manifest.json"

# Ingestion constants
WINDOW_SIZE = 100
PAD_TOKEN = 0
UNKNOWN_TOKEN = 1
MAX_RECENT_WINDOWS = 500

REQUIRED_FILES = [
    DRAIN_STATE_PATH,
    VOCAB_PATH,
    MODEL_PATH,
    MODEL_THRESHOLD_PATH,
    DECISION_REPORT_PATH,
]


def verify_manifest(
    manifest_path: Path | None = None,
    root_dir: Path | None = None,
) -> None:
    """
    Verify SHA-256 integrity of all artifacts against manifest.json.
    Raises RuntimeError if any file is missing or checksum does not match.
    """
    manifest_file = manifest_path or MANIFEST_PATH
    base_dir = root_dir or ROOT_DIR

    if not manifest_file.is_file():
        raise FileNotFoundError(
            f"Artifact manifest missing at {manifest_file}. Cannot verify system integrity."
        )

    try:
        data = json.loads(manifest_file.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RuntimeError(f"Failed to parse manifest {manifest_file}: {exc}") from exc

    artifacts = data.get("artifacts", {})
    if not artifacts:
        raise RuntimeError(f"Manifest {manifest_file} contains no artifact entries.")

    for rel_path, spec in artifacts.items():
        expected_sha = spec.get("sha256")
        target_path = base_dir / rel_path

        if not target_path.is_file():
            raise FileNotFoundError(
                f"Required artifact missing: {target_path} (from manifest {rel_path})"
            )

        hasher = hashlib.sha256()
        with target_path.open("rb") as f:
            while chunk := f.read(65536):
                hasher.update(chunk)
        actual_sha = hasher.hexdigest()

        if actual_sha != expected_sha:
            raise RuntimeError(
                f"Artifact integrity mismatch for {rel_path}: "
                f"expected {expected_sha}, found {actual_sha}."
            )


class ZentinelConfig:
    """Runtime configuration parsed from environment variables."""

    def __init__(self):
        self.root_dir = ROOT_DIR
        self.web_concurrency = int(os.getenv("WEB_CONCURRENCY", "1"))
        self.host = os.getenv("ZENTINEL_HOST", "127.0.0.1")
        self.port = int(os.getenv("ZENTINEL_PORT", "8001"))
        self.db_path = Path(
            os.getenv(
                "ZENTINEL_DB_PATH",
                str(DATA_DIR / "processed/zentinel_live.sqlite3"),
            )
        )
        if not self.db_path.is_absolute():
            self.db_path = ROOT_DIR / self.db_path

        # Security
        self.api_key_sha256 = os.getenv("ZENTINEL_API_KEY_SHA256", "")
        cors_raw = os.getenv(
            "ZENTINEL_CORS_ORIGINS",
            "http://localhost:8501,http://127.0.0.1:8501,http://localhost:8502,http://127.0.0.1:8502",
        )
        self.cors_origins = [
            origin.strip()
            for origin in cors_raw.split(",")
            if origin.strip()
        ]
        self.rate_limit_rps = float(os.getenv("ZENTINEL_RATE_LIMIT_RPS", "100.0"))
        self.rate_limit_burst = int(os.getenv("ZENTINEL_RATE_LIMIT_BURST", "200"))

        # Retention
        self.idempotency_retention_days = int(
            os.getenv("ZENTINEL_IDEMPOTENCY_RETENTION_DAYS", "7")
        )
        self.event_retention_days = int(
            os.getenv("ZENTINEL_EVENT_RETENTION_DAYS", "30")
        )

        # Windowing
        self.window_size = WINDOW_SIZE
        self.max_recent_windows = MAX_RECENT_WINDOWS
