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
        from app.models.finance.ar.invoice import InvoiceType
        from app.services.finance.ar.invoice import (
            ARInvoiceInput,
            ARInvoiceLineInput,
            ARInvoiceService,
        )

        customer = MockCustomer(organization_id=org_id)
        mock_account = MagicMock(organization_id=org_id)
        mock_db.get.side_effect = [customer, mock_account]

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

        with patch("app.services.finance.ar.invoice._batch_validate_org_refs"):
            with patch("app.services.finance.ar.invoice.Customer"):
                with patch("app.services.finance.ar.invoice.Invoice") as MockInv:
                    mock_invoice = MockInvoice(
                        organization_id=org_id,
                        customer_id=customer.customer_id,
                    )
                    MockInv.return_value = mock_invoice

                    with patch("app.services.finance.ar.invoice.InvoiceLine"):
                        with patch(
                            "app.services.finance.ar.invoice.SequenceService.get_next_number",
                            return_value="INV-0001",
                        ):
                            with patch(
                                "app.services.hooks.registry.HookRegistry.emit",
                                return_value=[],
                            ) as mock_emit:
                                ARInvoiceService.create_invoice(
                                    mock_db, org_id, invoice_input, user_id
                                )
                                mock_emit.assert_called_once()

        mock_db.add.assert_called()
        mock_db.commit.assert_called()

    def test_create_invoice_invalid_customer_fails(self, mock_db, org_id, user_id):
        """Test that invalid customer fails validation."""
        from fastapi import HTTPException

        from app.models.finance.ar.invoice import InvoiceType
        from app.services.finance.ar.invoice import (
            ARInvoiceInput,
            ARInvoiceLineInput,
            ARInvoiceService,
        )

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

        with patch("app.services.finance.ar.invoice.Customer"):
            with pytest.raises(HTTPException) as exc:
                ARInvoiceService.create_invoice(mock_db, org_id, invoice_input, user_id)

        assert exc.value.status_code == 404

    def test_create_invoice_empty_lines_fails(self, mock_db, org_id, user_id):
        """Test that invoice with no lines fails."""
        from fastapi import HTTPException

        from app.models.finance.ar.invoice import InvoiceType
        from app.services.finance.ar.invoice import (
            ARInvoiceInput,
            ARInvoiceService,
        )

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

        with patch("app.services.finance.ar.invoice.Customer"):
            with pytest.raises(HTTPException) as exc:
                ARInvoiceService.create_invoice(mock_db, org_id, invoice_input, user_id)

        assert exc.value.status_code == 400


class TestSubmitInvoice:
    """Tests for submit_invoice method."""

    def test_submit_draft_invoice(self, mock_db, org_id, user_id):
        """Test submitting a draft invoice."""
        from app.models.finance.ar.invoice import InvoiceStatus
        from app.services.finance.ar.invoice import ARInvoiceService

        invoice = MockInvoice(
            organization_id=org_id,
            status=InvoiceStatus.DRAFT,
        )
        mock_db.get.return_value = invoice

        with (
            patch("app.services.finance.ar.invoice.Invoice"),
            patch(
                "app.services.hooks.registry.HookRegistry.emit",
                return_value=[],
            ) as mock_emit,
        ):
            result = ARInvoiceService.submit_invoice(
                mock_db, org_id, invoice.invoice_id, user_id
            )
            mock_emit.assert_called_once()

        assert result.status == InvoiceStatus.SUBMITTED
        mock_db.commit.assert_called()

    def test_submit_non_draft_fails(self, mock_db, org_id, user_id):
        """Test submitting non-draft invoice fails."""
        from fastapi import HTTPException

        from app.models.finance.ar.invoice import InvoiceStatus
        from app.services.finance.ar.invoice import ARInvoiceService

        invoice = MockInvoice(
            organization_id=org_id,
            status=InvoiceStatus.APPROVED,
        )
        mock_db.get.return_value = invoice

        with patch("app.services.finance.ar.invoice.Invoice"):
            with pytest.raises(HTTPException) as exc:
                ARInvoiceService.submit_invoice(
                    mock_db, org_id, invoice.invoice_id, user_id
                )

        assert exc.value.status_code == 400


