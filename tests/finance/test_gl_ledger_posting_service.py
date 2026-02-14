from __future__ import annotations

from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.models.finance.gl.journal_entry import JournalStatus
from app.models.finance.gl.posting_batch import BatchStatus
from app.services.finance.gl.ledger_posting import (
    LedgerPostingService,
    PostingEntry,
    PostingRequest,
)


def _make_request(entries=None, idempotency_key="key"):
    return PostingRequest(
        organization_id=uuid4(),
        journal_entry_id=uuid4(),
        posting_date=date.today(),
        idempotency_key=idempotency_key,
        source_module="GL",
        entries=entries or [],
    )


def test_post_journal_entry_requires_idempotency_key():
    db = MagicMock()
    req = _make_request(idempotency_key="")
    with pytest.raises(HTTPException):
        LedgerPostingService.post_journal_entry(db, req)


def test_post_journal_entry_idempotent_posted_batch():
    db = MagicMock()
    batch = SimpleNamespace(
        batch_id=uuid4(),
        status=BatchStatus.POSTED,
        posted_entries=2,
        correlation_id="c",
    )
    db.query.return_value.filter.return_value.first.return_value = batch
    req = _make_request()
    result = LedgerPostingService.post_journal_entry(db, req)
    assert result.success is True
    assert result.batch_id == batch.batch_id


def test_post_journal_entry_retries_failed_batch_in_place():
    db = MagicMock()
    org_id = uuid4()
    journal_id = uuid4()

    failed_batch = SimpleNamespace(
        batch_id=uuid4(),
        status=BatchStatus.FAILED,
        posted_entries=0,
        failed_entries=1,
        total_entries=1,
        error_message="boom",
        submitted_by_user_id=uuid4(),
        correlation_id="c",
        # fields that will be updated
        organization_id=None,
        fiscal_period_id=None,
        idempotency_key="key",
        source_module=None,
        batch_description=None,
        processing_started_at=None,
    )

    db.query.return_value.filter.return_value.first.return_value = failed_batch

    req = PostingRequest(
        organization_id=org_id,
        journal_entry_id=journal_id,
        posting_date=date.today(),
        idempotency_key="key",
        source_module="GL",
        posted_by_user_id=uuid4(),
        entries=[
            PostingEntry(
                account_id=uuid4(),
                debit_amount_functional=Decimal("10"),
                credit_amount_functional=Decimal("0"),
            ),
            PostingEntry(
                account_id=uuid4(),
                debit_amount_functional=Decimal("0"),
                credit_amount_functional=Decimal("10"),
            ),
        ],
    )

    journal = SimpleNamespace(
        journal_entry_id=journal_id,
        organization_id=org_id,
        status=JournalStatus.APPROVED,
        journal_number="J-1",
        entry_date=date.today(),
        reference="REF",
        source_document_type=None,
        source_document_id=None,
        created_by_user_id=uuid4(),
    )

    # First db.get() is journal; second is FiscalPeriod (ignored)
    db.get.side_effect = [journal, SimpleNamespace(fiscal_period_id=uuid4())]
    db.query.return_value.filter.return_value.all.return_value = [
        SimpleNamespace(account_id=req.entries[0].account_id, account_code="1000"),
        SimpleNamespace(account_id=req.entries[1].account_id, account_code="2000"),
    ]

    with (
        patch(
            "app.services.finance.gl.ledger_posting.PeriodGuardService.require_open_period",
            return_value=uuid4(),
        ),
        patch(
            "app.services.finance.gl.ledger_posting.LedgerPostingService._publish_posting_event",
            return_value=None,
        ),
    ):
        result = LedgerPostingService.post_journal_entry(db, req)

    assert result.success is True
    assert result.batch_id == failed_batch.batch_id
    assert failed_batch.status == BatchStatus.POSTED


def test_post_journal_entry_missing_journal():
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None
    db.get.return_value = None
    req = _make_request()
    with pytest.raises(HTTPException):
        LedgerPostingService.post_journal_entry(db, req)


