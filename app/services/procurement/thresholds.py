"""
PPA 2007 Procurement Thresholds.

Utility module for enforcing Public Procurement Act 2007
threshold-based procurement method determination and
approving authority assignment.

Thresholds are configurable per-organization via settings.
Falls back to PPA 2007 statutory defaults when not configured.
"""

import logging
from decimal import Decimal
from uuid import UUID

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


# PPA 2007 statutory defaults (kept for backward compat in tests)
PPA_THRESHOLDS: list[tuple[Decimal, str, str]] = [
    (Decimal("2_500_000"), "DIRECT", "Accounting Officer"),
    (Decimal("50_000_000"), "SELECTIVE", "Tenders Board"),
    (Decimal("1_000_000_000"), "OPEN_COMPETITIVE", "Ministerial Tenders Board"),
    (Decimal("Infinity"), "OPEN_COMPETITIVE", "Federal Executive Council"),
]


def _get_thresholds(
    db: Session | None = None,
    organization_id: UUID | None = None,
) -> list[tuple[Decimal, str, str]]:
    """
    Get procurement thresholds, reading org-level settings when available.

    Falls back to PPA 2007 statutory defaults if no db/org provided
    or if settings are not configured.
    """
    if db is None or organization_id is None:
        return PPA_THRESHOLDS

    from app.models.domain_settings import SettingDomain
    from app.services.settings_cache import get_cached_setting

    direct_max = get_cached_setting(
        db,
        SettingDomain.procurement,
        "procurement_threshold_direct_max",
        default=2_500_000,
    )
    selective_max = get_cached_setting(
        db,
        SettingDomain.procurement,
        "procurement_threshold_selective_max",
        default=50_000_000,
    )
    ministerial_max = get_cached_setting(
        db,
        SettingDomain.procurement,
        "procurement_threshold_ministerial_max",
        default=1_000_000_000,
    )

    return [
        (Decimal(str(direct_max)), "DIRECT", "Accounting Officer"),
        (Decimal(str(selective_max)), "SELECTIVE", "Tenders Board"),
        (
            Decimal(str(ministerial_max)),
            "OPEN_COMPETITIVE",
            "Ministerial Tenders Board",
        ),
        (Decimal("Infinity"), "OPEN_COMPETITIVE", "Federal Executive Council"),
    ]


def determine_procurement_method(
    estimated_value: Decimal,
    db: Session | None = None,
    organization_id: UUID | None = None,
) -> tuple[str, str]:
    """
    Determine procurement method and approving authority.

    Uses org-level settings when db and organization_id are provided,
    otherwise falls back to PPA 2007 statutory defaults.

    Args:
        estimated_value: Estimated contract value in NGN.
        db: Database session (optional).
        organization_id: Organization UUID (optional).

    Returns:
        Tuple of (procurement_method, approving_authority).
    """
    thresholds = _get_thresholds(db, organization_id)

    for threshold, method, authority in thresholds:
        if estimated_value <= threshold:
            return method, authority

    # Fallback (shouldn't be reached due to Infinity)
    return "OPEN_COMPETITIVE", "Federal Executive Council"


def validate_procurement_method(
    method: str,
    value: Decimal,
    db: Session | None = None,
    organization_id: UUID | None = None,
) -> None:
    """
    Validate that the chosen procurement method is appropriate for the value.

    Uses org-level settings when db and organization_id are provided.

    Raises:
        ValueError: If the method doesn't match threshold requirements.
    """
    thresholds = _get_thresholds(db, organization_id)
    required_method, authority = determine_procurement_method(
        value, db, organization_id
    )

    # Find the threshold for DIRECT
    direct_max = thresholds[0][0]
    if method == "DIRECT" and value > direct_max:
        raise ValueError(
            f"Direct procurement not allowed for values exceeding "
            f"NGN {direct_max:,.0f}. Value: NGN {value:,.2f}. "
            f"Required method: {required_method} "
            f"(approving authority: {authority})."
        )

    # Find the threshold for SELECTIVE
    selective_max = thresholds[1][0]
    if method == "SELECTIVE" and value > selective_max:
        raise ValueError(
            f"Selective procurement not allowed for values exceeding "
            f"NGN {selective_max:,.0f}. Value: NGN {value:,.2f}. "
            f"Required method: {required_method} "
            f"(approving authority: {authority})."
        )

    logger.debug(
        "Procurement method %s validated for value NGN %s (approving authority: %s)",
        method,
        f"{value:,.2f}",
        authority,
    )
