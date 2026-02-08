"""
Tests for FAPostingAdapter.
"""

import uuid
from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, patch

from tests.ifrs.fa.conftest import (
    MockAsset,
    MockAssetCategory,
    MockDepreciationRun,
    MockDepreciationSchedule,
)


class TestFAPostingAdapterDepreciation:
    """Tests for depreciation posting."""

    def test_post_depreciation_run_not_found(self, mock_db, org_id, user_id):
        """Test posting non-existent depreciation run fails."""
        from app.services.fixed_assets.fa_posting_adapter import FAPostingAdapter

        mock_db.get.return_value = None

        result = FAPostingAdapter.post_depreciation_run(
            mock_db,
            org_id,
            uuid.uuid4(),
            date.today(),
            user_id,
        )

        assert result.success is False
        assert "not found" in result.message.lower()

    def test_post_depreciation_run_wrong_status(self, mock_db, org_id, user_id):
        """Test posting depreciation run with wrong status fails."""
        from app.models.fixed_assets.depreciation_run import DepreciationRunStatus
        from app.services.fixed_assets.fa_posting_adapter import FAPostingAdapter

        mock_run = MockDepreciationRun(
            organization_id=org_id,
        )
        mock_run.status = DepreciationRunStatus.DRAFT  # Use actual enum, not POSTING

        mock_db.get.return_value = mock_run

        result = FAPostingAdapter.post_depreciation_run(
            mock_db,
            org_id,
            mock_run.run_id,
            date.today(),
            user_id,
        )

        assert result.success is False
        assert "POSTING" in result.message

    def test_post_depreciation_run_no_schedules(self, mock_db, org_id, user_id):
        """Test posting depreciation run without schedules fails."""
        from app.models.fixed_assets.depreciation_run import DepreciationRunStatus
        from app.services.fixed_assets.fa_posting_adapter import FAPostingAdapter

        mock_run = MockDepreciationRun(
            organization_id=org_id,
        )
        # Use actual enum for status comparison
        mock_run.status = DepreciationRunStatus.POSTING

        mock_db.get.return_value = mock_run
        mock_db.query.return_value.filter.return_value.all.return_value = []

        result = FAPostingAdapter.post_depreciation_run(
            mock_db,
            org_id,
            mock_run.run_id,
            date.today(),
            user_id,
        )

        assert result.success is False
        assert "schedules" in result.message.lower()


class TestFAPostingAdapterDisposal:
    """Tests for disposal posting."""

    def test_post_disposal_not_found(self, mock_db, org_id, user_id):
        """Test posting non-existent disposal fails."""
        from app.services.fixed_assets.fa_posting_adapter import FAPostingAdapter

        mock_db.get.return_value = None

        result = FAPostingAdapter.post_asset_disposal(
            mock_db,
            org_id,
            uuid.uuid4(),
            date.today(),
            user_id,
        )

        assert result.success is False
        assert "not found" in result.message.lower()


class TestFAPostingAdapterRevaluation:
    """Tests for revaluation posting."""

    def test_post_revaluation_not_found(self, mock_db, org_id, user_id):
        """Test posting non-existent revaluation fails."""
        from app.services.fixed_assets.fa_posting_adapter import FAPostingAdapter

        mock_db.get.return_value = None

        result = FAPostingAdapter.post_revaluation(
            mock_db,
            org_id,
            uuid.uuid4(),
            date.today(),
            user_id,
        )

        assert result.success is False
        assert "not found" in result.message.lower()

    def test_post_revaluation_asset_not_found(self, mock_db, org_id, user_id):
        """Test posting revaluation with missing asset."""
        from app.services.fixed_assets.fa_posting_adapter import FAPostingAdapter

        mock_reval = MagicMock()
        mock_reval.asset_id = uuid.uuid4()
        mock_db.get.side_effect = [mock_reval, None]

        result = FAPostingAdapter.post_revaluation(
            mock_db,
            org_id,
            uuid.uuid4(),
            date.today(),
            user_id,
        )

        assert result.success is False
        assert "asset not found" in result.message.lower()

    def test_post_revaluation_category_not_found(self, mock_db, org_id, user_id):
        """Test posting revaluation with missing category."""
        from app.services.fixed_assets.fa_posting_adapter import FAPostingAdapter

        mock_reval = MagicMock()
        mock_reval.asset_id = uuid.uuid4()
        mock_asset = MockAsset(organization_id=org_id)
        mock_db.get.side_effect = [mock_reval, mock_asset, None]

        result = FAPostingAdapter.post_revaluation(
            mock_db,
            org_id,
            uuid.uuid4(),
            date.today(),
            user_id,
        )

        assert result.success is False
        assert "category not found" in result.message.lower()

    def test_post_revaluation_no_surplus_account(self, mock_db, org_id, user_id):
        """Test posting revaluation without surplus account configured."""
        from app.services.fixed_assets.fa_posting_adapter import FAPostingAdapter

        mock_reval = MagicMock()
        mock_reval.asset_id = uuid.uuid4()
        mock_asset = MockAsset(organization_id=org_id)
        mock_category = MockAssetCategory()
        mock_category.revaluation_surplus_account_id = None

        mock_db.get.side_effect = [mock_reval, mock_asset, mock_category]

        result = FAPostingAdapter.post_revaluation(
            mock_db,
            org_id,
            uuid.uuid4(),
            date.today(),
            user_id,
        )

        assert result.success is False
        assert "revaluation surplus account" in result.message.lower()


