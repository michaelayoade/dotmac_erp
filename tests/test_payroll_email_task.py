from contextlib import nullcontext
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

from app.models.people.hr.employee import EmployeeStatus
from app.models.person import PersonStatus
from app.tasks import payroll as payroll_tasks


def test_send_payslip_email_skips_inactive_employee(monkeypatch):
    person = SimpleNamespace(
        email="inactive.employee@example.com",
        is_active=True,
        status=PersonStatus.active,
    )
    employee = SimpleNamespace(
        employee_id=uuid4(),
        status=EmployeeStatus.TERMINATED,
        person=person,
        work_email=person.email,
        organization=None,
    )
    slip = SimpleNamespace(
        slip_id=uuid4(),
        employee=employee,
        start_date=date(2026, 1, 1),
        slip_number="SLIP-TEST-001",
        currency_code="NGN",
        net_pay=Decimal("1000.00"),
    )

    mock_db = MagicMock()
    mock_db.scalar.return_value = slip

    monkeypatch.setattr(
        payroll_tasks,
        "SessionLocal",
        lambda: nullcontext(mock_db),
    )

    send_calls: list[str] = []

    def _fake_send_email(*args, **kwargs):
        send_calls.append(kwargs.get("to_email") or args[1])
        return True

    monkeypatch.setattr("app.services.email.send_email", _fake_send_email)

    result = payroll_tasks.send_payslip_email(str(slip.slip_id), str(uuid4()))

    assert result["success"] is False
    assert "inactive" in str(result["error"]).lower()
    assert send_calls == []
