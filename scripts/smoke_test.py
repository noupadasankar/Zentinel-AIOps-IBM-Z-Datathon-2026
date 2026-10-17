import sys
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from fastapi.testclient import TestClient

from zentinel.api.live_api import app


def smoke_test():
    print("Executing Zentinel live smoke test...")
    with TestClient(app) as client:
        res = client.get("/health")
        if res.status_code != 200:
            print(f"FAILED: /health returned {res.status_code}")
            sys.exit(1)
        health_data = res.json()
        print(f"PASS: /health returned ok (vocab size={health_data['vocabulary_size']})")

        sample_bgl = (
            "- 1117838570 2005.06.03 "
            "R02-M1-N0-C:J12-U11 "
            "2005-06-03-15.42.50.363779 "
            "R02-M1-N0-C:J12-U11 "
            "RAS KERNEL INFO instruction cache parity error corrected"
        )
        decision = None
        for i in range(100):
            r = client.post(
                "/api/v1/live/ingest",
                json={"raw_line": sample_bgl, "idempotency_key": f"smoke-key-{i}"},
            )
            if r.status_code != 200:
                print(f"FAILED: /api/v1/live/ingest line {i + 1} returned {r.status_code}: {r.text}")
                sys.exit(1)
            body = r.json()
            if body.get("window_completed"):
                decision = body.get("window_decision")

        if not decision:
            print("FAILED: 100th line did not complete a window.")
            sys.exit(1)

        print(
            f"PASS: 100 lines ingested. Window decision: {decision['decision']} "
            f"(score={decision['lstm_anomaly_score']:.4f})"
        )

        status_res = client.get("/api/v1/live/status")
        if status_res.status_code != 200:
            print(f"FAILED: /api/v1/live/status returned {status_res.status_code}")
            sys.exit(1)
        print("PASS: /api/v1/live/status verified.")

    print("\nALL SMOKE CHECKS PASSED SUCCESSFULLY.")


if __name__ == "__main__":
    smoke_test()