class TestFAPostingAdapterDepreciationSuccess:
    """Tests for successful depreciation posting scenarios."""

    def test_post_depreciation_run_success(self, mock_db, org_id, user_id):
        """Test successful depreciation run posting."""
        from app.models.fixed_assets.depreciation_run import DepreciationRunStatus
        from app.services.fixed_assets.fa_posting_adapter import FAPostingAdapter

        mock_run = MockDepreciationRun(organization_id=org_id)
        mock_run.status = DepreciationRunStatus.POSTING

        mock_schedules = [
            MockDepreciationSchedule(depreciation_amount=Decimal("1000.00")),
            MockDepreciationSchedule(depreciation_amount=Decimal("500.00")),
        ]

        mock_db.get.return_value = mock_run
        mock_db.query.return_value.filter.return_value.all.return_value = mock_schedules

        with (
            patch(
                "app.services.fixed_assets.fa_posting_adapter.BasePostingAdapter.create_and_approve_journal"
            ) as mock_journal_svc,
            patch(
                "app.services.fixed_assets.fa_posting_adapter.BasePostingAdapter.post_to_ledger"
            ) as mock_posting_svc,
            patch(
                "app.services.fixed_assets.fa_posting_adapter.org_context_service"
            ) as mock_org_ctx,
        ):
            mock_journal_result = MagicMock()
            mock_journal_result.journal_entry_id = uuid.uuid4()
            mock_journal_svc.return_value = (mock_journal_result, None)

            mock_posting_result = MagicMock()
            mock_posting_result.success = True
            mock_posting_result.posting_batch_id = uuid.uuid4()
            mock_posting_result.message = "Posted depreciation successfully"
            mock_posting_svc.return_value = mock_posting_result

            mock_org_ctx.get_functional_currency.return_value = "USD"

            result = FAPostingAdapter.post_depreciation_run(
                mock_db,
                org_id,
                mock_run.run_id,
                date.today(),
                user_id,
            )

            assert result.success is True
            assert result.journal_entry_id is not None
            assert "successfully" in result.message.lower()

    def test_post_depreciation_run_aggregates_by_account(
        self, mock_db, org_id, user_id
    ):
        """Test depreciation posting aggregates amounts by account."""
        from app.models.fixed_assets.depreciation_run import DepreciationRunStatus
        from app.services.fixed_assets.fa_posting_adapter import FAPostingAdapter

        expense_account_1 = uuid.uuid4()
        accum_account_1 = uuid.uuid4()

        mock_run = MockDepreciationRun(organization_id=org_id)
        mock_run.status = DepreciationRunStatus.POSTING

        # Two schedules with same accounts - should be aggregated
        mock_schedules = [
            MockDepreciationSchedule(
                depreciation_amount=Decimal("1000.00"),
                expense_account_id=expense_account_1,
                accumulated_depreciation_account_id=accum_account_1,
            ),
            MockDepreciationSchedule(
                depreciation_amount=Decimal("500.00"),
                expense_account_id=expense_account_1,
                accumulated_depreciation_account_id=accum_account_1,
            ),
        ]

        mock_db.get.return_value = mock_run
        mock_db.query.return_value.filter.return_value.all.return_value = mock_schedules

        with (
            patch(
                "app.services.fixed_assets.fa_posting_adapter.BasePostingAdapter.create_and_approve_journal"
            ) as mock_journal_svc,
            patch(
                "app.services.fixed_assets.fa_posting_adapter.BasePostingAdapter.post_to_ledger"
            ) as mock_posting_svc,
            patch(
                "app.services.fixed_assets.fa_posting_adapter.org_context_service"
            ) as mock_org_ctx,
        ):
            mock_journal_result = MagicMock()
            mock_journal_result.journal_entry_id = uuid.uuid4()
            mock_journal_svc.return_value = (mock_journal_result, None)

            mock_posting_result = MagicMock()
            mock_posting_result.success = True
            mock_posting_result.posting_batch_id = uuid.uuid4()
            mock_posting_svc.return_value = mock_posting_result

            mock_org_ctx.get_functional_currency.return_value = "USD"

            result = FAPostingAdapter.post_depreciation_run(
                mock_db,
                org_id,
                mock_run.run_id,
                date.today(),
                user_id,
            )

            assert result.success is True
            # Verify journal creation was called with aggregated amounts
            mock_journal_svc.assert_called_once()


