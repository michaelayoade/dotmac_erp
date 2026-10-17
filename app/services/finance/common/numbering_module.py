"""Direct allocation from the composed ``dotmac-numbering`` module.

Series in ``CUT_OVER_SERIES`` are issued by the module, not by the
legacy allocator (Michael's ruling, 2026-10-17: direct cutover for
internal-facing series, module format accepted — ``STMT-2026-00042``,
not the legacy fused ``STMT2026-00042``). Numbers issued before a
series' cutover keep their legacy rendering; the two shapes cannot
collide. Counterparty and financial-document series stay on the
legacy allocator pending their own ruling.

The legacy ``NumberingSequence`` row is read once per call to seed a
fresh module period counter at the value the legacy allocator would
have issued next, and is then mirrored from the module's allocation.
The mirror is the rollback record: reverting this series means
pointing the caller back at the legacy allocator with the row already
at the last issued value. It is deleted when the legacy allocator
retires for the last series.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from dotmac_numbering import SeriesConfiguration, allocate, configure_series
from sqlalchemy.orm import Session

from app.tenancy import OrganizationTenantContext

from app.models.finance.core_config.numbering_sequence import (
    NumberingSequence,
    ResetFrequency,
    SequenceType,
)
from app.services.finance.common.sequence_utils import should_reset

#: Internal-facing series issued by the module. Bank statements cut over
#: first (#722); the rest of the internal batch follows the same pattern.
CUT_OVER_SERIES = frozenset(
    {
        SequenceType.BANK_STATEMENT,
        SequenceType.JOURNAL,
        SequenceType.SUPPORT_TICKET,
        SequenceType.PROJECT,
        SequenceType.TASK,
        SequenceType.MATERIAL_REQUEST,
        SequenceType.GOODS_RECEIPT,
        SequenceType.ITEM,
        SequenceType.ASSET,
        SequenceType.LEASE,
        SequenceType.CONTRACT,
        SequenceType.EMPLOYEE,
        SequenceType.LEAVE_APPLICATION,
        SequenceType.CUSTOMER,
        SequenceType.EXPENSE,
    }
)

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
    prefix = sequence.prefix or ""
    if sequence.separator:
        # Legacy prefixes may carry their own trailing separator (``EXP-``);
        # the module joins segments itself, so keep exactly one separator.
        prefix = prefix.rstrip(sequence.separator)
    return SeriesConfiguration(
        series_code=series_code_for(sequence),
        prefix=prefix,
        suffix=sequence.suffix or "",
        separator=sequence.separator,
        min_digits=sequence.min_digits,
        include_year=sequence.include_year,
        year_digits=sequence.year_format,
        include_month=sequence.include_month,
        reset_policy=_RESET_POLICIES[sequence.reset_frequency],
        start_value=start_value,
    )


def allocate_from_module(
    db: Session,
    *,
    organization_id: uuid.UUID,
    sequence: NumberingSequence,
    reference_date: date,
) -> str:
    """Issue the next number for this series from the composed module.

    Runs inside the caller's transaction; the caller holds the legacy
    row lock, which serializes seeding per organization and series.
    """
    scope = OrganizationTenantContext.for_organization(organization_id).tenant_scope
    next_value = (
        1
        if should_reset(sequence, reference_date)
        else int(sequence.current_number or 0) + 1
    )
    series = configure_series(
        db,
        scope=scope,
        configuration=series_configuration_for(sequence, start_value=next_value),
    )
    allocation = allocate(
        db,
        scope=scope,
        series_code=series_code_for(sequence),
        reference_date=reference_date,
        idempotency_key=(
            f"erp:{series_code_for(sequence)}:{organization_id}:{uuid.uuid4()}"
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
    sequence.current_number = allocation.value
    sequence.current_year = reference_date.year
    sequence.current_month = reference_date.month
    sequence.last_used_at = datetime.now()
    return allocation.formatted_number
