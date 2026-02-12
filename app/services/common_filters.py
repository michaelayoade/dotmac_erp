"""
Generic active filter builder for the compact_filters Jinja2 macro.

Used by web service list context methods to build the ``active_filters`` list
that drives the chip display and count badge.

Usage:
    from app.services.common_filters import build_active_filters

    active_filters = build_active_filters(
        params={"status": status, "customer_id": customer_id},
        labels={"status": "Status", "customer_id": "Customer"},
        options={"customer_id": {str(c.customer_id): c.legal_name for c in customers}},
    )
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def build_active_filters(
    *,
    params: Mapping[str, Any],
    labels: dict[str, str] | None = None,
    options: dict[str, dict[str, str]] | None = None,
) -> list[dict[str, str]]:
    """Build a list of active filter dicts for the compact_filters macro.

    Args:
        params: Map of param name → current value (None/empty = inactive).
        labels: Map of param name → human-readable label prefix.
            Defaults to title-casing the param name.
        options: Map of param name → {value: display_label} for entity lookups.
            Used for things like customer_id → "Acme Corp".

    Returns:
        List of ``{"name", "value", "display_value"}`` dicts for active filters.
    """
    labels = labels or {}
    options = options or {}
    result: list[dict[str, str]] = []

    for name, value in params.items():
        # Treat None/"" as inactive; keep False/0 as valid values.
        if value is None or value == "":
            continue
        value_str = str(value)

        # Resolve display value
        if name in options and value_str in options[name]:
            display = options[name][value_str]
        else:
            display = value_str.replace("_", " ").title()

        # Add label prefix for date fields
        label = labels.get(name)
        if label:
            display = f"{label}: {display}"

        result.append({"name": name, "value": value_str, "display_value": display})

    return result
