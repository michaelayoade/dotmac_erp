"""
Tests for app/services/ifrs/ar/bulk.py

Tests for CustomerBulkService that handles bulk operations on customer entities.
"""

from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from tests.ifrs.bulk.conftest import MockCustomer, MockCustomerType, MockRiskCategory


# ============ TestCanDelete ============


class TestCanDelete:
    """Tests for the can_delete method."""

    def test_can_delete_no_invoices(self, mock_db, mock_customer, organization_id):
        """Customer with no invoices can be deleted."""
        mock_db.query.return_value.filter.return_value.count.return_value = 0

        with patch("app.services.finance.ar.bulk.Customer", MagicMock()):
            from app.services.finance.ar.bulk import CustomerBulkService

            service = CustomerBulkService(mock_db, organization_id)
            can_delete, reason = service.can_delete(mock_customer)

            assert can_delete is True
            assert reason == ""

    def test_cannot_delete_with_invoices(self, mock_db, mock_customer, organization_id):
        """Customer with invoices cannot be deleted."""
        mock_db.query.return_value.filter.return_value.count.return_value = 8

        with patch("app.services.finance.ar.bulk.Customer", MagicMock()):
            from app.services.finance.ar.bulk import CustomerBulkService

            service = CustomerBulkService(mock_db, organization_id)
            can_delete, reason = service.can_delete(mock_customer)

            assert can_delete is False
            assert "8 invoice(s)" in reason

    def test_returns_invoice_count(self, mock_db, organization_id):
        """Error message should include the invoice count."""
        customer = MockCustomer(legal_name="ABC Corp")
        mock_db.query.return_value.filter.return_value.count.return_value = 15

        with patch("app.services.finance.ar.bulk.Customer", MagicMock()):
            from app.services.finance.ar.bulk import CustomerBulkService

            service = CustomerBulkService(mock_db, organization_id)
            can_delete, reason = service.can_delete(customer)

            assert "15 invoice(s)" in reason

    def test_returns_customer_name_in_message(self, mock_db, organization_id):
        """Error message should include the customer name."""
        customer = MockCustomer(legal_name="XYZ Industries Ltd")
        mock_db.query.return_value.filter.return_value.count.return_value = 5

        with patch("app.services.finance.ar.bulk.Customer", MagicMock()):
            from app.services.finance.ar.bulk import CustomerBulkService

            service = CustomerBulkService(mock_db, organization_id)
            can_delete, reason = service.can_delete(customer)

            assert "XYZ Industries Ltd" in reason

    def test_returns_tuple_format(self, mock_db, mock_customer, organization_id):
        """Method should return a tuple of (bool, str)."""
        mock_db.query.return_value.filter.return_value.count.return_value = 0

        with patch("app.services.finance.ar.bulk.Customer", MagicMock()):
            from app.services.finance.ar.bulk import CustomerBulkService

            service = CustomerBulkService(mock_db, organization_id)
            result = service.can_delete(mock_customer)

            assert isinstance(result, tuple)
            assert len(result) == 2
            assert isinstance(result[0], bool)
            assert isinstance(result[1], str)


# ============ TestGetExportValue ============


