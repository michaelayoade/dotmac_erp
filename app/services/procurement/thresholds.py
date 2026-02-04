"""
PPA 2007 Procurement Thresholds.

Utility module for enforcing Public Procurement Act 2007
threshold-based procurement method determination and
approving authority assignment.
"""

import logging
from decimal import Decimal

logger = logging.getLogger(__name__)


# PPA 2007 threshold tiers:
# (max_value, procurement_method, approving_authority)
PPA_THRESHOLDS: list[tuple[Decimal, str, str]] = [
    (Decimal("2_500_000"), "DIRECT", "Accounting Officer"),
    (Decimal("50_000_000"), "SELECTIVE", "Tenders Board"),
    (Decimal("1_000_000_000"), "OPEN_COMPETITIVE", "Ministerial Tenders Board"),
    (Decimal("Infinity"), "OPEN_COMPETITIVE", "Federal Executive Council"),
]


def determine_procurement_method(estimated_value: Decimal) -> tuple[str, str]:
    """
    Determine procurement method and approving authority based on PPA 2007.

    Args:
        estimated_value: Estimated contract value in NGN.

    Returns:
        Tuple of (procurement_method, approving_authority).
    """
    for threshold, method, authority in PPA_THRESHOLDS:
        if estimated_value <= threshold:
            return method, authority

    # Fallback (shouldn't be reached due to Infinity)
    return "OPEN_COMPETITIVE", "Federal Executive Council"


def validate_procurement_method(method: str, value: Decimal) -> None:
    """
    Validate that the chosen procurement method is appropriate for the value.

    Raises:
        ValueError: If the method doesn't match PPA 2007 threshold requirements.
    """
    required_method, authority = determine_procurement_method(value)

    # DIRECT can only be used for small values
    if method == "DIRECT" and value > Decimal("2_500_000"):
        raise ValueError(
            f"Direct procurement not allowed for values exceeding "
            f"NGN 2,500,000. Value: NGN {value:,.2f}. "
            f"Required method: {required_method} "
            f"(approving authority: {authority})."
        )

    # SELECTIVE can only be used up to NGN 50M
    if method == "SELECTIVE" and value > Decimal("50_000_000"):
        raise ValueError(
            f"Selective procurement not allowed for values exceeding "
            f"NGN 50,000,000. Value: NGN {value:,.2f}. "
            f"Required method: {required_method} "
            f"(approving authority: {authority})."
        )

    logger.debug(
        "Procurement method %s validated for value NGN %s (approving authority: %s)",
        method,
        f"{value:,.2f}",
        authority,
    )
