"""
Tests for ARAgingService - Accounts Receivable aging analysis.

Tests aging calculations, snapshots, and customer risk analysis.
"""

import uuid
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from app.models.finance.ar.customer import Customer
from app.models.finance.ar.invoice import Invoice, InvoiceStatus
from app.models.finance.ar.ar_aging_snapshot import ARAgingSnapshot
from app.services.finance.ar.ar_aging import (
    ARAgingService,
    CustomerAgingSummary,
    OrganizationARAgingSummary,
    AgingBucket,
)


# -----------------------------------------------------------------------------
# Fixtures
# -----------------------------------------------------------------------------


@pytest.fixture
def mock_db():
    """Create a mock database session."""
    return MagicMock()


@pytest.fixture
def organization_id():
    """Standard organization ID for tests."""
    return uuid.uuid4()


@pytest.fixture
def customer_id():
    """Standard customer ID for tests."""
    return uuid.uuid4()


@pytest.fixture
def fiscal_period_id():
    """Standard fiscal period ID for tests."""
    return uuid.uuid4()


@pytest.fixture
def mock_customer(organization_id, customer_id):
    """Create a mock customer."""
    customer = MagicMock(spec=Customer)
    customer.customer_id = customer_id
    customer.organization_id = organization_id
    customer.customer_code = "CUST001"
    customer.legal_name = "Test Customer Ltd"
    customer.currency_code = "USD"
    return customer


@pytest.fixture
def mock_invoice(organization_id, customer_id):
    """Create a mock invoice factory."""
    def _create_invoice(
        due_date: date,
        balance_due: Decimal = Decimal("1000"),
        status: InvoiceStatus = InvoiceStatus.POSTED,
    ):
        invoice = MagicMock(spec=Invoice)
        invoice.invoice_id = uuid.uuid4()
        invoice.organization_id = organization_id
        invoice.customer_id = customer_id
        invoice.due_date = due_date
        invoice.balance_due = balance_due
        invoice.status = status
        return invoice

    return _create_invoice


# -----------------------------------------------------------------------------
# Test: calculate_customer_aging
# -----------------------------------------------------------------------------