class TestGetExportValue:
    """Tests for the _get_export_value method."""

    def test_export_customer_type_company(self, mock_db, organization_id):
        """Should export customer_type enum value as string."""
        customer = MockCustomer(customer_type=MockCustomerType.company)

        with patch("app.services.finance.ar.bulk.Customer", MagicMock()):
            from app.services.finance.ar.bulk import CustomerBulkService

            service = CustomerBulkService(mock_db, organization_id)
            value = service._get_export_value(customer, "customer_type")

            assert value == "company"

    def test_export_customer_type_individual(self, mock_db, organization_id):
        """Should export individual type correctly."""
        customer = MockCustomer(customer_type=MockCustomerType.individual)

        with patch("app.services.finance.ar.bulk.Customer", MagicMock()):
            from app.services.finance.ar.bulk import CustomerBulkService

            service = CustomerBulkService(mock_db, organization_id)
            value = service._get_export_value(customer, "customer_type")

            assert value == "individual"

    def test_export_customer_type_government(self, mock_db, organization_id):
        """Should export government type correctly."""
        customer = MockCustomer(customer_type=MockCustomerType.government)

        with patch("app.services.finance.ar.bulk.Customer", MagicMock()):
            from app.services.finance.ar.bulk import CustomerBulkService

            service = CustomerBulkService(mock_db, organization_id)
            value = service._get_export_value(customer, "customer_type")

            assert value == "government"

    def test_export_customer_type_none(self, mock_db, organization_id):
        """Should handle None customer_type."""
        customer = MockCustomer(customer_type=None)

        with patch("app.services.finance.ar.bulk.Customer", MagicMock()):
            from app.services.finance.ar.bulk import CustomerBulkService

            service = CustomerBulkService(mock_db, organization_id)
            value = service._get_export_value(customer, "customer_type")

            assert value == ""

    def test_export_risk_category_low(self, mock_db, organization_id):
        """Should export risk_category enum value."""
        customer = MockCustomer(risk_category=MockRiskCategory.low)

        with patch("app.services.finance.ar.bulk.Customer", MagicMock()):
            from app.services.finance.ar.bulk import CustomerBulkService

            service = CustomerBulkService(mock_db, organization_id)
            value = service._get_export_value(customer, "risk_category")

            assert value == "low"

    def test_export_risk_category_medium(self, mock_db, organization_id):
        """Should export medium risk category."""
        customer = MockCustomer(risk_category=MockRiskCategory.medium)

        with patch("app.services.finance.ar.bulk.Customer", MagicMock()):
            from app.services.finance.ar.bulk import CustomerBulkService

            service = CustomerBulkService(mock_db, organization_id)
            value = service._get_export_value(customer, "risk_category")

            assert value == "medium"

    def test_export_risk_category_high(self, mock_db, organization_id):
        """Should export high risk category."""
        customer = MockCustomer(risk_category=MockRiskCategory.high)

        with patch("app.services.finance.ar.bulk.Customer", MagicMock()):
            from app.services.finance.ar.bulk import CustomerBulkService

            service = CustomerBulkService(mock_db, organization_id)
            value = service._get_export_value(customer, "risk_category")

            assert value == "high"

    def test_export_risk_category_none(self, mock_db, organization_id):
        """Should handle None risk_category."""
        customer = MockCustomer(risk_category=None)

        with patch("app.services.finance.ar.bulk.Customer", MagicMock()):
            from app.services.finance.ar.bulk import CustomerBulkService

            service = CustomerBulkService(mock_db, organization_id)
            value = service._get_export_value(customer, "risk_category")

            assert value == ""

    def test_export_primary_contact_name(self, mock_db, organization_id):
        """Should export primary contact name."""
        customer = MockCustomer(
            primary_contact={"name": "Jane Smith", "email": "jane@test.com"}
        )

        with patch("app.services.finance.ar.bulk.Customer", MagicMock()):
            from app.services.finance.ar.bulk import CustomerBulkService

            service = CustomerBulkService(mock_db, organization_id)
            value = service._get_export_value(customer, "primary_contact.name")

            assert value == "Jane Smith"

    def test_export_primary_contact_email(self, mock_db, organization_id):
        """Should export primary contact email."""
        customer = MockCustomer(
            primary_contact={
                "name": "Jane",
                "email": "jane@customer.com",
                "phone": "555-0200",
            }
        )

        with patch("app.services.finance.ar.bulk.Customer", MagicMock()):
            from app.services.finance.ar.bulk import CustomerBulkService

            service = CustomerBulkService(mock_db, organization_id)
            value = service._get_export_value(customer, "primary_contact.email")

            assert value == "jane@customer.com"

    def test_export_primary_contact_phone(self, mock_db, organization_id):
        """Should export primary contact phone."""
        customer = MockCustomer(
            primary_contact={
                "name": "Jane",
                "email": "jane@test.com",
                "phone": "+1-555-0200",
            }
        )

        with patch("app.services.finance.ar.bulk.Customer", MagicMock()):
            from app.services.finance.ar.bulk import CustomerBulkService

            service = CustomerBulkService(mock_db, organization_id)
            value = service._get_export_value(customer, "primary_contact.phone")

            assert value == "+1-555-0200"

    def test_export_primary_contact_none(self, mock_db, organization_id):
        """Should handle None primary_contact."""
        customer = MockCustomer(primary_contact=None)

        with patch("app.services.finance.ar.bulk.Customer", MagicMock()):
            from app.services.finance.ar.bulk import CustomerBulkService

            service = CustomerBulkService(mock_db, organization_id)
            value = service._get_export_value(customer, "primary_contact.name")

            assert value == ""

    def test_export_credit_hold_true(self, mock_db, organization_id):
        """Should export credit_hold boolean."""
        customer = MockCustomer(credit_hold=True)

        with patch("app.services.finance.ar.bulk.Customer", MagicMock()):
            from app.services.finance.ar.bulk import CustomerBulkService

            service = CustomerBulkService(mock_db, organization_id)
            value = service._get_export_value(customer, "credit_hold")

            assert value == "True"

    def test_export_credit_hold_false(self, mock_db, organization_id):
        """Should export credit_hold false value."""
        customer = MockCustomer(credit_hold=False)

        with patch("app.services.finance.ar.bulk.Customer", MagicMock()):
            from app.services.finance.ar.bulk import CustomerBulkService

            service = CustomerBulkService(mock_db, organization_id)
            value = service._get_export_value(customer, "credit_hold")

            assert value == "False"

    def test_export_credit_limit(self, mock_db, organization_id):
        """Should export credit_limit as string."""
        customer = MockCustomer(credit_limit=Decimal("50000.00"))

        with patch("app.services.finance.ar.bulk.Customer", MagicMock()):
            from app.services.finance.ar.bulk import CustomerBulkService

            service = CustomerBulkService(mock_db, organization_id)
            value = service._get_export_value(customer, "credit_limit")

            assert "50000" in value

    def test_export_simple_field_delegates(self, mock_db, organization_id):
        """Simple fields should delegate to parent class."""
        customer = MockCustomer(legal_name="Test Customer", currency_code="GBP")

        with patch("app.services.finance.ar.bulk.Customer", MagicMock()):
            from app.services.finance.ar.bulk import CustomerBulkService

            service = CustomerBulkService(mock_db, organization_id)
            name_value = service._get_export_value(customer, "legal_name")
            currency_value = service._get_export_value(customer, "currency_code")

            assert name_value == "Test Customer"
            assert currency_value == "GBP"


