"""
Tests for the LedgerBulkService in app/services/finance/gl/bulk.py.

The posted ledger is append-only and immutable, so this service only
supports CSV export (no delete / status updates).
"""

import uuid
from datetime import date
from decimal import Decimal

import pytest

from app.services.finance.gl.bulk import (
    LedgerBulkService,
    get_ledger_bulk_service,
)


class MockLedgerLine:
    """Mock PostedLedgerLine entity for testing."""

    def __init__(
        self,
        ledger_line_id: uuid.UUID | None = None,
        organization_id: uuid.UUID | None = None,
        account_code: str = "1000",
        description: str | None = "Opening balance",
        journal_reference: str | None = "JE-2026-0001",
        posting_date: date | None = None,
        entry_date: date | None = None,
        debit_amount: Decimal = Decimal("0"),
        credit_amount: Decimal = Decimal("0"),
        original_currency_code: str | None = "NGN",
        source_module: str | None = "GL",
    ):
        self.ledger_line_id = ledger_line_id or uuid.uuid4()
        self.organization_id = organization_id or uuid.UUID(
            "00000000-0000-0000-0000-000000000001"
        )
        self.account_code = account_code
        self.description = description
        self.journal_reference = journal_reference
        self.posting_date = posting_date or date(2026, 2, 7)
        self.entry_date = entry_date or date(2026, 2, 7)
        self.debit_amount = debit_amount
        self.credit_amount = credit_amount
        self.original_currency_code = original_currency_code
        self.source_module = source_module


@pytest.fixture
def mock_ledger_line(organization_id):
    """Create a mock posted ledger line."""
    return MockLedgerLine(
        organization_id=organization_id,
        account_code="4100",
        description="Consulting revenue",
        journal_reference="JE-2026-0042",
        posting_date=date(2026, 2, 7),
        entry_date=date(2026, 2, 6),
        debit_amount=Decimal("0"),
        credit_amount=Decimal("45000.00"),
    )


class TestCanDelete:
    """The immutable ledger must never permit deletes."""

    def test_cannot_delete(self, mock_db, mock_ledger_line, organization_id):
        service = LedgerBulkService(mock_db, organization_id)
        can_delete, reason = service.can_delete(mock_ledger_line)

        assert can_delete is False
        assert "immutable" in reason.lower()


class TestGetExportValue:
    """Tests for the _get_export_value method."""

    def test_dates_rendered_iso(self, mock_db, mock_ledger_line, organization_id):
        service = LedgerBulkService(mock_db, organization_id)

        assert (
            service._get_export_value(mock_ledger_line, "posting_date") == "2026-02-07"
        )
        assert service._get_export_value(mock_ledger_line, "entry_date") == "2026-02-06"

    def test_none_date_is_blank(self, mock_db, organization_id):
        line = MockLedgerLine()
        line.posting_date = None
        service = LedgerBulkService(mock_db, organization_id)

        assert service._get_export_value(line, "posting_date") == ""

    def test_simple_field_delegates(self, mock_db, mock_ledger_line, organization_id):
        service = LedgerBulkService(mock_db, organization_id)

        assert service._get_export_value(mock_ledger_line, "account_code") == "4100"
        assert (
            service._get_export_value(mock_ledger_line, "description")
            == "Consulting revenue"
        )


class TestGetExportFilename:
    """Tests for the _get_export_filename method."""

    def test_filename_prefix(self, mock_db, organization_id):
        service = LedgerBulkService(mock_db, organization_id)
        filename = service._get_export_filename()

        assert filename.startswith("ledger_export_")
        assert filename.endswith(".csv")


class TestExportAll:
    """Tests for export_all producing CSV output."""

    @pytest.mark.asyncio
    async def test_export_headers_and_data(
        self, mock_db, mock_ledger_line, organization_id
    ):
        mock_db.scalars.return_value.all.return_value = [mock_ledger_line]

        service = LedgerBulkService(mock_db, organization_id)
        response = await service.export_all()

        content = (
            response.body.decode()
            if isinstance(response.body, bytes)
            else response.body
        )

        header_row = content.split("\n")[0]
        assert "Posting Date" in header_row
        assert "Account Code" in header_row
        assert "Debit" in header_row
        assert "Credit" in header_row

        # Data row reflects the line.
        assert "4100" in content
        assert "Consulting revenue" in content
        assert "2026-02-07" in content

    @pytest.mark.asyncio
    async def test_export_empty(self, mock_db, organization_id):
        mock_db.scalars.return_value.all.return_value = []

        service = LedgerBulkService(mock_db, organization_id)
        response = await service.export_all()

        content = (
            response.body.decode()
            if isinstance(response.body, bytes)
            else response.body
        )

        # Header row is still present even with no data.
        assert "Posting Date" in content.split("\n")[0]


class TestCountAll:
    """Tests for count_all (drives the inline-vs-queue threshold decision)."""

    def test_count_all_returns_scalar(self, mock_db, organization_id):
        mock_db.scalar.return_value = 42

        service = LedgerBulkService(mock_db, organization_id)
        count = service.count_all(start_date="2025-01-01", end_date="2025-12-31")

        assert count == 42
        # A COUNT query was issued (not a full row fetch).
        assert mock_db.scalar.called

    def test_count_all_none_coerced_to_zero(self, mock_db, organization_id):
        mock_db.scalar.return_value = None

        service = LedgerBulkService(mock_db, organization_id)

        assert service.count_all() == 0


class TestFactoryFunction:
    """Tests for the get_ledger_bulk_service factory function."""

    def test_factory_creates_service(self, mock_db, organization_id, user_id):
        service = get_ledger_bulk_service(mock_db, organization_id, user_id)

        assert isinstance(service, LedgerBulkService)
        assert service.organization_id == organization_id
        assert service.user_id == user_id
