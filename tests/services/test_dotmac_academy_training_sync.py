"""Record academy course completions against employee HR records."""

from __future__ import annotations

import uuid
from datetime import date

import pytest
from sqlalchemy import select

from app.models.people.hr.employee import Employee, EmployeeStatus
from app.models.people.hr.employee_extended import EmployeeCertification
from app.models.people.training.learning_assessment import (
    TrainingCourse,
    TrainingCourseAssignment,
    TrainingCourseProgress,
    TrainingCourseStatus,
)
from app.models.person import Person
from app.config import settings
from app.services.dotmac_academy.events import dispatch
from app.services.dotmac_academy.training_sync import record_course_completion

ORG = uuid.UUID("00000000-0000-0000-0000-000000000001")


@pytest.fixture(autouse=True)
def _tables(engine):
    """Create the HR tables this module needs (ERP tests build tables per-module).

    Strips gen_random_uuid server defaults SQLite can't execute; the models also
    carry a Python-side uuid default so PKs still populate.
    """
    for table in (
        Person.__table__,
        Employee.__table__,
        EmployeeCertification.__table__,
        TrainingCourse.__table__,
        TrainingCourseAssignment.__table__,
        TrainingCourseProgress.__table__,
    ):
        for column in table.columns:
            default = column.server_default
            if default is None:
                continue
            default_text = str(getattr(default, "arg", default)).lower()
            if "gen_random_uuid" in default_text or "uuid_generate" in default_text:
                column.server_default = None
        table.create(engine, checkfirst=True)


def _employee(db_session, email):
    person = Person(
        id=uuid.uuid4(),
        organization_id=ORG,
        first_name="Staff",
        last_name="Member",
        email=email,
    )
    db_session.add(person)
    db_session.flush()
    emp = Employee(
        employee_id=uuid.uuid4(),
        organization_id=ORG,
        person_id=person.id,
        employee_code=f"E{uuid.uuid4().hex[:6]}",
        date_of_joining=date.today(),
        status=EmployeeStatus.ACTIVE,
    )
    db_session.add(emp)
    db_session.flush()
    return emp


def _payload(email, **over):
    payload = {
        "event": "course_completed",
        "email": email,
        "course_title": "Fiber Splicing",
        "passed": True,
        "completed_on": "2026-07-11",
        "certificate_ref": "cert-abc",
    }
    payload.update(over)
    return payload


def test_records_certification_for_matching_employee(db_session):
    emp = _employee(db_session, "tech1@dotmac.ng")
    result = record_course_completion(
        db_session, organization_id=ORG, payload=_payload("tech1@dotmac.ng")
    )
    assert result["status"] == "recorded"
    cert = (
        db_session.execute(
            select(EmployeeCertification).where(
                EmployeeCertification.employee_id == emp.employee_id
            )
        )
        .scalars()
        .first()
    )
    assert cert is not None
    assert cert.certification_name == "Fiber Splicing"
    assert cert.issuing_authority == settings.dotmac_academy_issuing_authority
    assert cert.credential_id == "cert-abc"
    assert cert.is_verified is True


def test_idempotent_on_certificate_ref(db_session):
    emp = _employee(db_session, "tech2@dotmac.ng")
    record_course_completion(
        db_session, organization_id=ORG, payload=_payload("tech2@dotmac.ng")
    )
    result = record_course_completion(
        db_session,
        organization_id=ORG,
        payload=_payload("tech2@dotmac.ng", course_title="Fiber Splicing v2"),
    )
    assert result["status"] == "updated"
    certs = (
        db_session.execute(
            select(EmployeeCertification).where(
                EmployeeCertification.employee_id == emp.employee_id
            )
        )
        .scalars()
        .all()
    )
    assert len(certs) == 1
    assert certs[0].certification_name == "Fiber Splicing v2"


def test_case_insensitive_email_match(db_session):
    emp = _employee(db_session, "tech4@dotmac.ng")
    result = record_course_completion(
        db_session, organization_id=ORG, payload=_payload("Tech4@Dotmac.NG")
    )
    assert result["status"] == "recorded"
    cert = (
        db_session.execute(
            select(EmployeeCertification).where(
                EmployeeCertification.employee_id == emp.employee_id
            )
        )
        .scalars()
        .first()
    )
    assert cert is not None


def test_ignored_when_no_matching_employee(db_session):
    result = record_course_completion(
        db_session, organization_id=ORG, payload=_payload("nobody@nowhere.ex")
    )
    assert result["status"] == "ignored"
    assert "no matching employee" in result["reason"]


def test_ignored_when_not_passed(db_session):
    _employee(db_session, "tech3@dotmac.ng")
    result = record_course_completion(
        db_session,
        organization_id=ORG,
        payload=_payload("tech3@dotmac.ng", passed=False),
    )
    assert result["status"] == "ignored"
    assert result["reason"] == "not passed"


# ---------------------------------------------------------------------------
# Versioned dispatch
# ---------------------------------------------------------------------------


def test_dispatch_routes_a_versioned_course_completed(db_session):
    emp = _employee(db_session, "disp1@dotmac.ng")
    result = dispatch(
        db_session,
        organization_id=ORG,
        event_type="course_completed",
        payload=_payload("disp1@dotmac.ng", version=1),
    )
    assert result["status"] == "recorded"
    assert emp.employee_id is not None


def test_dispatch_defaults_to_v1_when_version_absent(db_session):
    """The academy sent no version before 2026-08; those payloads still route."""
    _employee(db_session, "disp2@dotmac.ng")
    payload = _payload("disp2@dotmac.ng")
    payload.pop("version", None)
    result = dispatch(
        db_session,
        organization_id=ORG,
        event_type="course_completed",
        payload=payload,
    )
    assert result["status"] == "recorded"


