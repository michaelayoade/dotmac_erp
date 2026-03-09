"""
Tests for app/services/ifrs/ap/bulk.py

Tests for SupplierBulkService that handles bulk operations on supplier entities.
"""

import uuid
from unittest.mock import MagicMock, patch

import pytest

from tests.ifrs.bulk.conftest import MockSupplier, MockSupplierType

# ============ TestCanDelete ============


class TestCanDelete:
    """Tests for the can_delete method."""

    def test_can_delete_no_invoices(self, mock_db, mock_supplier, organization_id):
        """Supplier with no invoices can be deleted."""
        mock_db.scalar.return_value = 0

        with patch("app.services.finance.ap.bulk.Supplier", MagicMock()):
            from app.services.finance.ap.bulk import SupplierBulkService

            service = SupplierBulkService(mock_db, organization_id)
            can_delete, reason = service.can_delete(mock_supplier)

            assert can_delete is True
            assert reason == ""

    def test_cannot_delete_with_invoices(self, mock_db, mock_supplier, organization_id):
        """Supplier with invoices cannot be deleted."""
        mock_db.scalar.return_value = 5

        with patch("app.services.finance.ap.bulk.Supplier", MagicMock()):
            from app.services.finance.ap.bulk import SupplierBulkService

            service = SupplierBulkService(mock_db, organization_id)
            can_delete, reason = service.can_delete(mock_supplier)

            assert can_delete is False
            assert "5 invoice(s)" in reason

    def test_returns_invoice_count(self, mock_db, organization_id):
        """Error message should include the invoice count."""
        supplier = MockSupplier(legal_name="ABC Corp")
        mock_db.scalar.return_value = 10

        with patch("app.services.finance.ap.bulk.Supplier", MagicMock()):
            from app.services.finance.ap.bulk import SupplierBulkService

            service = SupplierBulkService(mock_db, organization_id)
            can_delete, reason = service.can_delete(supplier)

            assert "10 invoice(s)" in reason

    def test_returns_supplier_name_in_message(self, mock_db, organization_id):
        """Error message should include the supplier name."""
        supplier = MockSupplier(legal_name="XYZ Suppliers Ltd")
        mock_db.scalar.return_value = 3

        with patch("app.services.finance.ap.bulk.Supplier", MagicMock()):
            from app.services.finance.ap.bulk import SupplierBulkService

            service = SupplierBulkService(mock_db, organization_id)
            can_delete, reason = service.can_delete(supplier)

            assert "XYZ Suppliers Ltd" in reason

    def test_returns_tuple_format(self, mock_db, mock_supplier, organization_id):
        """Method should return a tuple of (bool, str)."""
        mock_db.scalar.return_value = 0

        with patch("app.services.finance.ap.bulk.Supplier", MagicMock()):
            from app.services.finance.ap.bulk import SupplierBulkService

            service = SupplierBulkService(mock_db, organization_id)
            result = service.can_delete(mock_supplier)

            assert isinstance(result, tuple)
            assert len(result) == 2
            assert isinstance(result[0], bool)
            assert isinstance(result[1], str)


# ============ TestGetExportValue ============


