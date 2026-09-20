import csv
import io
import uuid
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from app.models.expense import (
    ExpenseCategory,
    ExpenseClaim,
    ExpenseClaimItem,
    ExpenseClaimStatus,
)
from app.models.people.hr.department import Department
from app.models.people.hr.employee import Employee, EmployeeStatus
from app.models.person import Person
from app.services.expense.dashboard_web import expense_dashboard_service
from app.services.expense.expense_service import ExpenseService
from app.services.expense.web import expense_claims_web_service
from app.templates import templates


def _ensure_hr_tables(engine) -> None:
    for table in (Department.__table__, Employee.__table__):
        for column in table.columns:
            default = column.server_default
            if default is None:
                continue
            default_text = str(getattr(default, "arg", default)).lower()
            if "gen_random_uuid" in default_text or "uuid_generate" in default_text:
                column.server_default = None
    Department.__table__.create(engine, checkfirst=True)
    Employee.__table__.create(engine, checkfirst=True)


def _make_person(org_id: uuid.UUID, email: str) -> Person:
    return Person(
        id=uuid.uuid4(),
        organization_id=org_id,
        first_name=email.split("@", 1)[0].title(),
        last_name="User",
        email=email,
    )


def _make_employee(org_id: uuid.UUID, person: Person, code: str) -> Employee:
    return Employee(
        employee_id=uuid.uuid4(),
        organization_id=org_id,
        person_id=person.id,
        employee_code=code,
        date_of_joining=date.today(),
        status=EmployeeStatus.ACTIVE,
    )


def _make_department(org_id: uuid.UUID, name: str) -> Department:
    return Department(
        department_id=uuid.uuid4(),
        organization_id=org_id,
        department_code=name[:3].upper(),
        department_name=name,
        is_active=True,
    )


def _make_claim(
    org_id: uuid.UUID,
    employee_id: uuid.UUID,
    claim_number: str,
    amount: Decimal,
    status: ExpenseClaimStatus,
    *,
    approved_amount: Decimal | None = None,
) -> ExpenseClaim:
    return ExpenseClaim(
        claim_id=uuid.uuid4(),
        organization_id=org_id,
        claim_number=claim_number,
        employee_id=employee_id,
        claim_date=date.today(),
        purpose=f"Purpose {claim_number}",
        total_claimed_amount=amount,
        total_approved_amount=approved_amount,
        status=status,
        currency_code="NGN",
    )


def _make_item(
    org_id: uuid.UUID,
    claim_id: uuid.UUID,
    category_id: uuid.UUID,
    amount: Decimal,
    *,
    approved_amount: Decimal | None = None,
) -> ExpenseClaimItem:
    return ExpenseClaimItem(
        item_id=uuid.uuid4(),
        organization_id=org_id,
        claim_id=claim_id,
        expense_date=date.today(),
        category_id=category_id,
        description="Taxi",
        claimed_amount=amount,
        approved_amount=approved_amount,
    )


def test_by_employee_department_filter_uses_department_display_name() -> None:
    source, _, _ = templates.env.loader.get_source(
        templates.env,
        "expense/reports/by_employee.html",
    )

    assert "{{ dept.department_name }}" in source
    assert "{{ dept.name }}" not in source


