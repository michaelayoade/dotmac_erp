"""Execute dashboard queries against seeded ERP records (SQLite unit fixture).

Real PostgreSQL/RLS and browser acceptance remain distinct release checks.
"""

from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import uuid4

from app.models.people.hr.employee import Employee, EmployeeStatus
from app.models.people.hr.department import Department
from app.models.people.perf.kpi import KPI, KPIStatus
from app.models.person import Person
from app.services.people.perf.kpi_dashboard_contract import DashboardConfig
from app.services.people.perf.kpi_dashboard_service import (
    DashboardScope,
    KPIDashboardService,
)


def _tables(engine):
    for table in (Department.__table__, Employee.__table__, KPI.__table__):
        for column in table.columns:
            if column.server_default is not None and "gen_random_uuid" in str(
                column.server_default.arg
            ):
                column.server_default = None
        table.create(engine, checkfirst=True)


def _employee(db, org, department, code):
    person = Person(
        id=uuid4(),
        organization_id=org,
        first_name=code,
        last_name="Employee",
        email=f"{uuid4().hex}@example.com",
    )
    employee = Employee(
        employee_id=uuid4(),
        organization_id=org,
        person_id=person.id,
        employee_code=code,
        status=EmployeeStatus.ACTIVE,
        department_id=department,
        date_of_joining=date(2026, 1, 1),
    )
    db.add_all([person, employee])
    db.flush()
    return employee


def _kpi(
    db,
    org,
    employee,
    *,
    actual="1",
    status=KPIStatus.ACTIVE,
    name="Count",
    target="20",
    lower=False,
):
    item = KPI(
        kpi_id=uuid4(),
        organization_id=org,
        employee_id=employee.employee_id,
        kpi_name=name,
        target_value=Decimal(target),
        actual_value=Decimal(actual) if actual is not None else None,
        lower_is_better=lower,
        status=status,
        period_start=date(2026, 9, 1),
        period_end=date(2026, 9, 10),
    )
    db.add(item)
    return item


def _scope(org, department):
    return DashboardScope(
        org,
        uuid4(),
        False,
        ({"id": str(department), "name": "Allowed", "active": True},),
        "Africa/Lagos",
    )


def test_counts_names_and_charts_are_scoped_and_not_limited_to_current_page(
    db_session, engine
):
    _tables(engine)
    org, foreign_org = uuid4(), uuid4()
    allowed, forbidden = uuid4(), uuid4()
    db_session.add_all(
        [
            Department(
                department_id=allowed,
                organization_id=org,
                department_code="ONE",
                department_name="Allowed",
            ),
            Department(
                department_id=forbidden,
                organization_id=org,
                department_code="TWO",
                department_name="Forbidden",
            ),
        ]
    )
    db_session.flush()
    owner = _employee(db_session, org, allowed, "ALLOWED")
    missing = _employee(db_session, org, allowed, "NO-KPI")
    other = _employee(db_session, org, forbidden, "FORBIDDEN")
    outsider = _employee(db_session, foreign_org, allowed, "FOREIGN")
    for number in range(30):
        _kpi(db_session, org, owner, name=f"Count {number}")
    _kpi(db_session, org, other)
    _kpi(db_session, foreign_org, outsider)
    # Even a wrongly linked foreign-organization KPI must not expose its owner.
    _kpi(db_session, org, outsider)
    db_session.flush()
    result = KPIDashboardService(db_session).dashboard(
        _scope(org, allowed),
        DashboardConfig(),
        now=datetime(2026, 9, 15, tzinfo=timezone.utc),
    )
    assert result["summary"]["tracked"] == 30
    assert len(result["kpis"]) == 25
    assert result["total_pages"] == 2
    assert (
        sum(sum(series["data"]) for series in result["department_chart"]["datasets"])
        == 30
    )
    assert result["employee_counts"] == {"total": 2, "needs_review": 2, "no_kpis": 1}
    assert {row["employee_id"] for row in result["employee_rows"]} == {
        owner.employee_id,
        missing.employee_id,
    }
    assert result["kpis"][0]["employee_name"] == "ALLOWED Employee"


def test_missing_filter_results_are_not_misreported_as_no_assigned_kpis(
    db_session, engine
):
    _tables(engine)
    org, department = uuid4(), uuid4()
    db_session.add(
        Department(
            department_id=department,
            organization_id=org,
            department_code="D",
            department_name="Allowed",
        )
    )
    employee = _employee(db_session, org, department, "ACHIEVER")
    _kpi(db_session, org, employee, actual="20", status=KPIStatus.ACHIEVED)
    db_session.flush()
    result = KPIDashboardService(db_session).dashboard(
        _scope(org, department),
        DashboardConfig(statuses=("AT_RISK",), attention="needs_attention"),
        now=datetime(2026, 9, 15, tzinfo=timezone.utc),
    )
    assert result["employee_counts"]["no_kpis"] == 0
    assert result["employee_counts"]["needs_review"] == 0
    assert result["employee_rows"] == []


def test_all_overdue_does_not_flag_employees_without_overdue_kpis(db_session, engine):
    _tables(engine)
    org, department = uuid4(), uuid4()
    db_session.add(
        Department(
            department_id=department,
            organization_id=org,
            department_code="D",
            department_name="Allowed",
        )
    )
    employee = _employee(db_session, org, department, "CLEAR")
    _kpi(db_session, org, employee, actual="20", status=KPIStatus.ACHIEVED)
    db_session.flush()
    result = KPIDashboardService(db_session).dashboard(
        _scope(org, department),
        DashboardConfig(cohort="overdue", attention="needs_attention"),
        now=datetime(2026, 9, 15, tzinfo=timezone.utc),
    )
    assert result["employee_counts"]["needs_review"] == 0
    assert result["employee_rows"] == []


def test_lower_direction_and_missing_measurements_have_different_review_reasons(
    db_session, engine
):
    _tables(engine)
    org, department = uuid4(), uuid4()
    db_session.add(
        Department(
            department_id=department,
            organization_id=org,
            department_code="D",
            department_name="Allowed",
        )
    )
    employee = _employee(db_session, org, department, "REVIEW")
    _kpi(db_session, org, employee, actual="4", target="2", lower=True)
    _kpi(db_session, org, employee, actual=None, target="2", lower=True)
    _kpi(db_session, org, employee, actual="1", target="2", lower=True)
    db_session.flush()
    result = KPIDashboardService(db_session).dashboard(
        _scope(org, department),
        DashboardConfig(),
        now=datetime(2026, 9, 15, tzinfo=timezone.utc),
    )
    assert result["summary"]["below_target"] == 1
    assert result["summary"]["missing"] == 1
    assert result["summary"]["coverage"] == Decimal("66.7")
