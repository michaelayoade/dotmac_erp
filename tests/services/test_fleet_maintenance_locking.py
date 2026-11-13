from __future__ import annotations

from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql

from app.models.fleet.maintenance import MaintenanceRecord
from app.services.common import NotFoundError
from app.services.fleet.maintenance_service import MaintenanceService


def _postgres_sql(statement: object) -> str:
    return str(
        statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )


def test_maintenance_lock_avoids_nullable_eager_supplier_join() -> None:
    organization_id = uuid4()
    maintenance_id = uuid4()
    record = object()
    db = MagicMock()
    db.scalar.return_value = maintenance_id
    db.get.return_value = record

    result = MaintenanceService(db, organization_id).get_for_update(maintenance_id)

    assert result is record
    statement = db.scalar.call_args.args[0]
    sql = _postgres_sql(statement)
    assert "SELECT fleet.maintenance_record.maintenance_id" in sql
    assert "FOR UPDATE" in sql
    assert " JOIN " not in sql
    assert "fleet.maintenance_record.organization_id" in sql
    db.get.assert_called_once_with(MaintenanceRecord, maintenance_id)


def test_maintenance_lock_raises_when_scoped_row_is_missing() -> None:
    maintenance_id = uuid4()
    db = MagicMock()
    db.scalar.return_value = None

    with pytest.raises(NotFoundError, match=str(maintenance_id)):
        MaintenanceService(db, uuid4()).get_for_update(maintenance_id)

    db.get.assert_not_called()