class TestApproveInvoice:
    """Tests for approve_invoice method."""

    def test_approve_submitted_invoice(self, mock_db, org_id, user_id):
        """Test approving a submitted invoice."""
        from app.models.finance.ar.invoice import InvoiceStatus
        from app.services.finance.ar.invoice import ARInvoiceService

        submitter_id = uuid4()
        invoice = MockInvoice(
            organization_id=org_id,
            status=InvoiceStatus.SUBMITTED,
            submitted_by_user_id=submitter_id,
        )
        mock_db.get.return_value = invoice

        with patch("app.services.finance.ar.invoice.Invoice"):
            result = ARInvoiceService.approve_invoice(
                mock_db, org_id, invoice.invoice_id, user_id
            )

        assert result.status == InvoiceStatus.APPROVED
        mock_db.commit.assert_called()

    def test_self_approval_fails_sod(self, mock_db, org_id):
        """Test that self-approval fails segregation of duties."""
        from fastapi import HTTPException

        from app.models.finance.ar.invoice import InvoiceStatus
        from app.services.finance.ar.invoice import ARInvoiceService

        submitter_id = uuid4()
        invoice = MockInvoice(
            organization_id=org_id,
            status=InvoiceStatus.SUBMITTED,
            submitted_by_user_id=submitter_id,
        )
        mock_db.get.return_value = invoice

        with patch("app.services.finance.ar.invoice.Invoice"):
            with pytest.raises(HTTPException) as exc:
                # Same user tries to approve
                ARInvoiceService.approve_invoice(
                    mock_db, org_id, invoice.invoice_id, submitter_id
                )

        assert exc.value.status_code == 400
        assert (
            "segregation" in exc.value.detail.lower()
            or "same user" in exc.value.detail.lower()
        )


class TestVoidInvoice:
    """Tests for void_invoice method."""

    def test_void_draft_invoice(self, mock_db, org_id, user_id):
        """Test voiding a draft invoice."""
        from app.models.finance.ar.invoice import InvoiceStatus
        from app.services.finance.ar.invoice import ARInvoiceService

        invoice = MockInvoice(
            organization_id=org_id,
            status=InvoiceStatus.DRAFT,
        )
        mock_db.get.return_value = invoice

        with patch("app.services.finance.ar.invoice.Invoice"):
            result = ARInvoiceService.void_invoice(
                mock_db, org_id, invoice.invoice_id, user_id, "Not needed"
            )

        assert result.status == InvoiceStatus.VOID
        mock_db.commit.assert_called()

    def test_void_paid_invoice_fails(self, mock_db, org_id, user_id):
        """Test that voiding paid invoice fails."""
        from fastapi import HTTPException

        from app.models.finance.ar.invoice import InvoiceStatus
        from app.services.finance.ar.invoice import ARInvoiceService

        invoice = MockInvoice(
            organization_id=org_id,
            status=InvoiceStatus.PAID,
        )
        mock_db.get.return_value = invoice

        with patch("app.services.finance.ar.invoice.Invoice"):
            with pytest.raises(HTTPException) as exc:
                ARInvoiceService.void_invoice(
                    mock_db, org_id, invoice.invoice_id, user_id, "Mistake"
                )

        assert exc.value.status_code == 400


class TestGetInvoice:
    """Tests for get method."""

    def test_get_existing_invoice(self, mock_db, org_id):
        """Test getting existing invoice."""
        from app.services.finance.ar.invoice import ARInvoiceService

        invoice = MockInvoice(organization_id=org_id)
        mock_db.get.return_value = invoice

        with patch("app.services.finance.ar.invoice.Invoice"):
            result = ARInvoiceService.get(mock_db, org_id, str(invoice.invoice_id))

        assert result == invoice

    def test_get_nonexistent_raises(self, mock_db):
        """Test getting non-existent invoice raises exception."""
        from fastapi import HTTPException

        from app.services.finance.ar.invoice import ARInvoiceService

        mock_db.get.return_value = None

        with patch("app.services.finance.ar.invoice.Invoice"):
            with pytest.raises(HTTPException) as exc:
                ARInvoiceService.get(mock_db, uuid4(), str(uuid4()))

        assert exc.value.status_code == 404


