from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from app.services.people.leave.leave_service import (
    LeaveEligibilityError,
    LeaveService,
)


def _leave_type(code: str, name: str) -> SimpleNamespace:
    return SimpleNamespace(
        leave_type_code=code,
        leave_type_name=name,
        include_holidays=False,
        is_lwp=False,
    )


def _submit_leave(
    *,
    leave_type: SimpleNamespace,
    date_of_joining: date,
    today: date,
    total_days: Decimal,
):
    db = MagicMock()
    service = LeaveService(db)
    org_id = uuid4()
    employee_id = uuid4()
    leave_type_id = uuid4()
    application_id = uuid4()
    created_application = {}

    def add_side_effect(obj):
        created_application["application"] = obj

    def flush_side_effect():
        application = created_application.get("application")
        if application is not None and application.application_id is None:
            application.application_id = application_id

    db.add.side_effect = add_side_effect
    db.flush.side_effect = flush_side_effect
    db.scalar.return_value = None

    with (
        patch.object(service, "get_leave_type", return_value=leave_type),
        patch.object(service, "calculate_leave_days", return_value=total_days),
        patch.object(
            service,
            "_get_application_employee",
            return_value=SimpleNamespace(date_of_joining=date_of_joining),
        ),
        patch.object(service, "get_org_today", return_value=today),
        patch.object(service, "get_employee_balance", return_value=Decimal("10")),
        patch("app.services.people.discipline.DisciplineService") as discipline_service,
        patch.object(service, "_next_application_number", return_value="LVE-0001"),
        patch.object(service, "_notify_leave_submitted"),
        patch("app.services.people.leave.leave_service.fire_audit_event"),
    ):
        discipline_service.return_value.has_active_investigation.return_value = False

        return service.create_application(
            org_id,
            employee_id=employee_id,
            leave_type_id=leave_type_id,
            from_date=date(2026, 5, 4),
            to_date=date(2026, 5, 6),
            reason="Leave request",
        )


def test_under_one_year_staff_cannot_submit_annual_leave():
    with pytest.raises(LeaveEligibilityError, match="Annual leave"):
        _submit_leave(
            leave_type=_leave_type("ANNUAL", "Annual Leave"),
            date_of_joining=date(2025, 9, 1),
            today=date(2026, 8, 20),
            total_days=Decimal("2"),
        )


def test_under_one_year_staff_can_submit_sick_leave_up_to_two_days():
    application = _submit_leave(
        leave_type=_leave_type("SICK", "Sick Leave"),
        date_of_joining=date(2025, 9, 1),
        today=date(2026, 8, 20),
        total_days=Decimal("2"),
    )

    assert application.status.value == "SUBMITTED"
    assert application.total_leave_days == Decimal("2")


def test_under_one_year_staff_cannot_submit_sick_leave_over_two_days():
    with pytest.raises(LeaveEligibilityError, match="up to 2 days"):
        _submit_leave(
            leave_type=_leave_type("SICK", "Sick Leave"),
            date_of_joining=date(2025, 9, 1),
            today=date(2026, 8, 20),
            total_days=Decimal("2.5"),
        )


def test_staff_at_one_year_can_submit_annual_leave():
    application = _submit_leave(
        leave_type=_leave_type("ANNUAL", "Annual Leave"),
        date_of_joining=date(2025, 8, 20),
        today=date(2026, 8, 20),
        total_days=Decimal("3"),
    )

    assert application.status.value == "SUBMITTED"
    assert application.total_leave_days == Decimal("3")
