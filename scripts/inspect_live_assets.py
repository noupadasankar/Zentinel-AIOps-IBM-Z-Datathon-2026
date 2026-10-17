import ast
import json
from pathlib import Path

import pandas as pd
import torch

ROOT = Path(__file__).resolve().parents[1]


def inspect_json(relative_path):
    path = ROOT / relative_path
    print(f"\n--- {relative_path} ---")

    if not path.exists():
        print("MISSING")
        return

    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        print("Type:", type(data).__name__)
        print("Sample:", str(data)[:500])
        return

    print("Top-level keys:", list(data.keys())[:30])

    for key, value in data.items():
        if isinstance(value, dict):
            print(f"{key}: mapping with {len(value):,} entries")
            for sample_key, sample_value in list(value.items())[:5]:
                print(f"  {sample_key!r}: {str(sample_value)[:160]}")
        elif isinstance(value, list):
            print(f"{key}: list with {len(value):,} entries")
            print("  Sample:", value[:5])
        else:
            print(f"{key}: {str(value)[:200]}")


def inspect_parquet(relative_path):
    path = ROOT / relative_path
    print(f"\n--- {relative_path} ---")

    if not path.exists():
        print("MISSING")
        return

    df = pd.read_parquet(path)
    print("Shape:", df.shape)
    print("Columns:", list(df.columns))

    wanted = [
        "line_number",
        "label",
        "timestamp",
        "event_id",
        "event_template",
        "template_change_type",
        "parse_status",
        "message",
        "target_anomaly",
        "split",
    ]
    sample_columns = [c for c in wanted if c in df.columns]

    if sample_columns:
        print("\nFirst five rows:")
        print(df[sample_columns].head(5).to_string(index=False))

    if "event_id" in df.columns:
        ids = df["event_id"]
        print("\nUnique event IDs:", int(ids.nunique(dropna=True)))
        print("Null event IDs:", int(ids.isna().sum()))


def inspect_python(relative_path):
    path = ROOT / relative_path
    print(f"\n--- Definitions in {relative_path} ---")

    if not path.exists():
        print("MISSING")
        return

    try:
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                print(f"Function line {node.lineno}: {node.name}")
            elif isinstance(node, ast.ClassDef):
                print(f"Class line {node.lineno}: {node.name}")
    except Exception as exc:
        print("Could not inspect definitions:", exc)


def main():
    inspect_json("artifacts/event_token_vocabulary_v2.json")
    inspect_json("artifacts/lstm_known_run_threshold.json")
    inspect_json("reports/drain3_train_summary_0_7.json")
    inspect_json("reports/window_feature_columns.json")

    inspect_parquet("data/processed/bgl_frozen_events.parquet")

    model_path = ROOT / "artifacts/lstm_next_event.pt"
    print("\n--- LSTM checkpoint ---")

    if model_path.exists():
        try:
            state = torch.load(
                model_path,
                map_location="cpu",
                weights_only=True,
            )
            if isinstance(state, dict):
                print("Checkpoint keys:", list(state.keys())[:30])
                for key, value in list(state.items())[:20]:
                    if torch.is_tensor(value):
                        print(f"  {key}: shape={tuple(value.shape)}")
                    elif isinstance(value, dict):
                        print(f"  {key}: nested dictionary")
                    else:
                        print(f"  {key}: {type(value).__name__}")
            else:
                print("Checkpoint type:", type(state).__name__)
        except Exception as exc:
            print("Checkpoint inspection error:", repr(exc))
    else:
        print("MISSING")

    drain_path = ROOT / "artifacts/drain3_train_state_0_7.bin"
    print("\nDrain3 frozen model state exists:", drain_path.exists())
    if drain_path.exists():
        print("Drain3 state bytes:", drain_path.stat().st_size)

    inspect_python("scripts/match_frozen_templates.py")
    inspect_python("scripts/build_event_sequences_v2.py")
    inspect_python("scripts/score_lstm_known_runs.py")
    inspect_python("src/zentinel/parsing/drain_parser.py")


if __name__ == "__main__":
    main()
