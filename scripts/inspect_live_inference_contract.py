import inspect
import json
from pathlib import Path

import pandas as pd
import torch

ROOT = Path(__file__).resolve().parents[1]


def show_source(relative_path, max_lines=130):
    path = ROOT / relative_path
    print(f"\n{'=' * 24} {relative_path} {'=' * 24}")

    if not path.exists():
        print("FILE NOT FOUND")
        return

    lines = path.read_text(encoding="utf-8-sig").splitlines()
    for i, line in enumerate(lines[:max_lines], start=1):
        print(f"{i:4}: {line}")

    if len(lines) > max_lines:
        print(f"... {len(lines) - max_lines} additional lines omitted")


def inspect_checkpoint():
    path = ROOT / "artifacts/lstm_next_event.pt"
    print("\n================ LSTM CHECKPOINT ================")

    checkpoint = torch.load(
        path,
        map_location="cpu",
        weights_only=True,
    )

    print("Top-level keys:", list(checkpoint.keys()))

    config = checkpoint.get("config", {})
    print("\nExact model configuration:")
    print(json.dumps(config, indent=2, default=str))

    state = checkpoint.get("model_state_dict", {})
    print("\nModel state-dictionary parameters:")
    for key, tensor in state.items():
        if torch.is_tensor(tensor):
            print(f"  {key}: {tuple(tensor.shape)}")


def inspect_drain_api():
    print("\n================ DRAIN3 API ================")

    try:
        from drain3.file_persistence import FilePersistence
        from drain3.template_miner import TemplateMiner

        for method in (
            TemplateMiner.__init__,
            TemplateMiner.match,
            TemplateMiner.add_log_message,
            FilePersistence.__init__,
        ):
            try:
                print(f"\n{method.__qualname__}{inspect.signature(method)}")
            except (TypeError, ValueError):
                print(method.__qualname__, "(signature unavailable)")

        print("\nDrain3 match implementation:")
        try:
            print(inspect.getsource(TemplateMiner.match))
        except (OSError, TypeError):
            print("Source unavailable in this installation.")

    except Exception as exc:
        print("Could not inspect Drain3:", repr(exc))


def inspect_vocabulary_and_examples():
    print("\n================ VOCABULARY ================")

    vocabulary_path = ROOT / "artifacts/event_token_vocabulary_v2.json"
    vocabulary = json.loads(
        vocabulary_path.read_text(encoding="utf-8")
    )

    original_to_token = vocabulary["original_event_id_to_token"]
    print("Padding token:", vocabulary["padding_token"])
    print("Unknown token:", vocabulary["unknown_token"])
    print("Vocabulary size:", vocabulary["vocabulary_size"])
    print("Sample original event ID -> token:")
    for event_id, token in list(original_to_token.items())[:12]:
        print(f"  {event_id} -> {token}")

    print("\n================ FROZEN EVENT MATCHES ================")

    path = ROOT / "data/processed/bgl_frozen_events.parquet"
    df = pd.read_parquet(path)

    columns = [
        c for c in [
            "line_number",
            "label",
            "message",
            "frozen_event_id",
            "frozen_template",
            "template_match_status",
            "split",
        ]
        if c in df.columns
    ]

    if "template_match_status" in df.columns:
        print("Match-status counts:")
        print(df["template_match_status"].value_counts(dropna=False).to_string())

        for status, group in df.groupby(
            "template_match_status", dropna=False
        ):
            print(f"\nExamples for status {status!r}:")
            print(group[columns].head(3).to_string(index=False))
    else:
        print("No template_match_status column found.")
        print("Available columns:", list(df.columns))


def main():
    show_source("scripts/match_frozen_templates.py", 125)
    show_source("scripts/score_lstm_known_runs.py", 100)
    show_source("src/zentinel/parsing/drain_parser.py", 115)
    inspect_checkpoint()
    inspect_drain_api()
    inspect_vocabulary_and_examples()


if __name__ == "__main__":
    main()