# ============ TestGetExportFilename ============


class TestGetExportFilename:
    """Tests for the _get_export_filename method."""

    def test_filename_includes_customers(self, mock_db, organization_id):
        """Filename should include 'customers'."""
        with patch("app.services.finance.ar.bulk.Customer", MagicMock()):
            from app.services.finance.ar.bulk import CustomerBulkService

            service = CustomerBulkService(mock_db, organization_id)
            filename = service._get_export_filename()

            assert "customers" in filename

    def test_filename_includes_timestamp(self, mock_db, organization_id):
        """Filename should include a timestamp."""
        with patch("app.services.finance.ar.bulk.Customer", MagicMock()):
            from app.services.finance.ar.bulk import CustomerBulkService

            service = CustomerBulkService(mock_db, organization_id)
            filename = service._get_export_filename()

            parts = filename.replace(".csv", "").split("_")
            assert len(parts) >= 3

    def test_filename_ends_csv(self, mock_db, organization_id):
        """Filename should end with .csv."""
        with patch("app.services.finance.ar.bulk.Customer", MagicMock()):
            from app.services.finance.ar.bulk import CustomerBulkService

            service = CustomerBulkService(mock_db, organization_id)
            filename = service._get_export_filename()

            assert filename.endswith(".csv")


# ============ TestBulkDelete ============


