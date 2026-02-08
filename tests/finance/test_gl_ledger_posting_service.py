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
