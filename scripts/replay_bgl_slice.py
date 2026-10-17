import argparse
from collections import Counter
from itertools import islice
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
LOG_PATH = ROOT / "data/raw/BGL.log"


def main():
    parser = argparse.ArgumentParser(
        description="Replay a bounded slice of the BGL dataset into Zentinel."
    )
    parser.add_argument("--start-line", type=int, default=4400001)
    parser.add_argument("--count", type=int, default=200)
    parser.add_argument("--url", default="http://127.0.0.1:8001")
    args = parser.parse_args()

    if args.start_line < 1 or args.count < 1:
        raise ValueError("Start line and count must be positive.")

    if not LOG_PATH.is_file():
        raise FileNotFoundError(f"BGL dataset not found: {LOG_PATH}")

    print(f"Dataset: {LOG_PATH}")
    print(f"Starting at source line: {args.start_line:,}")
    print(f"Requested lines: {args.count:,}")
    print(f"API: {args.url}")

    with LOG_PATH.open(
        "r",
        encoding="utf-8",
        errors="replace",
    ) as file:
        selected = list(
            islice(file, args.start_line - 1, args.start_line - 1 + args.count)
        )

    if not selected:
        raise ValueError("No lines found at the requested starting position.")

    if len(selected) < args.count:
        print(
            f"WARNING: Only {len(selected)} lines are available "
            "from this starting position."
        )

    event_statuses = Counter()
    decisions = Counter()
    completed_windows = []
    failures = 0

    with httpx.Client(timeout=30.0) as client:
        for index, raw_line in enumerate(selected, start=1):
            line = raw_line.rstrip("\r\n")

            try:
                response = client.post(
                    f"{args.url.rstrip('/')}/api/v1/live/ingest",
                    json={"raw_line": line},
                )
                response.raise_for_status()
                result = response.json()

                event_statuses[result["event"]["event_status"]] += 1

                window = result.get("window_decision")
                if window:
                    decisions[window["decision"]] += 1
                    completed_windows.append(window)

            except (httpx.HTTPError, ValueError, KeyError) as exc:
                failures += 1
                print(f"Request failed at sample line {index}: {exc}")

            if index % 50 == 0 or index == len(selected):
                print(f"Submitted {index}/{len(selected)} lines")

    print("\n========== REAL BGL REPLAY RESULTS ==========")
    print(f"Submitted lines: {len(selected):,}")
    print(f"Request failures: {failures:,}")
    print(f"Event match statuses: {dict(event_statuses)}")
    print(f"Completed windows: {len(completed_windows):,}")
    print(f"Window decisions: {dict(decisions)}")

    for window in completed_windows:
        print(
            f"Window {window['window_id']}: "
            f"unknown_rate={window['unknown_event_rate']:.1%}, "
            f"scorable={window['model_scorable']}, "
            f"score={window['lstm_anomaly_score']}, "
            f"decision={window['decision']}"
        )

    print("\nReplay finished. Check the live dashboard for updated results.")


if __name__ == "__main__":
    main()
