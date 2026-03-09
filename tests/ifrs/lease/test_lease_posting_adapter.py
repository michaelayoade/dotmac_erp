"""
Tests for LeasePostingAdapter.
"""

import uuid
from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, patch

from app.models.finance.lease.lease_contract import LeaseStatus


class TestLeasePostingAdapterInitialRecognition:
    """Tests for initial recognition posting."""

    def test_post_initial_recognition_not_found(self, mock_db, org_id, user_id):
        """Test posting non-existent lease fails."""
        from app.services.finance.lease.lease_posting_adapter import LeasePostingAdapter

        mock_db.get.return_value = None

        result = LeasePostingAdapter.post_initial_recognition(
            mock_db,
            org_id,
            uuid.uuid4(),
            date.today(),
            user_id,
        )

        assert result.success is False
        assert "not found" in result.message.lower()

    def test_post_initial_recognition_wrong_status(
        self, mock_db, org_id, user_id, mock_contract
    ):
        """Test posting with wrong status fails."""
        from app.services.finance.lease.lease_posting_adapter import LeasePostingAdapter

        mock_contract.status = LeaseStatus.DRAFT
        mock_db.get.return_value = mock_contract

        result = LeasePostingAdapter.post_initial_recognition(
            mock_db,
            org_id,
            mock_contract.lease_id,
            date.today(),
            user_id,
        )

        assert result.success is False
        assert "ACTIVE" in result.message

    def test_post_initial_recognition_no_liability(
        self, mock_db, org_id, user_id, mock_active_contract
    ):
        """Test posting without liability fails."""
        from app.services.finance.lease.lease_posting_adapter import LeasePostingAdapter

        mock_db.get.return_value = mock_active_contract
        mock_db.scalars.return_value.first.return_value = None

        result = LeasePostingAdapter.post_initial_recognition(
            mock_db,
            org_id,
            mock_active_contract.lease_id,
            date.today(),
            user_id,
        )

        assert result.success is False
        assert "must exist" in result.message.lower()


class TestLeasePostingAdapterInterestAccrual:
    """Tests for interest accrual posting."""

    def test_post_interest_accrual_not_found(self, mock_db, org_id, user_id):
        """Test posting interest on non-existent lease fails."""
        from app.services.finance.lease.lease_posting_adapter import LeasePostingAdapter

        mock_db.get.return_value = None

        result = LeasePostingAdapter.post_interest_accrual(
            mock_db,
            org_id,
            uuid.uuid4(),
            date.today(),
            Decimal("1000.00"),
            user_id,
        )

        assert result.success is False
        assert "not found" in result.message.lower()

    def test_post_interest_accrual_no_liability(
        self, mock_db, org_id, user_id, mock_active_contract
    ):
        """Test posting interest without liability fails."""
        from app.services.finance.lease.lease_posting_adapter import LeasePostingAdapter

        mock_db.get.return_value = mock_active_contract
        mock_db.scalars.return_value.first.return_value = None

        result = LeasePostingAdapter.post_interest_accrual(
            mock_db,
            org_id,
            mock_active_contract.lease_id,
            date.today(),
            Decimal("1000.00"),
            user_id,
        )

        assert result.success is False
        assert "liability" in result.message.lower()

    def test_post_interest_accrual_zero_amount(
        self, mock_db, org_id, user_id, mock_active_contract, mock_liability
    ):
        """Test posting zero interest fails."""
        from app.services.finance.lease.lease_posting_adapter import LeasePostingAdapter

        mock_db.get.return_value = mock_active_contract
        mock_db.scalars.return_value.first.return_value = mock_liability

        result = LeasePostingAdapter.post_interest_accrual(
            mock_db,
            org_id,
            mock_active_contract.lease_id,
            date.today(),
            Decimal("0"),
            user_id,
        )

        assert result.success is False
        assert "positive" in result.message.lower()

    def test_post_interest_accrual_negative_amount(
        self, mock_db, org_id, user_id, mock_active_contract, mock_liability
    ):
        """Test posting negative interest fails."""
        from app.services.finance.lease.lease_posting_adapter import LeasePostingAdapter

        mock_db.get.return_value = mock_active_contract
        mock_db.scalars.return_value.first.return_value = mock_liability

        result = LeasePostingAdapter.post_interest_accrual(
            mock_db,
            org_id,
            mock_active_contract.lease_id,
            date.today(),
            Decimal("-100.00"),
            user_id,
        )

        assert result.success is False
        assert "positive" in result.message.lower()


