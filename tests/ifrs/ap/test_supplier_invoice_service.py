"""
Tests for SupplierInvoiceService.
"""

from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from tests.ifrs.ap.conftest import (
    MockSupplier,
    MockSupplierInvoice,
    MockSupplierInvoiceLine,
    MockSupplierInvoiceStatus,
    MockSupplierInvoiceType,
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


class TestCreateSupplierInvoice:
    """Tests for create_invoice method."""

    def test_create_invoice_success(self, mock_db, org_id, user_id):
        """Test successful invoice creation."""
        from app.services.ifrs.ap.supplier_invoice import (
            SupplierInvoiceService,
            SupplierInvoiceInput,
            InvoiceLineInput,
        )
        from app.models.ifrs.ap.supplier_invoice import SupplierInvoiceType

        supplier = MockSupplier(organization_id=org_id)
        mock_db.get.return_value = supplier

        mock_query = MagicMock()
        mock_query.filter.return_value = mock_query
        mock_query.first.return_value = None  # No duplicate invoice
        mock_db.query.return_value = mock_query

        lines = [
            InvoiceLineInput(
                description="Office Supplies",
                quantity=Decimal("10"),
                unit_price=Decimal("100.00"),
                expense_account_id=uuid4(),
            ),
        ]

        invoice_input = SupplierInvoiceInput(
            supplier_id=supplier.supplier_id,
            invoice_type=SupplierInvoiceType.STANDARD,
            supplier_invoice_number="SUP-INV-001",
            invoice_date=date.today(),
            received_date=date.today(),
            due_date=date.today() + timedelta(days=30),
            currency_code="USD",
            lines=lines,
        )

        with patch("app.services.ifrs.ap.supplier_invoice.Supplier"):
            with patch("app.services.ifrs.ap.supplier_invoice.SupplierInvoice") as MockInv:
                mock_invoice = MockSupplierInvoice(
                    organization_id=org_id,
                    supplier_id=supplier.supplier_id,
                )
                MockInv.return_value = mock_invoice

                with patch("app.services.ifrs.ap.supplier_invoice.SupplierInvoiceLine"):
                    with patch("app.services.ifrs.ap.supplier_invoice.SequenceService.get_next_number", return_value="APINV-0001"):
                        result = SupplierInvoiceService.create_invoice(
                            mock_db, org_id, invoice_input, user_id
                        )

        mock_db.add.assert_called()
        mock_db.commit.assert_called()

    def test_create_invoice_invalid_supplier_fails(self, mock_db, org_id, user_id):
        """Test that invalid supplier fails validation."""
        from fastapi import HTTPException
        from app.services.ifrs.ap.supplier_invoice import (
            SupplierInvoiceService,
            SupplierInvoiceInput,
            InvoiceLineInput,
        )
        from app.models.ifrs.ap.supplier_invoice import SupplierInvoiceType

        mock_db.get.return_value = None  # Supplier not found

        lines = [
            InvoiceLineInput(
                description="Test",
                quantity=Decimal("1"),
                unit_price=Decimal("100.00"),
                expense_account_id=uuid4(),
            ),
        ]

        invoice_input = SupplierInvoiceInput(
            supplier_id=uuid4(),
            invoice_type=SupplierInvoiceType.STANDARD,
            invoice_date=date.today(),
            received_date=date.today(),
            due_date=date.today() + timedelta(days=30),
            currency_code="USD",
            lines=lines,
        )

        with patch("app.services.ifrs.ap.supplier_invoice.Supplier"):
            with pytest.raises(HTTPException) as exc:
                SupplierInvoiceService.create_invoice(
                    mock_db, org_id, invoice_input, user_id
                )

        assert exc.value.status_code == 404

    def test_create_invoice_empty_lines_fails(self, mock_db, org_id, user_id):
        """Test that invoice with no lines fails."""
        from fastapi import HTTPException
        from app.services.ifrs.ap.supplier_invoice import (
            SupplierInvoiceService,
            SupplierInvoiceInput,
        )
        from app.models.ifrs.ap.supplier_invoice import SupplierInvoiceType

        supplier = MockSupplier(organization_id=org_id)
        mock_db.get.return_value = supplier

        invoice_input = SupplierInvoiceInput(
            supplier_id=supplier.supplier_id,
            invoice_type=SupplierInvoiceType.STANDARD,
            invoice_date=date.today(),
            received_date=date.today(),
            due_date=date.today() + timedelta(days=30),
            currency_code="USD",
            lines=[],
        )

        with patch("app.services.ifrs.ap.supplier_invoice.Supplier"):
            with pytest.raises(HTTPException) as exc:
                SupplierInvoiceService.create_invoice(
                    mock_db, org_id, invoice_input, user_id
                )

        assert exc.value.status_code == 400


class TestSubmitSupplierInvoice:
    """Tests for submit_invoice method."""

    def test_submit_draft_invoice(self, mock_db, org_id, user_id):
        """Test submitting a draft invoice."""
        from app.services.ifrs.ap.supplier_invoice import SupplierInvoiceService

        invoice = MockSupplierInvoice(
            organization_id=org_id,
            status=MockSupplierInvoiceStatus.DRAFT,
        )
        mock_db.get.return_value = invoice

        with patch("app.services.ifrs.ap.supplier_invoice.SupplierInvoice"):
            with patch("app.services.ifrs.ap.supplier_invoice.SupplierInvoiceStatus", MockSupplierInvoiceStatus):
                result = SupplierInvoiceService.submit_invoice(
                    mock_db, org_id, invoice.invoice_id, user_id
                )

        assert result.status == MockSupplierInvoiceStatus.SUBMITTED
        mock_db.commit.assert_called()

    def test_submit_non_draft_fails(self, mock_db, org_id, user_id):
        """Test submitting non-draft invoice fails."""
        from fastapi import HTTPException
        from app.services.ifrs.ap.supplier_invoice import SupplierInvoiceService

        invoice = MockSupplierInvoice(
            organization_id=org_id,
            status=MockSupplierInvoiceStatus.APPROVED,
        )
        mock_db.get.return_value = invoice

        with patch("app.services.ifrs.ap.supplier_invoice.SupplierInvoice"):
            with patch("app.services.ifrs.ap.supplier_invoice.SupplierInvoiceStatus", MockSupplierInvoiceStatus):
                with pytest.raises(HTTPException) as exc:
                    SupplierInvoiceService.submit_invoice(
                        mock_db, org_id, invoice.invoice_id, user_id
                    )

        assert exc.value.status_code == 400


class TestApproveSupplierInvoice:
    """Tests for approve_invoice method."""

    def test_approve_submitted_invoice(self, mock_db, org_id, user_id):
        """Test approving a submitted invoice."""
        from app.services.ifrs.ap.supplier_invoice import SupplierInvoiceService

        submitter_id = uuid4()
        invoice = MockSupplierInvoice(
            organization_id=org_id,
            status=MockSupplierInvoiceStatus.SUBMITTED,
            submitted_by_user_id=submitter_id,
        )
        mock_db.get.return_value = invoice

        with patch("app.services.ifrs.ap.supplier_invoice.SupplierInvoice"):
            with patch("app.services.ifrs.ap.supplier_invoice.SupplierInvoiceStatus", MockSupplierInvoiceStatus):
                result = SupplierInvoiceService.approve_invoice(
                    mock_db, org_id, invoice.invoice_id, user_id
                )

        assert result.status == MockSupplierInvoiceStatus.APPROVED
        mock_db.commit.assert_called()

    def test_self_approval_fails_sod(self, mock_db, org_id):
        """Test that self-approval fails segregation of duties."""
        from fastapi import HTTPException
        from app.services.ifrs.ap.supplier_invoice import SupplierInvoiceService

        submitter_id = uuid4()
        invoice = MockSupplierInvoice(
            organization_id=org_id,
            status=MockSupplierInvoiceStatus.SUBMITTED,
            submitted_by_user_id=submitter_id,
        )
        mock_db.get.return_value = invoice

        with patch("app.services.ifrs.ap.supplier_invoice.SupplierInvoice"):
            with patch("app.services.ifrs.ap.supplier_invoice.SupplierInvoiceStatus", MockSupplierInvoiceStatus):
                with pytest.raises(HTTPException) as exc:
                    # Same user tries to approve
                    SupplierInvoiceService.approve_invoice(
                        mock_db, org_id, invoice.invoice_id, submitter_id
                    )

        assert exc.value.status_code == 400
        assert "segregation" in exc.value.detail.lower() or "same user" in exc.value.detail.lower()


class TestVoidSupplierInvoice:
    """Tests for void_invoice method."""

    def test_void_draft_invoice(self, mock_db, org_id, user_id):
        """Test voiding a draft invoice."""
        from app.services.ifrs.ap.supplier_invoice import SupplierInvoiceService

        invoice = MockSupplierInvoice(
            organization_id=org_id,
            status=MockSupplierInvoiceStatus.DRAFT,
        )
        mock_db.get.return_value = invoice

        with patch("app.services.ifrs.ap.supplier_invoice.SupplierInvoice"):
            with patch("app.services.ifrs.ap.supplier_invoice.SupplierInvoiceStatus", MockSupplierInvoiceStatus):
                result = SupplierInvoiceService.void_invoice(
                    mock_db, org_id, invoice.invoice_id, user_id, "Not needed"
                )

        assert result.status == MockSupplierInvoiceStatus.VOID
        mock_db.commit.assert_called()

    def test_void_paid_invoice_fails(self, mock_db, org_id, user_id):
        """Test that voiding paid invoice fails."""
        from fastapi import HTTPException
        from app.services.ifrs.ap.supplier_invoice import SupplierInvoiceService

        invoice = MockSupplierInvoice(
            organization_id=org_id,
            status=MockSupplierInvoiceStatus.PAID,
        )
        mock_db.get.return_value = invoice

        with patch("app.services.ifrs.ap.supplier_invoice.SupplierInvoice"):
            with patch("app.services.ifrs.ap.supplier_invoice.SupplierInvoiceStatus", MockSupplierInvoiceStatus):
                with pytest.raises(HTTPException) as exc:
                    SupplierInvoiceService.void_invoice(
                        mock_db, org_id, invoice.invoice_id, user_id, "Mistake"
                    )

        assert exc.value.status_code == 400


class TestGetSupplierInvoice:
    """Tests for get method."""

    def test_get_existing_invoice(self, mock_db, org_id):
        """Test getting existing invoice."""
        from app.services.ifrs.ap.supplier_invoice import SupplierInvoiceService

        invoice = MockSupplierInvoice(organization_id=org_id)
        mock_db.get.return_value = invoice

        with patch("app.services.ifrs.ap.supplier_invoice.SupplierInvoice"):
            result = SupplierInvoiceService.get(mock_db, str(invoice.invoice_id))

        assert result == invoice

    def test_get_nonexistent_raises(self, mock_db):
        """Test getting non-existent invoice raises exception."""
        from fastapi import HTTPException
        from app.services.ifrs.ap.supplier_invoice import SupplierInvoiceService

        mock_db.get.return_value = None

        with patch("app.services.ifrs.ap.supplier_invoice.SupplierInvoice"):
            with pytest.raises(HTTPException) as exc:
                SupplierInvoiceService.get(mock_db, str(uuid4()))

        assert exc.value.status_code == 404


class TestListSupplierInvoices:
    """Tests for list method."""

    def test_list_with_filters(self, mock_db, org_id):
        """Test listing invoices with filters."""
        from app.services.ifrs.ap.supplier_invoice import SupplierInvoiceService

        invoices = [MockSupplierInvoice(organization_id=org_id)]
        mock_query = MagicMock()
        mock_query.filter.return_value = mock_query
        mock_query.order_by.return_value = mock_query
        mock_query.limit.return_value = mock_query
        mock_query.offset.return_value = mock_query
        mock_query.all.return_value = invoices
        mock_db.query.return_value = mock_query

        with patch("app.services.ifrs.ap.supplier_invoice.SupplierInvoice"):
            with patch("app.services.ifrs.ap.supplier_invoice.SupplierInvoiceStatus", MockSupplierInvoiceStatus):
                result = SupplierInvoiceService.list(
                    mock_db,
                    organization_id=str(org_id),
                    status=MockSupplierInvoiceStatus.DRAFT,
                )

        assert result == invoices


class TestGetInvoiceLines:
    """Tests for get_invoice_lines method."""

    def test_get_invoice_lines(self, mock_db, org_id):
        """Test getting lines for an invoice."""
        from app.services.ifrs.ap.supplier_invoice import SupplierInvoiceService

        invoice_id = uuid4()
        invoice = MockSupplierInvoice(
            invoice_id=invoice_id,
            organization_id=org_id,
        )
        lines = [
            MockSupplierInvoiceLine(invoice_id=invoice_id),
            MockSupplierInvoiceLine(invoice_id=invoice_id, line_number=2),
        ]

        # Mock db.get to find the invoice
        mock_db.get.return_value = invoice
        # Mock the query for lines
        mock_db.query.return_value.filter.return_value.order_by.return_value.all.return_value = lines

        with patch("app.services.ifrs.ap.supplier_invoice.SupplierInvoice"):
            with patch("app.services.ifrs.ap.supplier_invoice.SupplierInvoiceLine"):
                result = SupplierInvoiceService.get_invoice_lines(mock_db, org_id, invoice_id)

        assert len(result) == 2
