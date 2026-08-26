"""Provider-neutral Sub NCC regulatory projection endpoints."""

from __future__ import annotations

import uuid
from datetime import date

import pytest
from fastapi import HTTPException

from app.api.sync.dotmac_sub import (
    get_sub_ncc_financials,
    get_sub_ncc_staff_headcount,
    require_sub_ncc_read_scope,
)
from app.models.people.hr.designation import Designation, NccStaffCategory
from app.models.people.hr.employee import Employee, EmployeeStatus, Gender
from app.models.person import Person
from app.schemas.sync.sub_operational import (
    SubNccFinancialsResponse,
    SubNccStaffHeadcountResponse,
)


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
    designation = Designation(
        designation_id=uuid.uuid4(),
        organization_id=org_id,
        designation_code=f"D-{uuid.uuid4().hex[:6]}",
        designation_name="Role",
        ncc_staff_category=category,
    )
    db.add(designation)
    db.flush()
    return designation


def _employee(db, org_id, *, designation, nationality, gender) -> Employee:
    person = Person(
        id=uuid.uuid4(),
        organization_id=org_id,
        first_name="A",
        last_name="B",
        email=f"{uuid.uuid4().hex}@example.com",
    )
    employee = Employee(
        employee_id=uuid.uuid4(),
        organization_id=org_id,
        person_id=person.id,
        employee_code=f"EMP-{uuid.uuid4().hex[:8]}",
        date_of_joining=date(2026, 1, 1),
        status=EmployeeStatus.ACTIVE,
        gender=gender,
        nationality=nationality,
        designation_id=designation.designation_id,
    )
    db.add_all([person, employee])
    db.flush()
    return employee


def test_ncc_scope_accepts_only_the_neutral_sub_authority() -> None:
    assert require_sub_ncc_read_scope({"scopes": ["sub:ncc:read"]})
    with pytest.raises(HTTPException) as error:
        require_sub_ncc_read_scope({"scopes": ["crm:ncc:read"]})
    assert error.value.status_code == 403


def test_sub_ncc_staff_headcount_uses_service_org(db_session):
    _ensure(
        db_session.bind, Person.__table__, Employee.__table__, Designation.__table__
    )
    org = uuid.uuid4()
    manager = _designation(db_session, org, NccStaffCategory.MANAGERIAL)
    _employee(
        db_session,
        org,
        designation=manager,
        nationality="Nigerian",
        gender=Gender.MALE,
    )
    _employee(
        db_session,
        org,
        designation=manager,
        nationality="British",
        gender=Gender.FEMALE,
    )

    result = get_sub_ncc_staff_headcount(
        auth={"organization_id": str(org)}, db=db_session
    )
    assert isinstance(result, SubNccStaffHeadcountResponse)
    assert result.total_active == 2
    assert result.by_category["MANAGERIAL"]["nigerian"]["male"] == 1
    assert result.by_category["MANAGERIAL"]["expatriate"]["female"] == 1


def test_sub_ncc_financials_returns_local_owner_shape(monkeypatch, db_session):
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
    result = get_sub_ncc_financials(
        year=2026,
        auth={"organization_id": str(uuid.uuid4())},
        db=db_session,
    )
    assert isinstance(result, SubNccFinancialsResponse)
    assert result.period["year"] == 2026
    assert result.summary["total_revenue"] == "N100"
    assert result.summary["total_assets"] == "N500"
    assert result.note
