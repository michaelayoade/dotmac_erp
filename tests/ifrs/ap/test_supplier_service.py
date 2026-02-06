"""
Tests for SupplierService.
"""

from decimal import Decimal
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from app.services.finance.ap.supplier import (
    SupplierService,
    SupplierInput,
)
from tests.ifrs.ap.conftest import (
    MockSupplier,
    MockSupplierInvoice,
    MockSupplierInvoiceStatus,
)


@pytest.fixture
def mock_db():
    """Create mock database session."""
    return MagicMock()


@pytest.fixture
def org_id():
    """Create test organization ID."""
    return uuid4()


@pytest.fixture
def user_id():
    """Create test user ID."""
    return uuid4()


@pytest.fixture
def sample_supplier_input():
    """Create sample supplier input."""
    from app.models.finance.ap.supplier import SupplierType

    return SupplierInput(
        supplier_code="SUP-001",
        supplier_type=SupplierType.VENDOR,
        supplier_name="Acme Corporation",
        default_payable_account_id=uuid4(),
        trading_name="Acme Corp",
        tax_id="12-3456789",
        payment_terms_days=30,
        currency_code="USD",
    )


class TestCreateSupplier:
    """Tests for create_supplier method."""

    def test_create_supplier_success(self, mock_db, org_id, sample_supplier_input):
        """Test successful supplier creation."""
        mock_query = MagicMock()
        mock_query.filter.return_value = mock_query
        mock_query.first.return_value = None  # No duplicate
        mock_db.query.return_value = mock_query

        with patch("app.services.finance.ap.supplier.Supplier") as MockSupplierClass:
            mock_supplier = MockSupplier(
                organization_id=org_id,
                supplier_code=sample_supplier_input.supplier_code,
                legal_name=sample_supplier_input.supplier_name,
            )
            MockSupplierClass.return_value = mock_supplier

            result = SupplierService.create_supplier(
                mock_db, org_id, sample_supplier_input
            )

            mock_db.add.assert_called_once()
            mock_db.commit.assert_called_once()
            mock_db.refresh.assert_called_once()

    def test_create_duplicate_supplier_code_fails(
        self, mock_db, org_id, sample_supplier_input
    ):
        """Test that duplicate supplier code fails."""
        from fastapi import HTTPException

        # Existing supplier with same code
        existing = MockSupplier(
            organization_id=org_id,
            supplier_code=sample_supplier_input.supplier_code,
        )

        mock_query = MagicMock()
        mock_query.filter.return_value = mock_query
        mock_query.first.return_value = existing
        mock_db.query.return_value = mock_query

        with patch("app.services.finance.ap.supplier.Supplier"):
            with pytest.raises(HTTPException) as exc:
                SupplierService.create_supplier(mock_db, org_id, sample_supplier_input)

        assert exc.value.status_code == 400
        assert "already exists" in exc.value.detail


class TestUpdateSupplier:
    """Tests for update_supplier method."""

    def test_update_supplier_success(self, mock_db, org_id, sample_supplier_input):
        """Test successful supplier update."""
        supplier = MockSupplier(
            organization_id=org_id,
            supplier_code="OLD-CODE",
        )
        mock_db.get.return_value = supplier

        mock_query = MagicMock()
        mock_query.filter.return_value = mock_query
        mock_query.first.return_value = None  # No duplicate
        mock_db.query.return_value = mock_query

        with (
            patch("app.services.finance.ap.supplier.Supplier"),
            patch(
                "app.services.finance.common.helpers.get_model_pk_column",
                return_value="supplier_id",
            ),
        ):
            result = SupplierService.update_supplier(
                mock_db, org_id, supplier.supplier_id, sample_supplier_input
            )

        mock_db.commit.assert_called()
        assert result.supplier_code == sample_supplier_input.supplier_code

    def test_update_nonexistent_supplier_fails(
        self, mock_db, org_id, sample_supplier_input
    ):
        """Test updating non-existent supplier fails."""
        from fastapi import HTTPException

        mock_db.get.return_value = None

        with patch("app.services.finance.ap.supplier.Supplier"):
            with pytest.raises(HTTPException) as exc:
                SupplierService.update_supplier(
                    mock_db, org_id, uuid4(), sample_supplier_input
                )

        assert exc.value.status_code == 404

    def test_update_wrong_organization_fails(
        self, mock_db, org_id, sample_supplier_input
    ):
        """Test updating supplier from wrong organization fails."""
        from fastapi import HTTPException

        supplier = MockSupplier(
            organization_id=uuid4(),  # Different org
            supplier_code="OLD-CODE",
        )
        mock_db.get.return_value = supplier

        with patch("app.services.finance.ap.supplier.Supplier"):
            with pytest.raises(HTTPException) as exc:
                SupplierService.update_supplier(
                    mock_db, org_id, supplier.supplier_id, sample_supplier_input
                )

        assert exc.value.status_code == 404


class TestDeactivateSupplier:
    """Tests for deactivate_supplier method."""

    def test_deactivate_supplier_success(self, mock_db, org_id):
        """Test successful supplier deactivation."""
        supplier = MockSupplier(organization_id=org_id, is_active=True)
        mock_db.get.return_value = supplier
        # Mock db.scalar for the outstanding balance check (uses select() now)
        mock_db.scalar.return_value = Decimal("0")

        result = SupplierService.deactivate_supplier(
            mock_db, org_id, supplier.supplier_id
        )

        assert result.is_active is False
        mock_db.commit.assert_called()

    def test_deactivate_nonexistent_supplier_fails(self, mock_db, org_id):
        """Test deactivating non-existent supplier fails."""
        from fastapi import HTTPException

        mock_db.get.return_value = None

        with patch("app.services.finance.ap.supplier.Supplier"):
            with pytest.raises(HTTPException) as exc:
                SupplierService.deactivate_supplier(mock_db, org_id, uuid4())

        assert exc.value.status_code == 404


