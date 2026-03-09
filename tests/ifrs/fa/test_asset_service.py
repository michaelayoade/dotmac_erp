"""
Tests for AssetService and AssetCategoryService.
"""

import uuid
from datetime import date
from decimal import Decimal
from unittest.mock import patch

import pytest

from tests.ifrs.fa.conftest import (
    MockAsset,
    MockAssetCategory,
    MockAssetStatus,
)


class TestAssetCategoryService:
    """Tests for AssetCategoryService."""

    def test_create_category_success(self, mock_db, org_id):
        """Test successful category creation."""
        from app.services.fixed_assets.asset import (
            AssetCategoryInput,
            AssetCategoryService,
        )

        input_data = AssetCategoryInput(
            category_code="EQUIPMENT",
            category_name="Office Equipment",
            asset_account_id=uuid.uuid4(),
            accumulated_depreciation_account_id=uuid.uuid4(),
            depreciation_expense_account_id=uuid.uuid4(),
            gain_loss_disposal_account_id=uuid.uuid4(),
            useful_life_months=60,
        )

        # Mock no existing category with same code
        mock_db.scalars.return_value.first.return_value = None

        AssetCategoryService.create_category(mock_db, org_id, input_data)

        mock_db.add.assert_called_once()
        mock_db.flush.assert_called_once()
        mock_db.refresh.assert_called_once()

    def test_create_category_duplicate_code(self, mock_db, org_id):
        """Test category creation with duplicate code fails."""
        from fastapi import HTTPException

        from app.services.fixed_assets.asset import (
            AssetCategoryInput,
            AssetCategoryService,
        )

        existing_category = MockAssetCategory(organization_id=org_id)
        mock_db.scalars.return_value.first.return_value = existing_category

        input_data = AssetCategoryInput(
            category_code="EQUIPMENT",
            category_name="Office Equipment",
            asset_account_id=uuid.uuid4(),
            accumulated_depreciation_account_id=uuid.uuid4(),
            depreciation_expense_account_id=uuid.uuid4(),
            gain_loss_disposal_account_id=uuid.uuid4(),
            useful_life_months=60,
        )

        with pytest.raises(HTTPException) as exc_info:
            AssetCategoryService.create_category(mock_db, org_id, input_data)

        assert exc_info.value.status_code == 400
        assert "already exists" in exc_info.value.detail

    def test_get_category_success(self, mock_db, mock_category):
        """Test getting a category by ID."""
        from app.services.fixed_assets.asset import AssetCategoryService

        mock_db.get.return_value = mock_category

        result = AssetCategoryService.get(mock_db, str(mock_category.category_id))

        assert result is not None
        assert result.category_id == mock_category.category_id
        mock_db.get.assert_called_once()

    def test_get_category_not_found(self, mock_db):
        """Test getting non-existent category raises HTTPException."""
        from fastapi import HTTPException

        from app.services.fixed_assets.asset import AssetCategoryService

        mock_db.get.return_value = None

        with pytest.raises(HTTPException) as exc_info:
            AssetCategoryService.get(mock_db, str(uuid.uuid4()))

        assert exc_info.value.status_code == 404

    def test_list_categories(self, mock_db, org_id):
        """Test listing categories."""
        from app.services.fixed_assets.asset import AssetCategoryService

        mock_categories = [MockAssetCategory(organization_id=org_id) for _ in range(5)]
        mock_db.scalars.return_value.all.return_value = mock_categories

        result = AssetCategoryService.list(mock_db, str(org_id))

        assert len(result) == 5

    def test_list_categories_with_filters(self, mock_db, org_id):
        """Test listing categories with is_active filter."""
        from app.services.fixed_assets.asset import AssetCategoryService

        mock_categories = [MockAssetCategory(organization_id=org_id, is_active=True)]
        mock_db.scalars.return_value.all.return_value = mock_categories

        result = AssetCategoryService.list(mock_db, str(org_id), is_active=True)

        assert len(result) == 1