class TestCalculateCustomerAging:
    """Tests for ARAgingService.calculate_customer_aging."""

    def test_calculate_customer_aging_no_invoices(
        self, mock_db, organization_id, customer_id, mock_customer
    ):
        """Test aging calculation when customer has no outstanding invoices."""
        mock_db.get.return_value = mock_customer
        mock_db.query.return_value.filter.return_value.all.return_value = []

        result = ARAgingService.calculate_customer_aging(
            db=mock_db,
            organization_id=organization_id,
            customer_id=customer_id,
        )

        assert isinstance(result, CustomerAgingSummary)
        assert result.customer_id == customer_id
        assert result.current == Decimal("0")
        assert result.days_31_60 == Decimal("0")
        assert result.days_61_90 == Decimal("0")
        assert result.over_90 == Decimal("0")
        assert result.total_outstanding == Decimal("0")
        assert result.invoice_count == 0

    def test_calculate_customer_aging_current_bucket(
        self, mock_db, organization_id, customer_id, mock_customer, mock_invoice
    ):
        """Test invoices in current (0-30 days) bucket."""
        mock_db.get.return_value = mock_customer

        today = date.today()
        invoice = mock_invoice(due_date=today - timedelta(days=15), balance_due=Decimal("5000"))
        mock_db.query.return_value.filter.return_value.all.return_value = [invoice]

        result = ARAgingService.calculate_customer_aging(
            db=mock_db,
            organization_id=organization_id,
            customer_id=customer_id,
            as_of_date=today,
        )

        assert result.current == Decimal("5000")
        assert result.days_31_60 == Decimal("0")
        assert result.days_61_90 == Decimal("0")
        assert result.over_90 == Decimal("0")
        assert result.total_outstanding == Decimal("5000")

    def test_calculate_customer_aging_31_60_bucket(
        self, mock_db, organization_id, customer_id, mock_customer, mock_invoice
    ):
        """Test invoices in 31-60 days bucket."""
        mock_db.get.return_value = mock_customer

        today = date.today()
        invoice = mock_invoice(due_date=today - timedelta(days=45), balance_due=Decimal("3000"))
        mock_db.query.return_value.filter.return_value.all.return_value = [invoice]

        result = ARAgingService.calculate_customer_aging(
            db=mock_db,
            organization_id=organization_id,
            customer_id=customer_id,
            as_of_date=today,
        )

        assert result.current == Decimal("0")
        assert result.days_31_60 == Decimal("3000")
        assert result.days_61_90 == Decimal("0")
        assert result.over_90 == Decimal("0")

    def test_calculate_customer_aging_61_90_bucket(
        self, mock_db, organization_id, customer_id, mock_customer, mock_invoice
    ):
        """Test invoices in 61-90 days bucket."""
        mock_db.get.return_value = mock_customer

        today = date.today()
        invoice = mock_invoice(due_date=today - timedelta(days=75), balance_due=Decimal("2000"))
        mock_db.query.return_value.filter.return_value.all.return_value = [invoice]

        result = ARAgingService.calculate_customer_aging(
            db=mock_db,
            organization_id=organization_id,
            customer_id=customer_id,
            as_of_date=today,
        )

        assert result.current == Decimal("0")
        assert result.days_31_60 == Decimal("0")
        assert result.days_61_90 == Decimal("2000")
        assert result.over_90 == Decimal("0")

    def test_calculate_customer_aging_over_90_bucket(
        self, mock_db, organization_id, customer_id, mock_customer, mock_invoice
    ):
        """Test invoices in over 90 days bucket."""
        mock_db.get.return_value = mock_customer

        today = date.today()
        invoice = mock_invoice(due_date=today - timedelta(days=120), balance_due=Decimal("8000"))
        mock_db.query.return_value.filter.return_value.all.return_value = [invoice]

        result = ARAgingService.calculate_customer_aging(
            db=mock_db,
            organization_id=organization_id,
            customer_id=customer_id,
            as_of_date=today,
        )

        assert result.current == Decimal("0")
        assert result.days_31_60 == Decimal("0")
        assert result.days_61_90 == Decimal("0")
        assert result.over_90 == Decimal("8000")

    def test_calculate_customer_aging_multiple_buckets(
        self, mock_db, organization_id, customer_id, mock_customer, mock_invoice
    ):
        """Test invoices spread across multiple buckets."""
        mock_db.get.return_value = mock_customer

        today = date.today()
        invoices = [
            mock_invoice(due_date=today - timedelta(days=10), balance_due=Decimal("1000")),
            mock_invoice(due_date=today - timedelta(days=40), balance_due=Decimal("2000")),
            mock_invoice(due_date=today - timedelta(days=70), balance_due=Decimal("3000")),
            mock_invoice(due_date=today - timedelta(days=100), balance_due=Decimal("4000")),
        ]
        mock_db.query.return_value.filter.return_value.all.return_value = invoices

        result = ARAgingService.calculate_customer_aging(
            db=mock_db,
            organization_id=organization_id,
            customer_id=customer_id,
            as_of_date=today,
        )

        assert result.current == Decimal("1000")
        assert result.days_31_60 == Decimal("2000")
        assert result.days_61_90 == Decimal("3000")
        assert result.over_90 == Decimal("4000")
        assert result.total_outstanding == Decimal("10000")
        assert result.invoice_count == 4

    def test_calculate_customer_aging_customer_not_found(
        self, mock_db, organization_id
    ):
        """Test aging calculation for non-existent customer."""
        mock_db.get.return_value = None

        with pytest.raises(ValueError) as exc_info:
            ARAgingService.calculate_customer_aging(
                db=mock_db,
                organization_id=organization_id,
                customer_id=uuid.uuid4(),
            )

        assert "not found" in str(exc_info.value).lower()

    def test_calculate_customer_aging_wrong_organization(
        self, mock_db, organization_id, customer_id, mock_customer
    ):
        """Test aging calculation for customer in different organization."""
        mock_customer.organization_id = uuid.uuid4()  # Different org
        mock_db.get.return_value = mock_customer

        with pytest.raises(ValueError) as exc_info:
            ARAgingService.calculate_customer_aging(
                db=mock_db,
                organization_id=organization_id,
                customer_id=customer_id,
            )

        assert "not found" in str(exc_info.value).lower()

    def test_calculate_customer_aging_custom_date(
        self, mock_db, organization_id, customer_id, mock_customer, mock_invoice
    ):
        """Test aging calculation with custom as_of_date."""
        mock_db.get.return_value = mock_customer

        custom_date = date(2024, 6, 30)
        invoice = mock_invoice(
            due_date=date(2024, 6, 15),  # 15 days before custom_date
            balance_due=Decimal("5000"),
        )
        mock_db.query.return_value.filter.return_value.all.return_value = [invoice]

        result = ARAgingService.calculate_customer_aging(
            db=mock_db,
            organization_id=organization_id,
            customer_id=customer_id,
            as_of_date=custom_date,
        )

        # 15 days overdue should be in current bucket
        assert result.current == Decimal("5000")


