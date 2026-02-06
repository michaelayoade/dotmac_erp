"""
Tests for AssetRevaluationService.
"""

import uuid
from datetime import date
from decimal import Decimal

import pytest

from tests.ifrs.fa.conftest import (
    MockAssetRevaluation,
    MockAssetStatus,
)


class TestAssetRevaluationService:
    """Tests for AssetRevaluationService."""

    def test_create_revaluation_upward(
        self, mock_db, org_id, mock_asset, mock_category, user_id
    ):
        """Test creating an upward revaluation (surplus)."""
        from app.services.fixed_assets.revaluation import (
            AssetRevaluationService,
            RevaluationInput,
        )

        mock_asset.status = MockAssetStatus.ACTIVE
        mock_asset.net_book_value = Decimal("5000")
        mock_asset.organization_id = org_id
        mock_asset.accumulated_depreciation = Decimal("2000")

        mock_category.revaluation_model_allowed = True

        # db.get called for asset first, then category
        mock_db.get.side_effect = [mock_asset, mock_category]
        # Mock prior revaluations query
        mock_db.query.return_value.filter.return_value.order_by.return_value.all.return_value = []

        input_data = RevaluationInput(
            asset_id=mock_asset.asset_id,
            fiscal_period_id=uuid.uuid4(),
            revaluation_date=date.today(),
            fair_value=Decimal("6000"),
            valuation_method="Market Approach",
            valuer_name="Valuation Co.",
        )

        result = AssetRevaluationService.create_revaluation(
            mock_db, org_id, input_data, user_id
        )

        mock_db.add.assert_called_once()
        mock_db.commit.assert_called_once()

    def test_create_revaluation_downward(
        self, mock_db, org_id, mock_asset, mock_category, user_id
    ):
        """Test creating a downward revaluation (deficit)."""
        from app.services.fixed_assets.revaluation import (
            AssetRevaluationService,
            RevaluationInput,
        )

        mock_asset.status = MockAssetStatus.ACTIVE
        mock_asset.net_book_value = Decimal("5000")
        mock_asset.organization_id = org_id
        mock_asset.accumulated_depreciation = Decimal("2000")

        mock_category.revaluation_model_allowed = True

        mock_db.get.side_effect = [mock_asset, mock_category]
        mock_db.query.return_value.filter.return_value.order_by.return_value.all.return_value = []

        input_data = RevaluationInput(
            asset_id=mock_asset.asset_id,
            fiscal_period_id=uuid.uuid4(),
            revaluation_date=date.today(),
            fair_value=Decimal("4000"),
            valuation_method="Market Approach",
        )

        result = AssetRevaluationService.create_revaluation(
            mock_db, org_id, input_data, user_id
        )

        mock_db.add.assert_called_once()

    def test_create_revaluation_asset_not_found(self, mock_db, org_id, user_id):
        """Test revaluation creation fails when asset not found."""
        from app.services.fixed_assets.revaluation import (
            AssetRevaluationService,
            RevaluationInput,
        )
        from fastapi import HTTPException

        mock_db.get.return_value = None

        input_data = RevaluationInput(
            asset_id=uuid.uuid4(),
            fiscal_period_id=uuid.uuid4(),
            revaluation_date=date.today(),
            fair_value=Decimal("6000"),
            valuation_method="Market Approach",
        )

        with pytest.raises(HTTPException) as exc_info:
            AssetRevaluationService.create_revaluation(
                mock_db, org_id, input_data, user_id
            )

        assert exc_info.value.status_code == 404

    def test_create_revaluation_asset_not_active(
        self, mock_db, org_id, mock_asset, user_id
    ):
        """Test revaluation fails for non-active asset."""
        from app.services.fixed_assets.revaluation import (
            AssetRevaluationService,
            RevaluationInput,
        )
        from fastapi import HTTPException

        mock_asset.status = MockAssetStatus.DRAFT  # Not active
        mock_asset.organization_id = org_id

        mock_db.get.return_value = mock_asset

        input_data = RevaluationInput(
            asset_id=mock_asset.asset_id,
            fiscal_period_id=uuid.uuid4(),
            revaluation_date=date.today(),
            fair_value=Decimal("6000"),
            valuation_method="Market Approach",
        )

        with pytest.raises(HTTPException) as exc_info:
            AssetRevaluationService.create_revaluation(
                mock_db, org_id, input_data, user_id
            )

        assert exc_info.value.status_code == 400

    def test_create_revaluation_not_allowed(
        self, mock_db, org_id, mock_asset, mock_category, user_id
    ):
        """Test revaluation fails when not allowed for category."""
        from app.services.fixed_assets.revaluation import (
            AssetRevaluationService,
            RevaluationInput,
        )
        from fastapi import HTTPException

        mock_asset.status = MockAssetStatus.ACTIVE
        mock_asset.organization_id = org_id

        mock_category.revaluation_model_allowed = False  # Not allowed

        mock_db.get.side_effect = [mock_asset, mock_category]

        input_data = RevaluationInput(
            asset_id=mock_asset.asset_id,
            fiscal_period_id=uuid.uuid4(),
            revaluation_date=date.today(),
            fair_value=Decimal("6000"),
            valuation_method="Market Approach",
        )

        with pytest.raises(HTTPException) as exc_info:
            AssetRevaluationService.create_revaluation(
                mock_db, org_id, input_data, user_id
            )

        assert exc_info.value.status_code == 400
        assert "not allowed" in exc_info.value.detail

    def test_create_revaluation_category_not_found(
        self, mock_db, org_id, mock_asset, user_id
    ):
        """Test revaluation fails when category not found."""
        from app.services.fixed_assets.revaluation import (
            AssetRevaluationService,
            RevaluationInput,
        )
        from fastapi import HTTPException

        mock_asset.status = MockAssetStatus.ACTIVE
        mock_asset.organization_id = org_id

        # Asset found, category not found
        mock_db.get.side_effect = [mock_asset, None]

        input_data = RevaluationInput(
            asset_id=mock_asset.asset_id,
            fiscal_period_id=uuid.uuid4(),
            revaluation_date=date.today(),
            fair_value=Decimal("6000"),
            valuation_method="Market Approach",
        )

        with pytest.raises(HTTPException) as exc_info:
            AssetRevaluationService.create_revaluation(
                mock_db, org_id, input_data, user_id
            )

        assert exc_info.value.status_code == 404
        assert "category" in exc_info.value.detail.lower()

    def test_revaluation_with_prior_surplus(
        self, mock_db, org_id, mock_asset, mock_category, user_id
    ):
        """Test revaluation when asset has prior revaluation surplus."""
        from app.services.fixed_assets.revaluation import (
            AssetRevaluationService,
            RevaluationInput,
        )

        mock_asset.status = MockAssetStatus.ACTIVE
        mock_asset.net_book_value = Decimal("6000")  # After prior revaluation
        mock_asset.organization_id = org_id
        mock_asset.accumulated_depreciation = Decimal("0")

        mock_category.revaluation_model_allowed = True

        # Simulate prior surplus
        prior_reval = MockAssetRevaluation(
            asset_id=mock_asset.asset_id,
            revaluation_date=date(2024, 1, 1),
        )
        prior_reval.surplus_to_equity = Decimal("1000")
        prior_reval.deficit_to_pl = Decimal("0")

        mock_db.get.side_effect = [mock_asset, mock_category]
        mock_db.query.return_value.filter.return_value.order_by.return_value.all.return_value = [
            prior_reval
        ]

        input_data = RevaluationInput(
            asset_id=mock_asset.asset_id,
            fiscal_period_id=uuid.uuid4(),
            revaluation_date=date.today(),
            fair_value=Decimal("5500"),  # Downward but still above original
            valuation_method="Market Approach",
        )

        result = AssetRevaluationService.create_revaluation(
            mock_db, org_id, input_data, user_id
        )

        mock_db.add.assert_called_once()

    def test_revaluation_reverses_prior_deficit(
        self, mock_db, org_id, mock_asset, mock_category, user_id
    ):
        """Test upward revaluation reverses prior P&L deficit."""
        from app.services.fixed_assets.revaluation import (
            AssetRevaluationService,
            RevaluationInput,
        )

        mock_asset.status = MockAssetStatus.ACTIVE
        mock_asset.net_book_value = Decimal("4000")  # After prior impairment
        mock_asset.organization_id = org_id
        mock_asset.accumulated_depreciation = Decimal("0")

        mock_category.revaluation_model_allowed = True

        # Simulate prior deficit
        prior_reval = MockAssetRevaluation(
            asset_id=mock_asset.asset_id,
            revaluation_date=date(2024, 1, 1),
        )
        prior_reval.surplus_to_equity = Decimal("0")
        prior_reval.deficit_to_pl = Decimal("1000")

        mock_db.get.side_effect = [mock_asset, mock_category]
        mock_db.query.return_value.filter.return_value.order_by.return_value.all.return_value = [
            prior_reval
        ]

        input_data = RevaluationInput(
            asset_id=mock_asset.asset_id,
            fiscal_period_id=uuid.uuid4(),
            revaluation_date=date.today(),
            fair_value=Decimal("5500"),  # Upward from 4000
            valuation_method="Market Approach",
        )

        result = AssetRevaluationService.create_revaluation(
            mock_db, org_id, input_data, user_id
        )

        mock_db.add.assert_called_once()

    def test_approve_revaluation_success(
        self, mock_db, org_id, mock_asset, mock_category, user_id
    ):
        """Test successful revaluation approval."""
        from app.services.fixed_assets.revaluation import AssetRevaluationService

        creator_id = uuid.uuid4()
        revaluation = MockAssetRevaluation(
            organization_id=org_id,
            asset_id=mock_asset.asset_id,
            created_by_user_id=creator_id,
        )
        revaluation.approved_by_user_id = None
        revaluation.carrying_amount_after = Decimal("6000")
        revaluation.accumulated_depreciation_after = Decimal("0")

        mock_asset.organization_id = org_id
        mock_asset.category_id = mock_category.category_id

        # db.get called for revaluation, asset, category
        mock_db.get.side_effect = [revaluation, mock_asset, mock_category]

        result = AssetRevaluationService.approve_revaluation(
            mock_db, org_id, revaluation.revaluation_id, user_id
        )

        mock_db.commit.assert_called_once()

    def test_approve_revaluation_not_found(self, mock_db, org_id, user_id):
        """Test approving non-existent revaluation fails."""
        from app.services.fixed_assets.revaluation import AssetRevaluationService
        from fastapi import HTTPException

        mock_db.get.return_value = None

        with pytest.raises(HTTPException) as exc_info:
            AssetRevaluationService.approve_revaluation(
                mock_db, org_id, uuid.uuid4(), user_id
            )

        assert exc_info.value.status_code == 404

    def test_approve_revaluation_sod_violation(
        self, mock_db, org_id, mock_asset, user_id
    ):
        """Test creator cannot approve own revaluation (SoD)."""
        from app.services.fixed_assets.revaluation import AssetRevaluationService
        from fastapi import HTTPException

        revaluation = MockAssetRevaluation(
            organization_id=org_id,
            asset_id=mock_asset.asset_id,
            created_by_user_id=user_id,  # Same as approver
        )
        revaluation.approved_by_user_id = None

        mock_asset.organization_id = org_id
        mock_db.get.side_effect = [revaluation, mock_asset]

        with pytest.raises(HTTPException) as exc_info:
            AssetRevaluationService.approve_revaluation(
                mock_db, org_id, revaluation.revaluation_id, user_id
            )

        assert exc_info.value.status_code == 400
        assert "Segregation of duties" in exc_info.value.detail