class TestBulkDelete:
    """Tests for the bulk_delete method."""

    @pytest.mark.asyncio
    async def test_bulk_delete_all_success(self, mock_db, organization_id):
        """All customers should be deleted when none have invoices."""
        customer1 = MockCustomer()
        customer2 = MockCustomer()

        mock_db.query.return_value.filter.return_value.all.return_value = [
            customer1,
            customer2,
        ]
        mock_db.query.return_value.filter.return_value.count.return_value = 0

        with patch("app.services.finance.ar.bulk.Customer", MagicMock()):
            from app.services.finance.ar.bulk import CustomerBulkService

            service = CustomerBulkService(mock_db, organization_id)
            result = await service.bulk_delete(
                [customer1.customer_id, customer2.customer_id]
            )

            assert result.success_count == 2
            assert result.failed_count == 0

    @pytest.mark.asyncio
    async def test_bulk_delete_partial(self, mock_db, organization_id):
        """Some customers with invoices should fail to delete."""
        customer1 = MockCustomer(legal_name="No Invoices")
        customer2 = MockCustomer(legal_name="Has Invoices")

        mock_db.query.return_value.filter.return_value.all.return_value = [
            customer1,
            customer2,
        ]

        call_count = [0]

        def mock_count():
            call_count[0] += 1
            return 0 if call_count[0] == 1 else 3

        mock_db.query.return_value.filter.return_value.count = mock_count

        with patch("app.services.finance.ar.bulk.Customer", MagicMock()):
            from app.services.finance.ar.bulk import CustomerBulkService

            service = CustomerBulkService(mock_db, organization_id)
            result = await service.bulk_delete(
                [customer1.customer_id, customer2.customer_id]
            )

            assert result.success_count == 1
            assert result.failed_count == 1
            assert "Has Invoices" in result.errors[0]

    @pytest.mark.asyncio
    async def test_bulk_delete_empty_ids(self, mock_db, organization_id):
        """Empty IDs list should return failure."""
        with patch("app.services.finance.ar.bulk.Customer", MagicMock()):
            from app.services.finance.ar.bulk import CustomerBulkService

            service = CustomerBulkService(mock_db, organization_id)
            result = await service.bulk_delete([])

            assert result.success_count == 0
            assert "No IDs provided" in result.errors[0]


# ============ TestBulkExport ============


class TestBulkExport:
    """Tests for the bulk_export method."""

    @pytest.mark.asyncio
    async def test_export_csv_headers(self, mock_db, mock_customer, organization_id):
        """CSV export should include correct headers."""
        mock_db.query.return_value.filter.return_value.all.return_value = [
            mock_customer
        ]

        with patch("app.services.finance.ar.bulk.Customer", MagicMock()):
            from app.services.finance.ar.bulk import CustomerBulkService

            service = CustomerBulkService(mock_db, organization_id)
            response = await service.bulk_export([mock_customer.customer_id])

            content = (
                response.body.decode()
                if isinstance(response.body, bytes)
                else response.body
            )

            headers = content.split("\n")[0]
            assert "Customer Code" in headers
            assert "Legal Name" in headers
            assert "Credit Limit" in headers
            assert "Risk Category" in headers

    @pytest.mark.asyncio
    async def test_export_csv_data(self, mock_db, mock_customer, organization_id):
        """CSV export should include entity data."""
        mock_db.query.return_value.filter.return_value.all.return_value = [
            mock_customer
        ]

        with patch("app.services.finance.ar.bulk.Customer", MagicMock()):
            from app.services.finance.ar.bulk import CustomerBulkService

            service = CustomerBulkService(mock_db, organization_id)
            response = await service.bulk_export([mock_customer.customer_id])

            content = (
                response.body.decode()
                if isinstance(response.body, bytes)
                else response.body
            )

            assert mock_customer.legal_name in content
            assert mock_customer.customer_code in content


# ============ TestFactoryFunction ============


class TestFactoryFunction:
    """Tests for the get_customer_bulk_service factory function."""

    def test_factory_creates_service(self, mock_db, organization_id, user_id):
        """Factory should create CustomerBulkService instance."""
        with patch("app.services.finance.ar.bulk.Customer", MagicMock()):
            from app.services.finance.ar.bulk import get_customer_bulk_service

            service = get_customer_bulk_service(mock_db, organization_id, user_id)

            assert service.db is mock_db
            assert service.organization_id == organization_id
            assert service.user_id == user_id

    def test_factory_user_id_optional(self, mock_db, organization_id):
        """Factory should work without user_id."""
        with patch("app.services.finance.ar.bulk.Customer", MagicMock()):
            from app.services.finance.ar.bulk import get_customer_bulk_service

            service = get_customer_bulk_service(mock_db, organization_id)

            assert service.user_id is None