def test_expense_reports_exclude_rejected_claims_from_spend_totals(db_session, engine):
    _ensure_hr_tables(engine)
    org_id = uuid.uuid4()

    approvable_person = _make_person(org_id, "approved@example.com")
    rejected_person = _make_person(org_id, "rejected@example.com")
    approvable_employee = _make_employee(org_id, approvable_person, "EMP-001")
    rejected_employee = _make_employee(org_id, rejected_person, "EMP-002")
    category = ExpenseCategory(
        category_id=uuid.uuid4(),
        organization_id=org_id,
        category_code="TRAVEL",
        category_name="Travel",
        is_active=True,
    )

    approved_claim = _make_claim(
        org_id,
        approvable_employee.employee_id,
        "CLM-001",
        Decimal("100.00"),
        ExpenseClaimStatus.APPROVED,
        approved_amount=Decimal("90.00"),
    )
    rejected_claim = _make_claim(
        org_id,
        rejected_employee.employee_id,
        "CLM-002",
        Decimal("200.00"),
        ExpenseClaimStatus.REJECTED,
    )

    db_session.add_all(
        [
            approvable_person,
            rejected_person,
            approvable_employee,
            rejected_employee,
            category,
            approved_claim,
            rejected_claim,
            _make_item(
                org_id,
                approved_claim.claim_id,
                category.category_id,
                Decimal("100.00"),
                approved_amount=Decimal("90.00"),
            ),
            _make_item(
                org_id,
                rejected_claim.claim_id,
                category.category_id,
                Decimal("200.00"),
            ),
        ]
    )
    db_session.commit()

    svc = ExpenseService(db_session)

    summary = svc.get_expense_summary_report(org_id)
    assert summary["total_claims"] == 1
    assert summary["total_claimed"] == Decimal("100.00")
    assert summary["approved_count"] == 1
    assert summary["approved_amount"] == Decimal("90.00")
    assert summary["rejected_count"] == 1
    assert any(
        row["status"] == ExpenseClaimStatus.REJECTED.value and row["count"] == 1
        for row in summary["status_breakdown"]
    )

    by_category = svc.get_expense_by_category_report(org_id)
    assert by_category["total_claimed"] == Decimal("100.00")
    assert by_category["total_approved"] == Decimal("90.00")
    assert by_category["categories"][0]["claimed_amount"] == Decimal("100.00")
    assert by_category["categories"][0]["item_count"] == 1

    report_date = approved_claim.claim_date.isoformat()
    export_response = expense_claims_web_service.expense_by_category_export_response(
        auth=SimpleNamespace(organization_id=org_id),
        db=db_session,
        start_date=report_date,
        end_date=report_date,
    )
    export_rows = list(csv.reader(io.StringIO(export_response.body.decode("utf-8"))))
    assert export_rows == [
        [
            "Category Code",
            "Category Name",
            "Items",
            "Claimed Amount",
            "Reimbursed Amount",
            "Percent of Total",
        ],
        ["TRAVEL", "Travel", "1", "100.00", "90.00", "100.0"],
        ["TOTAL", "", "1", "100.00", "90.00", "100.0"],
    ]
    assert export_response.media_type == "text/csv"
    assert (
        f"expense_by_category_{report_date}_to_{report_date}.csv"
        in export_response.headers["Content-Disposition"]
    )

    summary_export = expense_claims_web_service.expense_summary_export_response(
        auth=SimpleNamespace(organization_id=org_id),
        db=db_session,
        start_date=report_date,
        end_date=report_date,
    )
    summary_rows = list(csv.reader(io.StringIO(summary_export.body.decode("utf-8"))))
    assert ["Total Claims", "1", "", ""] in summary_rows
    assert ["Total Claimed", "100.00", "", ""] in summary_rows
    assert ["Approved Claims", "1", "", ""] in summary_rows
    assert ["Reimbursed Amount", "90.00", "", ""] in summary_rows
    assert ["Rejected Claims", "1", "", ""] in summary_rows
    assert ["APPROVED", "1", "100.00", "100.0"] in summary_rows
    assert ["REJECTED", "1", "200.00", "200.0"] in summary_rows
    assert summary_rows[-1] == ["TOTAL", "1", "100.00", "100.0"]

    by_employee = svc.get_expense_by_employee_report(org_id)
    assert by_employee["total_claimed"] == Decimal("100.00")
    assert by_employee["total_approved"] == Decimal("90.00")
    assert len(by_employee["employees"]) == 1
    assert by_employee["employees"][0]["employee_id"] == str(
        approvable_employee.employee_id
    )
    assert by_employee["employees"][0]["claimed_amount"] == Decimal("100.00")

    employee_export = expense_claims_web_service.expense_by_employee_export_response(
        auth=SimpleNamespace(organization_id=org_id),
        db=db_session,
        start_date=report_date,
        end_date=report_date,
        department_id=None,
    )
    employee_rows = list(csv.reader(io.StringIO(employee_export.body.decode("utf-8"))))
    assert employee_rows[1] == [
        by_employee["employees"][0]["employee_name"],
        "No Department",
        "1",
        "100.00",
        "90.00",
        "90",
    ]
    assert employee_rows[-1] == ["TOTAL", "", "1", "100.00", "90.00", "90"]

    trends = svc.get_expense_trends_report(org_id, months=1)
    assert trends["total_claimed"] == Decimal("100.00")
    assert trends["total_approved"] == Decimal("90.00")
    assert trends["months"][0]["claim_count"] == 1
    assert trends["months"][0]["claimed_amount"] == Decimal("100.00")

    trends_export = expense_claims_web_service.expense_trends_export_response(
        auth=SimpleNamespace(organization_id=org_id),
        db=db_session,
        months=3,
    )
    trends_rows = list(csv.reader(io.StringIO(trends_export.body.decode("utf-8"))))
    assert trends_rows[-2] == ["TOTAL", "1", "100.00", "90.00", ""]
    assert trends_rows[-1] == ["AVERAGE MONTHLY", "", "33.33", "", ""]