class TestLeasePostingAdapterPayment:
    """Tests for lease payment posting."""

    def test_post_lease_payment_not_found(self, mock_db, org_id, user_id):
        """Test posting payment on non-existent lease fails."""
        from app.services.finance.lease.lease_posting_adapter import LeasePostingAdapter

        mock_db.get.return_value = None

        result = LeasePostingAdapter.post_lease_payment(
            mock_db,
            org_id,
            uuid.uuid4(),
            date.today(),
            Decimal("5000.00"),
            uuid.uuid4(),  # cash account
            user_id,
        )

        assert result.success is False
        assert "not found" in result.message.lower()

    def test_post_lease_payment_no_liability(
        self, mock_db, org_id, user_id, mock_active_contract
    ):
        """Test posting payment without liability fails."""
        from app.services.finance.lease.lease_posting_adapter import LeasePostingAdapter

        mock_db.get.return_value = mock_active_contract
        mock_db.scalars.return_value.first.return_value = None

        result = LeasePostingAdapter.post_lease_payment(
            mock_db,
            org_id,
            mock_active_contract.lease_id,
            date.today(),
            Decimal("5000.00"),
            uuid.uuid4(),
            user_id,
        )

        assert result.success is False
        assert "liability" in result.message.lower()

    def test_post_lease_payment_zero_amount(
        self, mock_db, org_id, user_id, mock_active_contract, mock_liability
    ):
        """Test posting zero payment fails."""
        from app.services.finance.lease.lease_posting_adapter import LeasePostingAdapter

        mock_db.get.return_value = mock_active_contract
        mock_db.scalars.return_value.first.return_value = mock_liability

        result = LeasePostingAdapter.post_lease_payment(
            mock_db,
            org_id,
            mock_active_contract.lease_id,
            date.today(),
            Decimal("0"),
            uuid.uuid4(),
            user_id,
        )

        assert result.success is False
        assert "positive" in result.message.lower()


class TestLeasePostingAdapterDepreciation:
    """Tests for ROU depreciation posting."""

    def test_post_rou_depreciation_not_found(self, mock_db, org_id, user_id):
        """Test posting depreciation on non-existent lease fails."""
        from app.services.finance.lease.lease_posting_adapter import LeasePostingAdapter

        mock_db.get.return_value = None

        result = LeasePostingAdapter.post_rou_depreciation(
            mock_db,
            org_id,
            uuid.uuid4(),
            date.today(),
            Decimal("2000.00"),
            user_id,
        )

        assert result.success is False
        assert "not found" in result.message.lower()

    def test_post_rou_depreciation_no_asset(
        self, mock_db, org_id, user_id, mock_active_contract
    ):
        """Test posting depreciation without asset fails."""
        from app.services.finance.lease.lease_posting_adapter import LeasePostingAdapter

        mock_db.get.return_value = mock_active_contract
        mock_db.scalars.return_value.first.return_value = None

        result = LeasePostingAdapter.post_rou_depreciation(
            mock_db,
            org_id,
            mock_active_contract.lease_id,
            date.today(),
            Decimal("2000.00"),
            user_id,
        )

        assert result.success is False
        assert "asset" in result.message.lower()

    def test_post_rou_depreciation_zero_amount(
        self, mock_db, org_id, user_id, mock_active_contract, mock_asset
    ):
        """Test posting zero depreciation fails."""
        from app.services.finance.lease.lease_posting_adapter import LeasePostingAdapter

        mock_db.get.return_value = mock_active_contract
        mock_db.scalars.return_value.first.return_value = mock_asset

        result = LeasePostingAdapter.post_rou_depreciation(
            mock_db,
            org_id,
            mock_active_contract.lease_id,
            date.today(),
            Decimal("0"),
            user_id,
        )

        assert result.success is False
        assert "positive" in result.message.lower()


