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


class TestUpdateSupplierInvoice:
    """Tests for update_invoice method."""

    def test_update_draft_invoice(self, mock_db, org_id):
        """Test updating a draft invoice."""
        from app.services.ifrs.ap.supplier_invoice import (
            SupplierInvoiceService,
            SupplierInvoiceInput,
            InvoiceLineInput,
        )
        from app.models.ifrs.ap.supplier_invoice import SupplierInvoiceType

        invoice = MockSupplierInvoice(
            organization_id=org_id,
            status=MockSupplierInvoiceStatus.DRAFT,
        )
        supplier = MockSupplier(organization_id=org_id, supplier_id=invoice.supplier_id)

        mock_db.get.side_effect = [invoice, supplier]

        mock_query = MagicMock()
        mock_query.filter.return_value = mock_query
        mock_query.delete.return_value = 1
        mock_db.query.return_value = mock_query

        lines = [
            InvoiceLineInput(
                description="Updated line",
                quantity=Decimal("5"),
                unit_price=Decimal("200.00"),
                expense_account_id=uuid4(),
            ),
        ]

        invoice_input = SupplierInvoiceInput(
            supplier_id=invoice.supplier_id,
            invoice_type=SupplierInvoiceType.STANDARD,
            invoice_date=date.today(),
            received_date=date.today(),
            due_date=date.today() + timedelta(days=45),
            currency_code="USD",
            lines=lines,
        )

        with patch("app.services.ifrs.ap.supplier_invoice.SupplierInvoice"):
            with patch("app.services.ifrs.ap.supplier_invoice.SupplierInvoiceLine"):
                with patch("app.services.ifrs.ap.supplier_invoice.SupplierInvoiceStatus", MockSupplierInvoiceStatus):
                    result = SupplierInvoiceService.update_invoice(
                        mock_db, org_id, invoice.invoice_id, invoice_input
                    )

        mock_db.commit.assert_called()

    def test_update_non_draft_fails(self, mock_db, org_id):
        """Test that updating non-draft invoice fails."""
        from fastapi import HTTPException
        from app.services.ifrs.ap.supplier_invoice import (
            SupplierInvoiceService,
            SupplierInvoiceInput,
            InvoiceLineInput,
        )
        from app.models.ifrs.ap.supplier_invoice import SupplierInvoiceType

        invoice = MockSupplierInvoice(
            organization_id=org_id,
            status=MockSupplierInvoiceStatus.APPROVED,
        )
        mock_db.get.return_value = invoice

        invoice_input = SupplierInvoiceInput(
            supplier_id=uuid4(),
            invoice_type=SupplierInvoiceType.STANDARD,
            invoice_date=date.today(),
            received_date=date.today(),
            due_date=date.today() + timedelta(days=30),
            currency_code="USD",
            lines=[InvoiceLineInput(description="Test", quantity=Decimal("1"), unit_price=Decimal("100"))],
        )

        with patch("app.services.ifrs.ap.supplier_invoice.SupplierInvoice"):
            with patch("app.services.ifrs.ap.supplier_invoice.SupplierInvoiceStatus", MockSupplierInvoiceStatus):
                with pytest.raises(HTTPException) as exc:
                    SupplierInvoiceService.update_invoice(
                        mock_db, org_id, invoice.invoice_id, invoice_input
                    )

        assert exc.value.status_code == 400

    def test_update_invoice_not_found(self, mock_db, org_id):
        """Test updating non-existent invoice."""
        from fastapi import HTTPException
        from app.services.ifrs.ap.supplier_invoice import (
            SupplierInvoiceService,
            SupplierInvoiceInput,
            InvoiceLineInput,
        )
        from app.models.ifrs.ap.supplier_invoice import SupplierInvoiceType

        mock_db.get.return_value = None

        invoice_input = SupplierInvoiceInput(
            supplier_id=uuid4(),
            invoice_type=SupplierInvoiceType.STANDARD,
            invoice_date=date.today(),
            received_date=date.today(),
            due_date=date.today() + timedelta(days=30),
            currency_code="USD",
            lines=[InvoiceLineInput(description="Test", quantity=Decimal("1"), unit_price=Decimal("100"))],
        )

        with patch("app.services.ifrs.ap.supplier_invoice.SupplierInvoice"):
            with pytest.raises(HTTPException) as exc:
                SupplierInvoiceService.update_invoice(
                    mock_db, org_id, uuid4(), invoice_input
                )

        assert exc.value.status_code == 404


