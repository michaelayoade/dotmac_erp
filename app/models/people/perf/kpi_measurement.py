"""Shared, transport-independent measurement rules for private employee KPIs.

These are target-attainment percentages, not OHCSF appraisal ratings, employee
rankings or a prediction of progress within an unfinished reporting period.
"""

from decimal import ROUND_HALF_UP, Decimal

SUPPORT_METRIC_KEYS = frozenset(
    {
        "support.tickets_resolved",
        "support.open_backlog",
        "support.resolution_rate",
        "support.avg_resolution_days",
    }
)
LOWER_SUPPORT_METRIC_KEYS = frozenset(
    {"support.open_backlog", "support.avg_resolution_days"}
)
MAX_STORED_ACHIEVEMENT = Decimal("999.99")  # Existing Numeric(5, 2) column.


def support_metric_key(*sources: str | None) -> str | None:
    text = " ".join(source.lower() for source in sources if source)
    keys = [key for key in sorted(SUPPORT_METRIC_KEYS) if key in text]
    return keys[0] if len(keys) == 1 else None


def lower_is_better(explicit: bool | None, *sources: str | None) -> bool:
    """Prefer the persisted direction; retain known legacy support-tag behavior."""
    if explicit is not None:
        return explicit
    text = " ".join(source.lower() for source in sources if source)
    return any(key in text for key in LOWER_SUPPORT_METRIC_KEYS)


def achievement(
    actual: Decimal | None, target: Decimal | None, *, lower: bool = False
) -> Decimal | None:
    """Score nonnegative ratio measures without fabricating observations.

    Missing/negative/non-finite measurements and a zero higher-is-better target
    are unscorable. Zero lower-is-better is an explicit zero-tolerance target.
    A real zero actual for a positive lower target meets that target; no activity
    must be passed as None, not zero. Ratios are capped to fit the existing field.
    """
    if actual is None or target is None:
        return None
    if not actual.is_finite() or not target.is_finite() or actual < 0 or target < 0:
        return None
    if lower:
        if actual == 0:
            score = Decimal("100")
        else:
            score = target / actual * Decimal("100")
    elif target == 0:
        return None
    else:
        score = actual / target * Decimal("100")
    rounded = min(score, MAX_STORED_ACHIEVEMENT).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    # Rounding must not turn a narrowly missed target into an achievement.
    return Decimal("99.99") if score < 100 and rounded >= 100 else rounded


def progress_status(score: Decimal | None) -> str:
    """Retain the private KPI 100/80 bands, explicitly including a zero result."""
    if score is None:
        return "PENDING"
    if score >= 100:
        return "ACHIEVED"
    if score >= 80:
        return "ON_TRACK"
    return "AT_RISK"


def parse_direction(value: str) -> bool | None:
    """The HTML contract distinguishes a legacy default from an explicit choice."""
    choices = {"": None, "higher": False, "lower": True}
    if value not in choices:
        raise ValueError("Choose a valid KPI measurement direction.")
    return choices[value]
