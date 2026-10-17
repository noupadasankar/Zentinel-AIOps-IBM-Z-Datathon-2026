"""Generate reports/project_audit.json from actual repository inspection.

Run: python scripts/generate_project_audit.py
Deterministic; reflects live filesystem, git HEAD, test inventory, and artifact hashes.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "reports"
TESTS = ROOT / "tests"


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def git(command: list[str]) -> str | None:
    try:
        result = subprocess.run(
            ["git", *command], cwd=ROOT, capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except OSError:
        pass
    return None


def main() -> int:
    audit: dict[str, object] = {
        "generated_at": datetime.now(UTC).isoformat(),
        "generator": "scripts/generate_project_audit.py",
    }

    audit["git"] = {
        "head": git(["log", "-1", "--format=%H"]),
        "head_subject": git(["log", "-1", "--format=%s"]),
        "branch": git(["rev-parse", "--abbrev-ref", "HEAD"]),
        "dirty_at_generation": bool(git(["status", "--porcelain"])),
    }

    audit["runtime"] = {
        "python": sys.version.split()[0],
        "executable": sys.executable,
    }

    key_paths = [
        "README.md", "requirements.txt", "requirements.lock",
        "pyproject.toml", "pytest.ini", "Dockerfile", "docker-compose.yml",
        "Makefile", ".env.example", ".streamlit/config.toml",
        "start_zentinel.bat", "run_tests.bat",
    ]
    audit["repo_files"] = {
        rel: {"exists": (ROOT / rel).is_file(), "bytes": (ROOT / rel).stat().st_size if (ROOT / rel).is_file() else None}
        for rel in key_paths
    }

    dirs = {}
    for d in ["src/zentinel", "scripts", "tests", "dashboard", "notebooks", "reports", "artifacts", "data"]:
        p = ROOT / d
        if p.is_dir():
            dirs[d] = {
                "files": sum(1 for f in p.rglob("*") if f.is_file() and "__pycache__" not in str(f)),
                "exists": True,
            }
    audit["directories"] = dirs

    src_modules = sorted(
        str(p.relative_to(ROOT)).replace("\\", "/")
        for p in (ROOT / "src" / "zentinel").rglob("*.py")
        if "__pycache__" not in str(p)
    )
    audit["source_modules"] = src_modules

    audit["scripts"] = sorted(
        str(p.relative_to(ROOT)).replace("\\", "/")
        for p in (ROOT / "scripts").glob("*.py")
    )

    audit["test_files"] = sorted(p.name for p in TESTS.glob("test_*.py"))

    audit["reports"] = sorted(p.name for p in REPORTS.glob("*.json"))

    artifacts = {}
    for p in sorted((ROOT / "artifacts").iterdir()):
        if p.is_file():
            artifacts[p.name] = {"bytes": p.stat().st_size, "sha256": sha256_of(p)}
    audit["artifacts"] = artifacts

    audit["manifest_check"] = {
        "manifest_exists": (ROOT / "artifacts" / "manifest.json").is_file(),
        "note": "Runtime verifies manifest via zentinel.config.verify_manifest() at API startup and in tests.",
    }

    live_api = ROOT / "src" / "zentinel" / "api" / "live_api.py"
    endpoints = []
    if live_api.is_file():
        for line in live_api.read_text(encoding="utf-8").splitlines():
            line_s = line.strip()
            if line_s.startswith('@app.'):
                method_marker = line_s.split("(")[0]
                decorators = method_marker
                if ".get(" in decorators or ".post(" in decorators:
                    method = "GET" if ".get(" in decorators else "POST"
                    if line_s.count('(') > 0 and '"' in line_s:
                        first = line_s.split('"')
                        if len(first) >= 2:
                            endpoints.append({"method": method, "path": first[1]})
    audit["api_endpoints"] = sorted(
        {f"{e['method']} {e['path']}" for e in endpoints}
    )

    processed = {}
    for p in sorted((ROOT / "data" / "processed").glob("*")):
        if p.is_file() and p.suffix != "-shm" and p.suffix != "-wal" and not p.name.endswith(("-shm", "-wal")):
            processed[p.name] = {"bytes": p.stat().st_size}
    audit["data_processed"] = processed

    raw_files = {}
    for p in sorted((ROOT / "data" / "raw").glob("*")):
        if p.is_file():
            raw_files[p.name] = {"bytes": p.stat().st_size}
    audit["data_raw"] = raw_files

    nb_dir = ROOT / "notebooks"
    audit["notebooks"] = sorted(p.name for p in nb_dir.glob("*.ipynb")) if nb_dir.is_dir() else []

    out_path = REPORTS / "project_audit.json"
    out_path.write_text(json.dumps(audit, indent=2), encoding="utf-8")
    print(f"audit written: {out_path}")
    print(f"git HEAD: {audit['git']['head']}")
    print(f"src modules: {len(src_modules)}, tests: {len(audit['test_files'])}, reports: {len(audit['reports'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
