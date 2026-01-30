"""
Tests for APAgingService.

Tests AP aging calculations, snapshots, and overdue invoice analysis.
"""

import pytest
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch
from uuid import uuid4

from app.models.finance.ap.supplier_invoice import SupplierInvoiceStatus
from app.services.finance.ap.ap_aging import (
    APAgingService,
    AgingBucket,
    SupplierAgingSummary,
    OrganizationAgingSummary,
)


class MockSupplier:
    """Mock Supplier model."""

    def __init__(
        self,
        supplier_id=None,
        organization_id=None,
        supplier_code="SUP-001",
        legal_name="Test Supplier",
        currency_code="USD",
    ):
        self.supplier_id = supplier_id or uuid4()
        self.organization_id = organization_id or uuid4()
        self.supplier_code = supplier_code
        self.legal_name = legal_name
        self.currency_code = currency_code


class MockSupplierInvoice:
    """Mock SupplierInvoice model."""

    def __init__(
        self,
        invoice_id=None,
        organization_id=None,
        supplier_id=None,
        invoice_number="INV-001",
        status=SupplierInvoiceStatus.POSTED,
        total_amount=Decimal("1000.00"),
        amount_paid=Decimal("0"),
        due_date=None,
    ):
        self.invoice_id = invoice_id or uuid4()
        self.organization_id = organization_id or uuid4()
        self.supplier_id = supplier_id or uuid4()
        self.invoice_number = invoice_number
        self.status = status
        self.total_amount = total_amount
        self.amount_paid = amount_paid
        self.due_date = due_date or date.today()

    @property
    def balance_due(self) -> Decimal:
        return self.total_amount - self.amount_paid


class MockAPAgingSnapshot:
    """Mock APAgingSnapshot model."""

    def __init__(
        self,
        snapshot_id=None,
        organization_id=None,
        supplier_id=None,
        snapshot_date=None,
        currency_code="USD",
        current_amount=Decimal("0"),
        days_31_60_amount=Decimal("0"),
        days_61_90_amount=Decimal("0"),
        over_90_amount=Decimal("0"),
        total_outstanding=Decimal("0"),
        invoice_count=0,
    ):
        self.snapshot_id = snapshot_id or uuid4()
        self.organization_id = organization_id or uuid4()
        self.supplier_id = supplier_id or uuid4()
        self.snapshot_date = snapshot_date or date.today()
        self.currency_code = currency_code
        self.current_amount = current_amount
        self.days_31_60_amount = days_31_60_amount
        self.days_61_90_amount = days_61_90_amount
        self.over_90_amount = over_90_amount
        self.total_outstanding = total_outstanding
        self.invoice_count = invoice_count


@pytest.fixture
def org_id():
    return uuid4()


@pytest.fixture
def user_id():
    return uuid4()


@pytest.fixture
def mock_db():
    return MagicMock()


class TestAgingBucketClass:
    """Tests for AgingBucket dataclass."""

    def test_aging_bucket_creation(self):
        """Test creating an aging bucket."""
        bucket = AgingBucket(
            bucket_name="Current",
            min_days=0,
            max_days=30,
            amount=Decimal("1000.00"),
            invoice_count=5,
        )

        assert bucket.bucket_name == "Current"
        assert bucket.min_days == 0
        assert bucket.max_days == 30
        assert bucket.amount == Decimal("1000.00")
        assert bucket.invoice_count == 5

    def test_aging_bucket_no_max_days(self):
        """Test aging bucket with no max days (over 90)."""
        bucket = AgingBucket(
            bucket_name="Over 90 Days",
            min_days=91,
            max_days=None,
        )

        assert bucket.max_days is None

    def test_default_values(self):
        """Test aging bucket default values."""
        bucket = AgingBucket(bucket_name="Test", min_days=0, max_days=30)

        assert bucket.amount == Decimal("0")
        assert bucket.invoice_count == 0