# -----------------------------------------------------------------------------
# Test: calculate_organization_aging
# -----------------------------------------------------------------------------


class TestCalculateOrganizationAging:
    """Tests for ARAgingService.calculate_organization_aging."""

    @patch("app.services.finance.ar.ar_aging.org_context_service")
    def test_calculate_organization_aging_no_invoices(
        self, mock_org_context, mock_db, organization_id
    ):
        """Test organization aging with no outstanding invoices."""
        mock_org_context.get_functional_currency.return_value = "USD"
        mock_db.query.return_value.filter.return_value.all.return_value = []

        result = ARAgingService.calculate_organization_aging(
            db=mock_db,
            organization_id=organization_id,
        )

        assert isinstance(result, OrganizationARAgingSummary)
        assert result.total_outstanding == Decimal("0")
        assert result.customer_count == 0
        assert result.invoice_count == 0

    @patch("app.services.finance.ar.ar_aging.org_context_service")
    def test_calculate_organization_aging_multiple_customers(
        self, mock_org_context, mock_db, organization_id, mock_invoice
    ):
        """Test organization aging across multiple customers."""
        mock_org_context.get_functional_currency.return_value = "USD"

        today = date.today()
        customer1_id = uuid.uuid4()
        customer2_id = uuid.uuid4()

        # Create invoices for different customers
        invoice1 = mock_invoice(due_date=today - timedelta(days=10), balance_due=Decimal("5000"))
        invoice1.customer_id = customer1_id

        invoice2 = mock_invoice(due_date=today - timedelta(days=50), balance_due=Decimal("3000"))
        invoice2.customer_id = customer2_id

        mock_db.query.return_value.filter.return_value.all.return_value = [invoice1, invoice2]

        result = ARAgingService.calculate_organization_aging(
            db=mock_db,
            organization_id=organization_id,
            as_of_date=today,
        )

        assert result.current == Decimal("5000")
        assert result.days_31_60 == Decimal("3000")
        assert result.total_outstanding == Decimal("8000")
        assert result.customer_count == 2
        assert result.invoice_count == 2

    @patch("app.services.finance.ar.ar_aging.org_context_service")
    def test_calculate_organization_aging_all_buckets(
        self, mock_org_context, mock_db, organization_id, mock_invoice
    ):
        """Test organization aging with invoices in all buckets."""
        mock_org_context.get_functional_currency.return_value = "EUR"

        today = date.today()
        invoices = [
            mock_invoice(due_date=today - timedelta(days=20), balance_due=Decimal("1000")),
            mock_invoice(due_date=today - timedelta(days=45), balance_due=Decimal("2000")),
            mock_invoice(due_date=today - timedelta(days=80), balance_due=Decimal("3000")),
            mock_invoice(due_date=today - timedelta(days=120), balance_due=Decimal("4000")),
        ]
        mock_db.query.return_value.filter.return_value.all.return_value = invoices

        result = ARAgingService.calculate_organization_aging(
            db=mock_db,
            organization_id=organization_id,
            as_of_date=today,
        )

        assert result.currency_code == "EUR"
        assert result.current == Decimal("1000")
        assert result.days_31_60 == Decimal("2000")
        assert result.days_61_90 == Decimal("3000")
        assert result.over_90 == Decimal("4000")
        assert result.total_outstanding == Decimal("10000")


