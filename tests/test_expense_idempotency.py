import uuid
from datetime import date

import pytest
from fastapi import HTTPException

from app.api.idempotency import build_request_hash, check_or_reserve_idempotency
from app.models.expense import (
    ExpenseClaim,
    ExpenseClaimAction,
    ExpenseClaimActionStatus,
    ExpenseClaimActionType,
    ExpenseClaimStatus,
)
from app.services.expense import ExpenseService
from app.services.finance.platform.idempotency import IdempotencyService


def _new_claim(org_id: uuid.UUID, status: ExpenseClaimStatus) -> ExpenseClaim:
    return ExpenseClaim(
        claim_id=uuid.uuid4(),
        organization_id=org_id,
        claim_number=f"EXP-TEST-{uuid.uuid4().hex[:8]}",
        claim_date=date.today(),
        purpose="Test claim",
        currency_code="NGN",
        status=status,
        total_claimed_amount=0,
        advance_adjusted=0,
    )


def test_idempotency_replay(db_session):
    org_id = uuid.UUID("00000000-0000-0000-0000-000000000001")
    key = "test-idem-1"
    endpoint = "/expenses/claims"
    request_hash = build_request_hash({"amount": 100})

    replay = check_or_reserve_idempotency(
        db_session,
        organization_id=org_id,
        idempotency_key=key,
        endpoint=endpoint,
        request_hash=request_hash,
    )
    assert replay is None

    IdempotencyService.update_response(
        db=db_session,
        organization_id=org_id,
        idempotency_key=key,
        endpoint=endpoint,
        response_status=201,
        response_body={"ok": True},
    )

    replay2 = check_or_reserve_idempotency(
        db_session,
        organization_id=org_id,
        idempotency_key=key,
        endpoint=endpoint,
        request_hash=request_hash,
    )
    assert replay2 is not None
    assert replay2.status_code == 201
    assert replay2.body == {"ok": True}


def test_idempotency_conflict(db_session):
    org_id = uuid.UUID("00000000-0000-0000-0000-000000000001")
    key = "test-idem-2"
    endpoint = "/expenses/claims"
    hash_one = build_request_hash({"amount": 100})
    hash_two = build_request_hash({"amount": 200})

    check_or_reserve_idempotency(
        db_session,
        organization_id=org_id,
        idempotency_key=key,
        endpoint=endpoint,
        request_hash=hash_one,
    )

    with pytest.raises(HTTPException) as exc:
        check_or_reserve_idempotency(
            db_session,
            organization_id=org_id,
            idempotency_key=key,
            endpoint=endpoint,
            request_hash=hash_two,
        )
    assert exc.value.status_code == 409


def test_double_approve_is_idempotent(db_session):
    org_id = uuid.UUID("00000000-0000-0000-0000-000000000001")
    claim = _new_claim(org_id, ExpenseClaimStatus.SUBMITTED)
    db_session.add(claim)
    db_session.commit()

    svc = ExpenseService(db_session)
    first = svc.approve_claim(org_id, claim.claim_id)
    db_session.commit()
    second = svc.approve_claim(org_id, claim.claim_id)
    db_session.commit()

    assert first.status == ExpenseClaimStatus.APPROVED
    assert second.status == ExpenseClaimStatus.APPROVED

    actions = (
        db_session.query(ExpenseClaimAction)
        .filter(
            ExpenseClaimAction.claim_id == claim.claim_id,
            ExpenseClaimAction.action_type == ExpenseClaimActionType.APPROVE,
        )
        .all()
    )
    assert len(actions) == 1
    assert actions[0].status == ExpenseClaimActionStatus.COMPLETED


def test_double_mark_paid_is_idempotent(db_session):
    org_id = uuid.UUID("00000000-0000-0000-0000-000000000001")
    claim = _new_claim(org_id, ExpenseClaimStatus.APPROVED)
    db_session.add(claim)
    db_session.commit()

    svc = ExpenseService(db_session)
    first = svc.mark_paid(org_id, claim.claim_id, payment_reference="REF1")
    db_session.commit()
    second = svc.mark_paid(org_id, claim.claim_id, payment_reference="REF1")
    db_session.commit()

    assert first.status == ExpenseClaimStatus.PAID
    assert second.status == ExpenseClaimStatus.PAID

    actions = (
        db_session.query(ExpenseClaimAction)
        .filter(
            ExpenseClaimAction.claim_id == claim.claim_id,
            ExpenseClaimAction.action_type == ExpenseClaimActionType.MARK_PAID,
        )
        .all()
    )
    assert len(actions) == 1
    assert actions[0].status == ExpenseClaimActionStatus.COMPLETED
