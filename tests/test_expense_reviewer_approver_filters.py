from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4
from datetime import UTC, date, datetime
from decimal import Decimal

from starlette.responses import HTMLResponse

from app.models.expense import ExpenseClaim, ExpenseClaimStatus
from app.models.expense.expense_claim_action import (
    ExpenseClaimAction,
    ExpenseClaimActionStatus,
    ExpenseClaimActionType,
)
from app.models.expense.limit_rule import ExpenseApproverLimit
from app.models.people.hr.employee import Employee, EmployeeStatus
from app.models.person import Person
from app.services.expense.limit_web import ExpenseLimitWebService
from app.web.deps import WebAuthContext


REPO_ROOT = Path(__file__).resolve().parents[1]


def _ensure_reviewer_tables(engine) -> None:
    for table in (Employee.__table__, ExpenseApproverLimit.__table__):
        for column in table.columns:
            default = column.server_default
            if default is None:
                continue
            default_text = str(getattr(default, "arg", default)).lower()
            if "gen_random_uuid" in default_text or "uuid_generate" in default_text:
                column.server_default = None
        table.create(engine, checkfirst=True)


def _make_person(org_id, email: str) -> Person:
    return Person(
        id=uuid4(),
        organization_id=org_id,
        first_name=email.split("@", 1)[0].title(),
        last_name="Reviewer",
        email=email,
    )


def _make_employee(org_id, person: Person, code: str) -> Employee:
    return Employee(
        employee_id=uuid4(),
        organization_id=org_id,
        person_id=person.id,
        employee_code=code,
        date_of_joining=date(2026, 1, 1),
        status=EmployeeStatus.ACTIVE,
    )


def _make_claim(
    org_id,
    employee_id,
    approver_id,
    claim_number: str,
    *,
    amount_paid: Decimal,
    paid_on: date,
) -> ExpenseClaim:
    return ExpenseClaim(
        claim_id=uuid4(),
        organization_id=org_id,
        claim_number=claim_number,
        employee_id=employee_id,
        approver_id=approver_id,
        claim_date=paid_on,
        purpose=f"Purpose {claim_number}",
        total_claimed_amount=amount_paid,
        total_approved_amount=amount_paid,
        net_payable_amount=amount_paid,
        amount_paid=amount_paid,
        paid_on=paid_on,
        status=ExpenseClaimStatus.PAID,
        currency_code="NGN",
    )


def _make_action(org_id, claim_id, *, action_type, created_at: datetime):
    return ExpenseClaimAction(
        action_id=uuid4(),
        organization_id=org_id,
        claim_id=claim_id,
        action_type=action_type,
        status=ExpenseClaimActionStatus.COMPLETED,
        action_key=f"{claim_id}:{action_type.value}",
        created_at=created_at,
    )


def _capture_reviewer_context(monkeypatch):
    captured = {}

    monkeypatch.setattr(
        "app.services.expense.limit_web.base_context",
        lambda request, auth, title, section: {"title": title, "section": section},
    )

    def _template_response(request, template_name, context):
        captured["template_name"] = template_name
        captured["context"] = context
        return HTMLResponse("ok")

    monkeypatch.setattr(
        "app.services.expense.limit_web.templates.TemplateResponse",
        _template_response,
    )
    return captured