class TestPostSupplierInvoice:
    """Tests for post_invoice method."""

    def test_post_approved_invoice(self, mock_db, org_id, user_id):
        """Test posting an approved invoice."""
        from app.services.ifrs.ap.supplier_invoice import SupplierInvoiceService

        invoice = MockSupplierInvoice(
            organization_id=org_id,
            status=MockSupplierInvoiceStatus.APPROVED,
        )
        mock_db.get.return_value = invoice

        # Mock the posting adapter result
        mock_result = MagicMock()
        mock_result.success = True
        mock_result.journal_entry_id = uuid4()
        mock_result.posting_batch_id = uuid4()
        mock_result.message = "Posted successfully"

        with patch("app.services.ifrs.ap.supplier_invoice.SupplierInvoice"):
            with patch("app.services.ifrs.ap.supplier_invoice.SupplierInvoiceStatus", MockSupplierInvoiceStatus):
                # APPostingAdapter is imported inside the method, so patch the actual module
                with patch("app.services.ifrs.ap.ap_posting_adapter.APPostingAdapter") as MockAdapter:
                    MockAdapter.post_invoice.return_value = mock_result
                    result = SupplierInvoiceService.post_invoice(
                        mock_db, org_id, invoice.invoice_id, user_id
                    )

        assert result.status == MockSupplierInvoiceStatus.POSTED
        mock_db.commit.assert_called()

    def test_post_non_approved_fails(self, mock_db, org_id, user_id):
        """Test that posting non-approved invoice fails."""
        from fastapi import HTTPException
        from app.services.ifrs.ap.supplier_invoice import SupplierInvoiceService

        invoice = MockSupplierInvoice(
            organization_id=org_id,
            status=MockSupplierInvoiceStatus.DRAFT,
        )
        mock_db.get.return_value = invoice

        with patch("app.services.ifrs.ap.supplier_invoice.SupplierInvoice"):
            with patch("app.services.ifrs.ap.supplier_invoice.SupplierInvoiceStatus", MockSupplierInvoiceStatus):
                with pytest.raises(HTTPException) as exc:
                    SupplierInvoiceService.post_invoice(
                        mock_db, org_id, invoice.invoice_id, user_id
                    )

        assert exc.value.status_code == 400

    def test_post_invoice_adapter_failure(self, mock_db, org_id, user_id):
        """Test posting fails when adapter returns error."""
        from fastapi import HTTPException
        from app.services.ifrs.ap.supplier_invoice import SupplierInvoiceService

        invoice = MockSupplierInvoice(
            organization_id=org_id,
            status=MockSupplierInvoiceStatus.APPROVED,
        )
        mock_db.get.return_value = invoice

        mock_result = MagicMock()
        mock_result.success = False
        mock_result.message = "Posting failed: invalid account"

        with patch("app.services.ifrs.ap.supplier_invoice.SupplierInvoice"):
            with patch("app.services.ifrs.ap.supplier_invoice.SupplierInvoiceStatus", MockSupplierInvoiceStatus):
                with patch("app.services.ifrs.ap.ap_posting_adapter.APPostingAdapter") as MockAdapter:
                    MockAdapter.post_invoice.return_value = mock_result
                    with pytest.raises(HTTPException) as exc:
                        SupplierInvoiceService.post_invoice(
                            mock_db, org_id, invoice.invoice_id, user_id
                        )

        assert exc.value.status_code == 400


