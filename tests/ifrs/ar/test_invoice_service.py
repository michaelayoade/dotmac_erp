"""
Tests for ARInvoiceService.
"""

from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from tests.ifrs.ar.conftest import (
    MockCustomer,
    MockInvoice,
    MockInvoiceLine,
    MockInvoiceStatus,
    MockInvoiceType,
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


class TestCreateInvoice:
    """Tests for create_invoice method."""

    def test_create_invoice_success(self, mock_db, org_id, user_id):
        """Test successful invoice creation."""
        from app.services.ifrs.ar.invoice import (
            ARInvoiceService,
            ARInvoiceInput,
            ARInvoiceLineInput,
        )
        from app.models.ifrs.ar.invoice import InvoiceType

        customer = MockCustomer(organization_id=org_id)
        mock_db.get.return_value = customer

        mock_query = MagicMock()
        mock_query.filter.return_value = mock_query
        mock_query.first.return_value = None  # No duplicate invoice
        mock_db.query.return_value = mock_query

        lines = [
            ARInvoiceLineInput(
                description="Consulting Services",
                quantity=Decimal("10"),
                unit_price=Decimal("100.00"),
                revenue_account_id=uuid4(),
            ),
        ]

        invoice_input = ARInvoiceInput(
            customer_id=customer.customer_id,
            invoice_type=InvoiceType.STANDARD,
            invoice_date=date.today(),
            due_date=date.today() + timedelta(days=30),
            currency_code="USD",
            lines=lines,
        )

        with patch("app.services.ifrs.ar.invoice.Customer"):
            with patch("app.services.ifrs.ar.invoice.Invoice") as MockInv:
                mock_invoice = MockInvoice(
                    organization_id=org_id,
                    customer_id=customer.customer_id,
                )
                MockInv.return_value = mock_invoice

                with patch("app.services.ifrs.ar.invoice.InvoiceLine"):
                    with patch("app.services.ifrs.ar.invoice.SequenceService.get_next_number", return_value="INV-0001"):
                        result = ARInvoiceService.create_invoice(
                            mock_db, org_id, invoice_input, user_id
                        )

        mock_db.add.assert_called()
        mock_db.commit.assert_called()

    def test_create_invoice_invalid_customer_fails(self, mock_db, org_id, user_id):
        """Test that invalid customer fails validation."""
        from fastapi import HTTPException
        from app.services.ifrs.ar.invoice import (
            ARInvoiceService,
            ARInvoiceInput,
            ARInvoiceLineInput,
        )
        from app.models.ifrs.ar.invoice import InvoiceType

        mock_db.get.return_value = None  # Customer not found

        lines = [
            ARInvoiceLineInput(
                description="Test",
                quantity=Decimal("1"),
                unit_price=Decimal("100.00"),
                revenue_account_id=uuid4(),
            ),
        ]

        invoice_input = ARInvoiceInput(
            customer_id=uuid4(),
            invoice_type=InvoiceType.STANDARD,
            invoice_date=date.today(),
            due_date=date.today() + timedelta(days=30),
            currency_code="USD",
            lines=lines,
        )

        with patch("app.services.ifrs.ar.invoice.Customer"):
            with pytest.raises(HTTPException) as exc:
                ARInvoiceService.create_invoice(
                    mock_db, org_id, invoice_input, user_id
                )

        assert exc.value.status_code == 404

    def test_create_invoice_empty_lines_fails(self, mock_db, org_id, user_id):
        """Test that invoice with no lines fails."""
        from fastapi import HTTPException
        from app.services.ifrs.ar.invoice import (
            ARInvoiceService,
            ARInvoiceInput,
        )
        from app.models.ifrs.ar.invoice import InvoiceType

        customer = MockCustomer(organization_id=org_id)
        mock_db.get.return_value = customer

        invoice_input = ARInvoiceInput(
            customer_id=customer.customer_id,
            invoice_type=InvoiceType.STANDARD,
            invoice_date=date.today(),
            due_date=date.today() + timedelta(days=30),
            currency_code="USD",
            lines=[],
        )

        with patch("app.services.ifrs.ar.invoice.Customer"):
            with pytest.raises(HTTPException) as exc:
                ARInvoiceService.create_invoice(
                    mock_db, org_id, invoice_input, user_id
                )

        assert exc.value.status_code == 400


class TestSubmitInvoice:
    """Tests for submit_invoice method."""

    def test_submit_draft_invoice(self, mock_db, org_id, user_id):
        """Test submitting a draft invoice."""
        from app.services.ifrs.ar.invoice import ARInvoiceService
        from app.models.ifrs.ar.invoice import InvoiceStatus

        invoice = MockInvoice(
            organization_id=org_id,
            status=InvoiceStatus.DRAFT,
        )
        mock_db.get.return_value = invoice

        with patch("app.services.ifrs.ar.invoice.Invoice"):
            result = ARInvoiceService.submit_invoice(
                mock_db, org_id, invoice.invoice_id, user_id
            )

        assert result.status == InvoiceStatus.SUBMITTED
        mock_db.commit.assert_called()

    def test_submit_non_draft_fails(self, mock_db, org_id, user_id):
        """Test submitting non-draft invoice fails."""
        from fastapi import HTTPException
        from app.services.ifrs.ar.invoice import ARInvoiceService
        from app.models.ifrs.ar.invoice import InvoiceStatus

        invoice = MockInvoice(
            organization_id=org_id,
            status=InvoiceStatus.APPROVED,
        )
        mock_db.get.return_value = invoice

        with patch("app.services.ifrs.ar.invoice.Invoice"):
            with pytest.raises(HTTPException) as exc:
                ARInvoiceService.submit_invoice(
                    mock_db, org_id, invoice.invoice_id, user_id
                )

        assert exc.value.status_code == 400


class TestApproveInvoice:
    """Tests for approve_invoice method."""

    def test_approve_submitted_invoice(self, mock_db, org_id, user_id):
        """Test approving a submitted invoice."""
        from app.services.ifrs.ar.invoice import ARInvoiceService
        from app.models.ifrs.ar.invoice import InvoiceStatus

        submitter_id = uuid4()
        invoice = MockInvoice(
            organization_id=org_id,
            status=InvoiceStatus.SUBMITTED,
            submitted_by_user_id=submitter_id,
        )
        mock_db.get.return_value = invoice

        with patch("app.services.ifrs.ar.invoice.Invoice"):
            result = ARInvoiceService.approve_invoice(
                mock_db, org_id, invoice.invoice_id, user_id
            )

        assert result.status == InvoiceStatus.APPROVED
        mock_db.commit.assert_called()

    def test_self_approval_fails_sod(self, mock_db, org_id):
        """Test that self-approval fails segregation of duties."""
        from fastapi import HTTPException
        from app.services.ifrs.ar.invoice import ARInvoiceService
        from app.models.ifrs.ar.invoice import InvoiceStatus

        submitter_id = uuid4()
        invoice = MockInvoice(
            organization_id=org_id,
            status=InvoiceStatus.SUBMITTED,
            submitted_by_user_id=submitter_id,
        )
        mock_db.get.return_value = invoice

        with patch("app.services.ifrs.ar.invoice.Invoice"):
            with pytest.raises(HTTPException) as exc:
                # Same user tries to approve
                ARInvoiceService.approve_invoice(
                    mock_db, org_id, invoice.invoice_id, submitter_id
                )

        assert exc.value.status_code == 400
        assert "segregation" in exc.value.detail.lower() or "same user" in exc.value.detail.lower()


class TestVoidInvoice:
    """Tests for void_invoice method."""

    def test_void_draft_invoice(self, mock_db, org_id, user_id):
        """Test voiding a draft invoice."""
        from app.services.ifrs.ar.invoice import ARInvoiceService
        from app.models.ifrs.ar.invoice import InvoiceStatus

        invoice = MockInvoice(
            organization_id=org_id,
            status=InvoiceStatus.DRAFT,
        )
        mock_db.get.return_value = invoice

        with patch("app.services.ifrs.ar.invoice.Invoice"):
            result = ARInvoiceService.void_invoice(
                mock_db, org_id, invoice.invoice_id, user_id, "Not needed"
            )

        assert result.status == InvoiceStatus.VOID
        mock_db.commit.assert_called()

    def test_void_paid_invoice_fails(self, mock_db, org_id, user_id):
        """Test that voiding paid invoice fails."""
        from fastapi import HTTPException
        from app.services.ifrs.ar.invoice import ARInvoiceService
        from app.models.ifrs.ar.invoice import InvoiceStatus

        invoice = MockInvoice(
            organization_id=org_id,
            status=InvoiceStatus.PAID,
        )
        mock_db.get.return_value = invoice

        with patch("app.services.ifrs.ar.invoice.Invoice"):
            with pytest.raises(HTTPException) as exc:
                ARInvoiceService.void_invoice(
                    mock_db, org_id, invoice.invoice_id, user_id, "Mistake"
                )

        assert exc.value.status_code == 400


class TestGetInvoice:
    """Tests for get method."""

    def test_get_existing_invoice(self, mock_db, org_id):
        """Test getting existing invoice."""
        from app.services.ifrs.ar.invoice import ARInvoiceService

        invoice = MockInvoice(organization_id=org_id)
        mock_db.get.return_value = invoice

        with patch("app.services.ifrs.ar.invoice.Invoice"):
            result = ARInvoiceService.get(mock_db, str(invoice.invoice_id))

        assert result == invoice

    def test_get_nonexistent_raises(self, mock_db):
        """Test getting non-existent invoice raises exception."""
        from fastapi import HTTPException
        from app.services.ifrs.ar.invoice import ARInvoiceService

        mock_db.get.return_value = None

        with patch("app.services.ifrs.ar.invoice.Invoice"):
            with pytest.raises(HTTPException) as exc:
                ARInvoiceService.get(mock_db, str(uuid4()))

        assert exc.value.status_code == 404


class TestListInvoices:
    """Tests for list method."""

    def test_list_with_filters(self, mock_db, org_id):
        """Test listing invoices with filters."""
        from app.services.ifrs.ar.invoice import ARInvoiceService
        from app.models.ifrs.ar.invoice import InvoiceStatus

        invoices = [MockInvoice(organization_id=org_id)]
        mock_query = MagicMock()
        mock_query.filter.return_value = mock_query
        mock_query.order_by.return_value = mock_query
        mock_query.limit.return_value = mock_query
        mock_query.offset.return_value = mock_query
        mock_query.all.return_value = invoices
        mock_db.query.return_value = mock_query

        with patch("app.services.ifrs.ar.invoice.Invoice"):
            result = ARInvoiceService.list(
                mock_db,
                organization_id=str(org_id),
                status=InvoiceStatus.DRAFT,
            )

        assert result == invoices


class TestGetInvoiceLines:
    """Tests for get_invoice_lines method."""

    def test_get_invoice_lines(self, mock_db, org_id):
        """Test getting lines for an invoice."""
        from app.services.ifrs.ar.invoice import ARInvoiceService

        invoice_id = uuid4()
        invoice = MockInvoice(
            invoice_id=invoice_id,
            organization_id=org_id,
        )
        lines = [
            MockInvoiceLine(invoice_id=invoice_id),
            MockInvoiceLine(invoice_id=invoice_id, line_number=2),
        ]

        # Mock db.get to find the invoice
        mock_db.get.return_value = invoice
        # Mock the query for lines
        mock_db.query.return_value.filter.return_value.order_by.return_value.all.return_value = lines

        with patch("app.services.ifrs.ar.invoice.Invoice"):
            with patch("app.services.ifrs.ar.invoice.InvoiceLine"):
                result = ARInvoiceService.get_invoice_lines(mock_db, org_id, invoice_id)

        assert len(result) == 2
