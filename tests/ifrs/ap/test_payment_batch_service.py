"""
Tests for PaymentBatchService.

Tests payment batch creation, approval, processing, and bank file generation.
"""

import pytest
from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch, PropertyMock
from uuid import uuid4

from fastapi import HTTPException

from app.models.ifrs.ap.payment_batch import APBatchStatus
from app.models.ifrs.ap.supplier_payment import APPaymentStatus
from app.services.ifrs.ap.payment_batch import (
    PaymentBatchService,
    PaymentBatchInput,
    BatchPaymentItem,
)


class MockPaymentBatch:
    """Mock APPaymentBatch model."""

    def __init__(
        self,
        batch_id=None,
        organization_id=None,
        batch_number="BATCH-PMT-202601-0001",
        batch_date=None,
        payment_method="BANK_TRANSFER",
        bank_account_id=None,
        currency_code="USD",
        total_payments=0,
        total_amount=Decimal("0"),
        status=APBatchStatus.DRAFT,
        created_by_user_id=None,
        approved_by_user_id=None,
        approved_at=None,
        bank_file_generated=False,
        bank_file_reference=None,
        bank_file_generated_at=None,
    ):
        self.batch_id = batch_id or uuid4()
        self.organization_id = organization_id or uuid4()
        self.batch_number = batch_number
        self.batch_date = batch_date or date.today()
        self.payment_method = payment_method
        self.bank_account_id = bank_account_id or uuid4()
        self.currency_code = currency_code
        self.total_payments = total_payments
        self.total_amount = total_amount
        self.status = status
        self.created_by_user_id = created_by_user_id or uuid4()
        self.approved_by_user_id = approved_by_user_id
        self.approved_at = approved_at
        self.bank_file_generated = bank_file_generated
        self.bank_file_reference = bank_file_reference
        self.bank_file_generated_at = bank_file_generated_at


class MockSupplierPayment:
    """Mock SupplierPayment model."""

    def __init__(
        self,
        payment_id=None,
        organization_id=None,
        supplier_id=None,
        payment_number="PMT-202601-0001",
        amount=Decimal("100.00"),
        status=APPaymentStatus.DRAFT,
        payment_batch_id=None,
        reference=None,
        approved_by_user_id=None,
        approved_at=None,
    ):
        self.payment_id = payment_id or uuid4()
        self.organization_id = organization_id or uuid4()
        self.supplier_id = supplier_id or uuid4()
        self.payment_number = payment_number
        self.amount = amount
        self.status = status
        self.payment_batch_id = payment_batch_id
        self.reference = reference
        self.approved_by_user_id = approved_by_user_id
        self.approved_at = approved_at


class MockSupplier:
    """Mock Supplier model."""

    def __init__(self, supplier_id=None, name="Test Supplier"):
        self.supplier_id = supplier_id or uuid4()
        self.name = name


@pytest.fixture
def org_id():
    return uuid4()


@pytest.fixture
def user_id():
    return uuid4()


@pytest.fixture
def mock_db():
    return MagicMock()


@pytest.fixture
def batch_input():
    return PaymentBatchInput(
        batch_date=date.today(),
        payment_method="BANK_TRANSFER",
        bank_account_id=uuid4(),
        currency_code="USD",
        payments=[
            BatchPaymentItem(
                supplier_id=uuid4(),
                amount=Decimal("100.00"),
                invoice_ids=[uuid4()],
                reference="INV-001",
            ),
            BatchPaymentItem(
                supplier_id=uuid4(),
                amount=Decimal("200.00"),
                invoice_ids=[uuid4()],
                reference="INV-002",
            ),
        ],
    )