class TestListInvoices:
    """Tests for list method."""

    def test_list_with_filters(self, mock_db, org_id):
        """Test listing invoices with filters."""
        from app.models.finance.ar.invoice import InvoiceStatus
        from app.services.finance.ar.invoice import ARInvoiceService

        invoices = [MockInvoice(organization_id=org_id)]
        mock_query = MagicMock()
        mock_query.options.return_value = mock_query
        mock_query.filter.return_value = mock_query
        mock_query.order_by.return_value = mock_query
        mock_query.limit.return_value = mock_query
        mock_query.offset.return_value = mock_query
        mock_query.all.return_value = invoices
        mock_db.scalars.return_value.unique.return_value.all.return_value = invoices

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
        from app.services.finance.ar.invoice import ARInvoiceService

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
        mock_db.scalars.return_value.all.return_value = lines

        result = ARInvoiceService.get_invoice_lines(mock_db, org_id, invoice_id)

        assert len(result) == 2

    def test_get_invoice_lines_not_found(self, mock_db, org_id):
        """Test getting lines for non-existent invoice."""
        from fastapi import HTTPException

        from app.services.finance.ar.invoice import ARInvoiceService

        mock_db.get.return_value = None

        with patch("app.services.finance.ar.invoice.Invoice"):
            with pytest.raises(HTTPException) as exc:
                ARInvoiceService.get_invoice_lines(mock_db, org_id, uuid4())

        assert exc.value.status_code == 404


class TestPostInvoice:
    """Tests for post_invoice method."""

    def test_post_approved_invoice(self, mock_db, org_id, user_id):
        """Test posting an approved invoice."""
        from app.models.finance.ar.invoice import InvoiceStatus
        from app.services.finance.ar.invoice import ARInvoiceService

        invoice = MockInvoice(
            organization_id=org_id,
            status=InvoiceStatus.APPROVED,
        )
        mock_db.get.return_value = invoice

        # Mock the posting adapter result
        mock_result = MagicMock()
        mock_result.success = True
        mock_result.journal_entry_id = uuid4()
        mock_result.posting_batch_id = uuid4()
        mock_result.message = "Posted successfully"

        with patch("app.services.finance.ar.invoice.Invoice"):
            # ARPostingAdapter is imported inside the method
            with patch(
                "app.services.finance.ar.ar_posting_adapter.ARPostingAdapter"
            ) as MockAdapter:
                MockAdapter.post_invoice.return_value = mock_result
                result = ARInvoiceService.post_invoice(
                    mock_db, org_id, invoice.invoice_id, user_id
                )

        assert result.status == InvoiceStatus.POSTED
        mock_db.commit.assert_called()

    def test_post_non_approved_fails(self, mock_db, org_id, user_id):
        """Test that posting non-approved invoice fails."""
        from fastapi import HTTPException

        from app.models.finance.ar.invoice import InvoiceStatus
        from app.services.finance.ar.invoice import ARInvoiceService

        invoice = MockInvoice(
            organization_id=org_id,
            status=InvoiceStatus.DRAFT,
        )
        mock_db.get.return_value = invoice

        with patch("app.services.finance.ar.invoice.Invoice"):
            with pytest.raises(HTTPException) as exc:
                ARInvoiceService.post_invoice(
                    mock_db, org_id, invoice.invoice_id, user_id
                )

        assert exc.value.status_code == 400

    def test_post_invoice_adapter_failure(self, mock_db, org_id, user_id):
        """Test posting fails when adapter returns error."""
        from fastapi import HTTPException

        from app.models.finance.ar.invoice import InvoiceStatus
        from app.services.finance.ar.invoice import ARInvoiceService

        invoice = MockInvoice(
            organization_id=org_id,
            status=InvoiceStatus.APPROVED,
        )
        mock_db.get.return_value = invoice

        mock_result = MagicMock()
        mock_result.success = False
        mock_result.message = "Posting failed: invalid account"

        with (
            patch("app.services.finance.ar.invoice.Invoice"),
            patch(
                "app.services.finance.ar.ar_posting_adapter.ARPostingAdapter"
            ) as MockAdapter,
        ):
            MockAdapter.post_invoice.return_value = mock_result
            with pytest.raises(HTTPException) as exc:
                ARInvoiceService.post_invoice(
                    mock_db, org_id, invoice.invoice_id, user_id
                )

        assert exc.value.status_code == 400

    def test_post_invoice_not_found(self, mock_db, org_id, user_id):
        """Test posting non-existent invoice."""
        from fastapi import HTTPException

        from app.services.finance.ar.invoice import ARInvoiceService

        mock_db.get.return_value = None

        with patch("app.services.finance.ar.invoice.Invoice"):
            with pytest.raises(HTTPException) as exc:
                ARInvoiceService.post_invoice(mock_db, org_id, uuid4(), user_id)

        assert exc.value.status_code == 404