class TestAssetService:
    """Tests for AssetService."""

    def test_create_asset_success(self, mock_db, org_id, mock_category, user_id):
        """Test successful asset creation."""
        from app.services.fixed_assets.asset import AssetInput, AssetService

        # Mock category lookup
        mock_db.get.return_value = mock_category

        input_data = AssetInput(
            asset_name="Office Computer",
            category_id=mock_category.category_id,
            acquisition_date=date.today(),
            acquisition_cost=Decimal("5000.00"),
            currency_code="USD",
        )

        with patch(
            "app.services.fixed_assets.asset.SequenceService.get_next_number"
        ) as mock_seq:
            mock_seq.return_value = "FA-0001"
            AssetService.create_asset(mock_db, org_id, input_data, user_id)

        mock_db.add.assert_called_once()
        mock_db.flush.assert_called_once()
        mock_db.refresh.assert_called_once()

    def test_create_asset_category_not_found(self, mock_db, org_id, user_id):
        """Test asset creation fails when category not found."""
        from fastapi import HTTPException

        from app.services.fixed_assets.asset import AssetInput, AssetService

        mock_db.get.return_value = None

        input_data = AssetInput(
            asset_name="Office Computer",
            category_id=uuid.uuid4(),
            acquisition_date=date.today(),
            acquisition_cost=Decimal("5000.00"),
            currency_code="USD",
        )

        with pytest.raises(HTTPException) as exc_info:
            AssetService.create_asset(mock_db, org_id, input_data, user_id)

        assert exc_info.value.status_code == 404

    def test_create_asset_category_inactive(
        self, mock_db, org_id, mock_category, user_id
    ):
        """Test asset creation fails when category is inactive."""
        from fastapi import HTTPException

        from app.services.fixed_assets.asset import AssetInput, AssetService

        mock_category.is_active = False
        mock_db.get.return_value = mock_category

        input_data = AssetInput(
            asset_name="Office Computer",
            category_id=mock_category.category_id,
            acquisition_date=date.today(),
            acquisition_cost=Decimal("5000.00"),
            currency_code="USD",
        )

        with pytest.raises(HTTPException) as exc_info:
            AssetService.create_asset(mock_db, org_id, input_data, user_id)

        assert exc_info.value.status_code == 400
        assert "not active" in exc_info.value.detail

    def test_create_asset_below_threshold(
        self, mock_db, org_id, mock_category, user_id
    ):
        """Test asset creation fails when cost is below capitalization threshold."""
        from fastapi import HTTPException

        from app.services.fixed_assets.asset import AssetInput, AssetService

        mock_category.capitalization_threshold = Decimal("1000")
        mock_db.get.return_value = mock_category

        input_data = AssetInput(
            asset_name="Office Computer",
            category_id=mock_category.category_id,
            acquisition_date=date.today(),
            acquisition_cost=Decimal("500.00"),  # Below threshold
            currency_code="USD",
        )

        with pytest.raises(HTTPException) as exc_info:
            AssetService.create_asset(mock_db, org_id, input_data, user_id)

        assert exc_info.value.status_code == 400
        assert "capitalization threshold" in exc_info.value.detail

    def test_get_asset_success(self, mock_db, mock_asset):
        """Test getting an asset by ID."""
        from app.services.fixed_assets.asset import AssetService

        mock_db.get.return_value = mock_asset

        result = AssetService.get(mock_db, str(mock_asset.asset_id))

        assert result is not None
        assert result.asset_id == mock_asset.asset_id

    def test_get_asset_not_found(self, mock_db):
        """Test getting non-existent asset raises HTTPException."""
        from fastapi import HTTPException

        from app.services.fixed_assets.asset import AssetService

        mock_db.get.return_value = None

        with pytest.raises(HTTPException) as exc_info:
            AssetService.get(mock_db, str(uuid.uuid4()))

        assert exc_info.value.status_code == 404

    def test_get_asset_by_number(self, mock_db, org_id, mock_asset):
        """Test getting asset by asset number."""
        from app.services.fixed_assets.asset import AssetService

        mock_db.scalars.return_value.first.return_value = mock_asset

        result = AssetService.get_by_number(mock_db, org_id, "FA-0001")

        assert result is not None
        assert result.asset_number == "FA-0001"

    def test_list_assets(self, mock_db, org_id):
        """Test listing assets."""
        from app.services.fixed_assets.asset import AssetService

        mock_assets = [MockAsset(organization_id=org_id) for _ in range(5)]
        mock_db.scalars.return_value.all.return_value = mock_assets

        result = AssetService.list(mock_db, str(org_id))

        assert len(result) == 5

    def test_list_assets_with_category_filter(self, mock_db, org_id, mock_category):
        """Test listing assets with category filter."""
        from app.services.fixed_assets.asset import AssetService

        mock_assets = [
            MockAsset(organization_id=org_id, category_id=mock_category.category_id)
        ]
        mock_db.scalars.return_value.all.return_value = mock_assets

        result = AssetService.list(
            mock_db, str(org_id), category_id=str(mock_category.category_id)
        )

        assert len(result) == 1

    def test_get_depreciable_assets(self, mock_db, org_id):
        """Test getting depreciable assets."""
        from app.services.fixed_assets.asset import AssetService

        mock_assets = [
            MockAsset(organization_id=org_id, status=MockAssetStatus.ACTIVE)
            for _ in range(3)
        ]
        mock_db.scalars.return_value.all.return_value = mock_assets

        result = AssetService.get_depreciable_assets(mock_db, org_id)

        assert len(result) == 3

    def test_update_asset_success(self, mock_db, org_id, mock_asset):
        """Test successful asset update."""
        from app.services.fixed_assets.asset import AssetService

        mock_asset.status = MockAssetStatus.DRAFT
        mock_db.get.return_value = mock_asset

        AssetService.update_asset(
            mock_db,
            org_id,
            mock_asset.asset_id,
            {"asset_name": "Updated Computer"},
        )

        mock_db.flush.assert_called_once()

    def test_update_asset_not_found(self, mock_db, org_id):
        """Test updating non-existent asset fails."""
        from fastapi import HTTPException

        from app.services.fixed_assets.asset import AssetService

        mock_db.get.return_value = None

        with pytest.raises(HTTPException) as exc_info:
            AssetService.update_asset(
                mock_db,
                org_id,
                uuid.uuid4(),
                {"asset_name": "Updated Computer"},
            )

        assert exc_info.value.status_code == 404

    def test_update_asset_restricted_after_activation(
        self, mock_db, org_id, mock_asset
    ):
        """Test that certain fields can't be updated after activation."""
        from fastapi import HTTPException

        from app.services.fixed_assets.asset import AssetService

        mock_asset.status = MockAssetStatus.ACTIVE  # Not DRAFT
        mock_db.get.return_value = mock_asset

        with pytest.raises(HTTPException) as exc_info:
            AssetService.update_asset(
                mock_db,
                org_id,
                mock_asset.asset_id,
                {"acquisition_cost": Decimal("10000")},  # Draft-only field
            )

        assert exc_info.value.status_code == 400
        assert "after asset activation" in exc_info.value.detail

    def test_activate_asset_success(self, mock_db, org_id, mock_asset):
        """Test successful asset activation."""
        from app.services.fixed_assets.asset import AssetService

        mock_asset.status = MockAssetStatus.DRAFT
        mock_db.get.return_value = mock_asset

        AssetService.activate_asset(mock_db, org_id, mock_asset.asset_id)

        mock_db.flush.assert_called_once()

    def test_activate_asset_wrong_status(self, mock_db, org_id, mock_asset):
        """Test activating asset with wrong status fails."""
        from fastapi import HTTPException

        from app.services.fixed_assets.asset import AssetService

        mock_asset.status = MockAssetStatus.ACTIVE  # Already active
        mock_db.get.return_value = mock_asset

        with pytest.raises(HTTPException) as exc_info:
            AssetService.activate_asset(mock_db, org_id, mock_asset.asset_id)

        assert exc_info.value.status_code == 400

    def test_get_asset_summary(self, mock_db, org_id):
        """Test getting asset summary statistics."""
        from app.services.fixed_assets.asset import AssetService

        mock_assets = [
            MockAsset(
                organization_id=org_id,
                status=MockAssetStatus.ACTIVE,
                acquisition_cost=Decimal("10000"),
                accumulated_depreciation=Decimal("2000"),
                net_book_value=Decimal("8000"),
            )
            for _ in range(3)
        ]
        mock_db.scalars.return_value.all.return_value = mock_assets

        result = AssetService.get_asset_summary(mock_db, org_id)

        assert result["total_assets"] == 3
        assert result["total_cost"] == Decimal("30000")
        assert result["total_accumulated_depreciation"] == Decimal("6000")
        assert result["total_net_book_value"] == Decimal("24000")

    def test_mark_fully_depreciated(self, mock_db, org_id, mock_asset):
        """Test marking an asset as fully depreciated."""
        from app.services.fixed_assets.asset import AssetService

        mock_asset.status = MockAssetStatus.ACTIVE
        mock_db.get.return_value = mock_asset

        AssetService.mark_fully_depreciated(mock_db, org_id, mock_asset.asset_id)

        mock_db.flush.assert_called_once()
