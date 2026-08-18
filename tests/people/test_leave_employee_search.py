"""Employee-search coverage for leave management reports."""

from unittest.mock import MagicMock
from uuid import uuid4

from sqlalchemy.dialects import postgresql

from app.services.people.leave.leave_service import LeaveService


def test_leave_balance_report_filters_employee_fields() -> None:
    """Balance report search matches employee identity fields within the org."""
    db = MagicMock()
    db.execute.return_value.all.return_value = []
    organization_id = uuid4()

    report = LeaveService(db).get_leave_balance_report(
        organization_id,
        year=2026,
        employee_search="Ada Lovelace",
    )

    statement = db.execute.call_args.args[0]
    sql = str(
        statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )

    assert "hr.employee.organization_id" in sql
    assert "hr.employee.employee_code ILIKE" in sql
    assert "people.first_name ILIKE" in sql
    assert "people.last_name ILIKE" in sql
    assert "people.display_name ILIKE" in sql
    assert "people.email ILIKE" in sql
    assert "Ada Lovelace" in sql
    assert report["employees"] == []
    assert report["total_employees"] == 0


def test_leave_balance_report_ignores_blank_employee_search() -> None:
    """Whitespace-only search leaves the report query unfiltered."""
    db = MagicMock()
    db.execute.return_value.all.return_value = []

    LeaveService(db).get_leave_balance_report(
        uuid4(),
        year=2026,
        employee_search="   ",
    )

    statement = db.execute.call_args.args[0]
    sql = str(statement.compile(dialect=postgresql.dialect()))
    assert "ILIKE" not in sql