def test_expense_trends_respects_explicit_date_range(db_session, engine):
    _ensure_hr_tables(engine)
    org_id = uuid.uuid4()
    person = _make_person(org_id, "trends@example.com")
    employee = _make_employee(org_id, person, "EMP-TREND")

    claims = []
    for claim_date, claim_number, amount in (
        (date(2026, 1, 15), "CLM-BEFORE", Decimal("10.00")),
        (date(2026, 1, 25), "CLM-JAN", Decimal("20.00")),
        (date(2026, 2, 5), "CLM-FEB", Decimal("30.00")),
        (date(2026, 2, 15), "CLM-AFTER", Decimal("40.00")),
    ):
        claim = _make_claim(
            org_id,
            employee.employee_id,
            claim_number,
            amount,
            ExpenseClaimStatus.PAID,
            approved_amount=amount,
        )
        claim.claim_date = claim_date
        claims.append(claim)

    db_session.add_all([person, employee, *claims])
    db_session.commit()

    start_date = date(2026, 1, 20)
    end_date = date(2026, 2, 10)
    report = ExpenseService(db_session).get_expense_trends_report(
        org_id,
        start_date=start_date,
        end_date=end_date,
    )

    assert report["start_date"] == start_date
    assert report["end_date"] == end_date
    assert report["total_claimed"] == Decimal("50.00")
    assert report["total_approved"] == Decimal("50.00")
    assert [month["claim_count"] for month in report["months"]] == [1, 1]
    assert report["months"][0]["start_date"] == start_date
    assert report["months"][0]["end_date"] == date(2026, 1, 31)
    assert report["months"][1]["start_date"] == date(2026, 2, 1)
    assert report["months"][1]["end_date"] == end_date

    export = expense_claims_web_service.expense_trends_export_response(
        auth=SimpleNamespace(organization_id=org_id),
        db=db_session,
        months=12,
        start_date=start_date.isoformat(),
        end_date=end_date.isoformat(),
    )
    rows = list(csv.reader(io.StringIO(export.body.decode("utf-8"))))
    assert rows[-2] == ["TOTAL", "2", "50.00", "50.00", ""]
    assert rows[-1] == ["AVERAGE MONTHLY", "", "25.00", "", ""]
    assert (
        "expense_trends_2026-01-20_to_2026-02-10.csv"
        in export.headers["Content-Disposition"]
    )


