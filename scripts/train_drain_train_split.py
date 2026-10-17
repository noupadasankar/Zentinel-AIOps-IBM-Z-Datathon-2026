"""Train Drain3 on the training period only; do not see future logs."""

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from drain3 import TemplateMiner
from drain3.file_persistence import FilePersistence
from drain3.template_miner_config import TemplateMinerConfig

from zentinel.parsing.drain_parser import parse_bgl_line

INPUT_PATH = ROOT / "data" / "raw" / "BGL.log"
SPLIT_PATH = ROOT / "reports" / "chronological_split_report.json"
STATE_PATH = ROOT / "artifacts" / "drain3_train_state.bin"
REPORT_PATH = ROOT / "reports" / "drain3_train_summary.json"

PROGRESS_INTERVAL = 250_000


def build_template_input(record) -> str:
    """Build model input without including the BGL target label."""
    parts = [
        record.event_type,
        record.component,
        record.severity,
        record.message,
    ]
    message = " ".join(part for part in parts if part)

    return message or "[EMPTY_LOG_MESSAGE]"


def main() -> None:
    for path in (INPUT_PATH, SPLIT_PATH):
        if not path.is_file():
            raise FileNotFoundError(f"Required file not found: {path}")

    # Use a separate state file. Never reuse the full-dataset state.
    if STATE_PATH.exists():
        raise SystemExit(
            f"Training state already exists: {STATE_PATH}\n"
            "Inspect the existing model before deciding whether to "
            "reuse or rebuild it. Do not overwrite it silently."
        )

    with SPLIT_PATH.open("r", encoding="utf-8") as file:
        split_report = json.load(file)

    train_end = split_report["line_boundaries"]["train"]["end"]

    if train_end <= 0:
        raise ValueError(f"Invalid training boundary: {train_end}")

    config = TemplateMinerConfig()
    config.drain_sim_th = 0.4
    config.drain_depth = 4

    # Intentionally use no persistence while fitting. This avoids
    # repeatedly serializing the model during millions of log updates.
    miner = TemplateMiner(persistence_handler=None, config=config)

    label_counts = Counter()
    parse_status_counts = Counter()
    records_processed = 0
    empty_messages = 0

    print("========== TRAINING-ONLY DRAIN3 ==========")
    print(f"Source: {INPUT_PATH}")
    print(f"Training rows: {train_end:,}")
    print("Validation and test rows will not be read during fitting.\n")

    with INPUT_PATH.open(
        "r",
        encoding="utf-8",
        errors="replace",
    ) as source:
        for line_number, raw_line in enumerate(source, start=1):
            if line_number > train_end:
                break

            raw_line = raw_line.rstrip("\r\n")
            record = parse_bgl_line(raw_line, line_number)

            # The label is recorded for auditing only; it is never
            # passed into Drain3 or included in the template input.
            label_counts[record.label] += 1
            parse_status_counts[record.parse_status] += 1

            if not record.message:
                empty_messages += 1

            miner.add_log_message(build_template_input(record))
            records_processed += 1

            if records_processed % PROGRESS_INTERVAL == 0:
                print(f"Trained on {records_processed:,} records | clusters={len(miner.drain.clusters):,}")

    if records_processed != train_end:
        raise RuntimeError(f"Expected {train_end:,} training records, processed {records_processed:,}.")

    # Persist only once training has finished.
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    persistence = FilePersistence(str(STATE_PATH))
    miner.persistence_handler = persistence
    miner.save_state("training_complete")

    clusters = sorted(
        miner.drain.clusters,
        key=lambda cluster: (-cluster.size, cluster.cluster_id),
    )

    top_templates = [
        {
            "event_id": cluster.cluster_id,
            "training_count": cluster.size,
            "template": cluster.get_template(),
        }
        for cluster in clusters[:30]
    ]

    report = {
        "dataset": "LogHub BGL",
        "method": "Training-period-only Drain3 template mining",
        "training_end_line_inclusive": train_end,
        "training_records_processed": records_processed,
        "validation_and_test_used_during_fit": False,
        "label_used_as_model_input": False,
        "drain_config": {
            "similarity_threshold": config.drain_sim_th,
            "depth": config.drain_depth,
        },
        "unique_training_templates": len(miner.drain.clusters),
        "label_counts_for_audit_only": dict(label_counts.most_common()),
        "parse_status_counts": dict(parse_status_counts),
        "empty_message_records": empty_messages,
        "model_state_file": str(STATE_PATH.relative_to(ROOT)),
        "model_state_size_bytes": STATE_PATH.stat().st_size,
        "top_training_templates": top_templates,
    }

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)

    with REPORT_PATH.open("w", encoding="utf-8") as file:
        json.dump(report, file, indent=2, ensure_ascii=False)

    print("\n========== TRAINING SUMMARY ==========")
    print(f"Training records: {records_processed:,}")
    print(f"Template count: {len(miner.drain.clusters):,}")
    print(f"Empty messages: {empty_messages:,}")
    print(f"Parser statuses: {dict(parse_status_counts)}")
    print(f"Model state size: {STATE_PATH.stat().st_size:,} bytes")

    print("\nTop 10 training templates:")
    for item in top_templates[:10]:
        print(f"  ID {item['event_id']}: {item['training_count']:,} logs | {item['template']}")

    print(f"\nSaved Drain3 state: {STATE_PATH}")
    print(f"Summary report: {REPORT_PATH}")
    print("\nSUCCESS: Training-period template mining completed.")


if __name__ == "__main__":
    main()