class TestGetExportValue:
    """Tests for the _get_export_value method."""

    def test_export_supplier_type_vendor(self, mock_db, organization_id):
        """Should export supplier_type enum value as string."""
        supplier = MockSupplier(supplier_type=MockSupplierType.vendor)

        with patch("app.services.finance.ap.bulk.Supplier", MagicMock()):
            from app.services.finance.ap.bulk import SupplierBulkService

            service = SupplierBulkService(mock_db, organization_id)
            value = service._get_export_value(supplier, "supplier_type")

            assert value == "vendor"

    def test_export_supplier_type_contractor(self, mock_db, organization_id):
        """Should export contractor type correctly."""
        supplier = MockSupplier(supplier_type=MockSupplierType.contractor)

        with patch("app.services.finance.ap.bulk.Supplier", MagicMock()):
            from app.services.finance.ap.bulk import SupplierBulkService

            service = SupplierBulkService(mock_db, organization_id)
            value = service._get_export_value(supplier, "supplier_type")

            assert value == "contractor"

    def test_export_supplier_type_none(self, mock_db, organization_id):
        """Should handle None supplier_type."""
        supplier = MockSupplier(supplier_type=None)

        with patch("app.services.finance.ap.bulk.Supplier", MagicMock()):
            from app.services.finance.ap.bulk import SupplierBulkService

            service = SupplierBulkService(mock_db, organization_id)
            value = service._get_export_value(supplier, "supplier_type")

            assert value == ""

    def test_export_primary_contact_name(self, mock_db, organization_id):
        """Should export primary contact name."""
        supplier = MockSupplier(
            primary_contact={"name": "John Doe", "email": "john@test.com"}
        )

        with patch("app.services.finance.ap.bulk.Supplier", MagicMock()):
            from app.services.finance.ap.bulk import SupplierBulkService

            service = SupplierBulkService(mock_db, organization_id)
            value = service._get_export_value(supplier, "primary_contact.name")

            assert value == "John Doe"

    def test_export_primary_contact_email(self, mock_db, organization_id):
        """Should export primary contact email."""
        supplier = MockSupplier(
            primary_contact={
                "name": "John",
                "email": "john@supplier.com",
                "phone": "555-0100",
            }
        )

        with patch("app.services.finance.ap.bulk.Supplier", MagicMock()):
            from app.services.finance.ap.bulk import SupplierBulkService

            service = SupplierBulkService(mock_db, organization_id)
            value = service._get_export_value(supplier, "primary_contact.email")

            assert value == "john@supplier.com"

    def test_export_primary_contact_phone(self, mock_db, organization_id):
        """Should export primary contact phone."""
        supplier = MockSupplier(
            primary_contact={
                "name": "John",
                "email": "john@test.com",
                "phone": "+1-555-0100",
            }
        )

        with patch("app.services.finance.ap.bulk.Supplier", MagicMock()):
            from app.services.finance.ap.bulk import SupplierBulkService

            service = SupplierBulkService(mock_db, organization_id)
            value = service._get_export_value(supplier, "primary_contact.phone")

            assert value == "+1-555-0100"

    def test_export_primary_contact_none(self, mock_db, organization_id):
        """Should handle None primary_contact."""
        supplier = MockSupplier(primary_contact=None)

        with patch("app.services.finance.ap.bulk.Supplier", MagicMock()):
            from app.services.finance.ap.bulk import SupplierBulkService

            service = SupplierBulkService(mock_db, organization_id)
            value = service._get_export_value(supplier, "primary_contact.name")

            assert value == ""

    def test_export_primary_contact_missing_field(self, mock_db, organization_id):
        """Should handle missing field in primary_contact."""
        supplier = MockSupplier(primary_contact={"name": "John"})

        with patch("app.services.finance.ap.bulk.Supplier", MagicMock()):
            from app.services.finance.ap.bulk import SupplierBulkService

            service = SupplierBulkService(mock_db, organization_id)
            value = service._get_export_value(supplier, "primary_contact.email")

            assert value == ""

    def test_export_boolean_field(self, mock_db, organization_id):
        """Should export boolean fields correctly."""
        supplier = MockSupplier(is_active=True, is_related_party=False)

        with patch("app.services.finance.ap.bulk.Supplier", MagicMock()):
            from app.services.finance.ap.bulk import SupplierBulkService

            service = SupplierBulkService(mock_db, organization_id)
            active_value = service._get_export_value(supplier, "is_active")
            related_value = service._get_export_value(supplier, "is_related_party")

            assert active_value == "True"
            assert related_value == "False"

    def test_export_simple_field_delegates(self, mock_db, organization_id):
        """Simple fields should delegate to parent class."""
        supplier = MockSupplier(legal_name="Test Supplier", currency_code="EUR")

        with patch("app.services.finance.ap.bulk.Supplier", MagicMock()):
            from app.services.finance.ap.bulk import SupplierBulkService

            service = SupplierBulkService(mock_db, organization_id)
            name_value = service._get_export_value(supplier, "legal_name")
            currency_value = service._get_export_value(supplier, "currency_code")

            assert name_value == "Test Supplier"
            assert currency_value == "EUR"


# ============ TestGetExportFilename ============


