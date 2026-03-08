from datetime import date
from decimal import Decimal
import uuid

import pytest
from starlette.requests import Request
from starlette.responses import HTMLResponse

from app.models.expense import (
    ExpenseCategory,
    ExpenseClaim,
    ExpenseClaimAction,
    ExpenseClaimActionStatus,
    ExpenseClaimActionType,
    ExpenseClaimApprovalStep,
    ExpenseClaimItem,
    ExpenseClaimStatus,
)
from app.models.people.hr.department import Department
from app.models.people.hr.employee import Employee, EmployeeStatus
from app.models.person import Person
from app.services.expense import ExpenseClaimsWebService, ExpenseService, ExpenseServiceError
from app.web.deps import WebAuthContext


def _ensure_hr_tables(engine) -> None:
    for table in (Department.__table__, Employee.__table__):
        for column in table.columns:
            default = column.server_default
            if default is None:
                continue
            default_text = str(getattr(default, "arg", default)).lower()
            if "gen_random_uuid" in default_text or "uuid_generate" in default_text:
                column.server_default = None
        table.create(engine, checkfirst=True)


def _make_person(org_id: uuid.UUID, email: str) -> Person:
    return Person(
        id=uuid.uuid4(),
        organization_id=org_id,
        first_name=email.split("@", 1)[0].title(),
        last_name="User",
        email=email,
    )


def _make_employee(
    org_id: uuid.UUID,
    person: Person,
    code: str,
    *,
    reports_to_id: uuid.UUID | None = None,
) -> Employee:
    return Employee(
        employee_id=uuid.uuid4(),
        organization_id=org_id,
        person_id=person.id,
        employee_code=code,
        date_of_joining=date.today(),
        reports_to_id=reports_to_id,
        status=EmployeeStatus.ACTIVE,
    )


def _make_claim(
    org_id: uuid.UUID,
    employee_id: uuid.UUID,
    claim_number: str,
    *,
    requested_approver_id: uuid.UUID | None = None,
    status: ExpenseClaimStatus = ExpenseClaimStatus.DRAFT,
    amount: Decimal = Decimal("100.00"),
) -> ExpenseClaim:
    return ExpenseClaim(
        claim_id=uuid.uuid4(),
        organization_id=org_id,
        claim_number=claim_number,
        employee_id=employee_id,
        requested_approver_id=requested_approver_id,
        claim_date=date.today(),
        purpose=f"Purpose {claim_number}",
        total_claimed_amount=amount,
        status=status,
        currency_code="NGN",
    )


def _make_category(org_id: uuid.UUID) -> ExpenseCategory:
    return ExpenseCategory(
        category_id=uuid.uuid4(),
        organization_id=org_id,
        category_code="TRAVEL",
        category_name="Travel",
        requires_receipt=False,
        is_active=True,
    )


def _make_item(
    org_id: uuid.UUID,
    claim_id: uuid.UUID,
    category_id: uuid.UUID,
    amount: Decimal,
) -> ExpenseClaimItem:
    return ExpenseClaimItem(
        item_id=uuid.uuid4(),
        organization_id=org_id,
        claim_id=claim_id,
        expense_date=date.today(),
        category_id=category_id,
        description="Taxi",
        claimed_amount=amount,
    )


def _request(path: str) -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": path,
            "headers": [],
        }
    )


def test_approve_claim_requires_employee_backed_approver(db_session, engine):
    _ensure_hr_tables(engine)
    org_id = uuid.uuid4()

    claimant_person = _make_person(org_id, "claimant@example.com")
    claimant = _make_employee(org_id, claimant_person, "EMP-001")
    claim = _make_claim(
        org_id,
        claimant.employee_id,
        "CLM-001",
        status=ExpenseClaimStatus.PENDING_APPROVAL,
    )

    db_session.add_all([claimant_person, claimant, claim])
    db_session.commit()

    svc = ExpenseService(db_session)
    with pytest.raises(ExpenseServiceError, match="employee record"):
        svc.approve_claim(org_id, claim.claim_id, approver_id=None, send_notification=False)


