"""Shared helpers for e2e tests."""

from __future__ import annotations


def reveal_filters(page) -> None:
    """Expand a collapsible ``compact_filters`` panel so its search/filter
    controls become visible and interactable.

    List pages render filters inside an Alpine ``x-data="{ open }"`` panel that
    is collapsed by default (toggled by a "Filters" button). Filter inputs are
    in the DOM but ``display:none`` until expanded, so Playwright cannot
    ``fill``/``select_option`` them. Call this after navigating, before
    interacting with a filter control. No-op when there is no collapsible panel.
    """
    try:
        toggle = page.get_by_role("button", name="Filters")
        if toggle.count() > 0 and toggle.first.is_visible():
            toggle.first.click()
            page.wait_for_timeout(150)
    except Exception:
        # Best-effort: never let revealing filters fail a test outright.
        pass
