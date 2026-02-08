"""
AssetDisposalService - Fixed asset disposal management.

Handles asset sales, scrapping, and other disposal types.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.finance.audit.audit_log import AuditAction
from app.models.fixed_assets.asset import Asset, AssetStatus
from app.models.fixed_assets.asset_disposal import AssetDisposal, DisposalType
from app.services.audit_dispatcher import fire_audit_event
from app.services.common import coerce_uuid
from app.services.response import ListResponseMixin

logger = logging.getLogger(__name__)


@dataclass
class DisposalInput:
    """Input for creating an asset disposal."""

    asset_id: UUID
    fiscal_period_id: UUID
    disposal_date: date
    disposal_type: DisposalType
    disposal_proceeds: Decimal = Decimal("0")
    costs_of_disposal: Decimal = Decimal("0")
    buyer_name: str | None = None
    buyer_reference: str | None = None
    invoice_number: str | None = None
    disposal_reason: str | None = None
    authorization_reference: str | None = None
    trade_in_asset_id: UUID | None = None
    insurance_claim_reference: str | None = None
    insurance_proceeds: Decimal | None = None


class AssetDisposalService(ListResponseMixin):
    """
    Service for fixed asset disposals.

    Handles sale, scrapping, donation, and other disposal types
    with gain/loss calculation and GL posting.
    """

    @staticmethod
    def create_disposal(
        db: Session,
        organization_id: UUID,
        input: DisposalInput,
        created_by_user_id: UUID,
    ) -> AssetDisposal:
        """
        Create a new asset disposal.

        Calculates gain/loss based on NBV vs net proceeds.

        Args:
            db: Database session
            organization_id: Organization scope
            input: Disposal input data
            created_by_user_id: User creating the disposal

        Returns:
            Created AssetDisposal
        """
        org_id = coerce_uuid(organization_id)
        user_id = coerce_uuid(created_by_user_id)
        ast_id = coerce_uuid(input.asset_id)

        # Load asset
        asset = db.get(Asset, ast_id)
        if not asset or asset.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Asset not found")

        if asset.status == AssetStatus.DISPOSED:
            raise HTTPException(
                status_code=400,
                detail="Asset is already disposed",
            )

        if asset.status == AssetStatus.DRAFT:
            raise HTTPException(
                status_code=400,
                detail="Cannot dispose of draft asset",
            )

        # Get values at disposal
        cost_at_disposal = asset.revalued_amount or asset.acquisition_cost
        accumulated_dep_at_disposal = asset.accumulated_depreciation
        nbv_at_disposal = asset.net_book_value

        # Calculate net proceeds
        net_proceeds = input.disposal_proceeds - input.costs_of_disposal

        # Add insurance proceeds if applicable
        if (
            input.disposal_type == DisposalType.INSURANCE_CLAIM
            and input.insurance_proceeds
        ):
            net_proceeds += input.insurance_proceeds

        # Calculate gain/loss
        gain_loss = net_proceeds - nbv_at_disposal

        disposal = AssetDisposal(
            asset_id=ast_id,
            fiscal_period_id=coerce_uuid(input.fiscal_period_id),
            disposal_date=input.disposal_date,
            disposal_type=input.disposal_type,
            cost_at_disposal=cost_at_disposal,
            accumulated_depreciation_at_disposal=accumulated_dep_at_disposal,
            net_book_value_at_disposal=nbv_at_disposal,
            disposal_proceeds=input.disposal_proceeds,
            costs_of_disposal=input.costs_of_disposal,
            net_proceeds=net_proceeds,
            gain_loss_on_disposal=gain_loss,
            buyer_name=input.buyer_name,
            buyer_reference=input.buyer_reference,
            invoice_number=input.invoice_number,
            disposal_reason=input.disposal_reason,
            authorization_reference=input.authorization_reference,
            trade_in_asset_id=input.trade_in_asset_id,
            insurance_claim_reference=input.insurance_claim_reference,
            insurance_proceeds=input.insurance_proceeds,
            created_by_user_id=user_id,
        )

        db.add(disposal)
        db.commit()
        db.refresh(disposal)

        fire_audit_event(
            db,
            org_id,
            "fa",
            "asset_disposal",
            str(disposal.disposal_id),
            AuditAction.INSERT,
            new_values={
                "asset_id": str(ast_id),
                "disposal_type": input.disposal_type.value,
                "disposal_date": str(input.disposal_date),
                "net_proceeds": str(disposal.net_proceeds),
                "gain_loss": str(disposal.gain_loss_on_disposal),
            },
            user_id=user_id,
        )

        return disposal

    @staticmethod
    def approve_disposal(
        db: Session,
        organization_id: UUID,
        disposal_id: UUID,
        approved_by_user_id: UUID,
    ) -> AssetDisposal:
        """
        Approve a disposal and mark asset as disposed.

        Args:
            db: Database session
            organization_id: Organization scope
            disposal_id: Disposal to approve
            approved_by_user_id: User approving

        Returns:
            Updated AssetDisposal
        """
        org_id = coerce_uuid(organization_id)
        disp_id = coerce_uuid(disposal_id)
        user_id = coerce_uuid(approved_by_user_id)

        disposal = db.get(AssetDisposal, disp_id)
        if not disposal:
            raise HTTPException(status_code=404, detail="Disposal not found")

        asset = db.get(Asset, disposal.asset_id)
        if not asset or asset.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Asset not found")

        if disposal.approved_by_user_id:
            raise HTTPException(
                status_code=400,
                detail="Disposal already approved",
            )

        # SoD check
        if disposal.created_by_user_id == user_id:
            raise HTTPException(
                status_code=400,
                detail="Segregation of duties violation: creator cannot approve",
            )

        # Update disposal
        disposal.approved_by_user_id = user_id
        disposal.approved_at = datetime.now(UTC)

        # Update asset status
        asset.status = AssetStatus.DISPOSED
        asset.disposal_date = disposal.disposal_date
        asset.disposal_proceeds = disposal.net_proceeds
        asset.disposal_gain_loss = disposal.gain_loss_on_disposal

        try:
            from app.services.finance.automation.event_dispatcher import (
                fire_workflow_event,
            )

            fire_workflow_event(
                db=db,
                organization_id=org_id,
                entity_type="ASSET_DISPOSAL",
                entity_id=disposal.disposal_id,
                event="ON_APPROVAL",
                old_values={},
                new_values={"status": "DISPOSED"},
                user_id=user_id,
            )
        except Exception:
            pass

        fire_audit_event(
            db,
            org_id,
            "fa",
            "asset_disposal",
            str(disposal.disposal_id),
            AuditAction.UPDATE,
            old_values={"status": "PENDING"},
            new_values={"status": "APPROVED", "approved_by": str(user_id)},
            user_id=user_id,
            reason="Asset disposal approved",
        )

        db.commit()
        db.refresh(disposal)

        return disposal

    @staticmethod
    def post_disposal(
        db: Session,
        organization_id: UUID,
        disposal_id: UUID,
        posted_by_user_id: UUID,
        posting_date: date | None = None,
    ) -> AssetDisposal:
        """
        Post an approved disposal to the GL.

        Args:
            db: Database session
            organization_id: Organization scope
            disposal_id: Disposal to post
            posted_by_user_id: User posting
            posting_date: Date for GL posting

        Returns:
            Updated AssetDisposal
        """
        from app.services.fixed_assets.fa_posting_adapter import FAPostingAdapter

        org_id = coerce_uuid(organization_id)
        disp_id = coerce_uuid(disposal_id)
        user_id = coerce_uuid(posted_by_user_id)

        disposal = db.get(AssetDisposal, disp_id)
        if not disposal:
            raise HTTPException(status_code=404, detail="Disposal not found")

        asset = db.get(Asset, disposal.asset_id)
        if not asset or asset.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Asset not found")

        if not disposal.approved_by_user_id:
            raise HTTPException(
                status_code=400,
                detail="Disposal must be approved before posting",
            )

        if disposal.journal_entry_id:
            raise HTTPException(
                status_code=400,
                detail="Disposal already posted",
            )

        result = FAPostingAdapter.post_asset_disposal(
            db=db,
            organization_id=org_id,
            disposal_id=disp_id,
            posting_date=posting_date or disposal.disposal_date,
            posted_by_user_id=user_id,
        )

        if not result.success:
            raise HTTPException(status_code=400, detail=result.message)

        disposal.journal_entry_id = result.journal_entry_id

        fire_audit_event(
            db,
            org_id,
            "fa",
            "asset_disposal",
            str(disposal.disposal_id),
            AuditAction.UPDATE,
            old_values={"journal_entry_id": None},
            new_values={"journal_entry_id": str(result.journal_entry_id)},
            user_id=user_id,
            reason="Asset disposal posted to GL",
        )

        db.commit()
        db.refresh(disposal)

        return disposal

    @staticmethod
    def calculate_gain_loss(
        cost: Decimal,
        accumulated_depreciation: Decimal,
        net_proceeds: Decimal,
    ) -> tuple[Decimal, Decimal]:
        """
        Calculate NBV and gain/loss on disposal.

        Args:
            cost: Original cost or revalued amount
            accumulated_depreciation: Accumulated depreciation
            net_proceeds: Net disposal proceeds

        Returns:
            Tuple of (net_book_value, gain_loss)
        """
        nbv = cost - accumulated_depreciation
        gain_loss = net_proceeds - nbv
        return (nbv, gain_loss)

    @staticmethod
    def get(
        db: Session,
        disposal_id: str,
        organization_id: UUID | None = None,
    ) -> AssetDisposal:
        """Get a disposal by ID."""
        disposal = db.get(AssetDisposal, coerce_uuid(disposal_id))
        if not disposal:
            raise HTTPException(status_code=404, detail="Disposal not found")
        if organization_id is not None:
            asset = db.get(Asset, disposal.asset_id)
            if not asset or asset.organization_id != coerce_uuid(organization_id):
                raise HTTPException(status_code=404, detail="Disposal not found")
        return disposal

    @staticmethod
    def get_asset_disposal(
        db: Session,
        organization_id: UUID,
        asset_id: UUID,
    ) -> AssetDisposal | None:
        """Get the disposal record for an asset if it exists."""
        org_id = coerce_uuid(organization_id)
        ast_id = coerce_uuid(asset_id)

        asset = db.get(Asset, ast_id)
        if not asset or asset.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Asset not found")

        return db.query(AssetDisposal).filter(AssetDisposal.asset_id == ast_id).first()

    @staticmethod
    def list(
        db: Session,
        organization_id: str | None = None,
        asset_id: str | None = None,
        disposal_type: DisposalType | None = None,
        fiscal_period_id: str | None = None,
        pending_approval: bool = False,
        from_date: date | None = None,
        to_date: date | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[AssetDisposal]:
        """List disposals with optional filters."""
        query = db.query(AssetDisposal)

        if asset_id:
            query = query.filter(AssetDisposal.asset_id == coerce_uuid(asset_id))
        elif organization_id:
            # Need to join to filter by org
            query = query.join(Asset, AssetDisposal.asset_id == Asset.asset_id).filter(
                Asset.organization_id == coerce_uuid(organization_id)
            )

        if disposal_type:
            query = query.filter(AssetDisposal.disposal_type == disposal_type)

        if fiscal_period_id:
            query = query.filter(
                AssetDisposal.fiscal_period_id == coerce_uuid(fiscal_period_id)
            )

        if pending_approval:
            query = query.filter(AssetDisposal.approved_by_user_id.is_(None))

        if from_date:
            query = query.filter(AssetDisposal.disposal_date >= from_date)

        if to_date:
            query = query.filter(AssetDisposal.disposal_date <= to_date)

        query = query.order_by(AssetDisposal.disposal_date.desc())
        return query.limit(limit).offset(offset).all()

    @staticmethod
    def get_disposal_summary(
        db: Session,
        organization_id: UUID,
        fiscal_period_id: UUID | None = None,
        from_date: date | None = None,
        to_date: date | None = None,
    ) -> dict:
        """
        Get summary of disposals.

        Args:
            db: Database session
            organization_id: Organization scope
            fiscal_period_id: Optional filter by period
            from_date: Optional start date filter
            to_date: Optional end date filter

        Returns:
            Summary statistics dictionary
        """
        org_id = coerce_uuid(organization_id)

        query = (
            db.query(AssetDisposal)
            .join(Asset, AssetDisposal.asset_id == Asset.asset_id)
            .filter(Asset.organization_id == org_id)
        )

        if fiscal_period_id:
            query = query.filter(
                AssetDisposal.fiscal_period_id == coerce_uuid(fiscal_period_id)
            )

        if from_date:
            query = query.filter(AssetDisposal.disposal_date >= from_date)

        if to_date:
            query = query.filter(AssetDisposal.disposal_date <= to_date)

        disposals = query.all()

        total_cost_disposed = Decimal("0")
        total_proceeds = Decimal("0")
        total_gain = Decimal("0")
        total_loss = Decimal("0")
        count_by_type: dict[str, int] = {}

        for disp in disposals:
            total_cost_disposed += disp.cost_at_disposal
            total_proceeds += disp.net_proceeds

            if disp.gain_loss_on_disposal >= 0:
                total_gain += disp.gain_loss_on_disposal
            else:
                total_loss += abs(disp.gain_loss_on_disposal)

            type_name = disp.disposal_type.value
            count_by_type[type_name] = count_by_type.get(type_name, 0) + 1

        return {
            "total_disposals": len(disposals),
            "total_cost_disposed": total_cost_disposed,
            "total_proceeds": total_proceeds,
            "total_gain": total_gain,
            "total_loss": total_loss,
            "net_gain_loss": total_gain - total_loss,
            "count_by_type": count_by_type,
        }


# Module-level singleton instance
asset_disposal_service = AssetDisposalService()