class TestLeasePostingAdapterTermination:
    """Tests for lease termination posting."""

    def test_post_lease_termination_not_found(self, mock_db, org_id, user_id):
        """Test posting termination on non-existent lease fails."""
        from app.services.finance.lease.lease_posting_adapter import LeasePostingAdapter

        mock_db.get.return_value = None

        result = LeasePostingAdapter.post_lease_termination(
            mock_db,
            org_id,
            uuid.uuid4(),
            date.today(),
            user_id,
        )

        assert result.success is False
        assert "not found" in result.message.lower()

    def test_post_lease_termination_wrong_status(
        self, mock_db, org_id, user_id, mock_active_contract
    ):
        """Test posting termination on non-terminated lease fails."""
        from app.services.finance.lease.lease_posting_adapter import LeasePostingAdapter

        mock_active_contract.status = LeaseStatus.ACTIVE  # Not TERMINATED
        mock_db.get.return_value = mock_active_contract

        result = LeasePostingAdapter.post_lease_termination(
            mock_db,
            org_id,
            mock_active_contract.lease_id,
            date.today(),
            user_id,
        )

        assert result.success is False
        assert "TERMINATED" in result.message

    def test_post_lease_termination_no_liability(
        self, mock_db, org_id, user_id, mock_contract
    ):
        """Test posting termination without liability fails."""
        from app.services.finance.lease.lease_posting_adapter import LeasePostingAdapter

        mock_contract.status = LeaseStatus.TERMINATED
        mock_db.get.return_value = mock_contract
        mock_db.scalars.return_value.first.return_value = None

        result = LeasePostingAdapter.post_lease_termination(
            mock_db,
            org_id,
            mock_contract.lease_id,
            date.today(),
            user_id,
        )

        assert result.success is False
        assert "must exist" in result.message.lower()