class TestPutOnHold:
    """Tests for put_on_hold method."""

    def test_put_posted_invoice_on_hold(self, mock_db, org_id):
        """Test putting a posted invoice on hold."""
        from app.services.ifrs.ap.supplier_invoice import SupplierInvoiceService

        invoice = MockSupplierInvoice(
            organization_id=org_id,
            status=MockSupplierInvoiceStatus.POSTED,
        )
        mock_db.get.return_value = invoice

        with patch("app.services.ifrs.ap.supplier_invoice.SupplierInvoice"):
            with patch("app.services.ifrs.ap.supplier_invoice.SupplierInvoiceStatus", MockSupplierInvoiceStatus):
                result = SupplierInvoiceService.put_on_hold(
                    mock_db, org_id, invoice.invoice_id, "Under review"
                )

        assert result.status == MockSupplierInvoiceStatus.ON_HOLD
        mock_db.commit.assert_called()

    def test_put_paid_invoice_on_hold_fails(self, mock_db, org_id):
        """Test that paid invoice cannot be put on hold."""
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
                    SupplierInvoiceService.put_on_hold(
                        mock_db, org_id, invoice.invoice_id, "Review"
                    )

        assert exc.value.status_code == 400


class TestReleaseFromHold:
    """Tests for release_from_hold method."""

    def test_release_approved_invoice_from_hold(self, mock_db, org_id):
        """Test releasing an approved invoice from hold."""
        from app.services.ifrs.ap.supplier_invoice import SupplierInvoiceService

        approver_id = uuid4()
        invoice = MockSupplierInvoice(
            organization_id=org_id,
            status=MockSupplierInvoiceStatus.ON_HOLD,
            approved_by_user_id=approver_id,
        )
        mock_db.get.return_value = invoice

        with patch("app.services.ifrs.ap.supplier_invoice.SupplierInvoice"):
            with patch("app.services.ifrs.ap.supplier_invoice.SupplierInvoiceStatus", MockSupplierInvoiceStatus):
                result = SupplierInvoiceService.release_from_hold(
                    mock_db, org_id, invoice.invoice_id
                )

        assert result.status == MockSupplierInvoiceStatus.APPROVED
        mock_db.commit.assert_called()

    def test_release_submitted_invoice_from_hold(self, mock_db, org_id):
        """Test releasing a submitted (not yet approved) invoice from hold."""
        from app.services.ifrs.ap.supplier_invoice import SupplierInvoiceService

        invoice = MockSupplierInvoice(
            organization_id=org_id,
            status=MockSupplierInvoiceStatus.ON_HOLD,
            approved_by_user_id=None,  # Not approved yet
        )
        mock_db.get.return_value = invoice

        with patch("app.services.ifrs.ap.supplier_invoice.SupplierInvoice"):
            with patch("app.services.ifrs.ap.supplier_invoice.SupplierInvoiceStatus", MockSupplierInvoiceStatus):
                result = SupplierInvoiceService.release_from_hold(
                    mock_db, org_id, invoice.invoice_id
                )

        assert result.status == MockSupplierInvoiceStatus.SUBMITTED
        mock_db.commit.assert_called()

    def test_release_non_held_invoice_fails(self, mock_db, org_id):
        """Test that releasing non-held invoice fails."""
        from fastapi import HTTPException
        from app.services.ifrs.ap.supplier_invoice import SupplierInvoiceService

        invoice = MockSupplierInvoice(
            organization_id=org_id,
            status=MockSupplierInvoiceStatus.POSTED,
        )
        mock_db.get.return_value = invoice

        with patch("app.services.ifrs.ap.supplier_invoice.SupplierInvoice"):
            with patch("app.services.ifrs.ap.supplier_invoice.SupplierInvoiceStatus", MockSupplierInvoiceStatus):
                with pytest.raises(HTTPException) as exc:
                    SupplierInvoiceService.release_from_hold(
                        mock_db, org_id, invoice.invoice_id
                    )

        assert exc.value.status_code == 400


