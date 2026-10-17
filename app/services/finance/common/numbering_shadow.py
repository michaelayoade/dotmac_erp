"""Shadow allocation against the composed ``dotmac-numbering`` module.

Slice 1 of the numbering replacement (bank statements only): the legacy
``SyncNumberingService`` still decides and prints every number. While
``ERP_NUMBERING_SHADOW=shadow``, the same transaction also allocates from
the composed module and compares the allocated value and period. Divergence
emits one structured warning naming both outcomes — the cutover evidence —
and never changes the issued number. Rendered strings are NOT compared yet:
the module joins every segment with the separator while ERP fuses
prefix+year, so string equality is a cutover-time format decision, not a
shadow gate.

Seeding is explicit and one-shot per series: the first shadow allocation
configures the module series from the legacy row with ``start_value`` set to
the legacy next value for the reference period, allocates (creating the
period counter), then reconfigures ``start_value`` back to 1 so future
periods start where the legacy reset would start them. Incoherent legacy
rows (per the module's series validation) are refused by name and shadow
stays off for that series until a human reconciles the row. Known semantic
scope note: ERP's ``ResetFrequency`` has no daily member, so the module's
daily policy is unreachable from legacy rows.
"""

from __future__ import annotations

import logging
import uuid
from datetime import date

from dotmac_numbering import SeriesConfiguration, allocate, configure_series
from dotmac_numbering.service import TenantScope
from sqlalchemy.orm import Session

from app.models.finance.core_config.numbering_sequence import (
    NumberingSequence,
    ResetFrequency,
)

logger = logging.getLogger(__name__)

_RESET_POLICIES = {
    ResetFrequency.NEVER: "never",
    ResetFrequency.YEARLY: "yearly",
    ResetFrequency.MONTHLY: "monthly",
}


def series_code_for(sequence: NumberingSequence) -> str:
    return str(sequence.sequence_type.value).lower()


def series_configuration_for(
    sequence: NumberingSequence, *, start_value: int = 1
) -> SeriesConfiguration:
    """Map one legacy row onto the module series shape, or refuse by name."""
    return SeriesConfiguration(
        series_code=series_code_for(sequence),
        prefix=sequence.prefix or "",
        suffix=sequence.suffix or "",
        separator=sequence.separator,
        min_digits=sequence.min_digits,
        include_year=sequence.include_year,
        year_digits=sequence.year_format,
        include_month=sequence.include_month,
        reset_policy=_RESET_POLICIES[sequence.reset_frequency],
        start_value=start_value,
    )


def shadow_allocate(
    db: Session,
    *,
    organization_id: uuid.UUID,
    sequence: NumberingSequence,
    reference_date: date,
    legacy_number: str,
    legacy_value: int,
) -> bool:
    """Allocate in shadow and compare value; True when module and legacy agree.

    Runs inside the caller's transaction. Any divergence is logged once with
    both outcomes and returns False; the legacy number already issued is
    never altered here.
    """
    scope = TenantScope(tenant_id=organization_id)
    series_code = series_code_for(sequence)
    # The caller has already issued ``legacy_value``; seeding a fresh
    # period counter with it makes the module's first value comparable.
    start = legacy_value
    series = configure_series(
        db,
        scope=scope,
        configuration=series_configuration_for(sequence, start_value=start),
    )
    allocation = allocate(
        db,
        scope=scope,
        series_code=series_code,
        reference_date=reference_date,
        idempotency_key=(
            f"erp-shadow:{series_code}:{organization_id}:{legacy_number}"
        ),
    )
    if series.start_value != 1:
        # The period counter now exists; future periods must start at 1 to
        # mirror the legacy reset, not at the seeded continuation value.
        configure_series(
            db,
            scope=scope,
            configuration=series_configuration_for(sequence, start_value=1),
        )
    if allocation.value != legacy_value:
        logger.warning(
            "numbering shadow divergence series=%s organization=%s "
            "legacy_value=%s module_value=%s legacy_number=%s module_number=%s",
            series_code,
            organization_id,
            legacy_value,
            allocation.value,
            legacy_number,
            allocation.formatted_number,
        )
        return False
    return True