def test_post_journal_entry_unbalanced_or_missing_functional():
    entries = [
        PostingEntry(
            account_id=uuid4(),
            debit_amount_functional=Decimal("10"),
            credit_amount_functional=Decimal("0"),
        ),
        PostingEntry(
            account_id=uuid4(),
            debit_amount_functional=Decimal("0"),
            credit_amount_functional=Decimal("5"),
        ),
    ]
    with pytest.raises(HTTPException):
        LedgerPostingService._validate_balance(entries)

    entries = [
        PostingEntry(
            account_id=uuid4(),
            debit_amount_functional=Decimal("0"),
            credit_amount_functional=Decimal("0"),
        ),
    ]
    with pytest.raises(HTTPException):
        LedgerPostingService._validate_functional_amounts(entries)

    entries = [
        PostingEntry(
            account_id=uuid4(),
            debit_amount_functional=Decimal("10"),
            credit_amount_functional=Decimal("1"),
        ),
    ]
    with pytest.raises(HTTPException):
        LedgerPostingService._validate_functional_amounts(entries)

    entries = [
        PostingEntry(
            account_id=uuid4(),
            debit_amount_functional=Decimal("-10"),
            credit_amount_functional=Decimal("0"),
        ),
    ]
    with pytest.raises(HTTPException):
        LedgerPostingService._validate_functional_amounts(entries)

    entries = [
        PostingEntry(
            account_id=uuid4(),
            debit_amount=Decimal("10"),
            credit_amount=Decimal("1"),
            debit_amount_functional=Decimal("10"),
            credit_amount_functional=Decimal("0"),
        ),
    ]
    with pytest.raises(HTTPException):
        LedgerPostingService._validate_functional_amounts(entries)

    entries = [
        PostingEntry(
            account_id=uuid4(),
            debit_amount=Decimal("-1"),
            credit_amount=Decimal("0"),
            debit_amount_functional=Decimal("1"),
            credit_amount_functional=Decimal("0"),
        ),
    ]
    with pytest.raises(HTTPException):
        LedgerPostingService._validate_functional_amounts(entries)


def test_post_journal_entry_rejects_non_approved_journal():
    db = MagicMock()
    org_id = uuid4()
    journal_id = uuid4()
    req = PostingRequest(
        organization_id=org_id,
        journal_entry_id=journal_id,
        posting_date=date.today(),
        idempotency_key="key",
        source_module="GL",
        entries=[
            PostingEntry(
                account_id=uuid4(),
                debit_amount_functional=Decimal("10"),
                credit_amount_functional=Decimal("0"),
            ),
            PostingEntry(
                account_id=uuid4(),
                debit_amount_functional=Decimal("0"),
                credit_amount_functional=Decimal("10"),
            ),
        ],
    )

    db.query.return_value.filter.return_value.first.return_value = None
    journal = SimpleNamespace(
        journal_entry_id=journal_id,
        organization_id=org_id,
        status=JournalStatus.DRAFT,
    )
    db.get.return_value = journal

    with pytest.raises(HTTPException) as exc:
        LedgerPostingService.post_journal_entry(db, req)

    assert exc.value.status_code == 400


def test_post_journal_entry_preserves_journal_line_traceability():
    """
    When PostingRequest.entries is empty, LedgerPostingService loads journal lines
    and should preserve `JournalEntryLine.line_id` into `PostedLedgerLine.journal_line_id`.
    """
    db = MagicMock()
    org_id = uuid4()
    journal_id = uuid4()
    account_id_1 = uuid4()
    account_id_2 = uuid4()

    req = PostingRequest(
        organization_id=org_id,
        journal_entry_id=journal_id,
        posting_date=date.today(),
        idempotency_key="key",
        source_module="GL",
        entries=[],
        posted_by_user_id=uuid4(),
    )

    batch_query = MagicMock()
    batch_query.filter.return_value.first.return_value = None

    line_id_1 = uuid4()
    line_id_2 = uuid4()
    line_query = MagicMock()
    line_query.filter.return_value.order_by.return_value.all.return_value = [
        SimpleNamespace(
            line_id=line_id_1,
            account_id=account_id_1,
            debit_amount=Decimal("10"),
            credit_amount=Decimal("0"),
            description="D1",
            debit_amount_functional=Decimal("10"),
            credit_amount_functional=Decimal("0"),
            currency_code="USD",
            exchange_rate=Decimal("1.0"),
            business_unit_id=None,
            cost_center_id=None,
            project_id=None,
            segment_id=None,
        ),
        SimpleNamespace(
            line_id=line_id_2,
            account_id=account_id_2,
            debit_amount=Decimal("0"),
            credit_amount=Decimal("10"),
            description="C1",
            debit_amount_functional=Decimal("0"),
            credit_amount_functional=Decimal("10"),
            currency_code="USD",
            exchange_rate=Decimal("1.0"),
            business_unit_id=None,
            cost_center_id=None,
            project_id=None,
            segment_id=None,
        ),
    ]

    acct_query = MagicMock()
    acct_query.filter.return_value.all.return_value = [
        SimpleNamespace(account_id=account_id_1, account_code="1000"),
        SimpleNamespace(account_id=account_id_2, account_code="2000"),
    ]

    def _query(model):
        if model.__name__ == "PostingBatch":
            return batch_query
        if model.__name__ == "JournalEntryLine":
            return line_query
        if model.__name__ == "Account":
            return acct_query
        raise AssertionError(f"Unexpected query model: {model}")

    db.query.side_effect = _query

    journal = SimpleNamespace(
        journal_entry_id=journal_id,
        organization_id=org_id,
        status=JournalStatus.APPROVED,
        journal_number="J-1",
        entry_date=date.today(),
        reference="REF",
        source_document_type=None,
        source_document_id=None,
        created_by_user_id=uuid4(),
        posting_date=req.posting_date,
        source_module="GL",
        correlation_id="c",
    )
    db.get.side_effect = [journal, SimpleNamespace(fiscal_period_id=uuid4())]

    added = []

    def _add(obj):
        added.append(obj)

    db.add.side_effect = _add

    with (
        patch(
            "app.services.finance.gl.ledger_posting.PeriodGuardService.require_open_period",
            return_value=uuid4(),
        ),
        patch(
            "app.services.finance.gl.ledger_posting.LedgerPostingService._publish_posting_event",
            return_value=None,
        ),
    ):
        result = LedgerPostingService.post_journal_entry(db, req)

    assert result.success is True

    posted_lines = [o for o in added if o.__class__.__name__ == "PostedLedgerLine"]
    assert len(posted_lines) == 2
    assert {l.journal_line_id for l in posted_lines} == {line_id_1, line_id_2}


