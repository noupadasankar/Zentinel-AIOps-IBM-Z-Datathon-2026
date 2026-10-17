"""Zentinel Live Ingestion Dashboard entry point."""

import runpy
from pathlib import Path

APP_PATH = Path(__file__).resolve().parent / "app.py"

runpy.run_path(str(APP_PATH), run_name="__main__")
