"""Bank-statement numbers are issued by the composed numbering module.

Proves value continuity from the legacy counter, the accepted module
format, period reset, the legacy-row mirror used as the rollback
record, and refusal of an incoherent legacy row.
"""

from __future__ import annotations

import uuid
from datetime import date

import dotmac_kernel.idempotency  # noqa: F401
import pytest
from dotmac_kernel.models import Base
from dotmac_numbering import NumberingError
from dotmac_numbering import models as numbering_models  # noqa: F401
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from app.models.finance.core_config.numbering_sequence import (
    NumberingSequence,
    ResetFrequency,
    SequenceType,
)
from app.services.finance.common.numbering import _default_sequence_kwargs
from app.services.finance.common.numbering_module import (
    CUT_OVER_SERIES,
    allocate_from_module,
    series_code_for,
    series_configuration_for,
)


def _sequence(**overrides) -> NumberingSequence:
    values = {
        "organization_id": uuid.uuid4(),
        "sequence_type": SequenceType.BANK_STATEMENT,
        "prefix": "STMT",
        "suffix": "",
        "separator": "-",
        "min_digits": 5,
        "include_year": True,
        "include_month": False,
        "year_format": 4,
        "current_number": 0,
        "current_year": None,
        "current_month": None,
        "reset_frequency": ResetFrequency.YEARLY,
    }
    values.update(overrides)
    return NumberingSequence(**values)