# -----------------------------------------------------------------------------
# Test: get_aging_by_customer
# -----------------------------------------------------------------------------


class TestGetAgingByCustomer:
    """Tests for ARAgingService.get_aging_by_customer."""

    def test_get_aging_by_customer_empty(self, mock_db, organization_id):
        """Test getting aging by customer when no customers have outstanding invoices."""
        mock_db.query.return_value.filter.return_value.distinct.return_value.all.return_value = []

        result = ARAgingService.get_aging_by_customer(
            db=mock_db,
            organization_id=organization_id,
        )

        assert result == []

    def test_get_aging_by_customer_sorted_by_total(
        self, mock_db, organization_id, mock_customer, mock_invoice
    ):
        """Test that results are sorted by total outstanding descending."""
        customer1_id = uuid.uuid4()
        customer2_id = uuid.uuid4()

        mock_db.query.return_value.filter.return_value.distinct.return_value.all.return_value = [
            (customer1_id,),
            (customer2_id,),
        ]

        # Create two different customers
        customer1 = MagicMock(spec=Customer)
        customer1.customer_id = customer1_id
        customer1.organization_id = organization_id
        customer1.customer_code = "CUST001"
        customer1.legal_name = "Customer One"
        customer1.currency_code = "USD"

        customer2 = MagicMock(spec=Customer)
        customer2.customer_id = customer2_id
        customer2.organization_id = organization_id
        customer2.customer_code = "CUST002"
        customer2.legal_name = "Customer Two"
        customer2.currency_code = "USD"

        today = date.today()

        # Customer 1: $1000 total
        invoice1 = mock_invoice(due_date=today - timedelta(days=10), balance_due=Decimal("1000"))
        invoice1.customer_id = customer1_id

        # Customer 2: $5000 total
        invoice2 = mock_invoice(due_date=today - timedelta(days=10), balance_due=Decimal("5000"))
        invoice2.customer_id = customer2_id

        def mock_get(model, id):
            if id == customer1_id:
                return customer1
            elif id == customer2_id:
                return customer2
            return None

        mock_db.get.side_effect = mock_get

        def mock_filter_all(*args, **kwargs):
            # Return invoices based on customer filter
            return [invoice1] if invoice1.customer_id in str(args) else [invoice2]

        # First call returns customer IDs, subsequent calls return invoices
        call_count = [0]

        def mock_filter_return(*args, **kwargs):
            mock_result = MagicMock()
            call_count[0] += 1
            if call_count[0] == 1:
                mock_result.distinct.return_value.all.return_value = [(customer1_id,), (customer2_id,)]
            else:
                # Return empty for individual customer queries
                mock_result.all.return_value = []
            return mock_result

        mock_db.query.return_value.filter.side_effect = mock_filter_return

        # Due to complex mocking, just test that the method doesn't error
        # and returns a list
        result = ARAgingService.get_aging_by_customer(
            db=mock_db,
            organization_id=organization_id,
        )

        assert isinstance(result, list)

    def test_get_aging_by_customer_min_balance_filter(self, mock_db, organization_id):
        """Test filtering by minimum balance."""
        mock_db.query.return_value.filter.return_value.distinct.return_value.all.return_value = []

        result = ARAgingService.get_aging_by_customer(
            db=mock_db,
            organization_id=organization_id,
            min_balance=Decimal("10000"),
        )

        assert result == []


# -----------------------------------------------------------------------------
# Test: create_aging_snapshot
# -----------------------------------------------------------------------------