class TestCreateBatch:
    """Tests for create_batch method."""

    @patch("app.services.ifrs.ap.payment_batch.SequenceService")
    def test_create_batch_success(self, mock_sequence, mock_db, org_id, user_id, batch_input):
        """Test successful batch creation."""
        mock_sequence.get_next_number.return_value = "PMT-202601-0001"

        result = PaymentBatchService.create_batch(
            mock_db, org_id, batch_input, user_id
        )

        mock_db.add.assert_called_once()
        mock_db.flush.assert_called_once()
        mock_db.commit.assert_called_once()

    @patch("app.services.ifrs.ap.payment_batch.SequenceService")
    def test_create_batch_calculates_totals(self, mock_sequence, mock_db, org_id, user_id, batch_input):
        """Test that batch creation calculates correct totals."""
        mock_sequence.get_next_number.return_value = "PMT-202601-0001"

        result = PaymentBatchService.create_batch(
            mock_db, org_id, batch_input, user_id
        )

        # The batch added should have correct totals
        added_batch = mock_db.add.call_args[0][0]
        assert added_batch.total_payments == 2
        assert added_batch.total_amount == Decimal("300.00")

    def test_create_batch_empty_payments_fails(self, mock_db, org_id, user_id):
        """Test that creating batch with no payments fails."""
        batch_input = PaymentBatchInput(
            batch_date=date.today(),
            payment_method="BANK_TRANSFER",
            bank_account_id=uuid4(),
            currency_code="USD",
            payments=[],
        )

        with pytest.raises(HTTPException) as exc:
            PaymentBatchService.create_batch(mock_db, org_id, batch_input, user_id)

        assert exc.value.status_code == 400
        assert "at least one payment" in exc.value.detail


class TestAddPaymentToBatch:
    """Tests for add_payment_to_batch method."""

    def test_add_payment_success(self, mock_db, org_id):
        """Test successful payment addition to batch."""
        batch = MockPaymentBatch(
            organization_id=org_id,
            status=APBatchStatus.DRAFT,
            total_payments=1,
            total_amount=Decimal("100.00"),
        )
        payment = MockSupplierPayment(
            organization_id=org_id,
            status=APPaymentStatus.DRAFT,
            amount=Decimal("50.00"),
            payment_batch_id=None,
        )

        # Setup query chain for batch and payment
        mock_query = MagicMock()
        mock_query.filter.return_value.first.side_effect = [batch, payment]
        mock_db.query.return_value = mock_query

        result = PaymentBatchService.add_payment_to_batch(
            mock_db, org_id, batch.batch_id, payment.payment_id
        )

        assert payment.payment_batch_id == batch.batch_id
        assert batch.total_payments == 2
        assert batch.total_amount == Decimal("150.00")
        mock_db.commit.assert_called_once()

    def test_add_payment_batch_not_found(self, mock_db, org_id):
        """Test adding payment to non-existent batch."""
        mock_query = MagicMock()
        mock_query.filter.return_value.first.return_value = None
        mock_db.query.return_value = mock_query

        with pytest.raises(HTTPException) as exc:
            PaymentBatchService.add_payment_to_batch(
                mock_db, org_id, uuid4(), uuid4()
            )

        assert exc.value.status_code == 404
        assert "Payment batch not found" in exc.value.detail

    def test_add_payment_batch_not_draft(self, mock_db, org_id):
        """Test adding payment to non-draft batch fails."""
        batch = MockPaymentBatch(
            organization_id=org_id,
            status=APBatchStatus.APPROVED,
        )

        mock_query = MagicMock()
        mock_query.filter.return_value.first.return_value = batch
        mock_db.query.return_value = mock_query

        with pytest.raises(HTTPException) as exc:
            PaymentBatchService.add_payment_to_batch(
                mock_db, org_id, batch.batch_id, uuid4()
            )

        assert exc.value.status_code == 400
        assert "Cannot modify batch" in exc.value.detail

    def test_add_payment_not_found(self, mock_db, org_id):
        """Test adding non-existent payment."""
        batch = MockPaymentBatch(organization_id=org_id, status=APBatchStatus.DRAFT)

        mock_query = MagicMock()
        mock_query.filter.return_value.first.side_effect = [batch, None]
        mock_db.query.return_value = mock_query

        with pytest.raises(HTTPException) as exc:
            PaymentBatchService.add_payment_to_batch(
                mock_db, org_id, batch.batch_id, uuid4()
            )

        assert exc.value.status_code == 404
        assert "Payment not found" in exc.value.detail

    def test_add_payment_already_in_batch(self, mock_db, org_id):
        """Test adding payment already in another batch."""
        batch = MockPaymentBatch(organization_id=org_id, status=APBatchStatus.DRAFT)
        payment = MockSupplierPayment(
            organization_id=org_id,
            payment_batch_id=uuid4(),  # Already in a batch
        )

        mock_query = MagicMock()
        mock_query.filter.return_value.first.side_effect = [batch, payment]
        mock_db.query.return_value = mock_query

        with pytest.raises(HTTPException) as exc:
            PaymentBatchService.add_payment_to_batch(
                mock_db, org_id, batch.batch_id, payment.payment_id
            )

        assert exc.value.status_code == 400
        assert "already belongs to a batch" in exc.value.detail

    def test_add_payment_wrong_status(self, mock_db, org_id):
        """Test adding payment with wrong status."""
        batch = MockPaymentBatch(organization_id=org_id, status=APBatchStatus.DRAFT)
        payment = MockSupplierPayment(
            organization_id=org_id,
            status=APPaymentStatus.CLEARED,  # Wrong status - cannot add cleared payment
            payment_batch_id=None,
        )

        mock_query = MagicMock()
        mock_query.filter.return_value.first.side_effect = [batch, payment]
        mock_db.query.return_value = mock_query

        with pytest.raises(HTTPException) as exc:
            PaymentBatchService.add_payment_to_batch(
                mock_db, org_id, batch.batch_id, payment.payment_id
            )

        assert exc.value.status_code == 400
        assert "Cannot add payment with status" in exc.value.detail