class TestMarkOverdue:
    """Tests for mark_overdue method."""

    def test_mark_overdue_invoices(self, mock_db, org_id):
        """Test marking overdue invoices."""
        from app.models.finance.ar.invoice import InvoiceStatus
        from app.services.finance.ar.invoice import ARInvoiceService

        # Create invoices that are past due with balance
        invoice1 = MockInvoice(
            organization_id=org_id,
            status=InvoiceStatus.POSTED,
            due_date=date.today() - timedelta(days=10),
            total_amount=Decimal("1000.00"),
            amount_paid=Decimal("0"),
        )
        invoice2 = MockInvoice(
            organization_id=org_id,
            status=InvoiceStatus.PARTIALLY_PAID,
            due_date=date.today() - timedelta(days=5),
            total_amount=Decimal("500.00"),
            amount_paid=Decimal("200.00"),
        )

        mock_db.scalars.return_value.all.return_value = [
            invoice1,
            invoice2,
        ]

        result = ARInvoiceService.mark_overdue(mock_db, org_id)

        assert result == 2
        assert invoice1.status == InvoiceStatus.OVERDUE
        assert invoice2.status == InvoiceStatus.OVERDUE
        mock_db.commit.assert_called()

    def test_mark_overdue_no_invoices(self, mock_db, org_id):
        """Test when no invoices are overdue."""
        from app.services.finance.ar.invoice import ARInvoiceService

        mock_db.scalars.return_value.all.return_value = []

        result = ARInvoiceService.mark_overdue(mock_db, org_id)

        assert result == 0

    def test_mark_overdue_with_custom_date(self, mock_db, org_id):
        """Test marking overdue with custom as_of_date."""
        from app.models.finance.ar.invoice import InvoiceStatus
        from app.services.finance.ar.invoice import ARInvoiceService

        invoice = MockInvoice(
            organization_id=org_id,
            status=InvoiceStatus.POSTED,
            due_date=date(2024, 6, 15),
            total_amount=Decimal("1000.00"),
            amount_paid=Decimal("0"),
        )

        mock_db.scalars.return_value.all.return_value = [invoice]

        result = ARInvoiceService.mark_overdue(
            mock_db, org_id, as_of_date=date(2024, 6, 30)
        )

        assert result == 1

    def test_mark_overdue_skips_fully_paid(self, mock_db, org_id):
        """Test that fully paid invoices are not marked overdue."""
        from app.models.finance.ar.invoice import InvoiceStatus
        from app.services.finance.ar.invoice import ARInvoiceService

        # Invoice with balance_due = 0
        invoice = MockInvoice(
            organization_id=org_id,
            status=InvoiceStatus.POSTED,
            due_date=date.today() - timedelta(days=10),
            total_amount=Decimal("1000.00"),
            amount_paid=Decimal("1000.00"),  # Fully paid
        )

        mock_db.scalars.return_value.all.return_value = [invoice]

        result = ARInvoiceService.mark_overdue(mock_db, org_id)

        # Should not be marked overdue since balance_due is 0
        assert result == 0


