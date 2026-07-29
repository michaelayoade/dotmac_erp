"""Tests for scripts/find_stranded_reposts.py — the read-only remediation
detector for invoices stranded by the pre-fix repost idempotency collision.

Runs against the golden-pin harness world (tests/test_golden_money_pins.py):
the stranded case is constructed to be EXACTLY the row state the pre-fix
reverse-and-repost flow produced (repost journal APPROVED with zero ledger
lines, invoice claiming POSTED against the stale reversed batch), alongside a
healthy invoice and a properly reposted invoice that must NOT be flagged.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import select

from app.models.finance.ar.invoice import Invoice, InvoiceStatus, InvoiceType
from app.models.finance.ar.invoice_line import InvoiceLine
from app.models.finance.gl.fiscal_period import FiscalPeriod
from app.models.finance.gl.journal_entry import (
    JournalEntry,
    JournalStatus,
    JournalType,
)
from app.models.finance.gl.posted_ledger_line import PostedLedgerLine
from scripts.find_stranded_reposts import find_stranded_reposts
from tests.test_golden_money_pins import _SubSyncHarness, _World

try:
    from datetime import UTC  # type: ignore
except ImportError:  # pragma: no cover
    UTC = timezone.utc

_TODAY = date(2026, 7, 15)
_USER = uuid.UUID("00000000-0000-0000-0000-0000000000aa")


def _make_posted_invoice(w: _World, number: str) -> Invoice:
    """Create a Sub-synced invoice and GL-post it via the sync repair path."""
    db = w.db
    now = datetime.now(UTC)
    invoice = Invoice(
        organization_id=w.org_id,
        customer_id=w.customer.customer_id,
        invoice_number=number,
        invoice_type=InvoiceType.STANDARD,
        invoice_date=_TODAY,
        due_date=_TODAY,
        currency_code="NGN",
        exchange_rate=Decimal("1.0"),
        subtotal=Decimal("100.00"),
        tax_amount=Decimal("0"),
        total_amount=Decimal("100.00"),
        functional_currency_amount=Decimal("100.00"),
        status=InvoiceStatus.POSTED,
        ar_control_account_id=w.ar_control.account_id,
        source_document_type="dotmac_sub_invoice",
        created_by_user_id=_USER,
        created_at=now,
    )
    db.add(invoice)
    db.flush()
    db.add(
        InvoiceLine(
            invoice_id=invoice.invoice_id,
            line_number=1,
            description="Internet service",
            quantity=Decimal("1"),
            unit_price=Decimal("100.00"),
            line_amount=Decimal("100.00"),
            tax_amount=Decimal("0"),
            revenue_account_id=w.revenue.account_id,
            created_at=now,
        )
    )
    db.flush()
    _SubSyncHarness(db, w.org_id)._ensure_synced_invoice_posted(invoice, _USER)
    assert invoice.journal_entry_id is not None
    return invoice


def test_find_stranded_reposts_flags_only_the_stranded_invoice(
    world: _World,
) -> None:
    db, org = world.db, world.org_id
    harness = _SubSyncHarness(db, org)
    now = datetime.now(UTC)

    # -- Invoice A: recreate EXACTLY the legacy stranded state the pre-fix
    # reverse-and-repost produced (old golden-pin test 3): original journal
    # REVERSED, repost journal stranded APPROVED with zero ledger lines,
    # invoice claiming POSTED against the stale reversed batch.
    stranded_inv = _make_posted_invoice(world, "SUB-INV-STRAND-1")
    original = db.get(JournalEntry, stranded_inv.journal_entry_id)
    assert original is not None
    original_batch_id = original.posting_batch_id
    assert (
        harness._reverse_posted_invoice_gl(stranded_inv, _USER, reason="resync") is True
    )
    period_id = db.scalar(
        select(FiscalPeriod.fiscal_period_id).where(FiscalPeriod.organization_id == org)
    )
    stranded_journal = JournalEntry(
        organization_id=org,
        journal_number="JV-STRANDED-1",
        journal_type=JournalType.STANDARD,
        entry_date=_TODAY,
        posting_date=_TODAY,
        fiscal_period_id=period_id,
        description="stranded repost (never ledger-posted)",
        currency_code="NGN",
        exchange_rate=Decimal("1.0"),
        total_debit=Decimal("100.00"),
        total_credit=Decimal("100.00"),
        total_debit_functional=Decimal("100.00"),
        total_credit_functional=Decimal("100.00"),
        status=JournalStatus.APPROVED,
        source_module="AR",
        source_document_type="INVOICE",
        source_document_id=stranded_inv.invoice_id,
        created_by_user_id=_USER,
        created_at=now,
    )
    db.add(stranded_journal)
    db.flush()
    assert (
        db.scalars(
            select(PostedLedgerLine).where(
                PostedLedgerLine.journal_entry_id == stranded_journal.journal_entry_id
            )
        ).all()
        == []
    )
    stranded_inv.journal_entry_id = stranded_journal.journal_entry_id
    stranded_inv.posting_batch_id = original_batch_id
    stranded_inv.posting_status = "POSTED"
    db.flush()

    # -- Invoice B: healthy — posted once, never reversed. Must NOT flag.
    healthy_inv = _make_posted_invoice(world, "SUB-INV-HEALTHY-1")

    # -- Invoice C: reversed then PROPERLY reposted (reversal-aware fix) —
    # a successor POSTED journal exists. Must NOT flag.
    reposted_inv = _make_posted_invoice(world, "SUB-INV-REPOST-1")
    assert (
        harness._reverse_posted_invoice_gl(reposted_inv, _USER, reason="resync") is True
    )
    harness._ensure_synced_invoice_posted(reposted_inv, _USER)
    repost_journal = db.get(JournalEntry, reposted_inv.journal_entry_id)
    assert repost_journal is not None
    assert repost_journal.status == JournalStatus.POSTED

    # -- Detection: exactly the stranded invoice, with its remediation data.
    rows = find_stranded_reposts(db)
    assert [row.invoice_id for row in rows] == [stranded_inv.invoice_id]
    row = rows[0]
    assert row.organization_id == org
    assert row.invoice_number == "SUB-INV-STRAND-1"
    assert row.currency_code == "NGN"
    assert row.total_amount == Decimal("100.00")
    assert row.functional_currency_amount == Decimal("100.00")
    assert row.original_journal_number == original.journal_number
    # Reversal date comes from the reversal journal the sync posted "today".
    assert row.reversal_date == date.today()
    assert healthy_inv.invoice_id not in {r.invoice_id for r in rows}
    assert reposted_inv.invoice_id not in {r.invoice_id for r in rows}
