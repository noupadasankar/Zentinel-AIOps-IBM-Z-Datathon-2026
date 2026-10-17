"""Canonical navigation model for the dashboard.

Single source of truth shared by the sidebar renderer and the page dispatcher,
so a display suffix (for example `Incidents (3)`) can never desynchronise the
radio options from the dispatch branch again.
"""

from __future__ import annotations

NAV_GROUPS: dict[str, tuple[str, ...]] = {
    "Home": ("Overview",),
    "Investigate": ("Incidents", "Review Queue", "Windows Browser"),
    "Observe": ("Stream & Composition", "Drift Diagnostics", "Model Evaluation"),
    "Configure": ("Thresholds & Models", "API Keys & Security", "Settings & Database"),
}

PAGES: tuple[str, ...] = tuple(page for options in NAV_GROUPS.values() for page in options)


def canonical_page(label: str, default: str = "Overview") -> str:
    """Normalize a navigation label to its canonical page name.

    Accepts display variants like `Incidents (3)` (count suffix) and falls back
    to `default` for anything unknown so the dispatcher can never dead-end.
    """
    if not label:
        return default
    base = label.split(" (", 1)[0].strip()
    return base if base in PAGES else default