def test_dispatch_reports_an_unknown_version_rather_than_absorbing_it(db_session):
    _employee(db_session, "disp3@dotmac.ng")
    result = dispatch(
        db_session,
        organization_id=ORG,
        event_type="course_completed",
        payload=_payload("disp3@dotmac.ng", version=99),
    )
    assert result["status"] == "unsupported"
    assert "version 99" in result["reason"]


def test_dispatch_reports_an_unknown_event(db_session):
    result = dispatch(
        db_session,
        organization_id=ORG,
        event_type="learner_enrolled",
        payload={"version": 1},
    )
    assert result["status"] == "unsupported"
    assert "learner_enrolled" in result["reason"]


def test_dispatch_reports_an_unreadable_version(db_session):
    result = dispatch(
        db_session,
        organization_id=ORG,
        event_type="course_completed",
        payload={"version": "not-a-number"},
    )
    assert result["status"] == "unsupported"
    assert "unreadable version" in result["reason"]


def _academy_course(db_session, reference="fiber-splicing"):
    course = TrainingCourse(
        organization_id=ORG,
        title="Fiber Splicing",
        academy_course_ref=reference,
        status=TrainingCourseStatus.PUBLISHED,
    )
    db_session.add(course)
    db_session.flush()
    return course


def _v2_payload(employee_ref, **over):
    payload = {
        "version": 2,
        "event": "training_progress_updated",
        "employee_ref": employee_ref,
        "academy_enrollment_ref": str(uuid.uuid4()),
        "academy_course_ref": "fiber-splicing",
        "course_title": "Fiber Splicing",
        "progress_pct": 65,
        "occurred_at": "2026-08-15T10:30:00+00:00",
    }
    payload.update(over)
    return payload


def test_v2_progress_projects_assignment_and_progress(db_session):
    employee = _employee(db_session, "progress@dotmac.ng")
    course = _academy_course(db_session)
    result = dispatch(
        db_session,
        organization_id=ORG,
        event_type="training_progress_updated",
        payload=_v2_payload(employee.employee_code),
    )
    assert result["status"] == "recorded"
    assignment = db_session.scalar(
        select(TrainingCourseAssignment).where(
            TrainingCourseAssignment.organization_id == ORG,
            TrainingCourseAssignment.course_id == course.id,
            TrainingCourseAssignment.employee_id == employee.employee_id,
        )
    )
    progress = db_session.scalar(
        select(TrainingCourseProgress).where(
            TrainingCourseProgress.organization_id == ORG,
            TrainingCourseProgress.course_id == course.id,
            TrainingCourseProgress.employee_id == employee.employee_id,
        )
    )
    assert assignment is not None
    assert assignment.assignment_source == "academy"
    assert progress is not None
    assert progress.completion_percentage == 65
    assert progress.status.value == "in_progress"


def test_v2_completion_also_records_certification(db_session):
    employee = _employee(db_session, "complete@dotmac.ng")
    _academy_course(db_session, "fiber-complete")
    payload = _v2_payload(
        employee.employee_code,
        event="course_completed",
        academy_course_ref="fiber-complete",
        progress_pct=100,
        certificate_ref="academy-cert-1",
        completed_on="2026-08-15",
    )
    result = dispatch(
        db_session,
        organization_id=ORG,
        event_type="course_completed",
        payload=payload,
    )
    assert result["status"] == "recorded"
    cert = db_session.scalar(
        select(EmployeeCertification).where(
            EmployeeCertification.organization_id == ORG,
            EmployeeCertification.employee_id == employee.employee_id,
            EmployeeCertification.credential_id == "academy-cert-1",
        )
    )
    assert cert is not None


def test_v2_rejects_unmapped_employee_and_course(db_session):
    result = dispatch(
        db_session,
        organization_id=ORG,
        event_type="training_progress_updated",
        payload=_v2_payload("MISSING"),
    )
    assert result == {"status": "ignored", "reason": "no matching employee_ref"}

    employee = _employee(db_session, "unmapped@dotmac.ng")
    result = dispatch(
        db_session,
        organization_id=ORG,
        event_type="training_progress_updated",
        payload=_v2_payload(employee.employee_code, academy_course_ref="missing"),
    )
    assert result == {
        "status": "ignored",
        "reason": "no course mapped to academy_course_ref",
    }


def test_v2_older_progress_cannot_overwrite_newer_state(db_session):
    employee = _employee(db_session, "ordered@dotmac.ng")
    course = _academy_course(db_session, "ordered-course")
    newer = _v2_payload(
        employee.employee_code,
        academy_course_ref="ordered-course",
        progress_pct=80,
        occurred_at="2026-08-15T11:00:00+00:00",
    )
    older = dict(newer, progress_pct=20, occurred_at="2026-08-15T10:00:00+00:00")
    assert dispatch(
        db_session,
        organization_id=ORG,
        event_type="training_progress_updated",
        payload=newer,
    )["status"] == "recorded"
    assert dispatch(
        db_session,
        organization_id=ORG,
        event_type="training_progress_updated",
        payload=older,
    )["status"] == "duplicate"
    progress = db_session.scalar(
        select(TrainingCourseProgress).where(
            TrainingCourseProgress.organization_id == ORG,
            TrainingCourseProgress.course_id == course.id,
            TrainingCourseProgress.employee_id == employee.employee_id,
        )
    )
    assert progress.completion_percentage == 80
