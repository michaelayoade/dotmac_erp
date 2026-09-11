from __future__ import annotations

from uuid import uuid4

from sqlalchemy.dialects import postgresql

from app.services.people.hr.info_change_service import InfoChangeService


class _ScalarRows:
    def all(self) -> list[object]:
        return []


class _CapturingSession:
    def __init__(self, batch: object) -> None:
        self.batch = batch
        self.scalar_statement: object | None = None
        self.scalars_statement: object | None = None

    def scalar(self, statement: object) -> object:
        self.scalar_statement = statement
        return self.batch

    def scalars(self, statement: object) -> _ScalarRows:
        self.scalars_statement = statement
        return _ScalarRows()


def _postgres_sql(statement: object) -> str:
    return str(
        statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )


def test_batch_lock_does_not_lock_nullable_eager_joins() -> None:
    batch = object()
    db = _CapturingSession(batch)
    organization_id = uuid4()
    batch_id = uuid4()

    result = InfoChangeService(db)._get_batch_for_update(organization_id, batch_id)

    assert result is batch
    batch_sql = _postgres_sql(db.scalar_statement)
    assert "FOR UPDATE" in batch_sql
    assert " JOIN " not in batch_sql
    assert "employee_info_change_batch.organization_id" in batch_sql

    request_sql = _postgres_sql(db.scalars_statement)
    assert "FOR UPDATE" in request_sql
    assert "employee_info_change_request.organization_id" in request_sql
    assert "employee_info_change_request.batch_id" in request_sql
    assert "ORDER BY hr.employee_info_change_request.request_id" in request_sql