class TestRemovePaymentFromBatch:
    """Tests for remove_payment_from_batch method."""

    def test_remove_payment_success(self, mock_db, org_id):
        """Test successful payment removal from batch."""
        batch = MockPaymentBatch(
            organization_id=org_id,
            status=APBatchStatus.DRAFT,
            total_payments=2,
            total_amount=Decimal("150.00"),
        )
        payment = MockSupplierPayment(
            payment_batch_id=batch.batch_id,
            amount=Decimal("50.00"),
        )

        mock_query = MagicMock()
        mock_query.filter.return_value.first.side_effect = [batch, payment]
        mock_db.query.return_value = mock_query

        result = PaymentBatchService.remove_payment_from_batch(
            mock_db, org_id, batch.batch_id, payment.payment_id
        )

        assert payment.payment_batch_id is None
        assert batch.total_payments == 1
        assert batch.total_amount == Decimal("100.00")
        mock_db.commit.assert_called_once()

    def test_remove_payment_batch_not_found(self, mock_db, org_id):
        """Test removing payment from non-existent batch."""
        mock_query = MagicMock()
        mock_query.filter.return_value.first.return_value = None
        mock_db.query.return_value = mock_query

        with pytest.raises(HTTPException) as exc:
            PaymentBatchService.remove_payment_from_batch(
                mock_db, org_id, uuid4(), uuid4()
            )

        assert exc.value.status_code == 404

    def test_remove_payment_batch_not_draft(self, mock_db, org_id):
        """Test removing payment from non-draft batch fails."""
        batch = MockPaymentBatch(
            organization_id=org_id,
            status=APBatchStatus.PROCESSING,
        )

        mock_query = MagicMock()
        mock_query.filter.return_value.first.return_value = batch
        mock_db.query.return_value = mock_query

        with pytest.raises(HTTPException) as exc:
            PaymentBatchService.remove_payment_from_batch(
                mock_db, org_id, batch.batch_id, uuid4()
            )

        assert exc.value.status_code == 400
        assert "Cannot modify batch" in exc.value.detail

    def test_remove_payment_not_in_batch(self, mock_db, org_id):
        """Test removing payment not in the batch."""
        batch = MockPaymentBatch(organization_id=org_id, status=APBatchStatus.DRAFT)

        mock_query = MagicMock()
        mock_query.filter.return_value.first.side_effect = [batch, None]
        mock_db.query.return_value = mock_query

        with pytest.raises(HTTPException) as exc:
            PaymentBatchService.remove_payment_from_batch(
                mock_db, org_id, batch.batch_id, uuid4()
            )

        assert exc.value.status_code == 404
        assert "Payment not found in this batch" in exc.value.detail