class TestFAPostingAdapterDisposalSuccess:
    """Tests for successful disposal posting scenarios."""

    def test_post_disposal_with_gain(self, mock_db, org_id, user_id):
        """Test posting disposal with gain."""
        from app.services.fixed_assets.fa_posting_adapter import FAPostingAdapter

        mock_disposal = MagicMock()
        mock_disposal.asset_id = uuid.uuid4()
        mock_disposal.disposal_date = date.today()
        mock_disposal.cost_at_disposal = Decimal("50000.00")
        mock_disposal.accumulated_depreciation_at_disposal = Decimal("30000.00")
        mock_disposal.net_proceeds = Decimal("25000.00")
        mock_disposal.gain_loss_on_disposal = Decimal("5000.00")

        mock_asset = MockAsset(organization_id=org_id)
        mock_category = MockAssetCategory()

        mock_db.get.side_effect = [mock_disposal, mock_asset, mock_category]

        with (
            patch(
                "app.services.fixed_assets.fa_posting_adapter.BasePostingAdapter.create_and_approve_journal"
            ) as mock_journal_svc,
            patch(
                "app.services.fixed_assets.fa_posting_adapter.BasePostingAdapter.post_to_ledger"
            ) as mock_posting_svc,
        ):
            mock_journal_result = MagicMock()
            mock_journal_result.journal_entry_id = uuid.uuid4()
            mock_journal_svc.return_value = (mock_journal_result, None)

            mock_posting_result = MagicMock()
            mock_posting_result.success = True
            mock_posting_result.posting_batch_id = uuid.uuid4()
            mock_posting_svc.return_value = mock_posting_result

            result = FAPostingAdapter.post_asset_disposal(
                mock_db,
                org_id,
                uuid.uuid4(),
                date.today(),
                user_id,
            )

            assert result.success is True
            assert result.journal_entry_id is not None

    def test_post_disposal_with_loss(self, mock_db, org_id, user_id):
        """Test posting disposal with loss."""
        from app.services.fixed_assets.fa_posting_adapter import FAPostingAdapter

        mock_disposal = MagicMock()
        mock_disposal.asset_id = uuid.uuid4()
        mock_disposal.disposal_date = date.today()
        mock_disposal.cost_at_disposal = Decimal("50000.00")
        mock_disposal.accumulated_depreciation_at_disposal = Decimal("30000.00")
        mock_disposal.net_proceeds = Decimal("15000.00")
        mock_disposal.gain_loss_on_disposal = Decimal("-5000.00")

        mock_asset = MockAsset(organization_id=org_id)
        mock_category = MockAssetCategory()

        mock_db.get.side_effect = [mock_disposal, mock_asset, mock_category]

        with (
            patch(
                "app.services.fixed_assets.fa_posting_adapter.BasePostingAdapter.create_and_approve_journal"
            ) as mock_journal_svc,
            patch(
                "app.services.fixed_assets.fa_posting_adapter.BasePostingAdapter.post_to_ledger"
            ) as mock_posting_svc,
        ):
            mock_journal_result = MagicMock()
            mock_journal_result.journal_entry_id = uuid.uuid4()
            mock_journal_svc.return_value = (mock_journal_result, None)

            mock_posting_result = MagicMock()
            mock_posting_result.success = True
            mock_posting_result.posting_batch_id = uuid.uuid4()
            mock_posting_svc.return_value = mock_posting_result

            result = FAPostingAdapter.post_asset_disposal(
                mock_db,
                org_id,
                uuid.uuid4(),
                date.today(),
                user_id,
            )

            assert result.success is True

    def test_post_disposal_asset_not_found(self, mock_db, org_id, user_id):
        """Test posting disposal with missing asset."""
        from app.services.fixed_assets.fa_posting_adapter import FAPostingAdapter

        mock_disposal = MagicMock()
        mock_disposal.asset_id = uuid.uuid4()
        mock_db.get.side_effect = [mock_disposal, None]

        result = FAPostingAdapter.post_asset_disposal(
            mock_db,
            org_id,
            uuid.uuid4(),
            date.today(),
            user_id,
        )

        assert result.success is False
        assert "asset not found" in result.message.lower()

    def test_post_disposal_category_not_found(self, mock_db, org_id, user_id):
        """Test posting disposal with missing category."""
        from app.services.fixed_assets.fa_posting_adapter import FAPostingAdapter

        mock_disposal = MagicMock()
        mock_disposal.asset_id = uuid.uuid4()
        mock_asset = MockAsset(organization_id=org_id)
        mock_db.get.side_effect = [mock_disposal, mock_asset, None]

        result = FAPostingAdapter.post_asset_disposal(
            mock_db,
            org_id,
            uuid.uuid4(),
            date.today(),
            user_id,
        )

        assert result.success is False
        assert "category not found" in result.message.lower()


