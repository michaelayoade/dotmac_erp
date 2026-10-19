"""The numbering continuity report names drift before invoice cutover.

Runs the real report logic against a small SQLite world holding just the
invoice and numbering-sequence tables: a clean counter is silent, a
counter below issued reality is named as drift, a duplicated rendered
number is named, and an externally numbered document is ignored.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.models.finance.ar.invoice import Invoice, InvoiceStatus, InvoiceType
from app.models.finance.core_config.numbering_sequence import (
    NumberingSequence,
    ResetFrequency,
    SequenceType,
)
from scripts.numbering_continuity_report import continuity_findings

ORG = uuid.uuid4()
CUSTOMER = uuid.uuid4()
USER = uuid.uuid4()


@pytest.fixture()
def db() -> Session:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        execution_options={"schema_translate_map": {"ar": None, "core_config": None}},
    )
    for table in (Invoice.__table__, NumberingSequence.__table__):
        for column in table.columns:
            default = column.server_default
            if default is not None and "gen_random_uuid" in str(
                getattr(default, "arg", default)
            ):
                column.server_default = None
        table.create(engine, checkfirst=True)
    session = sessionmaker(bind=engine, autoflush=False)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _sequence(current_number: int) -> NumberingSequence:
    return NumberingSequence(
        organization_id=ORG,
        sequence_type=SequenceType.INVOICE,
        prefix="INV",
        suffix="",
        separator="-",
        min_digits=4,
        include_year=True,
        include_month=True,
        year_format=4,
        current_number=current_number,
        current_year=2026,
        current_month=5,
        reset_frequency=ResetFrequency.MONTHLY,
    )


def _invoice(number: str, when: date = date(2026, 5, 10)) -> Invoice:
    now = datetime.now(timezone.utc)
    return Invoice(
        organization_id=ORG,
        customer_id=CUSTOMER,
        invoice_number=number,
        invoice_type=InvoiceType.STANDARD,
        invoice_date=when,
        due_date=when,
        currency_code="NGN",
        exchange_rate=Decimal("1"),
        subtotal=Decimal("1"),
        tax_amount=Decimal("0"),
        total_amount=Decimal("1"),
        functional_currency_amount=Decimal("1"),
        status=InvoiceStatus.POSTED,
        ar_control_account_id=uuid.uuid4(),
        created_by_user_id=USER,
        created_at=now,
    )


def test_a_clean_counter_is_silent(db: Session) -> None:
    db.add(_sequence(current_number=7))
    db.add(_invoice("INV202605-0007"))
    db.flush()

    assert continuity_findings(db) == []


def test_a_counter_below_issued_reality_is_named_as_drift(db: Session) -> None:
    db.add(_sequence(current_number=3))
    db.add(_invoice("INV202605-0009"))
    db.flush()

    findings = continuity_findings(db)

    assert [finding.kind for finding in findings] == ["drift"]
    assert "issued max 9" in findings[0].detail


def test_a_duplicated_value_under_two_renderings_is_named(db: Session) -> None:
    db.add(_sequence(current_number=7))
    db.add(_invoice("INV202605-0007"))
    db.add(_invoice("INV202605-007"))
    db.flush()

    findings = continuity_findings(db)

    assert [finding.kind for finding in findings] == ["duplicates"]


def test_an_externally_numbered_document_is_ignored(db: Session) -> None:
    db.add(_sequence(current_number=1))
    db.add(_invoice("EXT-9999"))
    db.flush()

    assert continuity_findings(db) == []


def test_the_scan_runs_under_allow_cross_org_and_restores_it(db: Session) -> None:
    """Production regression: the org listener refused the unprimed scan."""
    import scripts.numbering_continuity_report as report

    seen: list[bool] = []
    real_allow_cross_org = report.allow_cross_org

    from contextlib import contextmanager

    @contextmanager
    def spy(session):
        with real_allow_cross_org(session):
            seen.append(session.info.get("allow_cross_org") is True)
            yield

    db.add(_sequence(current_number=7))
    db.flush()
    original = report.allow_cross_org
    report.allow_cross_org = spy
    try:
        assert report.continuity_findings(db) == []
    finally:
        report.allow_cross_org = original

    assert seen == [True]
    assert db.info.get("allow_cross_org") in (False, None)