class TestCalculateSupplierAging:
    """Tests for calculate_supplier_aging method."""

    def test_calculate_supplier_aging_all_current(self, mock_db, org_id):
        """Test aging calculation with all current invoices."""
        supplier_id = uuid4()
        supplier = MockSupplier(
            supplier_id=supplier_id,
            organization_id=org_id,
        )
        invoices = [
            MockSupplierInvoice(
                supplier_id=supplier_id,
                total_amount=Decimal("1000.00"),
                due_date=date.today() + timedelta(days=10),  # Not due yet
            ),
            MockSupplierInvoice(
                supplier_id=supplier_id,
                total_amount=Decimal("500.00"),
                due_date=date.today() - timedelta(days=15),  # 15 days overdue
            ),
        ]

        mock_db.get.return_value = supplier
        mock_db.query.return_value.filter.return_value.all.return_value = invoices

        result = APAgingService.calculate_supplier_aging(
            mock_db, org_id, supplier_id
        )

        assert result.supplier_id == supplier_id
        assert result.current == Decimal("1500.00")
        assert result.days_31_60 == Decimal("0")
        assert result.days_61_90 == Decimal("0")
        assert result.over_90 == Decimal("0")
        assert result.total_outstanding == Decimal("1500.00")
        assert result.invoice_count == 2

    def test_calculate_supplier_aging_mixed_buckets(self, mock_db, org_id):
        """Test aging calculation with invoices in different buckets."""
        supplier_id = uuid4()
        supplier = MockSupplier(
            supplier_id=supplier_id,
            organization_id=org_id,
        )

        invoices = [
            MockSupplierInvoice(
                supplier_id=supplier_id,
                total_amount=Decimal("1000.00"),
                due_date=date.today() - timedelta(days=15),  # Current (0-30)
            ),
            MockSupplierInvoice(
                supplier_id=supplier_id,
                total_amount=Decimal("2000.00"),
                due_date=date.today() - timedelta(days=45),  # 31-60 days
            ),
            MockSupplierInvoice(
                supplier_id=supplier_id,
                total_amount=Decimal("3000.00"),
                due_date=date.today() - timedelta(days=75),  # 61-90 days
            ),
            MockSupplierInvoice(
                supplier_id=supplier_id,
                total_amount=Decimal("4000.00"),
                due_date=date.today() - timedelta(days=120),  # Over 90 days
            ),
        ]

        mock_db.get.return_value = supplier
        mock_db.query.return_value.filter.return_value.all.return_value = invoices

        result = APAgingService.calculate_supplier_aging(
            mock_db, org_id, supplier_id
        )

        assert result.current == Decimal("1000.00")
        assert result.days_31_60 == Decimal("2000.00")
        assert result.days_61_90 == Decimal("3000.00")
        assert result.over_90 == Decimal("4000.00")
        assert result.total_outstanding == Decimal("10000.00")
        assert result.invoice_count == 4

    def test_calculate_supplier_aging_with_partial_payment(self, mock_db, org_id):
        """Test aging calculation with partially paid invoices."""
        supplier_id = uuid4()
        supplier = MockSupplier(
            supplier_id=supplier_id,
            organization_id=org_id,
        )
        invoices = [
            MockSupplierInvoice(
                supplier_id=supplier_id,
                total_amount=Decimal("1000.00"),
                amount_paid=Decimal("400.00"),  # Balance: 600
                due_date=date.today() - timedelta(days=15),
            ),
        ]

        mock_db.get.return_value = supplier
        mock_db.query.return_value.filter.return_value.all.return_value = invoices

        result = APAgingService.calculate_supplier_aging(
            mock_db, org_id, supplier_id
        )

        assert result.current == Decimal("600.00")
        assert result.total_outstanding == Decimal("600.00")

    def test_calculate_supplier_aging_supplier_not_found(self, mock_db, org_id):
        """Test aging calculation with non-existent supplier."""
        mock_db.get.return_value = None

        with pytest.raises(ValueError) as exc:
            APAgingService.calculate_supplier_aging(mock_db, org_id, uuid4())

        assert "Supplier not found" in str(exc.value)

    def test_calculate_supplier_aging_wrong_org(self, mock_db, org_id):
        """Test aging calculation with supplier from different org."""
        supplier = MockSupplier(organization_id=uuid4())  # Different org
        mock_db.get.return_value = supplier

        with pytest.raises(ValueError) as exc:
            APAgingService.calculate_supplier_aging(mock_db, org_id, supplier.supplier_id)

        assert "Supplier not found" in str(exc.value)

    def test_calculate_supplier_aging_with_date(self, mock_db, org_id):
        """Test aging calculation with specific as_of_date."""
        supplier_id = uuid4()
        supplier = MockSupplier(
            supplier_id=supplier_id,
            organization_id=org_id,
        )
        invoices = [
            MockSupplierInvoice(
                supplier_id=supplier_id,
                total_amount=Decimal("1000.00"),
                due_date=date(2025, 1, 1),
            ),
        ]

        mock_db.get.return_value = supplier
        mock_db.query.return_value.filter.return_value.all.return_value = invoices

        # Calculate as of Jan 31, 2025 - 30 days overdue
        result = APAgingService.calculate_supplier_aging(
            mock_db, org_id, supplier_id, as_of_date=date(2025, 1, 31)
        )

        assert result.current == Decimal("1000.00")