def test_multi_approval_claim_remains_pending_until_all_steps_complete(
    db_session, engine, monkeypatch
):
    _ensure_hr_tables(engine)
    org_id = uuid.uuid4()

    claimant_person = _make_person(org_id, "claimant2@example.com")
    approver1_person = _make_person(org_id, "approver1@example.com")
    approver2_person = _make_person(org_id, "approver2@example.com")
    claimant = _make_employee(org_id, claimant_person, "EMP-101")
    approver1 = _make_employee(org_id, approver1_person, "EMP-102")
    approver2 = _make_employee(org_id, approver2_person, "EMP-103")
    claim = _make_claim(
        org_id,
        claimant.employee_id,
        "CLM-101",
        status=ExpenseClaimStatus.PENDING_APPROVAL,
    )

    db_session.add_all(
        [
            claimant_person,
            approver1_person,
            approver2_person,
            claimant,
            approver1,
            approver2,
            claim,
            ExpenseClaimApprovalStep(
                organization_id=org_id,
                claim_id=claim.claim_id,
                submission_round=1,
                step_number=1,
                approver_id=approver1.employee_id,
                approver_name=approver1.full_name,
                max_amount=Decimal("1000.00"),
                requires_all_approvals=True,
            ),
            ExpenseClaimApprovalStep(
                organization_id=org_id,
                claim_id=claim.claim_id,
                submission_round=1,
                step_number=2,
                approver_id=approver2.employee_id,
                approver_name=approver2.full_name,
                max_amount=Decimal("1000.00"),
                requires_all_approvals=True,
            ),
        ]
    )
    db_session.commit()

    svc = ExpenseService(db_session)
    monkeypatch.setattr(svc, "_validate_approver_authority", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        svc,
        "_validate_approver_weekly_budget",
        lambda *args, **kwargs: None,
    )

    first = svc.approve_claim(
        org_id,
        claim.claim_id,
        approver_id=approver1.employee_id,
        send_notification=False,
    )
    db_session.commit()

    assert first.status == ExpenseClaimStatus.PENDING_APPROVAL
    refreshed = svc.get_claim(org_id, claim.claim_id)
    assert refreshed.status == ExpenseClaimStatus.PENDING_APPROVAL
    assert refreshed.approver_id is None

    second = svc.approve_claim(
        org_id,
        claim.claim_id,
        approver_id=approver2.employee_id,
        send_notification=False,
    )
    db_session.commit()

    assert second.status == ExpenseClaimStatus.APPROVED
    steps = list(
        db_session.query(ExpenseClaimApprovalStep)
        .filter(ExpenseClaimApprovalStep.claim_id == claim.claim_id)
        .order_by(ExpenseClaimApprovalStep.step_number)
        .all()
    )
    assert [step.decision for step in steps] == ["APPROVED", "APPROVED"]