class TestGetExportFilename:
    """Tests for the _get_export_filename method."""

    def test_filename_includes_suppliers(self, mock_db, organization_id):
        """Filename should include 'suppliers'."""
        with patch("app.services.finance.ap.bulk.Supplier", MagicMock()):
            from app.services.finance.ap.bulk import SupplierBulkService

            service = SupplierBulkService(mock_db, organization_id)
            filename = service._get_export_filename()

            assert "suppliers" in filename

    def test_filename_includes_timestamp(self, mock_db, organization_id):
        """Filename should include a timestamp."""
        with patch("app.services.finance.ap.bulk.Supplier", MagicMock()):
            from app.services.finance.ap.bulk import SupplierBulkService

            service = SupplierBulkService(mock_db, organization_id)
            filename = service._get_export_filename()

            # Should have format like suppliers_export_20240115_143022.csv
            parts = filename.replace(".csv", "").split("_")
            assert len(parts) >= 3

    def test_filename_ends_csv(self, mock_db, organization_id):
        """Filename should end with .csv."""
        with patch("app.services.finance.ap.bulk.Supplier", MagicMock()):
            from app.services.finance.ap.bulk import SupplierBulkService

            service = SupplierBulkService(mock_db, organization_id)
            filename = service._get_export_filename()

            assert filename.endswith(".csv")


# ============ TestBulkDelete ============


class TestBulkDelete:
    """Tests for the bulk_delete method."""

    @pytest.mark.asyncio
    async def test_bulk_delete_all_success(self, mock_db, organization_id):
        """All suppliers should be deleted when none have invoices."""
        supplier1 = MockSupplier()
        supplier2 = MockSupplier()

        mock_db.scalars.return_value.all.return_value = [
            supplier1,
            supplier2,
        ]
        mock_db.scalar.return_value = 0

        with patch("app.services.finance.ap.bulk.Supplier", MagicMock()):
            from app.services.finance.ap.bulk import SupplierBulkService

            service = SupplierBulkService(mock_db, organization_id)
            result = await service.bulk_delete(
                [supplier1.supplier_id, supplier2.supplier_id]
            )

            assert result.success_count == 2
            assert result.failed_count == 0

    @pytest.mark.asyncio
    async def test_bulk_delete_partial(self, mock_db, organization_id):
        """Some suppliers with invoices should fail to delete."""
        supplier1 = MockSupplier(legal_name="No Invoices")
        supplier2 = MockSupplier(legal_name="Has Invoices")

        mock_db.scalars.return_value.all.return_value = [
            supplier1,
            supplier2,
        ]

        mock_db.scalar.side_effect = [0, 5]

        with patch("app.services.finance.ap.bulk.Supplier", MagicMock()):
            from app.services.finance.ap.bulk import SupplierBulkService

            service = SupplierBulkService(mock_db, organization_id)
            result = await service.bulk_delete(
                [supplier1.supplier_id, supplier2.supplier_id]
            )

            assert result.success_count == 1
            assert result.failed_count == 1
            assert "Has Invoices" in result.errors[0]

    @pytest.mark.asyncio
    async def test_bulk_delete_all_blocked(self, mock_db, organization_id):
        """All suppliers with invoices should fail to delete."""
        supplier1 = MockSupplier()
        supplier2 = MockSupplier()

        mock_db.scalars.return_value.all.return_value = [
            supplier1,
            supplier2,
        ]
        mock_db.scalar.return_value = 10

        with patch("app.services.finance.ap.bulk.Supplier", MagicMock()):
            from app.services.finance.ap.bulk import SupplierBulkService

            service = SupplierBulkService(mock_db, organization_id)
            result = await service.bulk_delete(
                [supplier1.supplier_id, supplier2.supplier_id]
            )

            assert result.success_count == 0
            assert result.failed_count == 2

    @pytest.mark.asyncio
    async def test_bulk_delete_empty_ids(self, mock_db, organization_id):
        """Empty IDs list should return failure."""
        with patch("app.services.finance.ap.bulk.Supplier", MagicMock()):
            from app.services.finance.ap.bulk import SupplierBulkService

            service = SupplierBulkService(mock_db, organization_id)
            result = await service.bulk_delete([])

            assert result.success_count == 0
            assert "No IDs provided" in result.errors[0]

    @pytest.mark.asyncio
    async def test_bulk_delete_commits(self, mock_db, organization_id):
        """Successful deletions should commit."""
        supplier = MockSupplier()
        mock_db.scalars.return_value.all.return_value = [supplier]
        mock_db.scalar.return_value = 0

        with patch("app.services.finance.ap.bulk.Supplier", MagicMock()):
            from app.services.finance.ap.bulk import SupplierBulkService

            service = SupplierBulkService(mock_db, organization_id)
            await service.bulk_delete([supplier.supplier_id])

            mock_db.commit.assert_called_once()


