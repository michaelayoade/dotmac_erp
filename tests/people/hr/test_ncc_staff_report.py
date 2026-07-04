from __future__ import annotations

import uuid
from datetime import date

from app.models.people.hr.designation import Designation, NccStaffCategory
from app.models.people.hr.employee import Employee, EmployeeStatus, Gender
from app.models.person import Person
from app.services.people.hr.ncc_staff_report import NccStaffReportService


def _ensure(engine, *tables) -> None:
    for table in tables:
        for col in table.columns:
            default = col.server_default
            if default is not None and "gen_random_uuid" in str(getattr(default, "arg", default)).lower():
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


def _employee(db, org_id, *, designation, nationality, gender, status=EmployeeStatus.ACTIVE) -> Employee:
    person = Person(
        id=uuid.uuid4(), organization_id=org_id, first_name="A", last_name="B",
        email=f"{uuid.uuid4().hex}@example.com",
    )
    emp = Employee(
        employee_id=uuid.uuid4(),
        organization_id=org_id,
        person_id=person.id,
        employee_code=f"EMP-{uuid.uuid4().hex[:8]}",
        date_of_joining=date(2026, 1, 1),
        status=status,
        gender=gender,
        nationality=nationality,
        designation_id=designation.designation_id if designation else None,
    )
    db.add_all([person, emp])
    db.flush()
    return emp


def test_staff_headcount_matrix(db_session):
    _ensure(db_session.bind, Person.__table__, Employee.__table__, Designation.__table__)
    org = uuid.uuid4()

    mgr = _designation(db_session, org, NccStaffCategory.MANAGERIAL)
    snr = _designation(db_session, org, NccStaffCategory.SENIOR_TECHNICAL)

    _employee(db_session, org, designation=mgr, nationality="Nigerian", gender=Gender.MALE)
    _employee(db_session, org, designation=mgr, nationality="Nigerian", gender=Gender.FEMALE)
    _employee(db_session, org, designation=snr, nationality="British", gender=Gender.MALE)  # expatriate
    _employee(db_session, org, designation=None, nationality="", gender=Gender.FEMALE)  # OTHER / unknown nat
    # excluded — not active
    _employee(db_session, org, designation=mgr, nationality="Nigerian", gender=Gender.MALE, status=EmployeeStatus.RESIGNED)

    report = NccStaffReportService(db_session).build(org)

    assert report["total_active"] == 4
    m = report["by_category"]
    assert m["MANAGERIAL"]["nigerian"] == {"male": 1, "female": 1, "other": 0}
    assert m["SENIOR_TECHNICAL"]["expatriate"]["male"] == 1
    assert m["OTHER"]["unknown"]["female"] == 1
    # untouched buckets stay zero
    assert m["JUNIOR_TECHNICAL"]["nigerian"] == {"male": 0, "female": 0, "other": 0}


def test_staff_headcount_scoped_to_org(db_session):
    _ensure(db_session.bind, Person.__table__, Employee.__table__, Designation.__table__)
    org_a, org_b = uuid.uuid4(), uuid.uuid4()
    da = _designation(db_session, org_a, NccStaffCategory.MANAGERIAL)
    db_ = _designation(db_session, org_b, NccStaffCategory.MANAGERIAL)
    _employee(db_session, org_a, designation=da, nationality="Nigerian", gender=Gender.MALE)
    _employee(db_session, org_b, designation=db_, nationality="Nigerian", gender=Gender.MALE)

    assert NccStaffReportService(db_session).build(org_a)["total_active"] == 1
