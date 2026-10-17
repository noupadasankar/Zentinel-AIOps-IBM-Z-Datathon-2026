"""Deterministic local secret scan (documented complement to the CI gitleaks step).

Scans every Git-tracked text file plus untracked, non-ignored working-tree files
(binary/generated artifacts are verified by digest instead) with conservative
credential regexes and writes `reports/secret_scan.json`.

Usage:
    python scripts/secret_scan.py
"""
from __future__ import annotations

import json
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

PATTERNS = {
    "aws_access_key_id": re.compile(r"AKIA[0-9A-Z]{16}"),
    "aws_secret_access_key": re.compile(
        r"(?i)aws(.{0,6})?secret(.{0,6})?access(.{0,6})?key(.{0,6})[=:'\"]\s*[A-Za-z0-9/+=]{16,}"
    ),
    "private_key_block": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "github_token": re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    "slack_webhook": re.compile(r"https://hooks\.slack\.com/services/[A-Za-z0-9/]+"),
    "openai_style_key": re.compile(r"sk-[A-Za-z0-9]{20,}"),
    "generic_api_key_assignment": re.compile(r"(?i)\b(api[_-]?key|token|secret|password)\b['\"]?\s*[:=]\s*['\"][^'\"\s]{12,}['\"]"),
    "jwt": re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
}

# Binary/generated artifacts are verified by digest and are not secret-bearing text.
ALLOW_SUFFIX = (".log", ".ipynb", ".csv", ".jsonl", ".parquet", ".npz", ".pth", ".onnx", ".joblib", ".bin")
ALLOW_PREFIX = ("reports/", "data/")


def candidate_files() -> list[str]:
    tracked = subprocess.run(
        ["git", "ls-files"], cwd=REPO, capture_output=True, text=True, check=True
    ).stdout.splitlines()
    untracked = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    return sorted({*tracked, *untracked})


def main() -> int:
    findings = []
    scanned = 0
    for rel in candidate_files():
        path = REPO / rel
        if not path.is_file():
            continue
        if rel.endswith(ALLOW_SUFFIX) or rel.startswith(ALLOW_PREFIX):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        scanned += 1
        for line_no, line in enumerate(text.splitlines(), start=1):
            for name, pattern in PATTERNS.items():
                if name == "generic_api_key_assignment":
                    # allow documented example placeholders (example/placeholder/your_/changeme)
                    if re.search(r"(?i)(example|placeholder|your[_.-]|changeme|xxx)", line):
                        continue
                if pattern.search(line):
                    findings.append({"file": rel, "line": line_no, "pattern": name, "text": line[:120]})

    report = {
        "scan": "local secret scan (regex; complement to CI gitleaks)",
        "generated_at": datetime.now(UTC).isoformat(),
        "files_scanned": scanned,
        "findings": findings,
        "status": "PASS" if not findings else "REVIEW_REQUIRED",
        "notes": "gitleaks runs in CI (.github/workflows/ci.yml); gitleaks binary not available for local execution",
    }
    out = REPO / "reports" / "secret_scan.json"
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("status:", report["status"], "| files scanned:", scanned, "| findings:", len(findings))
    for f in findings[:10]:
        print("  ", f)
    return 0 if not findings else 1


if __name__ == "__main__":
    raise SystemExit(main())