# ============ TestBulkExport ============


class TestBulkExport:
    """Tests for the bulk_export method."""

    @pytest.mark.asyncio
    async def test_export_csv_headers(self, mock_db, mock_supplier, organization_id):
        """CSV export should include correct headers."""
        mock_db.scalars.return_value.all.return_value = [
            mock_supplier
        ]

        with patch("app.services.finance.ap.bulk.Supplier", MagicMock()):
            from app.services.finance.ap.bulk import SupplierBulkService

            service = SupplierBulkService(mock_db, organization_id)
            response = await service.bulk_export([mock_supplier.supplier_id])

            content = (
                response.body.decode()
                if isinstance(response.body, bytes)
                else response.body
            )

            headers = content.split("\n")[0]
            assert "Supplier Code" in headers
            assert "Legal Name" in headers
            assert "Contact Email" in headers

    @pytest.mark.asyncio
    async def test_export_csv_data(self, mock_db, mock_supplier, organization_id):
        """CSV export should include entity data."""
        mock_db.scalars.return_value.all.return_value = [
            mock_supplier
        ]

        with patch("app.services.finance.ap.bulk.Supplier", MagicMock()):
            from app.services.finance.ap.bulk import SupplierBulkService

            service = SupplierBulkService(mock_db, organization_id)
            response = await service.bulk_export([mock_supplier.supplier_id])

            content = (
                response.body.decode()
                if isinstance(response.body, bytes)
                else response.body
            )

            assert mock_supplier.legal_name in content
            assert mock_supplier.supplier_code in content

    @pytest.mark.asyncio
    async def test_export_empty_raises(self, mock_db, organization_id):
        """Export with no entities should raise HTTPException."""
        from fastapi import HTTPException

        mock_db.scalars.return_value.all.return_value = []

        with patch("app.services.finance.ap.bulk.Supplier", MagicMock()):
            from app.services.finance.ap.bulk import SupplierBulkService

            service = SupplierBulkService(mock_db, organization_id)

            with pytest.raises(HTTPException) as exc_info:
                await service.bulk_export([uuid.uuid4()])

            assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_export_streaming_response(
        self, mock_db, mock_supplier, organization_id
    ):
        """Export should return a Response."""
        from fastapi import Response

        mock_db.scalars.return_value.all.return_value = [
            mock_supplier
        ]

        with patch("app.services.finance.ap.bulk.Supplier", MagicMock()):
            from app.services.finance.ap.bulk import SupplierBulkService

            service = SupplierBulkService(mock_db, organization_id)
            response = await service.bulk_export([mock_supplier.supplier_id])

            assert isinstance(response, Response)
            assert response.media_type == "text/csv"


# ============ TestFactoryFunction ============


class TestFactoryFunction:
    """Tests for the get_supplier_bulk_service factory function."""

    def test_factory_creates_service(self, mock_db, organization_id, user_id):
        """Factory should create SupplierBulkService instance."""
        with patch("app.services.finance.ap.bulk.Supplier", MagicMock()):
            from app.services.finance.ap.bulk import get_supplier_bulk_service

            service = get_supplier_bulk_service(mock_db, organization_id, user_id)

            assert service.db is mock_db
            assert service.organization_id == organization_id
            assert service.user_id == user_id

    def test_factory_user_id_optional(self, mock_db, organization_id):
        """Factory should work without user_id."""
        with patch("app.services.finance.ap.bulk.Supplier", MagicMock()):
            from app.services.finance.ap.bulk import get_supplier_bulk_service

            service = get_supplier_bulk_service(mock_db, organization_id)

            assert service.user_id is None
