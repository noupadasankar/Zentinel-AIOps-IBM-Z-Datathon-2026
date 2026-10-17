"""Verify the distribution archive produced by `scripts/build_distribution_zip.py`.

Post-build integrity protocol (mirrors `reports/ZIP_VERIFICATION.md`):
CRC check of every member, extraction to a clean directory, forbidden-name
scan, required-path presence, notebook execution check, and a full SHA-256
re-check of the bundled artifacts against the bundled `artifacts/manifest.json`
plus a `zentinel.config.verify_manifest()` run from the extracted tree.

Usage:
    python scripts/verify_distribution_zip.py [ZIP_PATH] [EXTRACT_DIR]
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DEFAULT_ZIP = REPO / "Zentinel-AIOps-IBM-Z-Datathon-2026.zip"

FORBIDDEN_NAMES = {"BGL.log", "BGL.tar.gz", ".env"}
FORBIDDEN_PARTS = {".venv", "venv", "__pycache__", ".pytest_cache", ".ruff_cache", ".ipynb_checkpoints", ".git"}
FORBIDDEN_SUFFIXES = (".egg-info", ".dist-info")

REQUIRED_PATHS = [
    "README.md",
    "Makefile",
    "pyproject.toml",
    "pytest.ini",
    "requirements.txt",
    "requirements.lock",
    "Dockerfile",
    "docker-compose.yml",
    ".env.example",
    "artifacts/manifest.json",
    "reports/FINAL_COMPLETION_REPORT.md",
    "src/zentinel/api/live_api.py",
    "src/zentinel/api/unified.py",
    "linuxone-validation/validate_linuxone.sh",
    "notebooks/00_environment_and_data_contracts.ipynb",
    "notebooks/12_final_results_and_figures.ipynb",
    ".streamlit/config.toml",
    "dashboard/app.py",
    "dashboard/navigation.py",
    "start_zentinel.bat",
    "run_tests.bat",
]


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    zip_path = Path(args[0]) if args else DEFAULT_ZIP
    extract = Path(args[1]) if len(args) > 1 else Path(tempfile.gettempdir()) / "zentinel_zip_verify_extract"

    if not zip_path.is_file():
        print(f"ERROR: archive not found: {zip_path}")
        return 1

    print("zip sha256:", hashlib.sha256(zip_path.read_bytes()).hexdigest())

    if extract.exists():
        shutil.rmtree(extract)
    extract.mkdir(parents=True)

    with zipfile.ZipFile(zip_path) as zf:
        bad = zf.testzip()
        print("crc check:", "OK" if bad is None else f"CORRUPT: {bad}")
        names = zf.namelist()
        zf.extractall(extract)

    banned = [
        n
        for n in names
        if Path(n).name in FORBIDDEN_NAMES
        or any(part in FORBIDDEN_PARTS for part in Path(n).parts)
        or any(part.endswith(FORBIDDEN_SUFFIXES) for part in Path(n).parts)
    ]
    print("extracted files:", len(names))
    print("forbidden found:", banned if banned else "NONE")

    missing = [r for r in REQUIRED_PATHS if r not in names]
    print("required missing:", missing if missing else "NONE")

    nb_count = sum(1 for n in names if n.startswith("notebooks/") and n.endswith(".ipynb"))
    print("notebooks in zip:", nb_count)

    env_copy = dict(os.environ)
    env_copy["PYTHONPATH"] = str(extract / "src")
    proc = subprocess.run(
        [sys.executable, "-c", "from zentinel.config import verify_manifest; print('MANIFEST_CHECK', verify_manifest())"],
        cwd=str(extract),
        env=env_copy,
        capture_output=True,
        text=True,
        timeout=600,
    )
    print("artifact manifest check:", proc.stdout.strip()[-200:] or proc.stderr.strip()[-200:])

    manifest = json.loads((extract / "artifacts" / "manifest.json").read_text(encoding="utf-8"))
    mismatched: list[str] = []
    for rel, spec in manifest["artifacts"].items():
        target = extract / rel
        if not target.is_file():
            mismatched.append(f"{rel}: MISSING")
            continue
        digest = hashlib.sha256(target.read_bytes()).hexdigest()
        if digest != spec["sha256"]:
            mismatched.append(rel)
    print("bundled artifact sha mismatch:", mismatched if mismatched else "NONE")

    unexecuted = []
    for n in names:
        if n.endswith(".ipynb"):
            nb = json.loads((extract / n).read_text(encoding="utf-8"))
            code_cells = [c for c in nb["cells"] if c["cell_type"] == "code"]
            executed = [c for c in code_cells if c.get("execution_count") or c.get("outputs")]
            if len(executed) != len(code_cells):
                unexecuted.append((n, len(executed), len(code_cells)))
    print("fully executed notebooks:", nb_count - len(unexecuted), "/", nb_count)
    print("unexecuted:", unexecuted if unexecuted else "NONE")

    ok = (
        bad is None
        and not banned
        and not missing
        and not mismatched
        and not unexecuted
        and "MANIFEST_CHECK None" in proc.stdout
    )
    print("VERDICT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