def test_resubmit_preserves_prior_approval_round_and_submit_creates_new_round(
    db_session, engine, monkeypatch
):
    _ensure_hr_tables(engine)
    org_id = uuid.uuid4()

    claimant_person = _make_person(org_id, "claimant3@example.com")
    approver_person = _make_person(org_id, "approver3@example.com")
    claimant = _make_employee(org_id, claimant_person, "EMP-201")
    approver = _make_employee(org_id, approver_person, "EMP-202")
    category = _make_category(org_id)
    claim = _make_claim(
        org_id,
        claimant.employee_id,
        "CLM-201",
        requested_approver_id=approver.employee_id,
        amount=Decimal("250.00"),
    )
    item = _make_item(org_id, claim.claim_id, category.category_id, Decimal("250.00"))

    db_session.add_all(
        [
            claimant_person,
            approver_person,
            claimant,
            approver,
            category,
            claim,
            item,
        ]
    )
    db_session.commit()

    monkeypatch.setattr(
        "app.services.expense.approval_service.ExpenseApprovalService._get_triggered_rules",
        lambda self, claim, employee: [],
    )
    monkeypatch.setattr(
        "app.services.expense.approval_service.ExpenseApprovalService._get_approver_max_amount",
        lambda self, org_id, approver: Decimal("1000.00"),
    )
    monkeypatch.setattr(
        "app.services.expense.expense_service.fire_audit_event",
        lambda **kwargs: None,
    )
    monkeypatch.setattr(
        "app.services.expense.expense_service.ExpenseService._notify_submission_confirmed",
        lambda self, claim: None,
    )
    monkeypatch.setattr(
        "app.services.finance.automation.event_dispatcher.fire_workflow_event",
        lambda *args, **kwargs: None,
    )

    svc = ExpenseService(db_session)
    submit_result = svc.submit_claim(org_id, claim.claim_id, skip_limit_check=True)
    db_session.commit()

    assert submit_result.claim.status == ExpenseClaimStatus.PENDING_APPROVAL

    rejected = svc.reject_claim(
        org_id,
        claim.claim_id,
        approver_id=approver.employee_id,
        reason="Missing details",
        send_notification=False,
    )
    db_session.commit()
    assert rejected.status == ExpenseClaimStatus.REJECTED

    reset = svc.resubmit_claim(org_id, claim.claim_id)
    db_session.commit()
    assert reset.status == ExpenseClaimStatus.DRAFT

    resubmitted = svc.submit_claim(org_id, claim.claim_id, skip_limit_check=True)
    db_session.commit()
    assert resubmitted.claim.status == ExpenseClaimStatus.PENDING_APPROVAL

    rounds = list(
        db_session.query(ExpenseClaimApprovalStep.submission_round, ExpenseClaimApprovalStep.decision)
        .filter(ExpenseClaimApprovalStep.claim_id == claim.claim_id)
        .order_by(
            ExpenseClaimApprovalStep.submission_round,
            ExpenseClaimApprovalStep.step_number,
        )
        .all()
    )
    assert rounds[0] == (1, "REJECTED")
    assert rounds[-1] == (2, None)

    submit_marker = (
        db_session.query(ExpenseClaimAction)
        .filter(
            ExpenseClaimAction.claim_id == claim.claim_id,
            ExpenseClaimAction.action_type == ExpenseClaimActionType.SUBMIT,
        )
        .one()
    )
    assert submit_marker.status == ExpenseClaimActionStatus.COMPLETED


def test_submitted_to_me_view_uses_pending_approval_steps(
    db_session, engine, monkeypatch
):
    _ensure_hr_tables(engine)
    org_id = uuid.uuid4()

    claimant_person = _make_person(org_id, "claimant4@example.com")
    approver_person = _make_person(org_id, "approver4@example.com")
    claimant = _make_employee(org_id, claimant_person, "EMP-301")
    approver = _make_employee(org_id, approver_person, "EMP-302")
    claim = _make_claim(
        org_id,
        claimant.employee_id,
        "CLM-301",
        status=ExpenseClaimStatus.PENDING_APPROVAL,
    )

    db_session.add_all(
        [
            claimant_person,
            approver_person,
            claimant,
            approver,
            claim,
            ExpenseClaimApprovalStep(
                organization_id=org_id,
                claim_id=claim.claim_id,
                submission_round=1,
                step_number=1,
                approver_id=approver.employee_id,
                approver_name=approver.full_name,
                max_amount=Decimal("1000.00"),
                requires_all_approvals=False,
            ),
        ]
    )
    db_session.commit()

    auth = WebAuthContext(
        is_authenticated=True,
        person_id=approver.person_id,
        employee_id=approver.employee_id,
        organization_id=org_id,
        roles=["admin"],
    )
    monkeypatch.setattr(
        "app.services.expense.web.base_context",
        lambda request, auth, title, section: {"user": auth.user},
    )
    monkeypatch.setattr(
        "app.services.expense.web.templates.TemplateResponse",
        lambda request, template_name, context: HTMLResponse(
            " ".join(claim.claim_number for claim in context["claims"])
        ),
    )
    response = ExpenseClaimsWebService.claims_list_response(
        request=_request("/expense/claims/list?view=submitted_to_me"),
        auth=auth,
        db=db_session,
        view="submitted_to_me",
        status=None,
        start_date=None,
        end_date=None,
    )

    assert response.status_code == 200
    assert "CLM-301" in response.body.decode()
