from __future__ import annotations

from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from app.services.finance.ap.web.receipt_approval_web import ReceiptApprovalWebService
from app.web.deps import WebAuthContext


class _Request:
    def __init__(self, form_data: dict[str, str]):
        self._form_data = form_data

    async def form(self):
        return self._form_data


def _auth(org_id, user_id):
    return WebAuthContext(
        is_authenticated=True,
        person_id=user_id,
        organization_id=org_id,
        user_name="Store Manager",
    )


@pytest.mark.asyncio
async def test_approve_receipt_approval_commits_success():
    service = ReceiptApprovalWebService()
    db = MagicMock()
    org_id = uuid4()
    user_id = uuid4()
    approval_id = uuid4()
    request = _Request(
        {
            "approved_quantity": "2",
            "warehouse_id": str(uuid4()),
            "receipt_serial_numbers": "",
        }
    )

    with patch(
        "app.services.finance.ap.web.receipt_approval_web."
        "ap_inventory_receipt_approval_service.approve"
    ) as approve:
        response = await service.approve_receipt_approval_response(
            request,
            _auth(org_id, user_id),
            db,
            str(approval_id),
        )

    approve.assert_called_once()
    db.commit.assert_called_once()
    db.rollback.assert_not_called()
    assert response.status_code == 303
    assert "success=Receipt+approved" in response.headers["location"]


@pytest.mark.asyncio
async def test_approve_receipt_approval_rolls_back_on_error():
    service = ReceiptApprovalWebService()
    db = MagicMock()
    org_id = uuid4()
    user_id = uuid4()
    approval_id = uuid4()
    request = _Request({"approved_quantity": "2"})

    with patch(
        "app.services.finance.ap.web.receipt_approval_web."
        "ap_inventory_receipt_approval_service.approve",
        side_effect=RuntimeError("boom"),
    ):
        response = await service.approve_receipt_approval_response(
            request,
            _auth(org_id, user_id),
            db,
            str(approval_id),
        )

    db.commit.assert_not_called()
    db.rollback.assert_called_once()
    assert response.status_code == 303
    assert "error=boom" in response.headers["location"]


@pytest.mark.asyncio
async def test_reject_receipt_approval_commits_success():
    service = ReceiptApprovalWebService()
    db = MagicMock()
    org_id = uuid4()
    user_id = uuid4()
    approval_id = uuid4()
    request = _Request({"rejection_reason": "Items not received"})

    with patch(
        "app.services.finance.ap.web.receipt_approval_web."
        "ap_inventory_receipt_approval_service.reject"
    ) as reject:
        response = await service.reject_receipt_approval_response(
            request,
            _auth(org_id, user_id),
            db,
            str(approval_id),
        )

    reject.assert_called_once()
    db.commit.assert_called_once()
    db.rollback.assert_not_called()
    assert response.status_code == 303
    assert "success=Receipt+approval+rejected" in response.headers["location"]