class TestApproveBatch:
    """Tests for approve_batch method."""

    def test_approve_batch_success(self, mock_db, org_id):
        """Test successful batch approval."""
        creator_id = uuid4()
        approver_id = uuid4()

        batch = MockPaymentBatch(
            organization_id=org_id,
            status=APBatchStatus.DRAFT,
            created_by_user_id=creator_id,
        )
        payments = [
            MockSupplierPayment(status=APPaymentStatus.DRAFT),
            MockSupplierPayment(status=APPaymentStatus.DRAFT),
        ]

        mock_query = MagicMock()
        mock_query.filter.return_value.first.return_value = batch
        mock_query.filter.return_value.count.return_value = 2
        mock_query.filter.return_value.all.return_value = payments
        mock_db.query.return_value = mock_query

        result = PaymentBatchService.approve_batch(
            mock_db, org_id, batch.batch_id, approver_id
        )

        assert batch.status == APBatchStatus.APPROVED
        assert batch.approved_by_user_id == approver_id
        assert batch.approved_at is not None
        # Payments should also be approved
        for payment in payments:
            assert payment.status == APPaymentStatus.APPROVED
        mock_db.commit.assert_called_once()

    def test_approve_batch_not_found(self, mock_db, org_id, user_id):
        """Test approving non-existent batch."""
        mock_query = MagicMock()
        mock_query.filter.return_value.first.return_value = None
        mock_db.query.return_value = mock_query

        with pytest.raises(HTTPException) as exc:
            PaymentBatchService.approve_batch(mock_db, org_id, uuid4(), user_id)

        assert exc.value.status_code == 404

    def test_approve_batch_not_draft(self, mock_db, org_id, user_id):
        """Test approving non-draft batch fails."""
        batch = MockPaymentBatch(
            organization_id=org_id,
            status=APBatchStatus.COMPLETED,
        )

        mock_query = MagicMock()
        mock_query.filter.return_value.first.return_value = batch
        mock_db.query.return_value = mock_query

        with pytest.raises(HTTPException) as exc:
            PaymentBatchService.approve_batch(
                mock_db, org_id, batch.batch_id, user_id
            )

        assert exc.value.status_code == 400
        assert "Cannot approve batch" in exc.value.detail

    def test_approve_batch_sod_violation(self, mock_db, org_id):
        """Test approval by creator fails (SoD violation)."""
        user_id = uuid4()
        batch = MockPaymentBatch(
            organization_id=org_id,
            status=APBatchStatus.DRAFT,
            created_by_user_id=user_id,  # Same as approver
        )

        mock_query = MagicMock()
        mock_query.filter.return_value.first.return_value = batch
        mock_db.query.return_value = mock_query

        with pytest.raises(HTTPException) as exc:
            PaymentBatchService.approve_batch(
                mock_db, org_id, batch.batch_id, user_id
            )

        assert exc.value.status_code == 400
        assert "Segregation of duties" in exc.value.detail

    def test_approve_empty_batch_fails(self, mock_db, org_id):
        """Test approving batch with no payments fails."""
        creator_id = uuid4()
        approver_id = uuid4()

        batch = MockPaymentBatch(
            organization_id=org_id,
            status=APBatchStatus.DRAFT,
            created_by_user_id=creator_id,
        )

        mock_query = MagicMock()
        mock_query.filter.return_value.first.return_value = batch
        mock_query.filter.return_value.count.return_value = 0  # No payments
        mock_db.query.return_value = mock_query

        with pytest.raises(HTTPException) as exc:
            PaymentBatchService.approve_batch(
                mock_db, org_id, batch.batch_id, approver_id
            )

        assert exc.value.status_code == 400
        assert "Cannot approve empty batch" in exc.value.detail