def test_post_journal_entry_success_flow():
    db = MagicMock()
    org_id = uuid4()
    journal_id = uuid4()
    req = PostingRequest(
        organization_id=org_id,
        journal_entry_id=journal_id,
        posting_date=date.today(),
        idempotency_key="key",
        source_module="GL",
        entries=[
            PostingEntry(
                account_id=uuid4(),
                debit_amount_functional=Decimal("10"),
                credit_amount_functional=Decimal("0"),
            ),
            PostingEntry(
                account_id=uuid4(),
                debit_amount_functional=Decimal("0"),
                credit_amount_functional=Decimal("10"),
            ),
        ],
    )

    db.query.return_value.filter.return_value.first.return_value = None
    journal = SimpleNamespace(
        journal_entry_id=journal_id,
        organization_id=org_id,
        status=JournalStatus.APPROVED,
        journal_number="J-1",
        entry_date=date.today(),
        reference="REF",
        source_document_type=None,
        source_document_id=None,
        created_by_user_id=uuid4(),
    )
    db.get.side_effect = [journal, SimpleNamespace(fiscal_period_id=uuid4())]
    db.query.return_value.filter.return_value.all.return_value = [
        SimpleNamespace(account_id=req.entries[0].account_id, account_code="1000"),
        SimpleNamespace(account_id=req.entries[1].account_id, account_code="2000"),
    ]

    with (
        patch(
            "app.services.finance.gl.ledger_posting.PeriodGuardService.require_open_period",
            return_value=uuid4(),
        ),
        patch(
            "app.services.finance.gl.ledger_posting.LedgerPostingService._publish_posting_event",
            return_value=None,
        ),
    ):
        result = LedgerPostingService.post_journal_entry(db, req)

    assert result.success is True
    assert result.posted_lines == 2


def test_get_batch_and_get_ledger_lines():
    db = MagicMock()
    batch = SimpleNamespace()
    db.get.return_value = batch
    assert LedgerPostingService.get_batch(db, str(uuid4())) == batch

    query = MagicMock()
    db.query.return_value = query
    query.filter.return_value = query
    query.order_by.return_value = query
    query.limit.return_value.offset.return_value.all.return_value = []
    LedgerPostingService.get_ledger_lines(db, uuid4())


def test_list_and_post_entry():
    db = MagicMock()
    query = MagicMock()
    db.query.return_value = query
    query.filter.return_value = query
    query.order_by.return_value = query
    query.limit.return_value.offset.return_value.all.return_value = []
    LedgerPostingService.list(db, organization_id=str(uuid4()))

    journal = SimpleNamespace(
        journal_entry_id=uuid4(), journal_number="J-1", organization_id=uuid4()
    )
    db.get.return_value = journal
    with patch(
        "app.services.finance.gl.journal.journal_service.post_journal",
        return_value=journal,
    ):
        result = LedgerPostingService.post_entry(
            db,
            journal.organization_id,
            journal.journal_entry_id,
            posted_by_user_id=uuid4(),
        )
    assert result.success is True