@pytest.fixture()
def session() -> Session:
    engine = create_engine("sqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def attach(dbapi_connection, _record):
        dbapi_connection.execute("ATTACH DATABASE ':memory:' AS mod_numbering")

    tables = [
        table
        for table in Base.metadata.tables.values()
        if table.schema == "mod_numbering" or table.name == "idempotency_records"
    ]
    Base.metadata.create_all(engine, tables=tables)
    with Session(engine) as db:
        yield db


def test_series_mapping_uses_the_legacy_row_shape() -> None:
    sequence = _sequence()
    configuration = series_configuration_for(sequence, start_value=7)

    assert series_code_for(sequence) == "bank_statement"
    assert configuration.prefix == "STMT"
    assert configuration.min_digits == 5
    assert configuration.include_year is True
    assert configuration.year_digits == 4
    assert configuration.include_month is False
    assert configuration.reset_policy == "yearly"
    assert configuration.start_value == 7


def test_cutover_continues_the_legacy_counter_in_module_format(
    session: Session,
) -> None:
    sequence = _sequence(current_number=41, current_year=2026, current_month=5)

    first = allocate_from_module(
        session,
        organization_id=sequence.organization_id,
        sequence=sequence,
        reference_date=date(2026, 5, 20),
    )
    second = allocate_from_module(
        session,
        organization_id=sequence.organization_id,
        sequence=sequence,
        reference_date=date(2026, 5, 21),
    )

    assert first == "STMT-2026-00042"
    assert second == "STMT-2026-00043"
    # The legacy row mirrors the module as the rollback record.
    assert sequence.current_number == 43
    assert sequence.current_year == 2026


def test_cutover_resets_at_the_year_boundary(session: Session) -> None:
    sequence = _sequence(current_number=41, current_year=2026, current_month=5)
    allocate_from_module(
        session,
        organization_id=sequence.organization_id,
        sequence=sequence,
        reference_date=date(2026, 5, 20),
    )

    number = allocate_from_module(
        session,
        organization_id=sequence.organization_id,
        sequence=sequence,
        reference_date=date(2027, 1, 5),
    )

    assert number == "STMT-2027-00001"
    assert sequence.current_number == 1
    assert sequence.current_year == 2027


def test_cutover_starts_a_fresh_organization_at_one(session: Session) -> None:
    sequence = _sequence()

    number = allocate_from_module(
        session,
        organization_id=sequence.organization_id,
        sequence=sequence,
        reference_date=date(2026, 3, 1),
    )

    assert number == "STMT-2026-00001"


def test_cutover_refuses_an_incoherent_legacy_row(session: Session) -> None:
    sequence = _sequence(year_format=3)

    with pytest.raises(NumberingError):
        allocate_from_module(
            session,
            organization_id=sequence.organization_id,
            sequence=sequence,
            reference_date=date(2026, 5, 20),
        )


def _default_sequence(sequence_type: SequenceType) -> NumberingSequence:
    return NumberingSequence(
        organization_id=uuid.uuid4(),
        sequence_type=sequence_type,
        **_default_sequence_kwargs(sequence_type),
    )


@pytest.mark.parametrize("sequence_type", sorted(CUT_OVER_SERIES, key=str))
def test_every_cut_over_series_issues_its_first_number(
    session: Session, sequence_type: SequenceType
) -> None:
    sequence = _default_sequence(sequence_type)

    number = allocate_from_module(
        session,
        organization_id=sequence.organization_id,
        sequence=sequence,
        reference_date=date(2026, 5, 20),
    )

    assert "--" not in number
    assert number.endswith("1".zfill(sequence.min_digits))
    assert sequence.current_number == 1


def test_monthly_series_renders_year_and_month_segments(session: Session) -> None:
    sequence = _default_sequence(SequenceType.JOURNAL)

    number = allocate_from_module(
        session,
        organization_id=sequence.organization_id,
        sequence=sequence,
        reference_date=date(2026, 5, 20),
    )

    assert number == "JE-2026-05-0001"


def test_never_reset_series_renders_without_date_segments(
    session: Session,
) -> None:
    sequence = _default_sequence(SequenceType.CONTRACT)

    number = allocate_from_module(
        session,
        organization_id=sequence.organization_id,
        sequence=sequence,
        reference_date=date(2026, 5, 20),
    )

    assert number == "CTR-000001"


def test_trailing_separator_in_legacy_prefix_is_cleaned(session: Session) -> None:
    sequence = _default_sequence(SequenceType.EXPENSE)
    assert sequence.prefix == "EXP-"

    number = allocate_from_module(
        session,
        organization_id=sequence.organization_id,
        sequence=sequence,
        reference_date=date(2026, 5, 20),
    )

    assert number == "EXP-2026-00001"


def test_preview_uses_the_module_formatter_for_cut_over_series() -> None:
    from app.services.finance.common.numbering import NumberingService

    service = NumberingService.__new__(NumberingService)
    sequence = _sequence()

    preview = service.preview_format(sequence, sample_number=42)

    assert preview == f"STMT-{date.today().year}-00042"


def test_credit_note_cutover_continues_the_legacy_counter(session: Session) -> None:
    """The production continuity report was clean, so seeding is exact.

    With INVOICE and CREDIT_NOTE cut over, every SequenceType is
    issued by the module and the legacy preview shape no longer has a
    live series to demonstrate against.
    """
    sequence = _sequence(
        sequence_type=SequenceType.CREDIT_NOTE,
        prefix="CN",
        min_digits=4,
        include_month=True,
        reset_frequency=ResetFrequency.MONTHLY,
        current_number=3,
        current_year=2026,
        current_month=5,
    )

    number = allocate_from_module(
        session,
        organization_id=sequence.organization_id,
        sequence=sequence,
        reference_date=date(2026, 5, 20),
    )

    assert number == "CN-2026-05-0004"
    assert sequence.current_number == 4


def test_invoice_cutover_continues_the_legacy_counter(session: Session) -> None:
    """The production continuity report was clean, so seeding is exact.

    Legacy issued ``INV202605-0007`` last (fused shape); the module
    continues at 8 in its segmented shape, which cannot collide with
    any legacy rendering under the (org, invoice_number) uniqueness.
    """
    sequence = _sequence(
        sequence_type=SequenceType.INVOICE,
        prefix="INV",
        min_digits=4,
        include_month=True,
        reset_frequency=ResetFrequency.MONTHLY,
        current_number=7,
        current_year=2026,
        current_month=5,
    )

    number = allocate_from_module(
        session,
        organization_id=sequence.organization_id,
        sequence=sequence,
        reference_date=date(2026, 5, 20),
    )

    assert number == "INV-2026-05-0008"
    assert sequence.current_number == 8


def _async_service_returning(sequence):
    from unittest.mock import AsyncMock, MagicMock

    from app.services.finance.common.numbering import NumberingService

    service = NumberingService.__new__(NumberingService)
    result = MagicMock()
    result.scalar_one_or_none.return_value = sequence
    service.db = MagicMock()
    service.db.execute = AsyncMock(return_value=result)
    service.db.flush = AsyncMock()
    return service


def test_settings_refuse_identity_edits_for_cut_over_series() -> None:
    import asyncio

    sequence = _sequence()
    service = _async_service_returning(sequence)

    with pytest.raises(ValueError, match="frozen for cut-over series"):
        asyncio.run(
            service.update_sequence(sequence.sequence_id or uuid.uuid4(), prefix="NEW")
        )


def test_settings_allow_unchanged_identity_for_cut_over_series() -> None:
    import asyncio

    sequence = _sequence()
    service = _async_service_returning(sequence)

    updated = asyncio.run(
        service.update_sequence(sequence.sequence_id or uuid.uuid4(), prefix="STMT")
    )

    assert updated is sequence


def test_settings_refuse_counter_reset_for_cut_over_series() -> None:
    import asyncio

    sequence = _sequence()
    service = _async_service_returning(sequence)

    with pytest.raises(ValueError, match="never move backward"):
        asyncio.run(
            service.reset_sequence_counter(
                sequence.sequence_id or uuid.uuid4(), new_value=0
            )
        )