class TestProcessBatch:
    """Tests for process_batch method."""

    @patch("app.services.ifrs.ap.supplier_payment.SupplierPaymentService")
    def test_process_batch_success(self, mock_payment_service, mock_db, org_id, user_id):
        """Test successful batch processing."""
        batch = MockPaymentBatch(
            organization_id=org_id,
            status=APBatchStatus.APPROVED,
        )
        payments = [
            MockSupplierPayment(status=APPaymentStatus.APPROVED),
            MockSupplierPayment(status=APPaymentStatus.APPROVED),
        ]

        mock_query = MagicMock()
        mock_query.filter.return_value.first.return_value = batch
        mock_query.filter.return_value.all.return_value = payments
        mock_db.query.return_value = mock_query

        result = PaymentBatchService.process_batch(
            mock_db, org_id, batch.batch_id, user_id
        )

        assert batch.status == APBatchStatus.COMPLETED
        assert mock_payment_service.post_payment.call_count == 2
        mock_db.commit.assert_called_once()

    @patch("app.services.ifrs.ap.supplier_payment.SupplierPaymentService")
    def test_process_batch_partial_failure(self, mock_payment_service, mock_db, org_id, user_id):
        """Test batch processing with partial failure."""
        batch = MockPaymentBatch(
            organization_id=org_id,
            status=APBatchStatus.APPROVED,
        )
        payments = [
            MockSupplierPayment(status=APPaymentStatus.APPROVED),
            MockSupplierPayment(status=APPaymentStatus.APPROVED),
        ]

        mock_query = MagicMock()
        mock_query.filter.return_value.first.return_value = batch
        mock_query.filter.return_value.all.return_value = payments
        mock_db.query.return_value = mock_query

        # First succeeds, second fails
        mock_payment_service.post_payment.side_effect = [
            None,
            HTTPException(status_code=400, detail="Processing failed"),
        ]

        result = PaymentBatchService.process_batch(
            mock_db, org_id, batch.batch_id, user_id
        )

        assert batch.status == APBatchStatus.FAILED
        assert payments[1].status == APPaymentStatus.REJECTED
        mock_db.commit.assert_called_once()

    def test_process_batch_not_found(self, mock_db, org_id, user_id):
        """Test processing non-existent batch."""
        mock_query = MagicMock()
        mock_query.filter.return_value.first.return_value = None
        mock_db.query.return_value = mock_query

        with pytest.raises(HTTPException) as exc:
            PaymentBatchService.process_batch(mock_db, org_id, uuid4(), user_id)

        assert exc.value.status_code == 404

    def test_process_batch_not_approved(self, mock_db, org_id, user_id):
        """Test processing non-approved batch fails."""
        batch = MockPaymentBatch(
            organization_id=org_id,
            status=APBatchStatus.DRAFT,
        )

        mock_query = MagicMock()
        mock_query.filter.return_value.first.return_value = batch
        mock_db.query.return_value = mock_query

        with pytest.raises(HTTPException) as exc:
            PaymentBatchService.process_batch(
                mock_db, org_id, batch.batch_id, user_id
            )

        assert exc.value.status_code == 400
        assert "Cannot process batch" in exc.value.detail


class TestGenerateBankFile:
    """Tests for generate_bank_file method."""

    def test_generate_bank_file_success(self, mock_db, org_id):
        """Test successful bank file generation."""
        batch = MockPaymentBatch(
            organization_id=org_id,
            status=APBatchStatus.APPROVED,
            total_amount=Decimal("300.00"),
            currency_code="USD",
        )
        supplier = MockSupplier(name="Acme Corp")
        payments = [
            MockSupplierPayment(
                supplier_id=supplier.supplier_id,
                payment_number="PMT-001",
                amount=Decimal("100.00"),
                reference="INV-001",
            ),
            MockSupplierPayment(
                supplier_id=supplier.supplier_id,
                payment_number="PMT-002",
                amount=Decimal("200.00"),
                reference="INV-002",
            ),
        ]

        mock_query = MagicMock()
        mock_query.filter.return_value.first.side_effect = [batch, supplier, supplier]
        mock_query.filter.return_value.all.return_value = payments
        mock_db.query.return_value = mock_query

        result = PaymentBatchService.generate_bank_file(
            mock_db, org_id, batch.batch_id, file_format="ACH"
        )

        assert "file_reference" in result
        assert result["file_format"] == "ACH"
        assert "content" in result
        assert result["payment_count"] == 2
        assert batch.bank_file_generated is True
        assert batch.bank_file_reference is not None
        mock_db.commit.assert_called_once()

    def test_generate_bank_file_not_found(self, mock_db, org_id):
        """Test generating bank file for non-existent batch."""
        mock_query = MagicMock()
        mock_query.filter.return_value.first.return_value = None
        mock_db.query.return_value = mock_query

        with pytest.raises(HTTPException) as exc:
            PaymentBatchService.generate_bank_file(mock_db, org_id, uuid4())

        assert exc.value.status_code == 404

    def test_generate_bank_file_wrong_status(self, mock_db, org_id):
        """Test generating bank file for draft batch fails."""
        batch = MockPaymentBatch(
            organization_id=org_id,
            status=APBatchStatus.DRAFT,
        )

        mock_query = MagicMock()
        mock_query.filter.return_value.first.return_value = batch
        mock_db.query.return_value = mock_query

        with pytest.raises(HTTPException) as exc:
            PaymentBatchService.generate_bank_file(
                mock_db, org_id, batch.batch_id
            )

        assert exc.value.status_code == 400
        assert "Cannot generate bank file" in exc.value.detail