def _seed_reviewer_activity(db_session, engine):
    _ensure_reviewer_tables(engine)
    org_id = uuid4()
    employee_person = _make_person(org_id, "employee@example.com")
    approver_person = _make_person(org_id, "jane.approver@example.com")
    other_person = _make_person(org_id, "other.approver@example.com")
    payment_person = _make_person(org_id, "payment.approver@example.com")
    employee = _make_employee(org_id, employee_person, "EMP-001")
    approver = _make_employee(org_id, approver_person, "APR-001")
    other_approver = _make_employee(org_id, other_person, "APR-002")
    payment_approver = _make_employee(org_id, payment_person, "APR-003")
    included = _make_claim(
        org_id,
        employee.employee_id,
        approver.employee_id,
        "CLM-IN",
        amount_paid=Decimal("125.50"),
        paid_on=date(2026, 8, 1),
    )
    boundary = _make_claim(
        org_id,
        employee.employee_id,
        approver.employee_id,
        "CLM-BOUND",
        amount_paid=Decimal("74.50"),
        paid_on=date(2026, 8, 31),
    )
    excluded = _make_claim(
        org_id,
        employee.employee_id,
        approver.employee_id,
        "CLM-OUT",
        amount_paid=Decimal("500.00"),
        paid_on=date(2026, 9, 1),
    )
    other = _make_claim(
        org_id,
        employee.employee_id,
        other_approver.employee_id,
        "CLM-OTHER",
        amount_paid=Decimal("20.00"),
        paid_on=date(2026, 8, 15),
    )
    payment_only = _make_claim(
        org_id,
        employee.employee_id,
        payment_approver.employee_id,
        "CLM-PAY",
        amount_paid=Decimal("33.33"),
        paid_on=date(2026, 8, 20),
    )
    db_session.add_all(
        [
            employee_person,
            approver_person,
            other_person,
            payment_person,
            employee,
            approver,
            other_approver,
            payment_approver,
            included,
            boundary,
            excluded,
            other,
            payment_only,
            _make_action(
                org_id,
                included.claim_id,
                action_type=ExpenseClaimActionType.APPROVE,
                created_at=datetime(2026, 8, 1, 10, 0, tzinfo=UTC),
            ),
            _make_action(
                org_id,
                boundary.claim_id,
                action_type=ExpenseClaimActionType.REJECT,
                created_at=datetime(2026, 8, 31, 18, 30, tzinfo=UTC),
            ),
            _make_action(
                org_id,
                excluded.claim_id,
                action_type=ExpenseClaimActionType.APPROVE,
                created_at=datetime(2026, 9, 1, 9, 0, tzinfo=UTC),
            ),
            _make_action(
                org_id,
                other.claim_id,
                action_type=ExpenseClaimActionType.APPROVE,
                created_at=datetime(2026, 8, 15, 12, 0, tzinfo=UTC),
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
    return SimpleNamespace(
        auth=auth,
        approver=approver,
        other_approver=other_approver,
        payment_approver=payment_approver,
    )


def _render_reviewer_list(db_session, monkeypatch, auth, **filters):
    captured = _capture_reviewer_context(monkeypatch)
    response = ExpenseLimitWebService().reviewer_approver_list_response(
        request=SimpleNamespace(),
        auth=auth,
        db=db_session,
        **filters,
    )
    assert response.status_code == 200
    assert captured["template_name"] == "expense/limits/reviewer_approvers.html"
    return captured["context"]


def test_reviewer_approvers_page_exposes_approver_and_date_filters():
    html = (REPO_ROOT / "templates/expense/limits/reviewer_approvers.html").read_text(
        encoding="utf-8"
    )

    assert 'name="q"' in html
    assert 'name="from_date"' in html
    assert 'name="to_date"' in html


def test_reviewer_approver_filter_still_matches_approver(
    db_session, engine, monkeypatch
):
    seeded = _seed_reviewer_activity(db_session, engine)

    context = _render_reviewer_list(
        db_session,
        monkeypatch,
        seeded.auth,
        q="Jane",
        from_date=None,
        to_date=None,
    )

    assert [row["employee_id"] for row in context["approvers"]] == [
        str(seeded.approver.employee_id)
    ]


def test_reviewer_date_range_filters_activity_and_paid_totals(
    db_session, engine, monkeypatch
):
    seeded = _seed_reviewer_activity(db_session, engine)

    context = _render_reviewer_list(
        db_session,
        monkeypatch,
        seeded.auth,
        q=None,
        from_date="2026-08-01",
        to_date="2026-08-31",
    )

    approver_row = next(
        row
        for row in context["approvers"]
        if row["employee_id"] == str(seeded.approver.employee_id)
    )
    assert approver_row["approved_count"] == 1
    assert approver_row["rejected_count"] == 1
    assert approver_row["paid_count"] == 2
    assert approver_row["paid_amount"] == Decimal("200.00")
    assert context["summary"]["paid_amount"] == Decimal("253.33")
    assert context["summary"]["paid_count"] == 4


def test_reviewer_date_range_includes_payment_activity_without_period_decision(
    db_session, engine, monkeypatch
):
    seeded = _seed_reviewer_activity(db_session, engine)

    context = _render_reviewer_list(
        db_session,
        monkeypatch,
        seeded.auth,
        q="APR-003",
        from_date="2026-08-01",
        to_date="2026-08-31",
    )

    assert [row["employee_id"] for row in context["approvers"]] == [
        str(seeded.payment_approver.employee_id)
    ]
    assert context["approvers"][0]["approved_count"] == 0
    assert context["approvers"][0]["paid_count"] == 1
    assert context["approvers"][0]["paid_amount"] == Decimal("33.33")


def test_reviewer_approver_and_date_filters_combine(db_session, engine, monkeypatch):
    seeded = _seed_reviewer_activity(db_session, engine)

    context = _render_reviewer_list(
        db_session,
        monkeypatch,
        seeded.auth,
        q="APR-001",
        from_date="2026-08-01",
        to_date="2026-08-31",
    )

    assert [row["employee_id"] for row in context["approvers"]] == [
        str(seeded.approver.employee_id)
    ]
    assert context["summary"]["paid_amount"] == Decimal("200.00")


def test_reviewer_date_boundaries_are_inclusive(db_session, engine, monkeypatch):
    seeded = _seed_reviewer_activity(db_session, engine)

    context = _render_reviewer_list(
        db_session,
        monkeypatch,
        seeded.auth,
        q="Jane",
        from_date="2026-08-31",
        to_date="2026-08-31",
    )

    assert len(context["approvers"]) == 1
    assert context["approvers"][0]["rejected_count"] == 1
    assert context["approvers"][0]["paid_amount"] == Decimal("74.50")


def test_reviewer_no_date_filter_preserves_existing_results(
    db_session, engine, monkeypatch
):
    seeded = _seed_reviewer_activity(db_session, engine)

    context = _render_reviewer_list(
        db_session,
        monkeypatch,
        seeded.auth,
        q="Jane",
        from_date=None,
        to_date=None,
    )

    assert len(context["approvers"]) == 1
    assert context["approvers"][0]["approved_count"] == 2
    assert context["approvers"][0]["paid_amount"] == Decimal("700.00")


def test_reviewer_invalid_date_range_reports_error(db_session, engine, monkeypatch):
    seeded = _seed_reviewer_activity(db_session, engine)

    context = _render_reviewer_list(
        db_session,
        monkeypatch,
        seeded.auth,
        q=None,
        from_date="2026-09-01",
        to_date="2026-08-01",
    )

    assert context["approvers"] == []
    assert context["summary"]["paid_amount"] == Decimal("0")
    assert context["filter_errors"] == {
        "date_range": "From date must be on or before To date"
    }


def test_reviewer_empty_date_range_renders_empty_context(
    db_session, engine, monkeypatch
):
    seeded = _seed_reviewer_activity(db_session, engine)

    context = _render_reviewer_list(
        db_session,
        monkeypatch,
        seeded.auth,
        q=None,
        from_date="2026-07-01",
        to_date="2026-07-31",
    )

    assert context["approvers"] == []
    assert context["summary"]["paid_amount"] == Decimal("0")
    assert context["filter_errors"] == {}


def test_reviewer_links_preserve_date_range(db_session, engine, monkeypatch):
    seeded = _seed_reviewer_activity(db_session, engine)

    context = _render_reviewer_list(
        db_session,
        monkeypatch,
        seeded.auth,
        q="Jane",
        from_date="2026-08-01",
        to_date="2026-08-31",
    )

    assert context["approvers"][0]["review_url"].endswith(
        f"/{seeded.approver.employee_id}?from_date=2026-08-01&to_date=2026-08-31"
    )
