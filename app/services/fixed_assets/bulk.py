"""
FA Asset Bulk Action Service.

Provides bulk operations for fixed asset master data.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.fixed_assets.asset import Asset, AssetStatus
from app.models.fixed_assets.depreciation_schedule import DepreciationSchedule
from app.services.bulk_actions import BulkActionService


class AssetBulkService(BulkActionService[Asset]):
    """
    Bulk operations for fixed assets.

    Supported actions:
    - delete: Remove assets (only DRAFT status with no depreciation)
    - activate: Set status to ACTIVE
    - export: Export to CSV
    """

    model = Asset
    id_field = "asset_id"
    org_field = "organization_id"

    # Fields to export in CSV
    export_fields = [
        ("asset_number", "Asset Number"),
        ("asset_name", "Asset Name"),
        ("description", "Description"),
        ("acquisition_date", "Acquisition Date"),
        ("in_service_date", "In Service Date"),
        ("acquisition_cost", "Acquisition Cost"),
        ("currency_code", "Currency"),
        ("residual_value", "Residual Value"),
        ("useful_life_months", "Useful Life (Months)"),
        ("depreciation_method", "Depreciation Method"),
        ("status", "Status"),
    ]

    def can_delete(self, entity: Asset) -> tuple[bool, str]:
        """
        Check if an asset can be deleted.

        An asset can only be deleted if:
        - Status is DRAFT
        - No depreciation schedules exist
        """
        # Only DRAFT assets can be deleted
        if entity.status != AssetStatus.DRAFT:
            return (
                False,
                f"Cannot delete '{entity.asset_name}': only DRAFT assets can be deleted (current status: {entity.status.value})",
            )

        # Check for depreciation schedules
        schedule_count = (
            self.db.query(DepreciationSchedule)
            .filter(DepreciationSchedule.asset_id == entity.asset_id)
            .count()
        )

        if schedule_count > 0:
            return (
                False,
                f"Cannot delete '{entity.asset_name}': has {schedule_count} depreciation schedule(s)",
            )

        return (True, "")

    def _get_export_value(self, entity: Asset, field_name: str) -> str:
        """Handle special field formatting for asset export."""
        if field_name == "status":
            return entity.status.value if entity.status else ""
        if field_name == "depreciation_method":
            return str(entity.depreciation_method) if entity.depreciation_method else ""
        if field_name in ("acquisition_date", "in_service_date"):
            val = getattr(entity, field_name, None)
            return val.isoformat() if val else ""

        return str(super()._get_export_value(entity, field_name))

    def _get_export_filename(self) -> str:
        """Get asset export filename."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return f"assets_export_{timestamp}.csv"


def get_asset_bulk_service(
    db: Session,
    organization_id: UUID,
    user_id: UUID | None = None,
) -> AssetBulkService:
    """Factory function to create an AssetBulkService instance."""
    return AssetBulkService(db, organization_id, user_id)