class TestRecordPayment:
    """Tests for record_payment method."""

    def test_record_partial_payment(self, mock_db, org_id):
        """Test recording a partial payment."""
        from app.models.finance.ar.invoice import InvoiceStatus
        from app.services.finance.ar.invoice import ARInvoiceService

        invoice = MockInvoice(
            organization_id=org_id,
            status=InvoiceStatus.POSTED,
            total_amount=Decimal("1000.00"),
            amount_paid=Decimal("0"),
        )
        mock_db.get.return_value = invoice

        with patch("app.services.finance.ar.invoice.Invoice"):
            result = ARInvoiceService.record_payment(
                mock_db, org_id, invoice.invoice_id, Decimal("500.00")
            )

        assert result.status == InvoiceStatus.PARTIALLY_PAID
        assert result.amount_paid == Decimal("500.00")
        mock_db.commit.assert_called()

    def test_record_full_payment(self, mock_db, org_id):
        """Test recording full payment."""
        from app.models.finance.ar.invoice import InvoiceStatus
        from app.services.finance.ar.invoice import ARInvoiceService

        invoice = MockInvoice(
            organization_id=org_id,
            status=InvoiceStatus.POSTED,
            total_amount=Decimal("1000.00"),
            amount_paid=Decimal("0"),
        )
        mock_db.get.return_value = invoice

        with patch("app.services.finance.ar.invoice.Invoice"):
            result = ARInvoiceService.record_payment(
                mock_db, org_id, invoice.invoice_id, Decimal("1000.00")
            )

        assert result.status == InvoiceStatus.PAID
        mock_db.commit.assert_called()

    def test_record_payment_on_partially_paid(self, mock_db, org_id):
        """Test recording payment on partially paid invoice."""
        from app.models.finance.ar.invoice import InvoiceStatus
        from app.services.finance.ar.invoice import ARInvoiceService

        invoice = MockInvoice(
            organization_id=org_id,
            status=InvoiceStatus.PARTIALLY_PAID,
            total_amount=Decimal("1000.00"),
            amount_paid=Decimal("400.00"),
        )
        mock_db.get.return_value = invoice

        with patch("app.services.finance.ar.invoice.Invoice"):
            result = ARInvoiceService.record_payment(
                mock_db, org_id, invoice.invoice_id, Decimal("600.00")
            )

        assert result.status == InvoiceStatus.PAID
        assert result.amount_paid == Decimal("1000.00")
        mock_db.commit.assert_called()

    def test_record_payment_on_overdue(self, mock_db, org_id):
        """Test recording payment on overdue invoice."""
        from app.models.finance.ar.invoice import InvoiceStatus
        from app.services.finance.ar.invoice import ARInvoiceService

        invoice = MockInvoice(
            organization_id=org_id,
            status=InvoiceStatus.OVERDUE,
            total_amount=Decimal("1000.00"),
            amount_paid=Decimal("0"),
        )
        mock_db.get.return_value = invoice

        with patch("app.services.finance.ar.invoice.Invoice"):
            result = ARInvoiceService.record_payment(
                mock_db, org_id, invoice.invoice_id, Decimal("500.00")
            )

        assert result.status == InvoiceStatus.PARTIALLY_PAID

    def test_record_payment_on_non_posted_fails(self, mock_db, org_id):
        """Test that payment on non-posted invoice fails."""
        from fastapi import HTTPException

        from app.models.finance.ar.invoice import InvoiceStatus
        from app.services.finance.ar.invoice import ARInvoiceService

        invoice = MockInvoice(
            organization_id=org_id,
            status=InvoiceStatus.APPROVED,
        )
        mock_db.get.return_value = invoice

        with patch("app.services.finance.ar.invoice.Invoice"):
            with pytest.raises(HTTPException) as exc:
                ARInvoiceService.record_payment(
                    mock_db, org_id, invoice.invoice_id, Decimal("100.00")
                )

        assert exc.value.status_code == 400

    def test_record_payment_not_found(self, mock_db, org_id):
        """Test recording payment on non-existent invoice."""
        from fastapi import HTTPException

        from app.services.finance.ar.invoice import ARInvoiceService

        mock_db.get.return_value = None

        with patch("app.services.finance.ar.invoice.Invoice"):
            with pytest.raises(HTTPException) as exc:
                ARInvoiceService.record_payment(
                    mock_db, org_id, uuid4(), Decimal("100.00")
                )

        assert exc.value.status_code == 404