class TestRecordPayment:
    """Tests for record_payment method."""

    def test_record_partial_payment(self, mock_db, org_id):
        """Test recording a partial payment."""
        from app.services.ifrs.ap.supplier_invoice import SupplierInvoiceService

        invoice = MockSupplierInvoice(
            organization_id=org_id,
            status=MockSupplierInvoiceStatus.POSTED,
            total_amount=Decimal("1000.00"),
            amount_paid=Decimal("0"),
        )
        mock_db.get.return_value = invoice

        with patch("app.services.ifrs.ap.supplier_invoice.SupplierInvoice"):
            with patch("app.services.ifrs.ap.supplier_invoice.SupplierInvoiceStatus", MockSupplierInvoiceStatus):
                result = SupplierInvoiceService.record_payment(
                    mock_db, org_id, invoice.invoice_id, Decimal("500.00")
                )

        assert result.status == MockSupplierInvoiceStatus.PARTIALLY_PAID
        assert result.amount_paid == Decimal("500.00")
        mock_db.commit.assert_called()

    def test_record_full_payment(self, mock_db, org_id):
        """Test recording full payment."""
        from app.services.ifrs.ap.supplier_invoice import SupplierInvoiceService

        invoice = MockSupplierInvoice(
            organization_id=org_id,
            status=MockSupplierInvoiceStatus.POSTED,
            total_amount=Decimal("1000.00"),
            amount_paid=Decimal("0"),
        )
        mock_db.get.return_value = invoice

        with patch("app.services.ifrs.ap.supplier_invoice.SupplierInvoice"):
            with patch("app.services.ifrs.ap.supplier_invoice.SupplierInvoiceStatus", MockSupplierInvoiceStatus):
                result = SupplierInvoiceService.record_payment(
                    mock_db, org_id, invoice.invoice_id, Decimal("1000.00")
                )

        assert result.status == MockSupplierInvoiceStatus.PAID
        mock_db.commit.assert_called()

    def test_record_payment_on_partially_paid(self, mock_db, org_id):
        """Test recording payment on partially paid invoice."""
        from app.services.ifrs.ap.supplier_invoice import SupplierInvoiceService

        invoice = MockSupplierInvoice(
            organization_id=org_id,
            status=MockSupplierInvoiceStatus.PARTIALLY_PAID,
            total_amount=Decimal("1000.00"),
            amount_paid=Decimal("400.00"),
        )
        mock_db.get.return_value = invoice

        with patch("app.services.ifrs.ap.supplier_invoice.SupplierInvoice"):
            with patch("app.services.ifrs.ap.supplier_invoice.SupplierInvoiceStatus", MockSupplierInvoiceStatus):
                result = SupplierInvoiceService.record_payment(
                    mock_db, org_id, invoice.invoice_id, Decimal("600.00")
                )

        assert result.status == MockSupplierInvoiceStatus.PAID
        assert result.amount_paid == Decimal("1000.00")
        mock_db.commit.assert_called()

    def test_record_payment_on_non_posted_fails(self, mock_db, org_id):
        """Test that payment on non-posted invoice fails."""
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
                    SupplierInvoiceService.record_payment(
                        mock_db, org_id, invoice.invoice_id, Decimal("100.00")
                    )

        assert exc.value.status_code == 400


