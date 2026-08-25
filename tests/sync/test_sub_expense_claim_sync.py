from __future__ import annotations

from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import Response
from sqlalchemy.exc import IntegrityError

from app.api.sync.dotmac_sub import create_sub_expense_claim
from app.models.expense.expense_claim import ExpenseClaimStatus
from app.schemas.sync.dotmac_sub import (
    SubExpenseClaimItemPayload,
    SubExpenseClaimPayload,
    SubExpenseClaimResponse,
)
from app.services.sync.dotmac_sub_sync_service import DotmacSubSyncService
from app.services.sync.sub.expenses import ExpenseClaimAcceptance
from app.services.sync.sub.errors import SubReplayConflictError, SubValidationError


def _payload(**overrides: object) -> SubExpenseClaimPayload:
    values: dict[str, object] = {
        "source_request_id": "expense-request-1",
        "purpose": "Site survey expenses",
        "claim_date": "2026-07-02",
        "requested_by_email": "field@example.com",
        "currency_code": "NGN",
        "remarks": "Approved by supervisor on site",
        "reference_number": "EXP-REQ-00042",
        "items": [
            SubExpenseClaimItemPayload(
                category_code="FUEL",
                description="Fuel for site visit",
                claimed_amount=Decimal("5000.00"),
                expense_date="2026-07-01",
            )
        ],
    }
    values.update(overrides)
    return SubExpenseClaimPayload.model_validate(values)


def _response(source_request_id: str = "expense-request-1") -> SubExpenseClaimResponse:
    return SubExpenseClaimResponse(
        claim_id=uuid4(),
        claim_number="EXP-2026-00001",
        status="submitted",
        source_request_id=source_request_id,
    )


def test_acceptance_reports_create_and_replay() -> None:
    service = DotmacSubSyncService(MagicMock())
    payload = _payload()
    outcome = _response()

    with (
        patch.object(service, "_find_claim", side_effect=[None, MagicMock()]),
        patch.object(service, "create_expense_claim", return_value=outcome) as create,
    ):
        created = service.accept_expense_claim(uuid4(), payload, uuid4())
        replayed = service.accept_expense_claim(uuid4(), payload, uuid4())

    assert created == ExpenseClaimAcceptance(outcome=outcome, replayed=False)
    assert replayed == ExpenseClaimAcceptance(outcome=outcome, replayed=True)
    assert create.call_count == 2


def test_unknown_employee_fails_closed() -> None:
    service = DotmacSubSyncService(MagicMock())
    with (
        patch.object(service, "_resolve_employee_id", return_value=None),
        pytest.raises(SubValidationError, match="No ERP employee matches email"),
    ):
        service.create_expense_claim(uuid4(), _payload(), uuid4())


def test_unknown_category_fails_closed() -> None:
    service = DotmacSubSyncService(MagicMock())
    with (
        patch.object(service, "_resolve_employee_id", return_value=uuid4()),
        patch.object(service, "_resolve_project_id", return_value=None),
        pytest.raises(SubValidationError, match="Unknown expense category code"),
    ):
        service.create_expense_claim(uuid4(), _payload(), uuid4())


def test_changed_replay_is_rejected() -> None:
    service = DotmacSubSyncService(MagicMock())
    employee_id = uuid4()
    category_id = uuid4()
    existing_line = MagicMock(
        sequence=0,
        category_id=category_id,
        expense_date=date(2026, 7, 1),
        description="Fuel for site visit",
        claimed_amount=Decimal("5000.00"),
        receipt_url=None,
        vendor_name=None,
        notes=None,
    )
    existing = MagicMock(
        employee_id=employee_id,
        claim_date=date(2026, 7, 2),
        purpose="Site survey expenses",
        project_id=None,
        currency_code="NGN",
        notes="Sub expense request: EXP-REQ-00042\nApproved by supervisor on site",
        items=[existing_line],
    )
    category = MagicMock(category_id=category_id)
    changed = _payload(
        items=[
            SubExpenseClaimItemPayload(
                category_code="FUEL",
                description="Fuel for site visit",
                claimed_amount=Decimal("9999.00"),
                expense_date="2026-07-01",
            )
        ]
    )

    with (
        patch.object(service, "_resolve_employee_id", return_value=employee_id),
        patch.object(service, "_resolve_project_id", return_value=None),
        patch.object(
            service, "_resolve_expense_categories", return_value={"FUEL": category}
        ),
        patch.object(service, "_find_claim", return_value=existing),
        pytest.raises(SubReplayConflictError, match="different immutable payload"),
    ):
        service.create_expense_claim(uuid4(), changed, uuid4())


def test_concurrent_expense_winner_with_changed_payload_is_rejected() -> None:
    service = DotmacSubSyncService(MagicMock())
    employee_id = uuid4()
    category_id = uuid4()
    category = MagicMock(category_id=category_id)
    raced_line = MagicMock(
        sequence=0,
        category_id=category_id,
        expense_date=date(2026, 7, 1),
        description="Fuel for site visit",
        claimed_amount=Decimal("9999.00"),
        receipt_url=None,
        vendor_name=None,
        notes=None,
    )
    raced = MagicMock(
        employee_id=employee_id,
        claim_date=date(2026, 7, 2),
        purpose="Site survey expenses",
        project_id=None,
        currency_code="NGN",
        notes="Sub expense request: EXP-REQ-00042\nApproved by supervisor on site",
        items=[raced_line],
    )

    with (
        patch.object(service, "_resolve_employee_id", return_value=employee_id),
        patch.object(service, "_resolve_project_id", return_value=None),
        patch.object(
            service, "_resolve_expense_categories", return_value={"FUEL": category}
        ),
        patch.object(service, "_find_claim", side_effect=[None, raced]),
        patch(
            "app.services.expense.ExpenseService.create_claim",
            side_effect=IntegrityError("insert", {}, Exception("duplicate")),
        ),
        pytest.raises(SubReplayConflictError, match="different immutable payload"),
    ):
        service.create_expense_claim(uuid4(), _payload(), uuid4())

    service.db.commit.assert_not_called()
    service.db.rollback.assert_not_called()


def test_status_response_uses_source_request_identity() -> None:
    db = MagicMock()
    claim = MagicMock(
        claim_id=uuid4(),
        claim_number="EXP-1",
        status=ExpenseClaimStatus.PAID,
        rejection_reason=None,
        paid_on=date(2026, 7, 5),
        total_claimed_amount=Decimal("5000.00"),
        total_approved_amount=Decimal("5000.00"),
    )
    db.scalar.return_value = claim

    result = DotmacSubSyncService(db).get_expense_claim_by_source_id(
        uuid4(), "expense-request-1"
    )

    assert result is not None
    assert result.source_request_id == "expense-request-1"
    assert result.status == "paid"


def test_route_returns_201_then_200_for_replay() -> None:
    outcome = _response()
    owner = MagicMock()
    owner.accept_expense_claim.side_effect = [
        ExpenseClaimAcceptance(outcome=outcome, replayed=False),
        ExpenseClaimAcceptance(outcome=outcome, replayed=True),
    ]
    auth = {"organization_id": uuid4(), "person_id": uuid4()}

    with patch("app.api.sync.dotmac_sub.DotmacSubSyncService", return_value=owner):
        first_response = Response()
        first = create_sub_expense_claim(
            _payload(), first_response, auth=auth, db=MagicMock()
        )
        second_response = Response()
        second = create_sub_expense_claim(
            _payload(), second_response, auth=auth, db=MagicMock()
        )

    assert first_response.status_code == 201
    assert second_response.status_code == 200
    assert first == second == outcome