class TestLeasePostingAdapterSuccessCases:
    """Tests for successful posting scenarios."""

    def test_post_initial_recognition_success(
        self, mock_db, org_id, user_id, mock_active_contract, mock_liability, mock_asset
    ):
        """Test successful initial recognition posting."""
        from app.services.finance.lease.lease_posting_adapter import LeasePostingAdapter

        mock_db.get.return_value = mock_active_contract
        mock_db.scalars.return_value.first.side_effect = [
            mock_liability,
            mock_asset,
        ]

        with (
            patch(
                "app.services.finance.lease.lease_posting_adapter.BasePostingAdapter.create_and_approve_journal"
            ) as mock_journal_svc,
            patch(
                "app.services.finance.lease.lease_posting_adapter.BasePostingAdapter.post_to_ledger"
            ) as mock_posting_svc,
        ):
            mock_journal_result = MagicMock()
            mock_journal_result.journal_entry_id = uuid.uuid4()
            mock_journal_svc.return_value = (mock_journal_result, None)

            mock_posting_result = MagicMock()
            mock_posting_result.success = True
            mock_posting_result.posting_batch_id = uuid.uuid4()
            mock_posting_result.message = "Posted successfully"
            mock_posting_svc.return_value = mock_posting_result

            result = LeasePostingAdapter.post_initial_recognition(
                mock_db,
                org_id,
                mock_active_contract.lease_id,
                date.today(),
                user_id,
            )

            assert result.success is True
            assert result.journal_entry_id is not None
            assert "successfully" in result.message.lower()

    def test_post_initial_recognition_with_restoration_obligation(
        self, mock_db, org_id, user_id, mock_active_contract, mock_liability, mock_asset
    ):
        """Test initial recognition with restoration obligation."""
        from app.services.finance.lease.lease_posting_adapter import LeasePostingAdapter

        mock_active_contract.restoration_obligation = Decimal("5000.00")
        mock_db.get.return_value = mock_active_contract
        mock_db.scalars.return_value.first.side_effect = [
            mock_liability,
            mock_asset,
        ]

        with (
            patch(
                "app.services.finance.lease.lease_posting_adapter.BasePostingAdapter.create_and_approve_journal"
            ) as mock_journal_svc,
            patch(
                "app.services.finance.lease.lease_posting_adapter.BasePostingAdapter.post_to_ledger"
            ) as mock_posting_svc,
        ):
            mock_journal_result = MagicMock()
            mock_journal_result.journal_entry_id = uuid.uuid4()
            mock_journal_svc.return_value = (mock_journal_result, None)

            mock_posting_result = MagicMock()
            mock_posting_result.success = True
            mock_posting_result.posting_batch_id = uuid.uuid4()
            mock_posting_result.message = "Posted successfully"
            mock_posting_svc.return_value = mock_posting_result

            result = LeasePostingAdapter.post_initial_recognition(
                mock_db,
                org_id,
                mock_active_contract.lease_id,
                date.today(),
                user_id,
            )

            assert result.success is True

    def test_post_interest_accrual_success(
        self, mock_db, org_id, user_id, mock_active_contract, mock_liability
    ):
        """Test successful interest accrual posting."""
        from app.services.finance.lease.lease_posting_adapter import LeasePostingAdapter

        mock_db.get.return_value = mock_active_contract
        mock_db.scalars.return_value.first.return_value = mock_liability

        with (
            patch(
                "app.services.finance.lease.lease_posting_adapter.BasePostingAdapter.create_and_approve_journal"
            ) as mock_journal_svc,
            patch(
                "app.services.finance.lease.lease_posting_adapter.BasePostingAdapter.post_to_ledger"
            ) as mock_posting_svc,
        ):
            mock_journal_result = MagicMock()
            mock_journal_result.journal_entry_id = uuid.uuid4()
            mock_journal_svc.return_value = (mock_journal_result, None)

            mock_posting_result = MagicMock()
            mock_posting_result.success = True
            mock_posting_result.posting_batch_id = uuid.uuid4()
            mock_posting_result.message = "Posted successfully"
            mock_posting_svc.return_value = mock_posting_result

            result = LeasePostingAdapter.post_interest_accrual(
                mock_db,
                org_id,
                mock_active_contract.lease_id,
                date.today(),
                Decimal("1500.00"),
                user_id,
            )

            assert result.success is True
            assert result.journal_entry_id is not None

    def test_post_lease_payment_success(
        self, mock_db, org_id, user_id, mock_active_contract, mock_liability
    ):
        """Test successful lease payment posting."""
        from app.services.finance.lease.lease_posting_adapter import LeasePostingAdapter

        mock_db.get.return_value = mock_active_contract
        mock_db.scalars.return_value.first.return_value = mock_liability

        with (
            patch(
                "app.services.finance.lease.lease_posting_adapter.BasePostingAdapter.create_and_approve_journal"
            ) as mock_journal_svc,
            patch(
                "app.services.finance.lease.lease_posting_adapter.BasePostingAdapter.post_to_ledger"
            ) as mock_posting_svc,
        ):
            mock_journal_result = MagicMock()
            mock_journal_result.journal_entry_id = uuid.uuid4()
            mock_journal_svc.return_value = (mock_journal_result, None)

            mock_posting_result = MagicMock()
            mock_posting_result.success = True
            mock_posting_result.posting_batch_id = uuid.uuid4()
            mock_posting_result.message = "Posted successfully"
            mock_posting_svc.return_value = mock_posting_result

            result = LeasePostingAdapter.post_lease_payment(
                mock_db,
                org_id,
                mock_active_contract.lease_id,
                date.today(),
                Decimal("5000.00"),
                uuid.uuid4(),  # cash account
                user_id,
            )

            assert result.success is True
            assert result.journal_entry_id is not None

    def test_post_rou_depreciation_success(
        self, mock_db, org_id, user_id, mock_active_contract, mock_asset
    ):
        """Test successful ROU depreciation posting."""
        from app.services.finance.lease.lease_posting_adapter import LeasePostingAdapter

        mock_db.get.return_value = mock_active_contract
        mock_db.scalars.return_value.first.return_value = mock_asset

        with (
            patch(
                "app.services.finance.lease.lease_posting_adapter.BasePostingAdapter.create_and_approve_journal"
            ) as mock_journal_svc,
            patch(
                "app.services.finance.lease.lease_posting_adapter.BasePostingAdapter.post_to_ledger"
            ) as mock_posting_svc,
        ):
            mock_journal_result = MagicMock()
            mock_journal_result.journal_entry_id = uuid.uuid4()
            mock_journal_svc.return_value = (mock_journal_result, None)

            mock_posting_result = MagicMock()
            mock_posting_result.success = True
            mock_posting_result.posting_batch_id = uuid.uuid4()
            mock_posting_result.message = "Posted successfully"
            mock_posting_svc.return_value = mock_posting_result

            result = LeasePostingAdapter.post_rou_depreciation(
                mock_db,
                org_id,
                mock_active_contract.lease_id,
                date.today(),
                Decimal("2000.00"),
                user_id,
            )

            assert result.success is True
            assert result.journal_entry_id is not None

    def test_post_lease_termination_success_with_gain(
        self, mock_db, org_id, user_id, mock_contract, mock_liability, mock_asset
    ):
        """Test successful lease termination with gain."""
        from app.services.finance.lease.lease_posting_adapter import LeasePostingAdapter

        mock_contract.status = LeaseStatus.TERMINATED
        mock_liability.current_liability_balance = Decimal("30000.00")
        mock_asset.carrying_amount = Decimal("25000.00")
        mock_asset.accumulated_depreciation = Decimal("15000.00")
        mock_asset.initial_rou_asset_value = Decimal("40000.00")

        mock_db.get.return_value = mock_contract
        mock_db.scalars.return_value.first.side_effect = [
            mock_liability,
            mock_asset,
        ]

        with (
            patch(
                "app.services.finance.lease.lease_posting_adapter.BasePostingAdapter.create_and_approve_journal"
            ) as mock_journal_svc,
            patch(
                "app.services.finance.lease.lease_posting_adapter.BasePostingAdapter.post_to_ledger"
            ) as mock_posting_svc,
        ):
            mock_journal_result = MagicMock()
            mock_journal_result.journal_entry_id = uuid.uuid4()
            mock_journal_svc.return_value = (mock_journal_result, None)

            mock_posting_result = MagicMock()
            mock_posting_result.success = True
            mock_posting_result.posting_batch_id = uuid.uuid4()
            mock_posting_result.message = "Posted successfully"
            mock_posting_svc.return_value = mock_posting_result

            result = LeasePostingAdapter.post_lease_termination(
                mock_db,
                org_id,
                mock_contract.lease_id,
                date.today(),
                user_id,
            )

            assert result.success is True
            assert "successfully" in result.message.lower()

    def test_post_lease_termination_success_with_loss(
        self, mock_db, org_id, user_id, mock_contract, mock_liability, mock_asset
    ):
        """Test successful lease termination with loss."""
        from app.services.finance.lease.lease_posting_adapter import LeasePostingAdapter

        mock_contract.status = LeaseStatus.TERMINATED
        mock_liability.current_liability_balance = Decimal("20000.00")
        mock_asset.carrying_amount = Decimal("30000.00")
        mock_asset.accumulated_depreciation = Decimal("10000.00")
        mock_asset.initial_rou_asset_value = Decimal("40000.00")

        mock_db.get.return_value = mock_contract
        mock_db.scalars.return_value.first.side_effect = [
            mock_liability,
            mock_asset,
        ]

        with (
            patch(
                "app.services.finance.lease.lease_posting_adapter.BasePostingAdapter.create_and_approve_journal"
            ) as mock_journal_svc,
            patch(
                "app.services.finance.lease.lease_posting_adapter.BasePostingAdapter.post_to_ledger"
            ) as mock_posting_svc,
        ):
            mock_journal_result = MagicMock()
            mock_journal_result.journal_entry_id = uuid.uuid4()
            mock_journal_svc.return_value = (mock_journal_result, None)

            mock_posting_result = MagicMock()
            mock_posting_result.success = True
            mock_posting_result.posting_batch_id = uuid.uuid4()
            mock_posting_result.message = "Posted successfully"
            mock_posting_svc.return_value = mock_posting_result

            result = LeasePostingAdapter.post_lease_termination(
                mock_db,
                org_id,
                mock_contract.lease_id,
                date.today(),
                user_id,
            )

            assert result.success is True


class TestLeasePostingResult:
    """Tests for LeasePostingResult dataclass."""

    def test_create_success_result(self):
        """Test creating successful result."""
        from app.services.finance.lease.lease_posting_adapter import LeasePostingResult

        journal_id = uuid.uuid4()
        batch_id = uuid.uuid4()

        result = LeasePostingResult(
            success=True,
            journal_entry_id=journal_id,
            posting_batch_id=batch_id,
            message="Posted successfully",
        )

        assert result.success is True
        assert result.journal_entry_id == journal_id
        assert result.posting_batch_id == batch_id

    def test_create_failure_result(self):
        """Test creating failure result."""
        from app.services.finance.lease.lease_posting_adapter import LeasePostingResult

        result = LeasePostingResult(success=False, message="Posting failed")

        assert result.success is False
        assert result.journal_entry_id is None
        assert result.posting_batch_id is None
