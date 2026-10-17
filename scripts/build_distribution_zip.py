"""Build the Zentinel AIOps distribution archive from an explicit include-list.

Reproduces the packaging protocol documented in `reports/ZIP_VERIFICATION.md`:
only whitelisted paths enter the archive, runtime state / caches / the raw
dataset never do, and the post-build integrity data (file count, size,
SHA-256, per-segment inventory) is printed as JSON for the verification report.

Usage:
    python scripts/build_distribution_zip.py [--output PATH] [--check-only]

`--check-only` performs the forbidden-name pre-flight scan without writing
the archive.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "Zentinel-AIOps-IBM-Z-Datathon-2026.zip"

ROOT_FILES = [
    ".dockerignore",
    ".env.example",
    ".gitignore",
    "Dockerfile",
    "Makefile",
    "README.md",
    "docker-compose.yml",
    "pyproject.toml",
    "pytest.ini",
    "requirements.lock",
    "requirements.txt",
    "run_tests.bat",
    "start_zentinel.bat",
]

INCLUDE_TREES = [
    ".github",
    ".streamlit",
    "artifacts",
    "dashboard",
    "linuxone-validation",
    "notebooks",
    "scripts",
    "src",
    "tests",
]

DATA_PROCESSED_WHITELIST = [
    ".gitkeep",
    "alert_evidence.csv",
    "bgl_event_sequences.npz",
    "bgl_event_sequences_v2.npz",
    "bgl_parsed_sample.jsonl",
    "bgl_window_features.parquet",
    "drift_report.csv",
    "isolation_forest_validation_scores.parquet",
    "lstm_known_run_test_scores.parquet",
    "lstm_known_run_validation_scores.parquet",
    "lstm_three_way_test_decisions.parquet",
    "lstm_validation_scores.parquet",
    "metrics_summary.csv",
    "retraining_review_queue.csv",
    "templates.csv",
    "test_model_comparison.parquet",
]

DATA_RAW_WHITELIST = [".gitkeep", "sample_replay.log"]

EXCLUDED_NAMES = [
    "BGL.log",
    "BGL.tar.gz",
    ".env",
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".ipynb_checkpoints",
    ".git",
]

# Suffix-based exclusions (build/test artifacts that must never ship).
EXCLUDED_SUFFIXES = (".egg-info", ".dist-info")

# Documented exclusion: this report documents the archive's own digest, so it
# can never live inside the archive (circular-digest rule).
REPORTS_EXCLUDE = {"ZIP_VERIFICATION.md"}


def _iter_tree(relative_dir: str) -> list[str]:
    base = ROOT / relative_dir
    files: list[str] = []
    for path in sorted(base.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(ROOT).as_posix()
        parts = rel.split("/")
        if any(part in EXCLUDED_NAMES for part in parts):
            continue
        if any(part.endswith(EXCLUDED_SUFFIXES) for part in parts):
            continue
        files.append(rel)
    return files


def collect_members() -> list[tuple[Path, str]]:
    members: list[tuple[Path, str]] = []

    for name in ROOT_FILES:
        path = ROOT / name
        if not path.is_file():
            raise FileNotFoundError(f"required root file missing: {name}")
        members.append((path, name))

    for tree in INCLUDE_TREES:
        for rel in _iter_tree(tree):
            members.append((ROOT / rel, rel))

    reports_dir = ROOT / "reports"
    for path in sorted(reports_dir.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(ROOT).as_posix()
        if path.name in REPORTS_EXCLUDE:
            continue
        if "__pycache__" in rel:
            continue
        members.append((path, rel))

    for name in DATA_PROCESSED_WHITELIST:
        path = ROOT / "data" / "processed" / name
        if not path.is_file():
            raise FileNotFoundError(f"whitelisted data file missing: data/processed/{name}")
        members.append((path, f"data/processed/{name}"))

    for name in DATA_RAW_WHITELIST:
        path = ROOT / "data" / "raw" / name
        if not path.is_file():
            raise FileNotFoundError(f"whitelisted data file missing: data/raw/{name}")
        members.append((path, f"data/raw/{name}"))

    seen: set[str] = set()
    for _, rel in members:
        if rel in seen:
            raise RuntimeError(f"duplicate archive member: {rel}")
        seen.add(rel)
    return members


def scan_forbidden(members: list[tuple[Path, str]]) -> list[str]:
    violations = []
    for _, rel in members:
        parts = rel.split("/")
        for forbidden in EXCLUDED_NAMES:
            if forbidden in parts:
                violations.append(rel)
                break
        else:
            if any(part.endswith(EXCLUDED_SUFFIXES) for part in parts):
                violations.append(rel)
    return violations


def segment_of(rel: str) -> str:
    if rel.startswith("data/"):
        return "data"
    if "/" in rel:
        return rel.split("/", 1)[0]
    return "root"


def build(output: Path) -> dict:
    members = collect_members()
    violations = scan_forbidden(members)
    if violations:
        raise RuntimeError(f"forbidden members detected: {violations[:10]}")

    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path, rel in members:
            archive.write(path, rel)

    hasher = hashlib.sha256()
    with output.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            hasher.update(chunk)

    size = output.stat().st_size
    file_count = len(members)
    segments: dict[str, dict[str, float]] = {}
    for path, rel in members:
        segment = segment_of(rel)
        entry = segments.setdefault(segment, {"files": 0, "bytes": 0})
        entry["files"] += 1
        entry["bytes"] += path.stat().st_size

    return {
        "archive": output.name,
        "file_count": file_count,
        "size_bytes": size,
        "size_mb": round(size / 1_000_000, 2),
        "sha256": hasher.hexdigest(),
        "segments": {k: {"files": v["files"], "mb": round(v["bytes"] / 1_000_000, 1)} for k, v in sorted(segments.items())},
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the Zentinel distribution ZIP from the whitelist.")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="Output archive path")
    parser.add_argument("--check-only", action="store_true", help="Only run the include-list pre-flight scan")
    args = parser.parse_args(argv)

    if args.check_only:
        members = collect_members()
        violations = scan_forbidden(members)
        print(json.dumps({"members": len(members), "forbidden": violations}, indent=2))
        return 1 if violations else 0

    summary = build(Path(args.output))
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
