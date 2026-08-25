"""Employee-search coverage for leave management pages and reports."""

from unittest.mock import MagicMock
from uuid import uuid4

from sqlalchemy.dialects import postgresql

from app.services.common import PaginationParams
from app.services.people.leave.leave_service import LeaveService


def _postgres_sql(statement) -> str:
    return str(
        statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )


def test_employee_filter_resolves_text_with_tenant_scoped_identity_search() -> None:
    """Names are valid employee filters and never fall into UUID validation."""
    db = MagicMock()
    organization_id = uuid4()
    matching_employee_id = uuid4()
    db.scalar.return_value = None
    db.scalars.return_value.all.return_value = [matching_employee_id]

    employee_id, employee_ids = LeaveService(db).resolve_employee_filter(
        organization_id,
        "Ada Lovelace",
    )

    assert employee_id is None
    assert employee_ids == [matching_employee_id]
    sql = _postgres_sql(db.scalars.call_args.args[0])
    assert "hr.employee.organization_id" in sql
    assert "people.organization_id" in sql
    assert "hr.employee.employee_code ILIKE" in sql
    assert "people.first_name ILIKE" in sql
    assert "people.last_name ILIKE" in sql
    assert "people.display_name ILIKE" in sql
    assert "people.email ILIKE" in sql
    assert "Ada Lovelace" in sql


def test_employee_filter_returns_empty_match_list_for_unknown_text() -> None:
    """An unknown employee term produces an empty page instead of an error."""
    db = MagicMock()
    db.scalar.return_value = None
    db.scalars.return_value.all.return_value = []

    employee_id, employee_ids = LeaveService(db).resolve_employee_filter(
        uuid4(),
        "Nobody Here",
    )

    assert employee_id is None
    assert employee_ids == []


def test_employee_filter_validates_uuid_inside_the_current_tenant() -> None:
    """A UUID must belong to the current tenant before it can filter results."""
    db = MagicMock()
    organization_id = uuid4()
    requested_employee_id = uuid4()
    db.scalar.return_value = requested_employee_id

    employee_id, employee_ids = LeaveService(db).resolve_employee_filter(
        organization_id,
        str(requested_employee_id),
    )

    assert employee_id == requested_employee_id
    assert employee_ids is None
    sql = _postgres_sql(db.scalar.call_args.args[0])
    assert str(organization_id) in sql
    assert str(requested_employee_id) in sql


def test_leave_application_employee_matches_apply_to_rows_and_count() -> None:
    """Employee search constrains both page rows and the pagination total."""
    db = MagicMock()
    organization_id = uuid4()
    matching_employee_ids = [uuid4(), uuid4()]
    db.scalar.return_value = 0
    db.scalars.return_value.all.return_value = []

    result = LeaveService(db).list_applications(
        organization_id,
        employee_ids=matching_employee_ids,
        pagination=PaginationParams(offset=0, limit=20),
    )

    count_sql = _postgres_sql(db.scalar.call_args.args[0])
    rows_sql = _postgres_sql(db.scalars.call_args.args[0])
    for sql in (count_sql, rows_sql):
        assert "leave.leave_application.organization_id" in sql
        assert "leave.leave_application.employee_id IN" in sql
        assert str(matching_employee_ids[0]) in sql
        assert str(matching_employee_ids[1]) in sql
    assert result.items == []
    assert result.total == 0


def test_leave_application_empty_employee_matches_skip_database() -> None:
    """A no-match employee search returns an empty page without a broad query."""
    db = MagicMock()

    result = LeaveService(db).list_applications(
        uuid4(),
        employee_ids=[],
        pagination=PaginationParams(offset=20, limit=20),
    )

    assert result.items == []
    assert result.total == 0
    assert result.offset == 20
    db.scalar.assert_not_called()
    db.scalars.assert_not_called()


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
    sql = _postgres_sql(statement)

    assert "hr.employee.organization_id" in sql
    assert "people.organization_id" in sql
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
