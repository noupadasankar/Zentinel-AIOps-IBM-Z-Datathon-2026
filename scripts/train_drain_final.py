"""Train the selected Drain3 configuration on training data only."""

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
STATE_PATH = ROOT / "artifacts" / "drain3_train_state_0_7.bin"
REPORT_PATH = ROOT / "reports" / "drain3_train_summary_0_7.json"

SIMILARITY_THRESHOLD = 0.70
DRAIN_DEPTH = 4
PROGRESS_INTERVAL = 250_000


def build_template_input(record) -> str:
    """Construct model input without leaking the reference label."""
    values = [
        record.event_type,
        record.component,
        record.severity,
        record.message,
    ]
    return " ".join(value for value in values if value) or ("[EMPTY_LOG_MESSAGE]")


def main() -> None:
    for path in (INPUT_PATH, SPLIT_PATH):
        if not path.is_file():
            raise FileNotFoundError(f"Required file not found: {path}")

    if STATE_PATH.exists():
        raise SystemExit(
            f"Model state already exists: {STATE_PATH}\nDo not overwrite a previously trained model blindly."
        )

    with SPLIT_PATH.open("r", encoding="utf-8") as file:
        split_report = json.load(file)

    train_end = split_report["line_boundaries"]["train"]["end"]

    config = TemplateMinerConfig()
    config.drain_sim_th = SIMILARITY_THRESHOLD
    config.drain_depth = DRAIN_DEPTH

    miner = TemplateMiner(persistence_handler=None, config=config)

    labels = Counter()
    empty_messages = 0
    processed = 0

    print("========== FINAL DRAIN3 TRAINING ==========")
    print(f"Training records: {train_end:,}")
    print(f"Similarity threshold: {SIMILARITY_THRESHOLD}")
    print("Validation and test logs will not be used for fitting.\n")

    with INPUT_PATH.open(
        "r",
        encoding="utf-8",
        errors="replace",
    ) as source:
        for line_number, raw_line in enumerate(source, start=1):
            if line_number > train_end:
                break

            record = parse_bgl_line(
                raw_line.rstrip("\r\n"),
                line_number,
            )

            miner.add_log_message(build_template_input(record))

            labels[record.label] += 1
            empty_messages += int(not record.message)
            processed += 1

            if processed % PROGRESS_INTERVAL == 0:
                print(f"Processed {processed:,} records | templates={len(miner.drain.clusters):,}")

    if processed != train_end:
        raise RuntimeError(f"Expected {train_end:,} training records; processed {processed:,}.")

    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    miner.persistence_handler = FilePersistence(str(STATE_PATH))
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
        "similarity_threshold": SIMILARITY_THRESHOLD,
        "drain_depth": DRAIN_DEPTH,
        "training_end_line_inclusive": train_end,
        "training_records_processed": processed,
        "validation_or_test_used_for_fitting": False,
        "labels_used_as_model_inputs": False,
        "label_counts_for_audit_only": dict(labels.most_common()),
        "empty_message_records": empty_messages,
        "unique_training_templates": len(clusters),
        "model_state_file": str(STATE_PATH.relative_to(ROOT)),
        "model_state_size_bytes": STATE_PATH.stat().st_size,
        "top_templates": top_templates,
    }

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)

    with REPORT_PATH.open("w", encoding="utf-8") as file:
        json.dump(report, file, indent=2, ensure_ascii=False)

    print("\n========== TRAINING RESULTS ==========")
    print(f"Training records: {processed:,}")
    print(f"Unique templates: {len(clusters):,}")
    print(f"Empty messages: {empty_messages:,}")
    print(f"Model state size: {STATE_PATH.stat().st_size:,} bytes")

    print("\nTop 10 templates:")
    for item in top_templates[:10]:
        print(f"  ID {item['event_id']}: {item['training_count']:,} records | {item['template']}")

    print(f"\nSaved model: {STATE_PATH}")
    print(f"Report: {REPORT_PATH}")
    print("\nSUCCESS: Final training-only Drain3 model saved.")


if __name__ == "__main__":
    main()