class TestCreditNoteHandling:
    """Tests for credit note handling."""

    def test_create_credit_note_negative_amounts(self, mock_db, org_id, user_id):
        """Test that credit notes have negative amounts."""
        from app.models.finance.ar.invoice import InvoiceType
        from app.services.finance.ar.invoice import (
            ARInvoiceInput,
            ARInvoiceLineInput,
            ARInvoiceService,
        )

        customer = MockCustomer(organization_id=org_id)
        mock_account = MagicMock(organization_id=org_id)
        mock_db.get.side_effect = [customer, mock_account]

        mock_query = MagicMock()
        mock_query.filter.return_value = mock_query
        mock_query.first.return_value = None
        mock_db.query.return_value = mock_query

        lines = [
            ARInvoiceLineInput(
                description="Credit for return",
                quantity=Decimal("1"),
                unit_price=Decimal("500.00"),
                revenue_account_id=uuid4(),
            ),
        ]

        invoice_input = ARInvoiceInput(
            customer_id=customer.customer_id,
            invoice_type=InvoiceType.CREDIT_NOTE,
            invoice_date=date.today(),
            due_date=date.today() + timedelta(days=30),
            currency_code="USD",
            lines=lines,
        )

        with patch("app.services.finance.ar.invoice._batch_validate_org_refs"):
            with patch("app.services.finance.ar.invoice.Customer"):
                with patch("app.services.finance.ar.invoice.Invoice") as MockInv:
                    mock_invoice = MockInvoice(
                        organization_id=org_id,
                        customer_id=customer.customer_id,
                    )
                    MockInv.return_value = mock_invoice

                    with patch("app.services.finance.ar.invoice.InvoiceLine"):
                        with patch(
                            "app.services.finance.ar.invoice.SequenceService.get_next_number",
                            return_value="INV-0002",
                        ):
                            ARInvoiceService.create_invoice(
                                mock_db, org_id, invoice_input, user_id
                            )

        # Verify the invoice was created with negative amounts
        call_kwargs = MockInv.call_args[1]
        assert call_kwargs["total_amount"] < 0
        assert call_kwargs["subtotal"] < 0