class TestCalculateOrganizationAging:
    """Tests for calculate_organization_aging method."""

    @patch("app.services.finance.ap.ap_aging.org_context_service")
    def test_calculate_organization_aging_success(
        self, mock_org_context, mock_db, org_id
    ):
        """Test organization-wide aging calculation."""
        supplier_id_1 = uuid4()
        supplier_id_2 = uuid4()

        invoices = [
            MockSupplierInvoice(
                supplier_id=supplier_id_1,
                total_amount=Decimal("1000.00"),
                due_date=date.today() - timedelta(days=15),
            ),
            MockSupplierInvoice(
                supplier_id=supplier_id_2,
                total_amount=Decimal("2000.00"),
                due_date=date.today() - timedelta(days=45),
            ),
        ]

        mock_db.query.return_value.filter.return_value.all.return_value = invoices
        mock_org_context.get_functional_currency.return_value = "USD"

        result = APAgingService.calculate_organization_aging(mock_db, org_id)

        assert isinstance(result, OrganizationAgingSummary)
        assert result.current == Decimal("1000.00")
        assert result.days_31_60 == Decimal("2000.00")
        assert result.total_outstanding == Decimal("3000.00")
        assert result.supplier_count == 2
        assert result.invoice_count == 2
        assert result.currency_code == "USD"

    @patch("app.services.finance.ap.ap_aging.org_context_service")
    def test_calculate_organization_aging_no_invoices(
        self, mock_org_context, mock_db, org_id
    ):
        """Test organization aging with no outstanding invoices."""
        mock_db.query.return_value.filter.return_value.all.return_value = []
        mock_org_context.get_functional_currency.return_value = "USD"

        result = APAgingService.calculate_organization_aging(mock_db, org_id)

        assert result.total_outstanding == Decimal("0")
        assert result.supplier_count == 0
        assert result.invoice_count == 0