class TestCreditNoteHandling:
    """Tests for credit note handling."""

    def test_create_credit_note_negative_amounts(self, mock_db, org_id, user_id):
        """Test that credit notes have negative amounts."""
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
        mock_query.first.return_value = None
        mock_db.query.return_value = mock_query

        lines = [
            InvoiceLineInput(
                description="Credit for return",
                quantity=Decimal("1"),
                unit_price=Decimal("500.00"),
                expense_account_id=uuid4(),
            ),
        ]

        invoice_input = SupplierInvoiceInput(
            supplier_id=supplier.supplier_id,
            invoice_type=SupplierInvoiceType.CREDIT_NOTE,
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
                    with patch("app.services.ifrs.ap.supplier_invoice.SequenceService.get_next_number", return_value="APINV-0002"):
                        result = SupplierInvoiceService.create_invoice(
                            mock_db, org_id, invoice_input, user_id
                        )

        # Verify the invoice was created with negative amounts
        call_kwargs = MockInv.call_args[1]
        assert call_kwargs['total_amount'] < 0
        assert call_kwargs['subtotal'] < 0


class TestInactiveSupplierHandling:
    """Tests for inactive supplier handling."""

    def test_create_invoice_inactive_supplier_fails(self, mock_db, org_id, user_id):
        """Test that creating invoice for inactive supplier fails."""
        from fastapi import HTTPException
        from app.services.ifrs.ap.supplier_invoice import (
            SupplierInvoiceService,
            SupplierInvoiceInput,
            InvoiceLineInput,
        )
        from app.models.ifrs.ap.supplier_invoice import SupplierInvoiceType

        supplier = MockSupplier(organization_id=org_id, is_active=False)
        mock_db.get.return_value = supplier

        lines = [
            InvoiceLineInput(
                description="Test",
                quantity=Decimal("1"),
                unit_price=Decimal("100.00"),
                expense_account_id=uuid4(),
            ),
        ]

        invoice_input = SupplierInvoiceInput(
            supplier_id=supplier.supplier_id,
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

        assert exc.value.status_code == 400
        assert "not active" in exc.value.detail.lower()


class TestInvoiceNotFoundScenarios:
    """Tests for invoice not found scenarios across methods."""

    def test_submit_invoice_not_found(self, mock_db, org_id, user_id):
        """Test submitting non-existent invoice."""
        from fastapi import HTTPException
        from app.services.ifrs.ap.supplier_invoice import SupplierInvoiceService

        mock_db.get.return_value = None

        with patch("app.services.ifrs.ap.supplier_invoice.SupplierInvoice"):
            with pytest.raises(HTTPException) as exc:
                SupplierInvoiceService.submit_invoice(
                    mock_db, org_id, uuid4(), user_id
                )

        assert exc.value.status_code == 404

    def test_approve_invoice_not_found(self, mock_db, org_id, user_id):
        """Test approving non-existent invoice."""
        from fastapi import HTTPException
        from app.services.ifrs.ap.supplier_invoice import SupplierInvoiceService

        mock_db.get.return_value = None

        with patch("app.services.ifrs.ap.supplier_invoice.SupplierInvoice"):
            with pytest.raises(HTTPException) as exc:
                SupplierInvoiceService.approve_invoice(
                    mock_db, org_id, uuid4(), user_id
                )

        assert exc.value.status_code == 404

    def test_void_invoice_not_found(self, mock_db, org_id, user_id):
        """Test voiding non-existent invoice."""
        from fastapi import HTTPException
        from app.services.ifrs.ap.supplier_invoice import SupplierInvoiceService

        mock_db.get.return_value = None

        with patch("app.services.ifrs.ap.supplier_invoice.SupplierInvoice"):
            with pytest.raises(HTTPException) as exc:
                SupplierInvoiceService.void_invoice(
                    mock_db, org_id, uuid4(), user_id, "Test"
                )

        assert exc.value.status_code == 404

    def test_get_invoice_lines_not_found(self, mock_db, org_id):
        """Test getting lines for non-existent invoice."""
        from fastapi import HTTPException
        from app.services.ifrs.ap.supplier_invoice import SupplierInvoiceService

        mock_db.get.return_value = None

        with patch("app.services.ifrs.ap.supplier_invoice.SupplierInvoice"):
            with pytest.raises(HTTPException) as exc:
                SupplierInvoiceService.get_invoice_lines(
                    mock_db, org_id, uuid4()
                )

        assert exc.value.status_code == 404