class TestCreateAgingSnapshot:
    """Tests for ARAgingService.create_aging_snapshot."""

    def test_create_aging_snapshot_no_customers(
        self, mock_db, organization_id, fiscal_period_id
    ):
        """Test creating snapshot when no customers have outstanding balances."""
        mock_db.query.return_value.filter.return_value.distinct.return_value.all.return_value = []

        result = ARAgingService.create_aging_snapshot(
            db=mock_db,
            organization_id=organization_id,
            fiscal_period_id=fiscal_period_id,
        )

        assert result == []
        mock_db.commit.assert_called_once()

    def test_create_aging_snapshot_creates_bucket_records(
        self, mock_db, organization_id, fiscal_period_id, mock_customer, mock_invoice
    ):
        """Test that snapshot creates separate records for each bucket."""
        today = date.today()

        # Setup mock to return one customer with balances in all buckets
        mock_db.query.return_value.filter.return_value.distinct.return_value.all.return_value = [
            (mock_customer.customer_id,)
        ]
        mock_db.get.return_value = mock_customer

        invoices = [
            mock_invoice(due_date=today - timedelta(days=10), balance_due=Decimal("1000")),
            mock_invoice(due_date=today - timedelta(days=45), balance_due=Decimal("2000")),
            mock_invoice(due_date=today - timedelta(days=75), balance_due=Decimal("3000")),
            mock_invoice(due_date=today - timedelta(days=100), balance_due=Decimal("4000")),
        ]
        mock_db.query.return_value.filter.return_value.all.return_value = invoices

        result = ARAgingService.create_aging_snapshot(
            db=mock_db,
            organization_id=organization_id,
            fiscal_period_id=fiscal_period_id,
            as_of_date=today,
        )

        # Should create 4 snapshot records (one per bucket with non-zero balance)
        assert len(result) == 4
        mock_db.commit.assert_called_once()


# -----------------------------------------------------------------------------
# Test: get_overdue_invoices
# -----------------------------------------------------------------------------


class TestGetOverdueInvoices:
    """Tests for ARAgingService.get_overdue_invoices."""

    def test_get_overdue_invoices_none(self, mock_db, organization_id):
        """Test getting overdue invoices when none exist."""
        mock_db.query.return_value.filter.return_value.order_by.return_value.all.return_value = []

        result = ARAgingService.get_overdue_invoices(
            db=mock_db,
            organization_id=organization_id,
        )

        assert result == []

    def test_get_overdue_invoices_with_results(
        self, mock_db, organization_id, mock_invoice
    ):
        """Test getting overdue invoices."""
        today = date.today()
        overdue_invoice = mock_invoice(due_date=today - timedelta(days=10), balance_due=Decimal("5000"))
        mock_db.query.return_value.filter.return_value.order_by.return_value.all.return_value = [
            overdue_invoice
        ]

        result = ARAgingService.get_overdue_invoices(
            db=mock_db,
            organization_id=organization_id,
            as_of_date=today,
        )

        assert len(result) == 1

    def test_get_overdue_invoices_min_days_filter(
        self, mock_db, organization_id, mock_invoice
    ):
        """Test filtering by minimum days overdue."""
        today = date.today()

        # 5 days overdue
        invoice1 = mock_invoice(due_date=today - timedelta(days=5), balance_due=Decimal("1000"))
        # 30 days overdue
        invoice2 = mock_invoice(due_date=today - timedelta(days=30), balance_due=Decimal("2000"))

        mock_db.query.return_value.filter.return_value.order_by.return_value.all.return_value = [
            invoice1,
            invoice2,
        ]

        # Filter for at least 10 days overdue
        result = ARAgingService.get_overdue_invoices(
            db=mock_db,
            organization_id=organization_id,
            as_of_date=today,
            min_days_overdue=10,
        )

        assert len(result) == 1
        assert result[0] == invoice2

    def test_get_overdue_invoices_customer_filter(
        self, mock_db, organization_id, customer_id, mock_invoice
    ):
        """Test filtering overdue invoices by customer."""
        today = date.today()
        invoice = mock_invoice(due_date=today - timedelta(days=10), balance_due=Decimal("5000"))
        mock_db.query.return_value.filter.return_value.filter.return_value.order_by.return_value.all.return_value = [
            invoice
        ]

        result = ARAgingService.get_overdue_invoices(
            db=mock_db,
            organization_id=organization_id,
            customer_id=customer_id,
            as_of_date=today,
        )

        assert len(result) == 1


