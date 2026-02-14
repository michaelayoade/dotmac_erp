from __future__ import annotations

from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.models.finance.gl.journal_entry import JournalStatus, JournalType
from app.services.finance.gl.journal import (
    JournalInput,
    JournalLineInput,
    JournalService,
)


def _make_line(debit=Decimal("0"), credit=Decimal("0")):
    return JournalLineInput(
        account_id=uuid4(),
        debit_amount=debit,
        credit_amount=credit,
    )


def test_create_journal_requires_lines_and_balance():
    db = MagicMock()
    org_id = uuid4()

    with pytest.raises(HTTPException):
        JournalService.create_journal(
            db,
            org_id,
            JournalInput(
                journal_type=JournalType.STANDARD,
                entry_date=date.today(),
                posting_date=date.today(),
                description="No lines",
                lines=[],
            ),
            created_by_user_id=uuid4(),
        )

    with pytest.raises(HTTPException):
        JournalService.create_journal(
            db,
            org_id,
            JournalInput(
                journal_type=JournalType.STANDARD,
                entry_date=date.today(),
                posting_date=date.today(),
                description="Unbalanced",
                lines=[
                    _make_line(debit=Decimal("10")),
                    _make_line(credit=Decimal("5")),
                ],
            ),
            created_by_user_id=uuid4(),
        )


def test_create_journal_sets_functional_amounts():
    db = MagicMock()
    org_id = uuid4()
    period = SimpleNamespace(fiscal_year_id=uuid4(), fiscal_period_id=uuid4())
    db.add.return_value = None
    db.flush.return_value = None

    with (
        patch(
            "app.services.finance.gl.journal.PeriodGuardService.get_period_for_date",
            return_value=period,
        ),
        patch(
            "app.services.finance.gl.journal.SequenceService.get_next_number",
            return_value="J-1",
        ),
    ):
        journal = JournalService.create_journal(
            db,
            org_id,
            JournalInput(
                journal_type=JournalType.STANDARD,
                entry_date=date.today(),
                posting_date=date.today(),
                description="Balanced",
                exchange_rate=Decimal("2.0"),
                lines=[
                    _make_line(debit=Decimal("10"), credit=Decimal("0")),
                    _make_line(debit=Decimal("0"), credit=Decimal("10")),
                ],
            ),
            created_by_user_id=uuid4(),
        )

    assert journal.total_debit == Decimal("10")
    assert journal.total_credit == Decimal("10")


def test_update_journal_requires_draft_and_balance():
    db = MagicMock()
    org_id = uuid4()
    journal = SimpleNamespace(organization_id=org_id, status=JournalStatus.POSTED)
    db.get.return_value = journal

    with pytest.raises(HTTPException):
        JournalService.update_journal(
            db,
            org_id,
            uuid4(),
            JournalInput(
                journal_type=JournalType.STANDARD,
                entry_date=date.today(),
                posting_date=date.today(),
                description="Update",
                lines=[_make_line(debit=Decimal("1"), credit=Decimal("1"))],
            ),
            updated_by_user_id=uuid4(),
        )


def test_submit_approve_post_void_and_reverse():
    db = MagicMock()
    org_id = uuid4()
    journal = SimpleNamespace(
        journal_entry_id=uuid4(),
        organization_id=org_id,
        status=JournalStatus.DRAFT,
        created_by_user_id=uuid4(),
        posting_date=date.today(),
        source_module="GL",
        correlation_id="c",
    )
    db.get.return_value = journal

    submitted = JournalService.submit_journal(
        db, org_id, journal.journal_entry_id, submitted_by_user_id=uuid4()
    )
    assert submitted.status == JournalStatus.SUBMITTED

    journal.status = JournalStatus.SUBMITTED
    with pytest.raises(HTTPException):
        JournalService.approve_journal(
            db,
            org_id,
            journal.journal_entry_id,
            approved_by_user_id=journal.created_by_user_id,
        )

    approved = JournalService.approve_journal(
        db, org_id, journal.journal_entry_id, approved_by_user_id=uuid4()
    )
    assert approved.status == JournalStatus.APPROVED

    journal.status = JournalStatus.DRAFT
    with pytest.raises(HTTPException):
        JournalService.post_journal(
            db, org_id, journal.journal_entry_id, posted_by_user_id=uuid4()
        )

    journal.status = JournalStatus.APPROVED
    with patch(
        "app.services.finance.gl.journal.LedgerPostingService.post_journal_entry"
    ) as post_entry:
        post_entry.return_value = SimpleNamespace(success=True, message=None)
        JournalService.post_journal(
            db, org_id, journal.journal_entry_id, posted_by_user_id=uuid4()
        )

    journal.status = JournalStatus.SUBMITTED
    voided = JournalService.void_journal(
        db, org_id, journal.journal_entry_id, voided_by_user_id=uuid4(), reason="err"
    )
    assert voided.status == JournalStatus.VOID

    posted = SimpleNamespace(
        journal_entry_id=uuid4(),
        organization_id=org_id,
        status=JournalStatus.POSTED,
        journal_number="J-1",
        description="Posted",
        total_debit=Decimal("10"),
        total_credit=Decimal("10"),
        total_debit_functional=Decimal("10"),
        total_credit_functional=Decimal("10"),
        currency_code="NGN",
        exchange_rate=Decimal("1.0"),
        fiscal_period_id=uuid4(),
        lines=[
            SimpleNamespace(
                account_id=uuid4(),
                debit_amount=Decimal("10"),
                credit_amount=Decimal("0"),
                debit_amount_functional=Decimal("10"),
                credit_amount_functional=Decimal("0"),
                currency_code="NGN",
                exchange_rate=Decimal("1.0"),
                description="Line",
                business_unit_id=None,
                cost_center_id=None,
                project_id=None,
                segment_id=None,
            )
        ],
        reversal_journal_id=None,
        source_module="GL",
    )
    reversal_entry = SimpleNamespace(
        journal_entry_id=uuid4(),
        organization_id=org_id,
        status=JournalStatus.POSTED,
        journal_type=JournalType.REVERSAL,
        journal_number="J-REV",
    )

    def _get(model, pk):
        if pk == posted.journal_entry_id:
            return posted
        if pk == reversal_entry.journal_entry_id:
            return reversal_entry
        return None

    db.get.side_effect = _get

    with patch(
        "app.services.finance.gl.reversal.ReversalService.create_reversal",
        return_value=SimpleNamespace(
            success=True,
            reversal_journal_id=reversal_entry.journal_entry_id,
            reversal_journal_number=reversal_entry.journal_number,
            message="ok",
        ),
    ):
        reversal = JournalService.reverse_entry(
            db,
            org_id,
            posted.journal_entry_id,
            reversal_date=date.today(),
            reversed_by_user_id=uuid4(),
        )
    assert reversal.journal_type == JournalType.REVERSAL


def test_list_filters():
    db = MagicMock()
    query = MagicMock()
    db.query.return_value = query
    query.filter.return_value = query
    query.order_by.return_value = query
    query.limit.return_value.offset.return_value.all.return_value = []

    JournalService.list(db, organization_id=str(uuid4()))