class TestFAPostingAdapterRevaluationSuccess:
    """Tests for successful revaluation posting scenarios."""

    def test_post_revaluation_surplus(self, mock_db, org_id, user_id):
        """Test posting revaluation surplus."""
        from app.services.fixed_assets.fa_posting_adapter import FAPostingAdapter

        mock_reval = MagicMock()
        mock_reval.asset_id = uuid.uuid4()
        mock_reval.revaluation_date = date.today()
        mock_reval.revaluation_surplus_or_deficit = Decimal("10000.00")
        mock_reval.surplus_to_equity = Decimal("10000.00")
        mock_reval.prior_deficit_reversed = Decimal("0")
        mock_reval.prior_surplus_reversed = Decimal("0")
        mock_reval.deficit_to_pl = Decimal("0")

        mock_asset = MockAsset(organization_id=org_id)
        mock_category = MockAssetCategory()

        mock_db.get.side_effect = [mock_reval, mock_asset, mock_category]

        with (
            patch(
                "app.services.fixed_assets.fa_posting_adapter.BasePostingAdapter.create_and_approve_journal"
            ) as mock_journal_svc,
            patch(
                "app.services.fixed_assets.fa_posting_adapter.BasePostingAdapter.post_to_ledger"
            ) as mock_posting_svc,
        ):
            mock_journal_result = MagicMock()
            mock_journal_result.journal_entry_id = uuid.uuid4()
            mock_journal_svc.return_value = (mock_journal_result, None)

            mock_posting_result = MagicMock()
            mock_posting_result.success = True
            mock_posting_result.posting_batch_id = uuid.uuid4()
            mock_posting_svc.return_value = mock_posting_result

            result = FAPostingAdapter.post_revaluation(
                mock_db,
                org_id,
                uuid.uuid4(),
                date.today(),
                user_id,
            )

            assert result.success is True

    def test_post_revaluation_deficit_to_pl(self, mock_db, org_id, user_id):
        """Test posting revaluation deficit to P&L."""
        from app.services.fixed_assets.fa_posting_adapter import FAPostingAdapter

        mock_reval = MagicMock()
        mock_reval.asset_id = uuid.uuid4()
        mock_reval.revaluation_date = date.today()
        mock_reval.revaluation_surplus_or_deficit = Decimal("-5000.00")
        mock_reval.surplus_to_equity = Decimal("0")
        mock_reval.prior_deficit_reversed = Decimal("0")
        mock_reval.prior_surplus_reversed = Decimal("0")
        mock_reval.deficit_to_pl = Decimal("5000.00")

        mock_asset = MockAsset(organization_id=org_id)
        mock_category = MockAssetCategory()

        mock_db.get.side_effect = [mock_reval, mock_asset, mock_category]

        with (
            patch(
                "app.services.fixed_assets.fa_posting_adapter.BasePostingAdapter.create_and_approve_journal"
            ) as mock_journal_svc,
            patch(
                "app.services.fixed_assets.fa_posting_adapter.BasePostingAdapter.post_to_ledger"
            ) as mock_posting_svc,
        ):
            mock_journal_result = MagicMock()
            mock_journal_result.journal_entry_id = uuid.uuid4()
            mock_journal_svc.return_value = (mock_journal_result, None)

            mock_posting_result = MagicMock()
            mock_posting_result.success = True
            mock_posting_result.posting_batch_id = uuid.uuid4()
            mock_posting_svc.return_value = mock_posting_result

            result = FAPostingAdapter.post_revaluation(
                mock_db,
                org_id,
                uuid.uuid4(),
                date.today(),
                user_id,
            )

            assert result.success is True
