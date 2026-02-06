"""
Tests for SupplierPaymentService.
"""

from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from tests.ifrs.ap.conftest import (
    MockSupplier,
    MockSupplierInvoice,
    MockSupplierPayment,
    MockAPPaymentAllocation,
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


class TestCreateSupplierPayment:
    """Tests for create_payment method."""

    def test_create_payment_success(self, mock_db, org_id, user_id):
        """Test successful payment creation."""
        from app.services.finance.ap.supplier_payment import (
            SupplierPaymentService,
            SupplierPaymentInput,
            PaymentAllocationInput,
        )
        from app.models.finance.ap.supplier_payment import APPaymentMethod
        from app.models.finance.ap.supplier_invoice import SupplierInvoiceStatus

        supplier = MockSupplier(organization_id=org_id)

        # Mock invoice query
        invoice = MockSupplierInvoice(
            organization_id=org_id,
            supplier_id=supplier.supplier_id,
            total_amount=Decimal("1000.00"),
            amount_paid=Decimal("0"),
            status=SupplierInvoiceStatus.POSTED,
        )

        # Mock db.get to return different objects based on what's queried
        def mock_get(model, id):
            if str(id) == str(supplier.supplier_id):
                return supplier
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

        payment_input = SupplierPaymentInput(
            supplier_id=supplier.supplier_id,
            payment_date=date.today(),
            payment_method=APPaymentMethod.CHECK,
            currency_code="USD",
            amount=Decimal("1000.00"),
            bank_account_id=uuid4(),
            allocations=allocations,
        )

        with patch("app.services.finance.ap.supplier_payment.Supplier"):
            with patch(
                "app.services.finance.ap.supplier_payment.SupplierPayment"
            ) as MockPay:
                mock_payment = MockSupplierPayment(
                    organization_id=org_id,
                    supplier_id=supplier.supplier_id,
                )
                MockPay.return_value = mock_payment

                with patch(
                    "app.services.finance.ap.supplier_payment.APPaymentAllocation"
                ):
                    with patch(
                        "app.services.finance.ap.supplier_payment.SupplierInvoice"
                    ):
                        with patch(
                            "app.services.finance.ap.supplier_payment.SequenceService.get_next_number",
                            return_value="PAY-0001",
                        ):
                            result = SupplierPaymentService.create_payment(
                                mock_db, org_id, payment_input, user_id
                            )

        mock_db.add.assert_called()
        mock_db.commit.assert_called()

    def test_create_payment_invalid_supplier_fails(self, mock_db, org_id, user_id):
        """Test that invalid supplier fails validation."""
        from fastapi import HTTPException
        from app.services.finance.ap.supplier_payment import (
            SupplierPaymentService,
            SupplierPaymentInput,
        )
        from app.models.finance.ap.supplier_payment import APPaymentMethod

        mock_db.get.return_value = None  # Supplier not found

        payment_input = SupplierPaymentInput(
            supplier_id=uuid4(),
            payment_date=date.today(),
            payment_method=APPaymentMethod.CHECK,
            currency_code="USD",
            amount=Decimal("1000.00"),
            bank_account_id=uuid4(),
            allocations=[],
        )

        with patch("app.services.finance.ap.supplier_payment.Supplier"):
            with pytest.raises(HTTPException) as exc:
                SupplierPaymentService.create_payment(
                    mock_db, org_id, payment_input, user_id
                )

        assert exc.value.status_code == 404


class TestApproveSupplierPayment:
    """Tests for approve_payment method."""

    def test_approve_pending_payment(self, mock_db, org_id, user_id):
        """Test approving a pending payment."""
        from app.services.finance.ap.supplier_payment import SupplierPaymentService
        from app.models.finance.ap.supplier_payment import APPaymentStatus

        creator_id = uuid4()
        payment = MockSupplierPayment(
            organization_id=org_id,
            status=APPaymentStatus.PENDING,
            created_by_user_id=creator_id,
        )
        mock_db.get.return_value = payment

        with patch("app.services.finance.ap.supplier_payment.SupplierPayment"):
            result = SupplierPaymentService.approve_payment(
                mock_db, org_id, payment.payment_id, user_id
            )

        assert result.status == APPaymentStatus.APPROVED
        mock_db.commit.assert_called()

    def test_self_approval_fails_sod(self, mock_db, org_id):
        """Test that self-approval fails segregation of duties."""
        from fastapi import HTTPException
        from app.services.finance.ap.supplier_payment import SupplierPaymentService
        from app.models.finance.ap.supplier_payment import APPaymentStatus

        creator_id = uuid4()
        payment = MockSupplierPayment(
            organization_id=org_id,
            status=APPaymentStatus.PENDING,
            created_by_user_id=creator_id,
        )
        mock_db.get.return_value = payment

        with patch("app.services.finance.ap.supplier_payment.SupplierPayment"):
            with pytest.raises(HTTPException) as exc:
                # Same user tries to approve
                SupplierPaymentService.approve_payment(
                    mock_db, org_id, payment.payment_id, creator_id
                )

        assert exc.value.status_code == 400


class TestPostSupplierPayment:
    """Tests for post_payment method."""

    def test_post_approved_payment(self, mock_db, org_id, user_id):
        """Test posting an approved payment."""
        from app.services.finance.ap.supplier_payment import SupplierPaymentService
        from app.models.finance.ap.supplier_payment import APPaymentStatus

        payment = MockSupplierPayment(
            organization_id=org_id,
            status=APPaymentStatus.APPROVED,
            amount=Decimal("1000.00"),
        )
        mock_db.get.return_value = payment

        # Mock the posting adapter to avoid deep integration
        with patch("app.services.finance.ap.supplier_payment.SupplierPayment"):
            with patch(
                "app.services.finance.ap.ap_posting_adapter.APPostingAdapter.post_payment"
            ) as mock_post:
                mock_post.return_value = MagicMock()  # Return a mock journal
                result = SupplierPaymentService.post_payment(
                    mock_db, org_id, payment.payment_id, user_id
                )

        assert result.status == APPaymentStatus.SENT
        mock_db.commit.assert_called()


class TestVoidSupplierPayment:
    """Tests for void_payment method."""

    def test_void_draft_payment(self, mock_db, org_id, user_id):
        """Test voiding a draft payment."""
        from app.services.finance.ap.supplier_payment import SupplierPaymentService
        from app.models.finance.ap.supplier_payment import APPaymentStatus

        payment = MockSupplierPayment(
            organization_id=org_id,
            status=APPaymentStatus.DRAFT,
        )
        mock_db.get.return_value = payment

        with patch("app.services.finance.ap.supplier_payment.SupplierPayment"):
            result = SupplierPaymentService.void_payment(
                mock_db, org_id, payment.payment_id, user_id, "Duplicate"
            )

        assert result.status == APPaymentStatus.VOID
        mock_db.commit.assert_called()

    def test_void_cleared_payment_fails(self, mock_db, org_id, user_id):
        """Test that voiding cleared payment fails."""
        from fastapi import HTTPException
        from app.services.finance.ap.supplier_payment import SupplierPaymentService
        from app.models.finance.ap.supplier_payment import APPaymentStatus

        payment = MockSupplierPayment(
            organization_id=org_id,
            status=APPaymentStatus.CLEARED,
        )
        mock_db.get.return_value = payment

        with patch("app.services.finance.ap.supplier_payment.SupplierPayment"):
            with pytest.raises(HTTPException) as exc:
                SupplierPaymentService.void_payment(
                    mock_db, org_id, payment.payment_id, user_id, "Error"
                )

        assert exc.value.status_code == 400


class TestGetSupplierPayment:
    """Tests for get method."""

    def test_get_existing_payment(self, mock_db, org_id):
        """Test getting existing payment."""
        from app.services.finance.ap.supplier_payment import SupplierPaymentService

        payment = MockSupplierPayment(organization_id=org_id)
        mock_db.get.return_value = payment

        with patch("app.services.finance.ap.supplier_payment.SupplierPayment"):
            result = SupplierPaymentService.get(mock_db, str(payment.payment_id))

        assert result == payment

    def test_get_nonexistent_raises(self, mock_db):
        """Test getting non-existent payment raises exception."""
        from fastapi import HTTPException
        from app.services.finance.ap.supplier_payment import SupplierPaymentService

        mock_db.get.return_value = None

        with patch("app.services.finance.ap.supplier_payment.SupplierPayment"):
            with pytest.raises(HTTPException) as exc:
                SupplierPaymentService.get(mock_db, str(uuid4()))

        assert exc.value.status_code == 404


class TestListSupplierPayments:
    """Tests for list method."""

    def test_list_with_filters(self, mock_db, org_id):
        """Test listing payments with filters."""
        from app.services.finance.ap.supplier_payment import SupplierPaymentService
        from app.models.finance.ap.supplier_payment import APPaymentStatus

        payments = [MockSupplierPayment(organization_id=org_id)]
        # list() now uses db.scalars(select(...).where(...).order_by(...).limit().offset()).all()
        mock_db.scalars.return_value.all.return_value = payments

        result = SupplierPaymentService.list(
            mock_db,
            organization_id=str(org_id),
            status=APPaymentStatus.DRAFT,
        )

        assert result == payments


class TestGetPaymentAllocations:
    """Tests for get_payment_allocations method."""

    def test_get_allocations(self, mock_db, org_id):
        """Test getting allocations for a payment."""
        from app.services.finance.ap.supplier_payment import SupplierPaymentService

        payment_id = uuid4()
        payment = MockSupplierPayment(
            payment_id=payment_id,
            organization_id=org_id,
        )
        allocations = [
            MockAPPaymentAllocation(payment_id=payment_id),
            MockAPPaymentAllocation(payment_id=payment_id),
        ]

        # Mock db.get to find the payment
        mock_db.get.return_value = payment
        # get_payment_allocations now uses db.scalars(select(...).where(...)).all()
        mock_db.scalars.return_value.all.return_value = allocations

        result = SupplierPaymentService.get_payment_allocations(
            mock_db, org_id, payment_id
        )

        assert len(result) == 2