class TestGetBatchPayments:
    """Tests for get_batch_payments method."""

    def test_get_batch_payments_success(self, mock_db, org_id):
        """Test getting batch payments."""
        batch = MockPaymentBatch(organization_id=org_id)
        payments = [
            MockSupplierPayment(payment_number="PMT-001"),
            MockSupplierPayment(payment_number="PMT-002"),
        ]

        mock_query = MagicMock()
        mock_query.filter.return_value.first.return_value = batch
        mock_query.filter.return_value.order_by.return_value.all.return_value = payments
        mock_db.query.return_value = mock_query

        result = PaymentBatchService.get_batch_payments(
            mock_db, org_id, batch.batch_id
        )

        assert len(result) == 2

    def test_get_batch_payments_not_found(self, mock_db, org_id):
        """Test getting payments for non-existent batch."""
        mock_query = MagicMock()
        mock_query.filter.return_value.first.return_value = None
        mock_db.query.return_value = mock_query

        with pytest.raises(HTTPException) as exc:
            PaymentBatchService.get_batch_payments(mock_db, org_id, uuid4())

        assert exc.value.status_code == 404


class TestGetBatch:
    """Tests for get method."""

    def test_get_batch_success(self, mock_db):
        """Test getting a batch by ID."""
        batch = MockPaymentBatch()

        mock_query = MagicMock()
        mock_query.filter.return_value.first.return_value = batch
        mock_db.query.return_value = mock_query

        result = PaymentBatchService.get(mock_db, str(batch.batch_id))

        assert result == batch

    def test_get_batch_not_found(self, mock_db):
        """Test getting non-existent batch."""
        mock_query = MagicMock()
        mock_query.filter.return_value.first.return_value = None
        mock_db.query.return_value = mock_query

        result = PaymentBatchService.get(mock_db, str(uuid4()))

        assert result is None


class TestListBatches:
    """Tests for list method."""

    def test_list_batches_all(self, mock_db):
        """Test listing all batches."""
        batches = [MockPaymentBatch(), MockPaymentBatch()]

        mock_query = MagicMock()
        mock_query.order_by.return_value.offset.return_value.limit.return_value.all.return_value = batches
        mock_db.query.return_value = mock_query

        result = PaymentBatchService.list(mock_db)

        assert len(result) == 2

    def test_list_batches_with_org_filter(self, mock_db, org_id):
        """Test listing batches filtered by organization."""
        batches = [MockPaymentBatch(organization_id=org_id)]

        mock_query = MagicMock()
        mock_query.filter.return_value.order_by.return_value.offset.return_value.limit.return_value.all.return_value = batches
        mock_db.query.return_value = mock_query

        result = PaymentBatchService.list(mock_db, organization_id=str(org_id))

        assert len(result) == 1

    def test_list_batches_with_status_filter(self, mock_db, org_id):
        """Test listing batches filtered by status."""
        batches = [MockPaymentBatch(status=APBatchStatus.APPROVED)]

        mock_query = MagicMock()
        mock_query.filter.return_value = mock_query
        mock_query.order_by.return_value.offset.return_value.limit.return_value.all.return_value = batches
        mock_db.query.return_value = mock_query

        result = PaymentBatchService.list(
            mock_db, organization_id=str(org_id), status=APBatchStatus.APPROVED
        )

        assert len(result) == 1

    def test_list_batches_with_date_filter(self, mock_db, org_id):
        """Test listing batches filtered by date range."""
        batches = [MockPaymentBatch()]

        mock_query = MagicMock()
        mock_query.filter.return_value = mock_query
        mock_query.order_by.return_value.offset.return_value.limit.return_value.all.return_value = batches
        mock_db.query.return_value = mock_query

        result = PaymentBatchService.list(
            mock_db,
            organization_id=str(org_id),
            from_date=date(2025, 1, 1),
            to_date=date(2025, 12, 31),
        )

        assert len(result) == 1

    def test_list_batches_pagination(self, mock_db):
        """Test batch list pagination."""
        batches = [MockPaymentBatch()]

        mock_query = MagicMock()
        mock_query.order_by.return_value.offset.return_value.limit.return_value.all.return_value = batches
        mock_db.query.return_value = mock_query

        result = PaymentBatchService.list(mock_db, limit=10, offset=20)

        mock_query.order_by.return_value.offset.assert_called_with(20)
        mock_query.order_by.return_value.offset.return_value.limit.assert_called_with(10)
