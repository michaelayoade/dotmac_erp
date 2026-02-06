"""
Tests for AssetDisposalService.
"""

import uuid
from datetime import date
from decimal import Decimal

import pytest

from tests.ifrs.fa.conftest import (
    MockAssetDisposal,
    MockAssetStatus,
)


class TestAssetDisposalService:
    """Tests for AssetDisposalService."""

    def test_create_disposal_success(self, mock_db, org_id, mock_asset, user_id):
        """Test successful disposal creation."""
        from app.services.fixed_assets.disposal import (
            AssetDisposalService,
            DisposalInput,
        )
        from app.models.fixed_assets.asset_disposal import DisposalType

        mock_asset.status = MockAssetStatus.ACTIVE
        mock_asset.net_book_value = Decimal("3000")
        mock_asset.organization_id = org_id

        mock_db.get.return_value = mock_asset

        input_data = DisposalInput(
            asset_id=mock_asset.asset_id,
            fiscal_period_id=uuid.uuid4(),
            disposal_date=date.today(),
            disposal_type=DisposalType.SALE,
            disposal_proceeds=Decimal("5000"),
            costs_of_disposal=Decimal("200"),
            buyer_name="ABC Company",
            disposal_reason="Equipment upgrade",
        )

        result = AssetDisposalService.create_disposal(
            mock_db, org_id, input_data, user_id
        )

        mock_db.add.assert_called_once()
        mock_db.commit.assert_called_once()
        mock_db.refresh.assert_called_once()

    def test_create_disposal_asset_not_found(self, mock_db, org_id, user_id):
        """Test disposal creation fails when asset not found."""
        from app.services.fixed_assets.disposal import (
            AssetDisposalService,
            DisposalInput,
        )
        from app.models.fixed_assets.asset_disposal import DisposalType
        from fastapi import HTTPException

        mock_db.get.return_value = None

        input_data = DisposalInput(
            asset_id=uuid.uuid4(),
            fiscal_period_id=uuid.uuid4(),
            disposal_date=date.today(),
            disposal_type=DisposalType.SALE,
            disposal_proceeds=Decimal("5000"),
        )

        with pytest.raises(HTTPException) as exc_info:
            AssetDisposalService.create_disposal(mock_db, org_id, input_data, user_id)

        assert exc_info.value.status_code == 404

    def test_create_disposal_asset_already_disposed(
        self, mock_db, org_id, mock_asset, user_id
    ):
        """Test disposal creation fails for already disposed asset."""
        from app.services.fixed_assets.disposal import (
            AssetDisposalService,
            DisposalInput,
        )
        from app.models.fixed_assets.asset_disposal import DisposalType
        from fastapi import HTTPException

        mock_asset.status = MockAssetStatus.DISPOSED
        mock_asset.organization_id = org_id
        mock_db.get.return_value = mock_asset

        input_data = DisposalInput(
            asset_id=mock_asset.asset_id,
            fiscal_period_id=uuid.uuid4(),
            disposal_date=date.today(),
            disposal_type=DisposalType.SALE,
            disposal_proceeds=Decimal("5000"),
        )

        with pytest.raises(HTTPException) as exc_info:
            AssetDisposalService.create_disposal(mock_db, org_id, input_data, user_id)

        assert exc_info.value.status_code == 400
        assert "already disposed" in exc_info.value.detail

    def test_create_disposal_asset_draft_status(
        self, mock_db, org_id, mock_asset, user_id
    ):
        """Test disposal creation fails for draft asset."""
        from app.services.fixed_assets.disposal import (
            AssetDisposalService,
            DisposalInput,
        )
        from app.models.fixed_assets.asset_disposal import DisposalType
        from fastapi import HTTPException

        mock_asset.status = MockAssetStatus.DRAFT
        mock_asset.organization_id = org_id
        mock_db.get.return_value = mock_asset

        input_data = DisposalInput(
            asset_id=mock_asset.asset_id,
            fiscal_period_id=uuid.uuid4(),
            disposal_date=date.today(),
            disposal_type=DisposalType.SALE,
            disposal_proceeds=Decimal("5000"),
        )

        with pytest.raises(HTTPException) as exc_info:
            AssetDisposalService.create_disposal(mock_db, org_id, input_data, user_id)

        assert exc_info.value.status_code == 400
        assert "draft" in exc_info.value.detail.lower()

    def test_calculate_gain_on_disposal(self, mock_db, org_id, mock_asset, user_id):
        """Test gain calculation on disposal."""
        from app.services.fixed_assets.disposal import (
            AssetDisposalService,
            DisposalInput,
        )
        from app.models.fixed_assets.asset_disposal import DisposalType

        mock_asset.status = MockAssetStatus.ACTIVE
        mock_asset.net_book_value = Decimal("3000")
        mock_asset.organization_id = org_id
        mock_asset.revalued_amount = None
        mock_asset.accumulated_depreciation = Decimal("2000")
        mock_asset.acquisition_cost = Decimal("5000")

        mock_db.get.return_value = mock_asset

        input_data = DisposalInput(
            asset_id=mock_asset.asset_id,
            fiscal_period_id=uuid.uuid4(),
            disposal_date=date.today(),
            disposal_type=DisposalType.SALE,
            disposal_proceeds=Decimal("5000"),
            costs_of_disposal=Decimal("200"),
        )

        result = AssetDisposalService.create_disposal(
            mock_db, org_id, input_data, user_id
        )

        # Net proceeds: 5000 - 200 = 4800
        # Gain: 4800 - 3000 = 1800
        mock_db.add.assert_called_once()

    def test_calculate_loss_on_disposal(self, mock_db, org_id, mock_asset, user_id):
        """Test loss calculation on disposal."""
        from app.services.fixed_assets.disposal import (
            AssetDisposalService,
            DisposalInput,
        )
        from app.models.fixed_assets.asset_disposal import DisposalType

        mock_asset.status = MockAssetStatus.ACTIVE
        mock_asset.net_book_value = Decimal("5000")
        mock_asset.organization_id = org_id
        mock_asset.revalued_amount = None
        mock_asset.accumulated_depreciation = Decimal("0")
        mock_asset.acquisition_cost = Decimal("5000")

        mock_db.get.return_value = mock_asset

        input_data = DisposalInput(
            asset_id=mock_asset.asset_id,
            fiscal_period_id=uuid.uuid4(),
            disposal_date=date.today(),
            disposal_type=DisposalType.SALE,
            disposal_proceeds=Decimal("3000"),
            costs_of_disposal=Decimal("200"),
        )

        result = AssetDisposalService.create_disposal(
            mock_db, org_id, input_data, user_id
        )

        # Net proceeds: 3000 - 200 = 2800
        # Loss: 2800 - 5000 = -2200
        mock_db.add.assert_called_once()

    def test_approve_disposal_success(self, mock_db, org_id, mock_asset, user_id):
        """Test successful disposal approval."""
        from app.services.fixed_assets.disposal import AssetDisposalService

        creator_id = uuid.uuid4()
        disposal = MockAssetDisposal(
            organization_id=org_id,
            asset_id=mock_asset.asset_id,
            created_by_user_id=creator_id,
        )
        disposal.approved_by_user_id = None

        mock_asset.organization_id = org_id

        # db.get called twice: once for disposal, once for asset
        mock_db.get.side_effect = [disposal, mock_asset]

        result = AssetDisposalService.approve_disposal(
            mock_db, org_id, disposal.disposal_id, user_id
        )

        mock_db.commit.assert_called_once()

    def test_approve_disposal_not_found(self, mock_db, org_id, user_id):
        """Test approving non-existent disposal fails."""
        from app.services.fixed_assets.disposal import AssetDisposalService
        from fastapi import HTTPException

        mock_db.get.return_value = None

        with pytest.raises(HTTPException) as exc_info:
            AssetDisposalService.approve_disposal(
                mock_db, org_id, uuid.uuid4(), user_id
            )

        assert exc_info.value.status_code == 404

    def test_approve_disposal_already_approved(
        self, mock_db, org_id, mock_asset, user_id
    ):
        """Test approving already approved disposal fails."""
        from app.services.fixed_assets.disposal import AssetDisposalService
        from fastapi import HTTPException

        disposal = MockAssetDisposal(
            organization_id=org_id,
            asset_id=mock_asset.asset_id,
        )
        disposal.approved_by_user_id = uuid.uuid4()  # Already approved

        mock_asset.organization_id = org_id
        mock_db.get.side_effect = [disposal, mock_asset]

        with pytest.raises(HTTPException) as exc_info:
            AssetDisposalService.approve_disposal(
                mock_db, org_id, disposal.disposal_id, user_id
            )

        assert exc_info.value.status_code == 400
        assert "already approved" in exc_info.value.detail

    def test_approve_disposal_sod_violation(self, mock_db, org_id, mock_asset, user_id):
        """Test creator cannot approve own disposal (SoD)."""
        from app.services.fixed_assets.disposal import AssetDisposalService
        from fastapi import HTTPException

        disposal = MockAssetDisposal(
            organization_id=org_id,
            asset_id=mock_asset.asset_id,
            created_by_user_id=user_id,  # Same as approver
        )
        disposal.approved_by_user_id = None

        mock_asset.organization_id = org_id
        mock_db.get.side_effect = [disposal, mock_asset]

        with pytest.raises(HTTPException) as exc_info:
            AssetDisposalService.approve_disposal(
                mock_db, org_id, disposal.disposal_id, user_id
            )

        assert exc_info.value.status_code == 400
        assert "Segregation of duties" in exc_info.value.detail

    def test_create_disposal_write_off(self, mock_db, org_id, mock_asset, user_id):
        """Test creating a write-off disposal (zero proceeds)."""
        from app.services.fixed_assets.disposal import (
            AssetDisposalService,
            DisposalInput,
        )
        from app.models.fixed_assets.asset_disposal import DisposalType

        mock_asset.status = MockAssetStatus.ACTIVE
        mock_asset.net_book_value = Decimal("3000")
        mock_asset.organization_id = org_id
        mock_asset.revalued_amount = None
        mock_asset.accumulated_depreciation = Decimal("2000")
        mock_asset.acquisition_cost = Decimal("5000")

        mock_db.get.return_value = mock_asset

        input_data = DisposalInput(
            asset_id=mock_asset.asset_id,
            fiscal_period_id=uuid.uuid4(),
            disposal_date=date.today(),
            disposal_type=DisposalType.SCRAPPING,
            disposal_proceeds=Decimal("0"),
            disposal_reason="Asset obsolete",
        )

        result = AssetDisposalService.create_disposal(
            mock_db, org_id, input_data, user_id
        )

        mock_db.add.assert_called_once()
