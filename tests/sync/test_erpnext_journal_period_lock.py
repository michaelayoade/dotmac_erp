"""
Regression test for ERPNext journal sync period-lock bypass.

Pre-fix: ``JournalEntrySyncService.create_entity`` resolved the fiscal
period by date range only, never checking whether the period was closed.
ERPNext-pushed entries dated within hard-closed periods landed silently.

This test patches ``PeriodGuardService.require_open_period`` to raise
HTTPException (the behavior it exhibits when called against a hard-closed
period) and asserts that ``create_entity`` propagates the rejection.
"""

from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException


def _make_sync_service():
    from app.services.erpnext.sync.journal_entry import JournalEntrySyncService

    db = MagicMock()
    org_id = uuid4()
    user_id = uuid4()
    return JournalEntrySyncService(db, org_id, user_id), db, org_id


def _journal_payload():
    return {
        "posting_date": date(2024, 1, 15),
        "_voucher_type": "Journal Entry",
        "_accounts": [],
        "_user_remark": "Test entry",
        "_docstatus": 1,
        "total_debit": Decimal("100"),
        "total_credit": Decimal("100"),
    }


def test_create_entity_rejects_post_into_closed_period():
    """
    create_entity must invoke PeriodGuardService.require_open_period and
    propagate its HTTPException(400) when the target period is closed.
    """
    service, _, _ = _make_sync_service()

    fiscal_period_id = uuid4()
    service._resolve_fiscal_period = MagicMock(return_value=fiscal_period_id)

    with patch(
        "app.services.finance.gl.period_guard.PeriodGuardService.require_open_period",
        side_effect=HTTPException(
            status_code=400, detail="Period is permanently closed"
        ),
    ) as guard:
        with pytest.raises(HTTPException) as exc_info:
            service.create_entity(_journal_payload())

    assert exc_info.value.status_code == 400
    assert "closed" in exc_info.value.detail.lower()
    assert guard.called, "PeriodGuardService.require_open_period was not called"


def test_create_entity_does_not_persist_when_period_closed():
    """
    When PeriodGuard rejects the post, no JournalEntry should be added to
    the session — the rejection must occur before db.add().
    """
    service, db, _ = _make_sync_service()

    service._resolve_fiscal_period = MagicMock(return_value=uuid4())

    with patch(
        "app.services.finance.gl.period_guard.PeriodGuardService.require_open_period",
        side_effect=HTTPException(status_code=400, detail="Period is closed"),
    ):
        with pytest.raises(HTTPException):
            service.create_entity(_journal_payload())

    assert not db.add.called, "Journal was persisted despite period rejection"
