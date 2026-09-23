from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

from app.models.finance.gl.journal_entry import JournalStatus
from app.services.dotmac_sub.sync._invoices import InvoiceSyncMixin


class _Harness(InvoiceSyncMixin):
    pass


def _case(status: JournalStatus):
    organization_id = uuid4()
    invoice_id = uuid4()
    journal_id = uuid4()
    user_id = uuid4()
    invoice = SimpleNamespace(
        invoice_id=invoice_id,
        journal_entry_id=journal_id,
        posting_batch_id=uuid4(),
        posting_status="POSTED",
        created_by_user_id=user_id,
    )
    journal = SimpleNamespace(
        journal_entry_id=journal_id,
        organization_id=organization_id,
        source_module="AR",
        source_document_type="INVOICE",
        source_document_id=invoice_id,
        is_reversal=False,
        status=status,
        posting_batch_id=None,
        posted_at=None,
        posted_by_user_id=None,
        reversal_journal_id=None,
    )
    harness = _Harness()
    harness.organization_id = organization_id
    harness.db = MagicMock()
    harness.db.get.return_value = journal
    harness.db.scalar.return_value = None
    return harness, invoice, journal, user_id


def test_approved_unposted_journal_is_audited_void_before_repost() -> None:
    harness, invoice, journal, user_id = _case(JournalStatus.APPROVED)

    with (
        patch(
            "app.services.finance.gl.journal.JournalService.void_journal"
        ) as void_journal,
        patch(
            "app.services.finance.gl.reversal.ReversalService.create_reversal"
        ) as create_reversal,
    ):
        recovered = harness._reverse_posted_invoice_gl(
            invoice, user_id, reason="sub resync: accounting changed"
        )

    assert recovered is True
    void_journal.assert_called_once_with(
        db=harness.db,
        organization_id=harness.organization_id,
        journal_entry_id=journal.journal_entry_id,
        voided_by_user_id=user_id,
        reason="sub resync: accounting changed",
    )
    create_reversal.assert_not_called()
    assert invoice.journal_entry_id is None
    assert invoice.posting_batch_id is None
    assert invoice.posting_status == "NOT_POSTED"


def test_posted_journal_still_uses_controlled_reversal() -> None:
    harness, invoice, _journal, user_id = _case(JournalStatus.POSTED)
    reversal = SimpleNamespace(success=True)

    with (
        patch(
            "app.services.finance.gl.reversal.ReversalService.create_reversal",
            return_value=reversal,
        ) as create_reversal,
        patch(
            "app.services.finance.gl.journal.JournalService.void_journal"
        ) as void_journal,
    ):
        recovered = harness._reverse_posted_invoice_gl(
            invoice, user_id, reason="sub resync: accounting changed"
        )

    assert recovered is True
    create_reversal.assert_called_once()
    void_journal.assert_not_called()


def test_unposted_journal_with_posting_batch_fails_closed() -> None:
    harness, invoice, journal, user_id = _case(JournalStatus.APPROVED)
    journal.posting_batch_id = uuid4()

    with patch(
        "app.services.finance.gl.journal.JournalService.void_journal"
    ) as void_journal:
        recovered = harness._reverse_posted_invoice_gl(
            invoice, user_id, reason="sub resync: accounting changed"
        )

    assert recovered is False
    void_journal.assert_not_called()
    assert invoice.journal_entry_id == journal.journal_entry_id
    assert invoice.posting_status == "POSTED"


def test_unposted_journal_with_ledger_row_fails_closed() -> None:
    harness, invoice, journal, user_id = _case(JournalStatus.APPROVED)
    harness.db.scalar.return_value = uuid4()

    with patch(
        "app.services.finance.gl.journal.JournalService.void_journal"
    ) as void_journal:
        recovered = harness._reverse_posted_invoice_gl(
            invoice, user_id, reason="sub resync: accounting changed"
        )

    assert recovered is False
    void_journal.assert_not_called()
    assert invoice.journal_entry_id == journal.journal_entry_id


def test_mismatched_journal_provenance_fails_closed() -> None:
    harness, invoice, journal, user_id = _case(JournalStatus.APPROVED)
    journal.source_document_id = uuid4()

    with patch(
        "app.services.finance.gl.journal.JournalService.void_journal"
    ) as void_journal:
        recovered = harness._reverse_posted_invoice_gl(
            invoice, user_id, reason="sub resync: accounting changed"
        )

    assert recovered is False
    void_journal.assert_not_called()
    harness.db.scalar.assert_not_called()
    assert invoice.journal_entry_id == journal.journal_entry_id