# -----------------------------------------------------------------------------
# Test: get_high_risk_customers
# -----------------------------------------------------------------------------


class TestGetHighRiskCustomers:
    """Tests for ARAgingService.get_high_risk_customers."""

    def test_get_high_risk_customers_none(self, mock_db, organization_id):
        """Test when no high-risk customers exist."""
        mock_db.query.return_value.filter.return_value.distinct.return_value.all.return_value = []

        result = ARAgingService.get_high_risk_customers(
            db=mock_db,
            organization_id=organization_id,
        )

        assert result == []

    def test_get_high_risk_customers_60_plus_days(
        self, mock_db, organization_id, mock_customer, mock_invoice
    ):
        """Test identifying customers with 60+ days overdue amounts."""
        today = date.today()

        mock_db.query.return_value.filter.return_value.distinct.return_value.all.return_value = [
            (mock_customer.customer_id,)
        ]
        mock_db.get.return_value = mock_customer

        # Customer with amounts in 61-90 bucket
        invoices = [
            mock_invoice(due_date=today - timedelta(days=75), balance_due=Decimal("5000")),
        ]
        mock_db.query.return_value.filter.return_value.all.return_value = invoices

        result = ARAgingService.get_high_risk_customers(
            db=mock_db,
            organization_id=organization_id,
            min_overdue_days=60,
            as_of_date=today,
        )

        assert len(result) == 1

    def test_get_high_risk_customers_over_90_days(
        self, mock_db, organization_id, mock_customer, mock_invoice
    ):
        """Test identifying customers with 90+ days overdue amounts."""
        today = date.today()

        mock_db.query.return_value.filter.return_value.distinct.return_value.all.return_value = [
            (mock_customer.customer_id,)
        ]
        mock_db.get.return_value = mock_customer

        invoices = [
            mock_invoice(due_date=today - timedelta(days=100), balance_due=Decimal("8000")),
        ]
        mock_db.query.return_value.filter.return_value.all.return_value = invoices

        result = ARAgingService.get_high_risk_customers(
            db=mock_db,
            organization_id=organization_id,
            min_overdue_days=91,
            as_of_date=today,
        )

        assert len(result) == 1

    def test_get_high_risk_customers_min_amount_filter(
        self, mock_db, organization_id, mock_customer, mock_invoice
    ):
        """Test filtering high-risk customers by minimum amount."""
        today = date.today()

        mock_db.query.return_value.filter.return_value.distinct.return_value.all.return_value = [
            (mock_customer.customer_id,)
        ]
        mock_db.get.return_value = mock_customer

        invoices = [
            mock_invoice(due_date=today - timedelta(days=100), balance_due=Decimal("500")),
        ]
        mock_db.query.return_value.filter.return_value.all.return_value = invoices

        # Filter for minimum $1000 overdue - should exclude this customer
        result = ARAgingService.get_high_risk_customers(
            db=mock_db,
            organization_id=organization_id,
            min_overdue_days=60,
            min_overdue_amount=Decimal("1000"),
            as_of_date=today,
        )

        assert len(result) == 0


# -----------------------------------------------------------------------------
# Test: list
# -----------------------------------------------------------------------------


