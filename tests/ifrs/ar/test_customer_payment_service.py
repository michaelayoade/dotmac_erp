"""
Tests for CustomerPaymentService.
"""

from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from tests.ifrs.ar.conftest import (
    MockCustomer,
    MockCustomerPayment,
    MockInvoice,
    MockInvoiceStatus,
    MockPaymentAllocation,
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


class TestCreateCustomerPayment:
    """Tests for create_payment method."""

    def test_create_payment_success(self, mock_db, org_id, user_id):
        """Test successful payment creation."""
        from app.models.finance.ar.customer_payment import PaymentMethod
        from app.models.finance.ar.invoice import InvoiceStatus
        from app.services.finance.ar.customer_payment import (
            CustomerPaymentInput,
            CustomerPaymentService,
            PaymentAllocationInput,
        )

        customer = MockCustomer(organization_id=org_id)

        # Mock invoice
        invoice = MockInvoice(
            organization_id=org_id,
            customer_id=customer.customer_id,
            total_amount=Decimal("1000.00"),
            amount_paid=Decimal("0"),
            status=InvoiceStatus.POSTED,
        )

        # Mock db.get to return different objects based on what's queried
        def mock_get(model, id):
            if str(id) == str(customer.customer_id):
                return customer
            elif str(id) == str(invoice.invoice_id):
                return invoice
            return None

        mock_db.get.side_effect = mock_get

        allocations = [
            PaymentAllocationInput(
                invoice_id=invoice.invoice_id,
                amount=Decimal("1000.00"),
            ),
        ]

        payment_input = CustomerPaymentInput(
            customer_id=customer.customer_id,
            payment_date=date.today(),
            payment_method=PaymentMethod.CHECK,
            currency_code="USD",
            amount=Decimal("1000.00"),
            bank_account_id=uuid4(),
            allocations=allocations,
        )

        with patch("app.services.finance.ar.customer_payment.Customer"):
            with patch(
                "app.services.finance.ar.customer_payment.CustomerPayment"
            ) as MockPay:
                mock_payment = MockCustomerPayment(
                    organization_id=org_id,
                    customer_id=customer.customer_id,
                )
                MockPay.return_value = mock_payment

                with patch(
                    "app.services.finance.ar.customer_payment.PaymentAllocation"
                ):
                    with patch("app.services.finance.ar.customer_payment.Invoice"):
                        with patch(
                            "app.services.finance.ar.customer_payment.SequenceService.get_next_number",
                            return_value="RCP-0001",
                        ):
                            CustomerPaymentService.create_payment(
                                mock_db, org_id, payment_input, user_id
                            )

        mock_db.add.assert_called()
        mock_db.commit.assert_called()

    def test_create_payment_invalid_customer_fails(self, mock_db, org_id, user_id):
        """Test that invalid customer fails validation."""
        from app.models.finance.ar.customer_payment import PaymentMethod
        from app.services.common import NotFoundError
        from app.services.finance.ar.customer_payment import (
            CustomerPaymentInput,
            CustomerPaymentService,
        )

        mock_db.get.return_value = None  # Customer not found

        payment_input = CustomerPaymentInput(
            customer_id=uuid4(),
            payment_date=date.today(),
            payment_method=PaymentMethod.CHECK,
            currency_code="USD",
            amount=Decimal("1000.00"),
            bank_account_id=uuid4(),
            allocations=[],
        )

        with patch("app.services.finance.ar.customer_payment.Customer"):
            with pytest.raises(NotFoundError):
                CustomerPaymentService.create_payment(
                    mock_db, org_id, payment_input, user_id
                )


class TestPostPayment:
    """Tests for post_payment method."""

    def test_post_pending_payment(self, mock_db, org_id, user_id):
        """Test posting a pending payment."""
        from app.models.finance.ar.customer_payment import PaymentStatus
        from app.services.finance.ar.customer_payment import CustomerPaymentService

        customer = MockCustomer(organization_id=org_id)

        payment = MockCustomerPayment(
            organization_id=org_id,
            customer_id=customer.customer_id,
            status=PaymentStatus.PENDING,
            amount=Decimal("1000.00"),
        )
        payment.allocations = [
            MockPaymentAllocation(
                payment_id=payment.payment_id,
                invoice_id=uuid4(),
                allocated_amount=Decimal("1000.00"),
            )
        ]

        def mock_get(model, id):
            if str(id) == str(payment.payment_id):
                return payment
            elif str(id) == str(customer.customer_id):
                return customer
            return None

        mock_db.get.side_effect = mock_get
        # Mock query for allocations
        mock_db.query.return_value.filter.return_value.all.return_value = (
            payment.allocations
        )

        with patch("app.services.finance.ar.customer_payment.CustomerPayment"):
            with patch("app.services.finance.ar.customer_payment.Customer"):
                with patch(
                    "app.services.finance.ar.customer_payment.PaymentAllocation"
                ):
                    # Patch at source modules since imports are local
                    with patch(
                        "app.services.finance.gl.journal.JournalService"
                    ) as mock_journal:
                        with patch(
                            "app.services.finance.gl.ledger_posting.LedgerPostingService"
                        ) as mock_posting:
                            mock_journal.create_journal.return_value = MagicMock(
                                journal_entry_id=uuid4()
                            )
                            mock_posting.post_journal_entry.return_value = MagicMock(
                                success=True
                            )
                            result = CustomerPaymentService.post_payment(
                                mock_db, org_id, payment.payment_id, user_id
                            )

        assert result.status == PaymentStatus.CLEARED
        mock_db.commit.assert_called()


class TestMarkBouncedPayment:
    """Tests for mark_bounced method."""

    def test_mark_bounced_pending_payment(self, mock_db, org_id):
        """Test marking a pending payment as bounced."""
        from app.models.finance.ar.customer_payment import PaymentStatus
        from app.services.finance.ar.customer_payment import CustomerPaymentService

        payment = MockCustomerPayment(
            organization_id=org_id,
            status=PaymentStatus.PENDING,
            amount=Decimal("1000.00"),
        )
        mock_db.get.return_value = payment

        with patch("app.services.finance.ar.customer_payment.CustomerPayment"):
            result = CustomerPaymentService.mark_bounced(
                mock_db, org_id, payment.payment_id, "NSF"
            )

        assert result.status == PaymentStatus.BOUNCED
        mock_db.commit.assert_called()

    def test_mark_bounced_cleared_payment(self, mock_db, org_id):
        """Test marking a cleared payment as bounced reverses allocations."""
        from app.models.finance.ar.customer_payment import PaymentStatus
        from app.services.finance.ar.customer_payment import CustomerPaymentService

        payment = MockCustomerPayment(
            organization_id=org_id,
            status=PaymentStatus.CLEARED,
            amount=Decimal("1000.00"),
        )
        payment.allocations = [
            MockPaymentAllocation(
                payment_id=payment.payment_id,
                invoice_id=uuid4(),
                allocated_amount=Decimal("1000.00"),
            )
        ]

        invoice = MockInvoice(
            invoice_id=payment.allocations[0].invoice_id,
            organization_id=org_id,
            total_amount=Decimal("1000.00"),
            amount_paid=Decimal("1000.00"),  # Was paid
            status=MockInvoiceStatus.PAID,
        )

        def mock_get(model, id):
            if str(id) == str(payment.payment_id):
                return payment
            elif str(id) == str(invoice.invoice_id):
                return invoice
            return None

        mock_db.get.side_effect = mock_get
        mock_db.query.return_value.filter.return_value.all.return_value = (
            payment.allocations
        )

        with patch("app.services.finance.ar.customer_payment.CustomerPayment"):
            with patch("app.services.finance.ar.customer_payment.Invoice"):
                with patch(
                    "app.services.finance.ar.customer_payment.PaymentAllocation"
                ):
                    result = CustomerPaymentService.mark_bounced(
                        mock_db, org_id, payment.payment_id, "NSF"
                    )

        assert result.status == PaymentStatus.BOUNCED
        mock_db.commit.assert_called()


class TestVoidCustomerPayment:
    """Tests for void_payment method."""

    def test_void_pending_payment(self, mock_db, org_id, user_id):
        """Test voiding a pending payment."""
        from app.models.finance.ar.customer_payment import PaymentStatus
        from app.services.finance.ar.customer_payment import CustomerPaymentService

        payment = MockCustomerPayment(
            organization_id=org_id,
            status=PaymentStatus.PENDING,
        )
        mock_db.get.return_value = payment

        with patch("app.services.finance.ar.customer_payment.CustomerPayment"):
            result = CustomerPaymentService.void_payment(
                mock_db, org_id, payment.payment_id, user_id, "Duplicate"
            )

        assert result.status == PaymentStatus.VOID
        mock_db.commit.assert_called()

    def test_void_already_void_fails(self, mock_db, org_id, user_id):
        """Test that voiding already voided payment fails."""
        from app.models.finance.ar.customer_payment import PaymentStatus
        from app.services.common import ValidationError
        from app.services.finance.ar.customer_payment import CustomerPaymentService

        payment = MockCustomerPayment(
            organization_id=org_id,
            status=PaymentStatus.VOID,
        )
        mock_db.get.return_value = payment

        with patch("app.services.finance.ar.customer_payment.CustomerPayment"):
            with pytest.raises(ValidationError, match="already voided"):
                CustomerPaymentService.void_payment(
                    mock_db, org_id, payment.payment_id, user_id, "Error"
                )


class TestGetCustomerPayment:
    """Tests for get method."""

    def test_get_existing_payment(self, mock_db, org_id):
        """Test getting existing payment."""
        from app.services.finance.ar.customer_payment import CustomerPaymentService

        payment = MockCustomerPayment(organization_id=org_id)
        mock_db.get.return_value = payment

        with patch("app.services.finance.ar.customer_payment.CustomerPayment"):
            result = CustomerPaymentService.get(mock_db, str(payment.payment_id), org_id)

        assert result == payment

    def test_get_nonexistent_raises(self, mock_db, org_id):
        """Test getting non-existent payment raises exception."""
        from app.services.common import NotFoundError
        from app.services.finance.ar.customer_payment import CustomerPaymentService

        mock_db.get.return_value = None

        with patch("app.services.finance.ar.customer_payment.CustomerPayment"):
            with pytest.raises(NotFoundError):
                CustomerPaymentService.get(mock_db, str(uuid4()), org_id)

    def test_get_wrong_org_raises(self, mock_db, org_id):
        """Test getting payment from wrong org raises NotFoundError."""
        from app.services.common import NotFoundError
        from app.services.finance.ar.customer_payment import CustomerPaymentService

        payment = MockCustomerPayment(organization_id=org_id)
        mock_db.get.return_value = payment
        wrong_org = uuid4()

        with patch("app.services.finance.ar.customer_payment.CustomerPayment"):
            with pytest.raises(NotFoundError):
                CustomerPaymentService.get(mock_db, str(payment.payment_id), wrong_org)


class TestListCustomerPayments:
    """Tests for list method."""

    def test_list_with_filters(self, mock_db, org_id):
        """Test listing payments with filters."""
        from app.models.finance.ar.customer_payment import PaymentStatus
        from app.services.finance.ar.customer_payment import CustomerPaymentService

        payments = [MockCustomerPayment(organization_id=org_id)]
        mock_query = MagicMock()
        mock_query.filter.return_value = mock_query
        mock_query.order_by.return_value = mock_query
        mock_query.limit.return_value = mock_query
        mock_query.offset.return_value = mock_query
        mock_query.all.return_value = payments
        mock_db.query.return_value = mock_query

        with patch("app.services.finance.ar.customer_payment.CustomerPayment"):
            result = CustomerPaymentService.list(
                mock_db,
                organization_id=str(org_id),
                status=PaymentStatus.PENDING,
            )

        assert result == payments


class TestGetPaymentAllocations:
    """Tests for get_payment_allocations method."""

    def test_get_allocations(self, mock_db, org_id):
        """Test getting allocations for a payment."""
        from app.services.finance.ar.customer_payment import CustomerPaymentService

        payment_id = uuid4()
        payment = MockCustomerPayment(
            payment_id=payment_id,
            organization_id=org_id,
        )
        allocations = [
            MockPaymentAllocation(payment_id=payment_id),
            MockPaymentAllocation(payment_id=payment_id),
        ]

        # Mock db.get to find the payment
        mock_db.get.return_value = payment
        # Mock the query for allocations
        mock_db.query.return_value.filter.return_value.all.return_value = allocations

        with patch("app.services.finance.ar.customer_payment.CustomerPayment"):
            with patch("app.services.finance.ar.customer_payment.PaymentAllocation"):
                result = CustomerPaymentService.get_payment_allocations(
                    mock_db, org_id, payment_id
                )

        assert len(result) == 2