def test_expense_dashboard_spend_helpers_exclude_rejected_claims(db_session, engine):
    _ensure_hr_tables(engine)
    org_id = uuid.uuid4()

    approved_person = _make_person(org_id, "chart-approved@example.com")
    rejected_person = _make_person(org_id, "chart-rejected@example.com")
    approved_employee = _make_employee(org_id, approved_person, "EMP-101")
    rejected_employee = _make_employee(org_id, rejected_person, "EMP-102")
    category = ExpenseCategory(
        category_id=uuid.uuid4(),
        organization_id=org_id,
        category_code="MEALS",
        category_name="Meals",
        is_active=True,
    )

    approved_claim = _make_claim(
        org_id,
        approved_employee.employee_id,
        "CLM-101",
        Decimal("75.00"),
        ExpenseClaimStatus.PAID,
        approved_amount=Decimal("75.00"),
    )
    rejected_claim = _make_claim(
        org_id,
        rejected_employee.employee_id,
        "CLM-102",
        Decimal("300.00"),
        ExpenseClaimStatus.REJECTED,
    )

    db_session.add_all(
        [
            approved_person,
            rejected_person,
            approved_employee,
            rejected_employee,
            category,
            approved_claim,
            rejected_claim,
            _make_item(
                org_id,
                approved_claim.claim_id,
                category.category_id,
                Decimal("75.00"),
                approved_amount=Decimal("75.00"),
            ),
            _make_item(
                org_id,
                rejected_claim.claim_id,
                category.category_id,
                Decimal("300.00"),
            ),
        ]
    )
    db_session.commit()

    top_spenders = expense_dashboard_service._get_top_spenders(db_session, org_id, None)
    assert len(top_spenders) == 1
    assert top_spenders[0]["amount"] == 75.0

    category_distribution = expense_dashboard_service._get_category_distribution(
        db_session, org_id, None
    )
    assert len(category_distribution) == 1
    assert category_distribution[0]["amount"] == 75.0

    monthly_amounts = expense_dashboard_service._get_monthly_amounts(db_session, org_id)
    assert monthly_amounts[-1]["claimed"] == 75.0


def test_expense_dashboard_trend_excludes_rejected_claims(db_session, engine):
    _ensure_hr_tables(engine)
    org_id = uuid.uuid4()

    approved_person = _make_person(org_id, "trend-approved@example.com")
    rejected_person = _make_person(org_id, "trend-rejected@example.com")
    approved_employee = _make_employee(org_id, approved_person, "EMP-201")
    rejected_employee = _make_employee(org_id, rejected_person, "EMP-202")

    approved_claim = _make_claim(
        org_id,
        approved_employee.employee_id,
        "CLM-201",
        Decimal("120.00"),
        ExpenseClaimStatus.APPROVED,
        approved_amount=Decimal("100.00"),
    )
    rejected_claim = _make_claim(
        org_id,
        rejected_employee.employee_id,
        "CLM-202",
        Decimal("450.00"),
        ExpenseClaimStatus.REJECTED,
    )

    db_session.add_all(
        [
            approved_person,
            rejected_person,
            approved_employee,
            rejected_employee,
            approved_claim,
            rejected_claim,
        ]
    )
    db_session.commit()

    expense_trend = expense_dashboard_service._get_expense_trend(db_session, org_id)
    assert expense_trend[-1]["submitted"] == 120.0


def test_expense_dashboard_department_spending_excludes_rejected_claims(
    db_session, engine
):
    _ensure_hr_tables(engine)
    org_id = uuid.uuid4()

    ops_department = _make_department(org_id, "Operations")
    sales_department = _make_department(org_id, "Sales")
    approved_person = _make_person(org_id, "dept-approved@example.com")
    rejected_person = _make_person(org_id, "dept-rejected@example.com")
    approved_employee = _make_employee(org_id, approved_person, "EMP-301")
    rejected_employee = _make_employee(org_id, rejected_person, "EMP-302")
    approved_employee.department_id = ops_department.department_id
    rejected_employee.department_id = sales_department.department_id

    approved_claim = _make_claim(
        org_id,
        approved_employee.employee_id,
        "CLM-301",
        Decimal("80.00"),
        ExpenseClaimStatus.PAID,
        approved_amount=Decimal("80.00"),
    )
    rejected_claim = _make_claim(
        org_id,
        rejected_employee.employee_id,
        "CLM-302",
        Decimal("999.00"),
        ExpenseClaimStatus.REJECTED,
    )

    db_session.add_all(
        [
            ops_department,
            sales_department,
            approved_person,
            rejected_person,
            approved_employee,
            rejected_employee,
            approved_claim,
            rejected_claim,
        ]
    )
    db_session.commit()

    department_spending = expense_dashboard_service._get_department_spending(
        db_session, org_id, None
    )

    assert department_spending == [{"department": "Operations", "amount": 80.0}]
