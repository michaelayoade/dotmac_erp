from __future__ import annotations

import uuid
from datetime import date

from app.models.people.hr.designation import Designation, NccStaffCategory
from app.models.people.hr.employee import Employee, EmployeeStatus
from app.models.person import Person


def _ensure(engine, *tables) -> None:
    # Strip Postgres-only gen_random_uuid server defaults before creating on SQLite.
    for table in tables:
        for col in table.columns:
            default = col.server_default
            if default is None:
                continue
            if "gen_random_uuid" in str(getattr(default, "arg", default)).lower():
                col.server_default = None
        table.create(engine, checkfirst=True)


def test_employee_nationality_and_designation_ncc_category(db_session):
    _ensure(db_session.bind, Person.__table__, Employee.__table__, Designation.__table__)
    org_id = uuid.uuid4()

    person = Person(
        id=uuid.uuid4(),
        organization_id=org_id,
        first_name="Ada",
        last_name="Okafor",
        email=f"{uuid.uuid4().hex}@example.com",
    )
    employee = Employee(
        employee_id=uuid.uuid4(),
        organization_id=org_id,
        person_id=person.id,
        employee_code=f"EMP-{uuid.uuid4().hex[:8]}",
        date_of_joining=date(2026, 1, 1),
        status=EmployeeStatus.DRAFT,
        nationality="Nigerian",
    )
    designation = Designation(
        designation_id=uuid.uuid4(),
        organization_id=org_id,
        designation_code=f"D-{uuid.uuid4().hex[:6]}",
        designation_name="Network Engineer",
        ncc_staff_category=NccStaffCategory.SENIOR_TECHNICAL,
    )
    db_session.add_all([person, employee, designation])
    db_session.flush()
    db_session.expire_all()

    emp = db_session.get(Employee, employee.employee_id)
    des = db_session.get(Designation, designation.designation_id)
    assert emp.nationality == "Nigerian"
    assert des.ncc_staff_category is NccStaffCategory.SENIOR_TECHNICAL


def test_new_fields_default_to_null(db_session):
    _ensure(db_session.bind, Person.__table__, Employee.__table__, Designation.__table__)
    org_id = uuid.uuid4()
    person = Person(id=uuid.uuid4(), organization_id=org_id, first_name="B", last_name="C", email=f"{uuid.uuid4().hex}@example.com")
    employee = Employee(
        employee_id=uuid.uuid4(),
        organization_id=org_id,
        person_id=person.id,
        employee_code=f"EMP-{uuid.uuid4().hex[:8]}",
        date_of_joining=date(2026, 1, 1),
        status=EmployeeStatus.DRAFT,
    )
    designation = Designation(
        designation_id=uuid.uuid4(),
        organization_id=org_id,
        designation_code=f"D-{uuid.uuid4().hex[:6]}",
        designation_name="Unset",
    )
    db_session.add_all([person, employee, designation])
    db_session.flush()
    assert employee.nationality is None
    assert designation.ncc_staff_category is None
