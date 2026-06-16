"""Tests for the books of prime entry — Journal Proper partition.

Focus: the Journal Proper must exclude entries that already belong to another
book of prime entry. Besides the trade invoices themselves (Sales/Purchases day
books), this includes the cash-basis VAT *deferral* legs auto-generated as a
companion to each invoice — they are a mechanical byproduct of a document
already booked elsewhere, not a genuine adjusting entry (see day_books.py and
feedback_vat_cash_basis).
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest

from app.db.session_context import allow_cross_org
from app.models.finance.gl.account import Account
from app.models.finance.gl.account_category import AccountCategory
from app.models.finance.gl.journal_entry import (
    JournalEntry,
    JournalStatus,
    JournalType,
)
from app.models.finance.gl.journal_entry_line import JournalEntryLine
from app.services.finance.rpt.day_books import (
    _JOURNAL_PROPER_EXCLUDED_SOURCES,
    journal_day_book_context,
)

ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


@pytest.fixture()
def gl_tables(engine):
    """Create the GL tables the Journal Proper context touches (empty is fine).

    ``_cash_bank_accounts`` joins Account -> AccountCategory, so those tables
    must exist even though we create no cash accounts (which makes the cash
    exclusion short-circuit, isolating the source_document_type filter).
    """
    tables = [
        m.__table__ for m in (AccountCategory, Account, JournalEntry, JournalEntryLine)
    ]
    # SQLite can't compile Postgres ``gen_random_uuid()`` server defaults;
    # the test session sets PKs explicitly, so drop them (mirrors conftest).
    for table in tables:
        for column in table.columns:
            default = column.server_default
            if default is None:
                continue
            default_text = str(getattr(default, "arg", default)).lower()
            if "gen_random_uuid" in default_text or "uuid_generate" in default_text:
                column.server_default = None
    for table in tables:
        table.create(engine, checkfirst=True)
    return engine


def _make_journal(
    db,
    *,
    number: str,
    source: str | None,
    amount: str = "100.00",
    status: JournalStatus = JournalStatus.POSTED,
    posting_date: date = date(2025, 6, 1),
) -> JournalEntry:
    je = JournalEntry(
        organization_id=ORG_ID,
        journal_number=number,
        journal_type=JournalType.STANDARD,
        entry_date=posting_date,
        posting_date=posting_date,
        fiscal_period_id=uuid.uuid4(),
        description=f"Journal {number}",
        currency_code="NGN",
        exchange_rate=Decimal("1"),
        total_debit=Decimal(amount),
        total_credit=Decimal(amount),
        total_debit_functional=Decimal(amount),
        total_credit_functional=Decimal(amount),
        status=status,
        source_document_type=source,
        created_by_user_id=uuid.uuid4(),
    )
    db.add(je)
    return je


def test_deferral_sources_are_in_exclusion_contract() -> None:
    """Regression guard for the Finding B fix: deferral legs stay excluded."""
    assert "AR_INVOICE_VAT_DEFERRAL" in _JOURNAL_PROPER_EXCLUDED_SOURCES
    assert "SUPPLIER_INVOICE_VAT_DEFERRAL" in _JOURNAL_PROPER_EXCLUDED_SOURCES
    # The trade invoices themselves remain excluded too.
    assert "INVOICE" in _JOURNAL_PROPER_EXCLUDED_SOURCES
    assert "SUPPLIER_INVOICE" in _JOURNAL_PROPER_EXCLUDED_SOURCES


def test_exclusion_list_is_the_shared_constant() -> None:
    """Finding C: report exclusion is sourced from the single posting-layer
    constant, so the two cannot drift apart."""
    from app.services.finance.common.source_types import (
        AP_INVOICE_SOURCE,
        AR_INVOICE_SOURCE,
        INVOICE_JOURNAL_SOURCE_TYPES,
    )

    assert _JOURNAL_PROPER_EXCLUDED_SOURCES is INVOICE_JOURNAL_SOURCE_TYPES
    # The canonical invoice tags every posting path now writes must be the ones
    # the report excludes (the AR saga used to write "CUSTOMER_INVOICE").
    assert AR_INVOICE_SOURCE in INVOICE_JOURNAL_SOURCE_TYPES
    assert AP_INVOICE_SOURCE in INVOICE_JOURNAL_SOURCE_TYPES


def test_journal_proper_excludes_invoices_and_vat_deferral(gl_tables, db_session):
    """Trade invoices and their VAT-deferral legs drop out; adjusting entries stay."""
    with allow_cross_org(db_session):
        _make_journal(db_session, number="JE-INV", source="INVOICE")
        _make_journal(db_session, number="JE-SINV", source="SUPPLIER_INVOICE")
        _make_journal(db_session, number="JE-ARVAT", source="AR_INVOICE_VAT_DEFERRAL")
        _make_journal(
            db_session, number="JE-APVAT", source="SUPPLIER_INVOICE_VAT_DEFERRAL"
        )
        _make_journal(db_session, number="JE-RECLASS", source="RECLASS", amount="50.00")
        _make_journal(db_session, number="JE-MANUAL", source=None, amount="25.00")
        db_session.flush()

        ctx = journal_day_book_context(
            db_session, str(ORG_ID), "2025-01-01", "2025-12-31"
        )

    numbers = {r["journal_number"] for r in ctx["rows"]}
    # Genuine adjusting entries remain.
    assert numbers == {"JE-RECLASS", "JE-MANUAL"}
    # Invoices and their VAT-deferral companions are excluded.
    assert "JE-INV" not in numbers
    assert "JE-ARVAT" not in numbers
    assert "JE-APVAT" not in numbers
    assert ctx["row_count"] == 2
    # Excluding internally-balanced journals leaves the book balanced.
    assert ctx["is_balanced"] is True
    assert ctx["total_debit_raw"] == pytest.approx(75.0)
    assert ctx["total_credit_raw"] == pytest.approx(75.0)
    assert "VAT deferral" in ctx["scope_note"]


def test_journal_proper_defaults_to_posted_only(gl_tables, db_session):
    """Unposted journals never reach a day book (book of *posted* prime entry)."""
    with allow_cross_org(db_session):
        _make_journal(db_session, number="JE-POSTED", source="RECLASS")
        _make_journal(
            db_session,
            number="JE-DRAFT",
            source="RECLASS",
            status=JournalStatus.DRAFT,
        )
        db_session.flush()

        ctx = journal_day_book_context(
            db_session, str(ORG_ID), "2025-01-01", "2025-12-31"
        )

    numbers = {r["journal_number"] for r in ctx["rows"]}
    assert numbers == {"JE-POSTED"}