class TestInactiveCustomerHandling:
    """Tests for inactive customer handling."""

    def test_create_invoice_inactive_customer_fails(self, mock_db, org_id, user_id):
        """Test that creating invoice for inactive customer fails."""
        from fastapi import HTTPException

        from app.models.finance.ar.invoice import InvoiceType
        from app.services.finance.ar.invoice import (
            ARInvoiceInput,
            ARInvoiceLineInput,
            ARInvoiceService,
        )

        customer = MockCustomer(organization_id=org_id, is_active=False)
        mock_db.get.return_value = customer

        lines = [
            ARInvoiceLineInput(
                description="Test",
                quantity=Decimal("1"),
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

        with patch("app.services.finance.ar.invoice.Customer"):
            with pytest.raises(HTTPException) as exc:
                ARInvoiceService.create_invoice(mock_db, org_id, invoice_input, user_id)

        assert exc.value.status_code == 400
        assert "not active" in exc.value.detail.lower()


class TestInvoiceNotFoundScenarios:
    """Tests for invoice not found scenarios across methods."""

    def test_submit_invoice_not_found(self, mock_db, org_id, user_id):
        """Test submitting non-existent invoice."""
        from fastapi import HTTPException

        from app.services.finance.ar.invoice import ARInvoiceService

        mock_db.get.return_value = None

        with patch("app.services.finance.ar.invoice.Invoice"):
            with pytest.raises(HTTPException) as exc:
                ARInvoiceService.submit_invoice(mock_db, org_id, uuid4(), user_id)

        assert exc.value.status_code == 404

    def test_approve_invoice_not_found(self, mock_db, org_id, user_id):
        """Test approving non-existent invoice."""
        from fastapi import HTTPException

        from app.services.finance.ar.invoice import ARInvoiceService

        mock_db.get.return_value = None

        with patch("app.services.finance.ar.invoice.Invoice"):
            with pytest.raises(HTTPException) as exc:
                ARInvoiceService.approve_invoice(mock_db, org_id, uuid4(), user_id)

        assert exc.value.status_code == 404

    def test_void_invoice_not_found(self, mock_db, org_id, user_id):
        """Test voiding non-existent invoice."""
        from fastapi import HTTPException

        from app.services.finance.ar.invoice import ARInvoiceService

        mock_db.get.return_value = None

        with patch("app.services.finance.ar.invoice.Invoice"):
            with pytest.raises(HTTPException) as exc:
                ARInvoiceService.void_invoice(mock_db, org_id, uuid4(), user_id, "Test")

        assert exc.value.status_code == 404


class TestListInvoicesEdgeCases:
    """Additional tests for list method."""

    def test_list_requires_organization_id(self, mock_db):
        """Test that list requires organization_id."""
        from fastapi import HTTPException

        from app.services.finance.ar.invoice import ARInvoiceService

        with patch("app.services.finance.ar.invoice.Invoice"):
            with pytest.raises(HTTPException) as exc:
                ARInvoiceService.list(mock_db, organization_id=None)

        assert exc.value.status_code == 400

    def test_list_overdue_only(self, mock_db, org_id):
        """Test listing only overdue invoices."""
        from app.services.finance.ar.invoice import ARInvoiceService

        invoices = [MockInvoice(organization_id=org_id)]
        mock_query = MagicMock()
        mock_query.options.return_value = mock_query
        mock_query.filter.return_value = mock_query
        mock_query.order_by.return_value = mock_query
        mock_query.limit.return_value = mock_query
        mock_query.offset.return_value = mock_query
        mock_query.all.return_value = invoices
        mock_db.scalars.return_value.unique.return_value.all.return_value = invoices

        result = ARInvoiceService.list(
            mock_db,
            organization_id=str(org_id),
            overdue_only=True,
        )

        assert result == invoices

    def test_list_by_customer(self, mock_db, org_id):
        """Test listing invoices filtered by customer."""
        from app.services.finance.ar.invoice import ARInvoiceService

        customer_id = uuid4()
        invoices = [MockInvoice(organization_id=org_id, customer_id=customer_id)]
        mock_query = MagicMock()
        mock_query.options.return_value = mock_query
        mock_query.filter.return_value = mock_query
        mock_query.order_by.return_value = mock_query
        mock_query.limit.return_value = mock_query
        mock_query.offset.return_value = mock_query
        mock_query.all.return_value = invoices
        mock_db.scalars.return_value.unique.return_value.all.return_value = invoices

        result = ARInvoiceService.list(
            mock_db,
            organization_id=str(org_id),
            customer_id=str(customer_id),
        )

        assert result == invoices

    def test_list_by_date_range(self, mock_db, org_id):
        """Test listing invoices filtered by date range."""
        from app.services.finance.ar.invoice import ARInvoiceService

        invoices = [MockInvoice(organization_id=org_id)]
        mock_query = MagicMock()
        mock_query.options.return_value = mock_query
        mock_query.filter.return_value = mock_query
        mock_query.order_by.return_value = mock_query
        mock_query.limit.return_value = mock_query
        mock_query.offset.return_value = mock_query
        mock_query.all.return_value = invoices
        mock_db.scalars.return_value.unique.return_value.all.return_value = invoices

        result = ARInvoiceService.list(
            mock_db,
            organization_id=str(org_id),
            from_date=date(2024, 1, 1),
            to_date=date(2024, 12, 31),
        )

        assert result == invoices
