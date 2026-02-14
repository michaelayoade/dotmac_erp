from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

from app.models.finance.banking.bank_statement import BankStatementStatus
from app.services.finance.banking.bulk import (
    StatementBulkService,
    get_statement_bulk_service,
)


def test_can_delete_blocks_reconciled_and_closed():
    service = StatementBulkService(MagicMock(), uuid4())

    reconciled = SimpleNamespace(
        status=BankStatementStatus.reconciled,
        statement_number="ST-1",
    )
    closed = SimpleNamespace(
        status=BankStatementStatus.closed,
        statement_number="ST-2",
    )
    imported = SimpleNamespace(
        status=BankStatementStatus.imported,
        statement_number="ST-3",
    )

    can_del, reason = service.can_delete(reconciled)
    assert can_del is False
    assert "Cannot delete 'ST-1'" in reason

    can_del, reason = service.can_delete(closed)
    assert can_del is False
    assert "Cannot delete 'ST-2'" in reason

    can_del, reason = service.can_delete(imported)
    assert can_del is True
    assert reason == ""


def test_get_export_value_formats_status_and_defaults():
    service = StatementBulkService(MagicMock(), uuid4())

    with_status = SimpleNamespace(status=BankStatementStatus.processing)
    no_status = SimpleNamespace(status=None, statement_number="S1")

    assert service._get_export_value(with_status, "status") == "processing"
    assert service._get_export_value(no_status, "status") == ""
    assert service._get_export_value(no_status, "statement_number") == "S1"


def test_export_filename_has_expected_prefix_and_suffix():
    service = StatementBulkService(MagicMock(), uuid4())
    name = service._get_export_filename()

    assert name.startswith("bank_statements_export_")
    assert name.endswith(".csv")


def test_get_statement_bulk_service_factory():
    db = MagicMock()
    org_id = uuid4()
    user_id = uuid4()

    service = get_statement_bulk_service(db, org_id, user_id)

    assert isinstance(service, StatementBulkService)
    assert service.db is db
    assert service.organization_id == org_id
    assert service.user_id == user_id