class TestActivateSupplier:
    """Tests for activate_supplier method."""

    def test_activate_supplier_success(self, mock_db, org_id):
        """Test successful supplier activation."""
        supplier = MockSupplier(organization_id=org_id, is_active=False)
        mock_db.get.return_value = supplier

        with patch("app.services.finance.ap.supplier.Supplier"):
            result = SupplierService.activate_supplier(
                mock_db, org_id, supplier.supplier_id
            )

        assert result.is_active is True
        mock_db.commit.assert_called()


class TestGetSupplier:
    """Tests for get method."""

    def test_get_existing_supplier(self, mock_db, org_id):
        """Test getting existing supplier."""
        supplier = MockSupplier(organization_id=org_id)
        mock_db.get.return_value = supplier

        with patch("app.services.finance.ap.supplier.Supplier"):
            result = SupplierService.get(mock_db, org_id, str(supplier.supplier_id))

        assert result == supplier

    def test_get_nonexistent_supplier_raises(self, mock_db, org_id):
        """Test getting non-existent supplier raises exception."""
        from fastapi import HTTPException

        mock_db.get.return_value = None

        with patch("app.services.finance.ap.supplier.Supplier"):
            with pytest.raises(HTTPException) as exc:
                SupplierService.get(mock_db, org_id, str(uuid4()))

        assert exc.value.status_code == 404


class TestGetSupplierByCode:
    """Tests for get_by_code method."""

    def test_get_supplier_by_code(self, mock_db, org_id):
        """Test getting supplier by code."""
        supplier = MockSupplier(
            organization_id=org_id,
            supplier_code="SUP-001",
        )
        # get_by_code now uses db.scalars(select(...)).first()
        mock_db.scalars.return_value.first.return_value = supplier

        result = SupplierService.get_by_code(mock_db, org_id, "SUP-001")

        assert result == supplier

    def test_get_supplier_by_code_not_found(self, mock_db, org_id):
        """Test getting non-existent supplier by code returns None."""
        mock_db.scalars.return_value.first.return_value = None

        result = SupplierService.get_by_code(mock_db, org_id, "NOTFOUND")

        assert result is None


class TestListSuppliers:
    """Tests for list method."""

    def test_list_with_filters(self, mock_db, org_id):
        """Test listing suppliers with filters."""
        suppliers = [MockSupplier(organization_id=org_id)]
        mock_query = MagicMock()
        mock_query.filter.return_value = mock_query
        mock_query.order_by.return_value = mock_query
        mock_query.limit.return_value = mock_query
        mock_query.offset.return_value = mock_query
        mock_query.all.return_value = suppliers
        mock_db.query.return_value = mock_query

        with patch("app.services.finance.ap.supplier.Supplier"):
            result = SupplierService.list(
                mock_db,
                organization_id=str(org_id),
                is_active=True,
            )

        assert result == suppliers

    def test_list_with_search(self, mock_db, org_id):
        """Test listing suppliers with search."""
        suppliers = [MockSupplier(organization_id=org_id, legal_name="Acme Corp")]
        mock_query = MagicMock()
        mock_query.filter.return_value = mock_query
        mock_query.order_by.return_value = mock_query
        mock_query.limit.return_value = mock_query
        mock_query.offset.return_value = mock_query
        mock_query.all.return_value = suppliers
        mock_db.query.return_value = mock_query

        with (
            patch("app.services.finance.ap.supplier.Supplier"),
            patch(
                "app.services.finance.ap.supplier.apply_search_filter",
                return_value=mock_query,
            ),
        ):
            result = SupplierService.list(
                mock_db,
                organization_id=str(org_id),
                search="Acme",
            )

        assert result == suppliers


class TestGetSupplierSummary:
    """Tests for get_supplier_summary method."""

    def test_get_supplier_summary(self, mock_db, org_id):
        """Test getting supplier summary with balance info."""
        supplier = MockSupplier(organization_id=org_id)
        mock_db.get.return_value = supplier

        # Mock outstanding invoices
        invoice1 = MockSupplierInvoice(
            supplier_id=supplier.supplier_id,
            total_amount=Decimal("1000.00"),
            amount_paid=Decimal("0"),
            status=MockSupplierInvoiceStatus.POSTED,
        )
        invoice2 = MockSupplierInvoice(
            supplier_id=supplier.supplier_id,
            total_amount=Decimal("500.00"),
            amount_paid=Decimal("200.00"),
            status=MockSupplierInvoiceStatus.PARTIALLY_PAID,
        )

        # get_supplier_summary now uses db.scalars(select(...)).all()
        mock_db.scalars.return_value.all.return_value = [invoice1, invoice2]

        result = SupplierService.get_supplier_summary(
            mock_db, org_id, supplier.supplier_id
        )

        assert result["supplier_id"] == supplier.supplier_id
        assert result["outstanding_invoice_count"] == 2
        # balance_due = 1000 + 300 = 1300
        assert result["outstanding_balance"] == Decimal("1300.00")

    def test_get_supplier_summary_not_found(self, mock_db, org_id):
        """Test getting summary for non-existent supplier."""
        from fastapi import HTTPException

        mock_db.get.return_value = None

        with patch("app.services.finance.ap.supplier.Supplier"):
            with pytest.raises(HTTPException) as exc:
                SupplierService.get_supplier_summary(mock_db, org_id, uuid4())

        assert exc.value.status_code == 404
