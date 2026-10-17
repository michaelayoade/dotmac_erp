"""Slice-1 shadow allocation for bank-statement numbering.

The legacy allocator still decides every issued number. These tests prove
the shadow seam maps legacy rows onto the composed module, seeds mid-period
continuation without changing future-period starts, and reports value
divergence without touching the legacy outcome.
"""

from __future__ import annotations

import uuid
from datetime import date

import pytest
from dotmac_kernel.models import Base
from dotmac_numbering import NumberingError
import dotmac_kernel.idempotency  # noqa: F401
from dotmac_numbering import models as numbering_models  # noqa: F401
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from app.models.finance.core_config.numbering_sequence import (
    NumberingSequence,
    ResetFrequency,
    SequenceType,
)
from app.services.finance.common.numbering_shadow import (
    series_code_for,
    series_configuration_for,
    shadow_allocate,
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


def test_shadow_seeds_mid_period_and_agrees_with_legacy(session: Session) -> None:
    sequence = _sequence(current_number=42, current_year=2026, current_month=5)

    agreed = shadow_allocate(
        session,
        organization_id=sequence.organization_id,
        sequence=sequence,
        reference_date=date(2026, 5, 20),
        legacy_number="STMT2026-00042",
        legacy_value=42,
    )

    assert agreed is True


def test_shadow_reports_a_planted_divergence(session: Session) -> None:
    sequence = _sequence(current_number=42, current_year=2026, current_month=5)
    # Seed the period counter first; divergence is only observable once the
    # module counter exists independently of the legacy value.
    assert shadow_allocate(
        session,
        organization_id=sequence.organization_id,
        sequence=sequence,
        reference_date=date(2026, 5, 20),
        legacy_number="STMT2026-00042",
        legacy_value=42,
    )

    agreed = shadow_allocate(
        session,
        organization_id=sequence.organization_id,
        sequence=sequence,
        reference_date=date(2026, 5, 20),
        legacy_number="STMT2026-00099",
        legacy_value=99,
    )

    assert agreed is False


def test_shadow_refuses_an_incoherent_legacy_row(session: Session) -> None:
    sequence = _sequence(year_format=3)

    with pytest.raises(NumberingError):
        shadow_allocate(
            session,
            organization_id=sequence.organization_id,
            sequence=sequence,
            reference_date=date(2026, 5, 20),
            legacy_number="STMT2026-00001",
            legacy_value=1,
        )