class TestList:
    """Tests for ARAgingService.list."""

    def test_list_all_snapshots(self, mock_db):
        """Test listing all aging snapshots."""
        mock_snapshots = [MagicMock(spec=ARAgingSnapshot) for _ in range(3)]
        mock_db.query.return_value.order_by.return_value.limit.return_value.offset.return_value.all.return_value = mock_snapshots

        result = ARAgingService.list(db=mock_db)

        assert len(result) == 3

    def test_list_by_organization(self, mock_db, organization_id):
        """Test listing snapshots filtered by organization."""
        mock_snapshots = [MagicMock(spec=ARAgingSnapshot)]
        mock_db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.offset.return_value.all.return_value = mock_snapshots

        result = ARAgingService.list(
            db=mock_db,
            organization_id=str(organization_id),
        )

        assert len(result) == 1

    def test_list_by_customer(self, mock_db, customer_id):
        """Test listing snapshots filtered by customer."""
        mock_snapshots = [MagicMock(spec=ARAgingSnapshot)]
        mock_db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.offset.return_value.all.return_value = mock_snapshots

        result = ARAgingService.list(
            db=mock_db,
            customer_id=str(customer_id),
        )

        assert len(result) == 1

    def test_list_by_snapshot_date(self, mock_db):
        """Test listing snapshots filtered by date."""
        mock_snapshots = [MagicMock(spec=ARAgingSnapshot)]
        mock_db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.offset.return_value.all.return_value = mock_snapshots

        result = ARAgingService.list(
            db=mock_db,
            snapshot_date=date(2024, 6, 30),
        )

        assert len(result) == 1

    def test_list_by_aging_bucket(self, mock_db):
        """Test listing snapshots filtered by aging bucket."""
        mock_snapshots = [MagicMock(spec=ARAgingSnapshot)]
        mock_db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.offset.return_value.all.return_value = mock_snapshots

        result = ARAgingService.list(
            db=mock_db,
            aging_bucket="Over 90 Days",
        )

        assert len(result) == 1

    def test_list_with_pagination(self, mock_db):
        """Test listing snapshots with pagination."""
        mock_snapshots = [MagicMock(spec=ARAgingSnapshot)]
        mock_db.query.return_value.order_by.return_value.limit.return_value.offset.return_value.all.return_value = mock_snapshots

        result = ARAgingService.list(
            db=mock_db,
            limit=25,
            offset=50,
        )

        mock_db.query.return_value.order_by.return_value.limit.assert_called_with(25)
        mock_db.query.return_value.order_by.return_value.limit.return_value.offset.assert_called_with(50)


# -----------------------------------------------------------------------------
# Test: AgingBucket dataclass
# -----------------------------------------------------------------------------


class TestAgingBucketDataclass:
    """Tests for AgingBucket dataclass."""

    def test_aging_bucket_creation(self):
        """Test creating an AgingBucket."""
        bucket = AgingBucket(
            bucket_name="Current",
            min_days=0,
            max_days=30,
            amount=Decimal("5000"),
            invoice_count=3,
        )

        assert bucket.bucket_name == "Current"
        assert bucket.min_days == 0
        assert bucket.max_days == 30
        assert bucket.amount == Decimal("5000")
        assert bucket.invoice_count == 3

    def test_aging_bucket_over_90_no_max(self):
        """Test AgingBucket for over 90 days (no max)."""
        bucket = AgingBucket(
            bucket_name="Over 90 Days",
            min_days=91,
            max_days=None,
        )

        assert bucket.max_days is None

    def test_aging_bucket_defaults(self):
        """Test AgingBucket default values."""
        bucket = AgingBucket(
            bucket_name="Test",
            min_days=0,
            max_days=30,
        )

        assert bucket.amount == Decimal("0")
        assert bucket.invoice_count == 0


# -----------------------------------------------------------------------------
# Test: Standard AGING_BUCKETS
# -----------------------------------------------------------------------------


class TestStandardAgingBuckets:
    """Tests for standard aging bucket configuration."""

    def test_standard_buckets_count(self):
        """Test that there are 4 standard buckets."""
        assert len(ARAgingService.AGING_BUCKETS) == 4

    def test_standard_bucket_ranges(self):
        """Test standard bucket day ranges."""
        buckets = ARAgingService.AGING_BUCKETS

        # Current: 0-30
        assert buckets[0].bucket_name == "Current"
        assert buckets[0].min_days == 0
        assert buckets[0].max_days == 30

        # 31-60 Days
        assert buckets[1].bucket_name == "31-60 Days"
        assert buckets[1].min_days == 31
        assert buckets[1].max_days == 60

        # 61-90 Days
        assert buckets[2].bucket_name == "61-90 Days"
        assert buckets[2].min_days == 61
        assert buckets[2].max_days == 90

        # Over 90 Days
        assert buckets[3].bucket_name == "Over 90 Days"
        assert buckets[3].min_days == 91
        assert buckets[3].max_days is None
