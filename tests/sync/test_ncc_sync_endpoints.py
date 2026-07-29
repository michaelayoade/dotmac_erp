"""Wiring tests for the service-authenticated NCC sync endpoints.

The endpoints in app/api/sync/dotmac_crm.py are thin wrappers over
``NccStaffReportService`` and ``ncc_financials_context`` (both covered by their
own service-level tests). These tests verify the sync wrappers extract the org
from the service-auth context and build the response models correctly, so the
CRM regulatory-pack aggregator gets Section F/G over the X-API-Key channel.
"""

from __future__ import annotations

import uuid
from datetime import date

from app.api.sync.dotmac_crm import (
    NccFinancialsResponse,
    NccStaffHeadcountResponse,
    ncc_financials,
    ncc_staff_headcount,
)
from app.models.people.hr.designation import Designation, NccStaffCategory
from app.models.people.hr.employee import Employee, EmployeeStatus, Gender
from app.models.person import Person


def _ensure(engine, *tables) -> None:
    for table in tables:
        for col in table.columns:
            default = col.server_default
            if (
                default is not None
                and "gen_random_uuid" in str(getattr(default, "arg", default)).lower()
            ):
                col.server_default = None
        table.create(engine, checkfirst=True)


def _designation(db, org_id, category) -> Designation:
    d = Designation(
        designation_id=uuid.uuid4(),
        organization_id=org_id,
        designation_code=f"D-{uuid.uuid4().hex[:6]}",
        designation_name="Role",
        ncc_staff_category=category,
    )
    db.add(d)
    db.flush()
    return d


def _employee(db, org_id, *, designation, nationality, gender) -> Employee:
    person = Person(
        id=uuid.uuid4(),
        organization_id=org_id,
        first_name="A",
        last_name="B",
        email=f"{uuid.uuid4().hex}@example.com",
    )
    emp = Employee(
        employee_id=uuid.uuid4(),
        organization_id=org_id,
        person_id=person.id,
        employee_code=f"EMP-{uuid.uuid4().hex[:8]}",
        date_of_joining=date(2026, 1, 1),
        status=EmployeeStatus.ACTIVE,
        gender=gender,
        nationality=nationality,
        designation_id=designation.designation_id if designation else None,
    )
    db.add_all([person, emp])
    db.flush()
    return emp


def test_ncc_staff_headcount_endpoint_uses_service_org(db_session):
    _ensure(
        db_session.bind, Person.__table__, Employee.__table__, Designation.__table__
    )
    org = uuid.uuid4()
    mgr = _designation(db_session, org, NccStaffCategory.MANAGERIAL)
    _employee(
        db_session, org, designation=mgr, nationality="Nigerian", gender=Gender.MALE
    )
    _employee(
        db_session, org, designation=mgr, nationality="British", gender=Gender.FEMALE
    )

    # org is derived from the service-auth context, mirroring require_service_auth.
    result = ncc_staff_headcount(auth={"organization_id": org}, db=db_session)

    assert isinstance(result, NccStaffHeadcountResponse)
    assert result.total_active == 2
    assert result.by_category["MANAGERIAL"]["nigerian"]["male"] == 1
    assert result.by_category["MANAGERIAL"]["expatriate"]["female"] == 1


def test_ncc_staff_headcount_endpoint_coerces_string_org(db_session):
    _ensure(
        db_session.bind, Person.__table__, Employee.__table__, Designation.__table__
    )
    org = uuid.uuid4()
    mgr = _designation(db_session, org, NccStaffCategory.MANAGERIAL)
    _employee(
        db_session, org, designation=mgr, nationality="Nigerian", gender=Gender.MALE
    )

    # A string org id (as some auth paths yield) is coerced, not rejected.
    result = ncc_staff_headcount(auth={"organization_id": str(org)}, db=db_session)

    assert result.total_active == 1


def test_ncc_financials_endpoint_returns_shape(monkeypatch, db_session):
    # Stub the three underlying statement services (their own tests cover them)
    # so this exercises only the sync wrapper's org-extraction + response build.
    monkeypatch.setattr(
        "app.services.finance.rpt.income_statement.income_statement_context",
        lambda db, org, start, end: {
            "total_revenue": "N100",
            "net_income": "N20",
            "net_income_raw": 20.0,
            "start_date_iso": start,
            "end_date_iso": end,
            "period_name": "FY2026",
            "income_statement_lines": [],
        },
    )
    monkeypatch.setattr(
        "app.services.finance.rpt.balance_sheet.balance_sheet_context",
        lambda db, org, as_of: {
            "as_of_date_iso": as_of,
            "total_assets": "N500",
            "total_liabilities": "N300",
            "total_equity": "N200",
            "is_balanced": True,
            "current_assets": [],
            "non_current_assets": [],
            "current_liabilities": [],
            "non_current_liabilities": [],
            "equity": [],
        },
    )
    monkeypatch.setattr(
        "app.services.finance.rpt.expense_summary.expense_summary_context",
        lambda db, org, start, end: {
            "total_expenses": "N80",
            "total_expenses_raw": 80.0,
            "expense_items": [],
        },
    )
    org = uuid.uuid4()

    result = ncc_financials(year=2026, auth={"organization_id": org}, db=db_session)

    assert isinstance(result, NccFinancialsResponse)
    assert result.period["year"] == 2026
    assert result.summary["total_revenue"] == "N100"
    assert result.summary["total_assets"] == "N500"
    assert result.note
