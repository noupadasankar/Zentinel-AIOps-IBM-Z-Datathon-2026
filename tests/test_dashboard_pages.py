"""End-to-end AppTest smoke coverage for every dashboard page.

Runs the real dashboard script offline (API pointed at a closed port so the
app renders its clearly-labelled demo data). Proves:

1. All ten pages render without exceptions.
2. The navigation radio offers exactly the canonical page list (no display
   suffixes such as `Incidents (3)` - those made the incidents page
   unreachable before the fix and are now guarded against re-introduction).
3. The dispatcher's canonicalization helper handles suffix variants and
   unknown labels safely (pure-function tests, no Streamlit runtime).
"""

import importlib.util
import sys
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

ROOT_DIR = Path(__file__).resolve().parents[1]
APP_PATH = ROOT_DIR / "dashboard" / "app.py"

# Closed local port: connection is refused instantly, driving the offline demo path.
OFFLINE_API_URL = "http://127.0.0.1:59999"

PAGES = [
    "Overview",
    "Incidents",
    "Review Queue",
    "Windows Browser",
    "Stream & Composition",
    "Drift Diagnostics",
    "Model Evaluation",
    "Thresholds & Models",
    "API Keys & Security",
    "Settings & Database",
]


def _load_navigation_module():
    """Import the pure navigation module by path (no Streamlit runtime needed)."""
    spec = importlib.util.spec_from_file_location(
        "dashboard_navigation", ROOT_DIR / "dashboard" / "navigation.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["dashboard_navigation"] = module
    spec.loader.exec_module(module)
    return module


def _offline_app() -> AppTest:
    at = AppTest.from_file(str(APP_PATH), default_timeout=90)
    at.session_state["zen_api_url"] = OFFLINE_API_URL
    at.run()
    assert not at.exception, f"initial run failed: {[str(e) for e in at.exception]}"
    return at


@pytest.mark.parametrize("page", PAGES)
def test_dashboard_page_renders_offline(page: str):
    at = _offline_app()
    at.session_state["zen_nav"] = page
    at.run()
    assert not at.exception, f"page '{page}' raised: {[str(e) for e in at.exception]}"


def test_offline_mode_is_demo_labelled():
    at = _offline_app()
    markdown_all = "\n".join(m.value for m in at.markdown)
    assert "DEMO DATA" in markdown_all, "offline run must be clearly labelled as demo data"
    assert "System overview" in markdown_all


def test_nav_radio_offers_exactly_canonical_pages():
    """Regression guard: no count-suffixed labels may re-enter the radio options."""
    at = _offline_app()
    nav_radios = [r for r in at.radio if r.key == "zen_nav"]
    assert len(nav_radios) == 1, "the dashboard must render exactly one navigation radio"
    assert list(nav_radios[0].options) == PAGES


def test_navigation_canonical_page_helper():
    nav = _load_navigation_module()
    assert nav.PAGES == tuple(PAGES)
    assert nav.canonical_page("Incidents (3)") == "Incidents"
    assert nav.canonical_page("Incidents") == "Incidents"
    assert nav.canonical_page("Windows Browser") == "Windows Browser"
    assert nav.canonical_page("") == "Overview"
    assert nav.canonical_page("Nonexistent Page") == "Overview"
    assert nav.canonical_page("  Review Queue ", default="Review Queue") == "Review Queue"