class TestGetAgingBySupplier:
    """Tests for get_aging_by_supplier method."""

    def test_get_aging_by_supplier_success(self, mock_db, org_id):
        """Test getting aging by supplier."""
        supplier_1_id = uuid4()
        supplier_2_id = uuid4()

        supplier_1 = MockSupplier(
            supplier_id=supplier_1_id,
            organization_id=org_id,
            legal_name="Supplier A",
        )
        supplier_2 = MockSupplier(
            supplier_id=supplier_2_id,
            organization_id=org_id,
            legal_name="Supplier B",
        )

        # Setup mock for distinct supplier IDs query
        mock_distinct_result = MagicMock()
        mock_distinct_result.all.return_value = [(supplier_1_id,), (supplier_2_id,)]

        # Setup mock query chain
        mock_query = MagicMock()
        mock_query.filter.return_value.distinct.return_value = mock_distinct_result

        # For each supplier's aging calculation
        invoices_1 = [
            MockSupplierInvoice(
                supplier_id=supplier_1_id,
                total_amount=Decimal("1000.00"),
                due_date=date.today() - timedelta(days=15),
            ),
        ]
        invoices_2 = [
            MockSupplierInvoice(
                supplier_id=supplier_2_id,
                total_amount=Decimal("2000.00"),
                due_date=date.today() - timedelta(days=45),
            ),
        ]

        def mock_get(model, id):
            if id == supplier_1_id:
                return supplier_1
            elif id == supplier_2_id:
                return supplier_2
            return None

        mock_db.get.side_effect = mock_get
        mock_db.query.return_value = mock_query

        # Setup filter for each supplier's invoices
        call_count = [0]

        def mock_filter_all(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 2:
                return invoices_1
            elif call_count[0] == 3:
                return invoices_2
            return []

        mock_query.filter.return_value.all.side_effect = mock_filter_all

        result = APAgingService.get_aging_by_supplier(mock_db, org_id)

        assert len(result) == 2
        # Results should be sorted by total_outstanding descending
        assert result[0].total_outstanding >= result[1].total_outstanding

    def test_get_aging_by_supplier_with_min_balance(self, mock_db, org_id):
        """Test getting aging by supplier with minimum balance filter."""
        supplier_1_id = uuid4()
        supplier_2_id = uuid4()

        supplier_1 = MockSupplier(
            supplier_id=supplier_1_id,
            organization_id=org_id,
        )
        supplier_2 = MockSupplier(
            supplier_id=supplier_2_id,
            organization_id=org_id,
        )

        mock_distinct_result = MagicMock()
        mock_distinct_result.all.return_value = [(supplier_1_id,), (supplier_2_id,)]

        mock_query = MagicMock()
        mock_query.filter.return_value.distinct.return_value = mock_distinct_result

        invoices_1 = [
            MockSupplierInvoice(
                supplier_id=supplier_1_id,
                total_amount=Decimal("5000.00"),
                due_date=date.today(),
            ),
        ]
        invoices_2 = [
            MockSupplierInvoice(
                supplier_id=supplier_2_id,
                total_amount=Decimal("100.00"),
                due_date=date.today(),
            ),
        ]

        def mock_get(model, id):
            if id == supplier_1_id:
                return supplier_1
            elif id == supplier_2_id:
                return supplier_2
            return None

        mock_db.get.side_effect = mock_get
        mock_db.query.return_value = mock_query

        call_count = [0]

        def mock_filter_all(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 2:
                return invoices_1
            elif call_count[0] == 3:
                return invoices_2
            return []

        mock_query.filter.return_value.all.side_effect = mock_filter_all

        result = APAgingService.get_aging_by_supplier(
            mock_db, org_id, min_balance=Decimal("1000.00")
        )

        # Only supplier_1 should be included (balance >= 1000)
        assert len(result) == 1
        assert result[0].total_outstanding == Decimal("5000.00")


class TestCreateAgingSnapshot:
    """Tests for create_aging_snapshot method."""

    @patch("app.services.finance.ap.ap_aging.APAgingSnapshot")
    def test_create_aging_snapshot_success(self, mock_snapshot_class, mock_db, org_id, user_id):
        """Test creating aging snapshot."""
        fiscal_period_id = uuid4()
        supplier_id = uuid4()
        supplier = MockSupplier(
            supplier_id=supplier_id,
            organization_id=org_id,
        )
        invoices = [
            MockSupplierInvoice(
                supplier_id=supplier_id,
                total_amount=Decimal("1000.00"),
                due_date=date.today() - timedelta(days=15),
            ),
        ]

        mock_distinct_result = MagicMock()
        mock_distinct_result.all.return_value = [(supplier_id,)]

        mock_query = MagicMock()
        mock_query.filter.return_value.distinct.return_value = mock_distinct_result
        mock_query.filter.return_value.all.return_value = invoices

        mock_db.query.return_value = mock_query
        mock_db.get.return_value = supplier

        mock_snapshot_class.return_value = MockAPAgingSnapshot()

        result = APAgingService.create_aging_snapshot(
            mock_db, org_id, fiscal_period_id, created_by_user_id=user_id
        )

        mock_db.add.assert_called()
        mock_db.commit.assert_called_once()


class TestGetOverdueInvoices:
    """Tests for get_overdue_invoices method."""

    def test_get_overdue_invoices_success(self, mock_db, org_id):
        """Test getting overdue invoices."""
        invoices = [
            MockSupplierInvoice(
                total_amount=Decimal("1000.00"),
                due_date=date.today() - timedelta(days=10),
            ),
            MockSupplierInvoice(
                total_amount=Decimal("2000.00"),
                due_date=date.today() - timedelta(days=30),
            ),
        ]

        mock_query = MagicMock()
        mock_query.filter.return_value.order_by.return_value.all.return_value = invoices
        mock_db.query.return_value = mock_query

        result = APAgingService.get_overdue_invoices(mock_db, org_id)

        assert len(result) == 2

    def test_get_overdue_invoices_min_days_filter(self, mock_db, org_id):
        """Test getting overdue invoices with minimum days filter."""
        invoices = [
            MockSupplierInvoice(
                total_amount=Decimal("1000.00"),
                due_date=date.today() - timedelta(days=5),  # 5 days overdue
            ),
            MockSupplierInvoice(
                total_amount=Decimal("2000.00"),
                due_date=date.today() - timedelta(days=30),  # 30 days overdue
            ),
        ]

        mock_query = MagicMock()
        mock_query.filter.return_value.order_by.return_value.all.return_value = invoices
        mock_db.query.return_value = mock_query

        result = APAgingService.get_overdue_invoices(
            mock_db, org_id, min_days_overdue=10
        )

        # Only invoice with 30 days overdue should be included
        assert len(result) == 1
        assert result[0].total_amount == Decimal("2000.00")

    def test_get_overdue_invoices_by_supplier(self, mock_db, org_id):
        """Test getting overdue invoices for specific supplier."""
        supplier_id = uuid4()
        invoices = [
            MockSupplierInvoice(
                supplier_id=supplier_id,
                total_amount=Decimal("1000.00"),
                due_date=date.today() - timedelta(days=10),
            ),
        ]

        mock_query = MagicMock()
        mock_query.filter.return_value.filter.return_value.order_by.return_value.all.return_value = invoices
        mock_db.query.return_value = mock_query

        result = APAgingService.get_overdue_invoices(
            mock_db, org_id, supplier_id=supplier_id
        )

        assert len(result) == 1

    def test_get_overdue_invoices_empty(self, mock_db, org_id):
        """Test getting overdue invoices when none exist."""
        mock_query = MagicMock()
        mock_query.filter.return_value.order_by.return_value.all.return_value = []
        mock_db.query.return_value = mock_query

        result = APAgingService.get_overdue_invoices(mock_db, org_id)

        assert len(result) == 0


class TestListSnapshots:
    """Tests for list method."""

    def test_list_all_snapshots(self, mock_db):
        """Test listing all snapshots."""
        snapshots = [MockAPAgingSnapshot(), MockAPAgingSnapshot()]

        mock_query = MagicMock()
        mock_query.order_by.return_value.limit.return_value.offset.return_value.all.return_value = snapshots
        mock_db.query.return_value = mock_query

        result = APAgingService.list(mock_db)

        assert len(result) == 2

    def test_list_snapshots_by_organization(self, mock_db, org_id):
        """Test listing snapshots filtered by organization."""
        snapshots = [MockAPAgingSnapshot(organization_id=org_id)]

        mock_query = MagicMock()
        mock_query.filter.return_value.order_by.return_value.limit.return_value.offset.return_value.all.return_value = snapshots
        mock_db.query.return_value = mock_query

        result = APAgingService.list(mock_db, organization_id=str(org_id))

        assert len(result) == 1

    def test_list_snapshots_by_supplier(self, mock_db, org_id):
        """Test listing snapshots filtered by supplier."""
        supplier_id = uuid4()
        snapshots = [MockAPAgingSnapshot(supplier_id=supplier_id)]

        mock_query = MagicMock()
        mock_query.filter.return_value = mock_query
        mock_query.order_by.return_value.limit.return_value.offset.return_value.all.return_value = snapshots
        mock_db.query.return_value = mock_query

        result = APAgingService.list(
            mock_db, organization_id=str(org_id), supplier_id=str(supplier_id)
        )

        assert len(result) == 1

    def test_list_snapshots_by_date(self, mock_db, org_id):
        """Test listing snapshots filtered by date."""
        snapshot_date = date.today()
        snapshots = [MockAPAgingSnapshot(snapshot_date=snapshot_date)]

        mock_query = MagicMock()
        mock_query.filter.return_value = mock_query
        mock_query.order_by.return_value.limit.return_value.offset.return_value.all.return_value = snapshots
        mock_db.query.return_value = mock_query

        result = APAgingService.list(
            mock_db, organization_id=str(org_id), snapshot_date=snapshot_date
        )

        assert len(result) == 1

    def test_list_snapshots_pagination(self, mock_db):
        """Test snapshot list pagination."""
        snapshots = [MockAPAgingSnapshot()]

        mock_query = MagicMock()
        mock_query.order_by.return_value.limit.return_value.offset.return_value.all.return_value = snapshots
        mock_db.query.return_value = mock_query

        result = APAgingService.list(mock_db, limit=10, offset=20)

        mock_query.order_by.return_value.limit.assert_called_with(10)
        mock_query.order_by.return_value.limit.return_value.offset.assert_called_with(20)


class TestAgingBuckets:
    """Tests for AGING_BUCKETS constant."""

    def test_aging_buckets_defined(self):
        """Test that aging buckets are properly defined."""
        buckets = APAgingService.AGING_BUCKETS

        assert len(buckets) == 4
        assert buckets[0].bucket_name == "Current"
        assert buckets[0].min_days == 0
        assert buckets[0].max_days == 30

        assert buckets[1].bucket_name == "31-60 Days"
        assert buckets[1].min_days == 31
        assert buckets[1].max_days == 60

        assert buckets[2].bucket_name == "61-90 Days"
        assert buckets[2].min_days == 61
        assert buckets[2].max_days == 90

        assert buckets[3].bucket_name == "Over 90 Days"
        assert buckets[3].min_days == 91
        assert buckets[3].max_days is None
