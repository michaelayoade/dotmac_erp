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

from dotmac_numbering import (
    NumberSeries,
    SeriesConfiguration,
    allocate,
    configure_series,
    format_number,
)
from sqlalchemy.orm import Session

from app.models.finance.core_org.organization import Organization
from app.services.tenant_projection import reconcile_organization_tenant
from app.tenancy import OrganizationTenantContext

from app.models.finance.core_config.numbering_sequence import (
    NumberingSequence,
    ResetFrequency,
    SequenceType,
)
from app.services.finance.common.sequence_utils import should_reset

#: Series issued by the module. Bank statements cut over first (#722),
#: the internal batch followed (#723), then the counterparty, payroll and
#: treasury series. INVOICE follows its clean production continuity
#: report (2026-10-19, exit 0, zero findings). Only CREDIT_NOTE remains
#: on the legacy allocator.
CUT_OVER_SERIES = frozenset(
    {
        SequenceType.INVOICE,
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
        SequenceType.EXPENSE,
        SequenceType.CUSTOMER,
        SequenceType.QUOTE,
        SequenceType.SALES_ORDER,
        SequenceType.SHIPMENT,
        SequenceType.PURCHASE_ORDER,
        SequenceType.SUPPLIER_INVOICE,
        SequenceType.EXPENSE_INVOICE,
        SequenceType.SALARY_SLIP,
        SequenceType.PAYROLL_ENTRY,
        SequenceType.PAYMENT,
        SequenceType.RECEIPT,
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


def preview_from_module(
    sequence: NumberingSequence, *, sample_number: int = 1, reference_date: date
) -> str:
    """Render a settings preview with the module's one formatter.

    Uses a transient series built from the same configuration mapping
    as allocation, so the preview can never drift from issued numbers.
    """
    configuration = series_configuration_for(sequence)
    series = NumberSeries(
        series_code=configuration.series_code,
        prefix=configuration.prefix,
        suffix=configuration.suffix,
        separator=configuration.separator,
        min_digits=configuration.min_digits,
        include_year=int(configuration.include_year),
        year_digits=configuration.year_digits,
        include_month=int(configuration.include_month),
        reset_policy=configuration.reset_policy,
        start_value=configuration.start_value,
    )
    return format_number(series, value=sample_number, reference_date=reference_date)


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
    # Stateful shared modules key their rows to the kernel tenant catalogue;
    # reconcile the Organization's projection in this transaction so the
    # allocation cannot fail on a missing tenant row. The catalogue only
    # exists on PostgreSQL (the SQLite unit harness has no tenant table).
    if db.get_bind().dialect.name == "postgresql":
        organization = db.get(Organization, organization_id)
        if organization is not None:
            reconcile_organization_tenant(db, organization)
            db.flush()
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
